"""On-demand company research, grounded only in primary documents.

Pipeline (yields progress events so the UI can show the agent working):
  1. screener index -> reported financials + links to transcripts / annual report
  2. numbers.analyse -> trajectory, balance sheet, cash quality, rule-based flags (no model)
  3. cheap model x4  -> per-transcript extraction: guidance with verbatim quotes + page refs
  4. quote check     -> every quote is matched back to the transcript text (no model)
  5. cheap model     -> business snapshot from the annual report's most relevant pages
  6. strong model    -> Promise Ledger + narrative vs numbers + bull/bear, from 3-5 only
Total: ~6 model calls, ~120k input tokens, all cached by content.
"""
from __future__ import annotations

import datetime as dt
import json
import re
import time
from pathlib import Path

from finn import llm
from finn.pull import numbers as N
from finn.net import get_bytes
from finn.sources import pdfs, screener

ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT / "data" / "research"
OUT.mkdir(parents=True, exist_ok=True)

GROUND = ("Use ONLY the document text provided. Do not use any outside knowledge about this company, "
          "its industry or its history. If something is not in the text, leave it out. Return JSON only.")

EXTRACT_SYS = "You are an equity analyst extracting management commitments from an earnings-call transcript. " + GROUND
EXTRACT = """Company: {name}. Earnings call discussing {quarter} (call month {date}).
Transcript with [pN] page markers:
<<<
{text}
>>>

Extract what MANAGEMENT (not analysts) explicitly guided or committed to about the future, plus what they
claimed was delivered this quarter. Prefer quantified statements. Ignore boilerplate safe-harbour text.

Return JSON:
{{
 "guidance": [  // max 8, most important first
   {{"metric": "revenue growth"|"margin"|"profit"|"capex"|"volume"|"orders"|"debt"|"other",
     "promise": "<=18 words, normalised: what, how much, by when. e.g. 'FY27 revenue growth 18-20% in USD'",
     "horizon": "e.g. Q3 FY27, FY27, FY28, next 2-3 years",
     "low": number|null, "high": number|null, "unit": "%"|"bps"|"cr"|"x"|"units"|null,
     "quote": "exact words from the transcript, <=40 words, copied verbatim",
     "page": page number from the nearest [pN] marker before the quote,
     "speaker": "name/role if stated"}}
 ],
 "claims": [ // max 4: management's own description of THIS quarter's performance
   {{"claim": "<=16 words", "quote": "verbatim <=30 words", "page": int}}
 ],
 "pushback": [ // max 3: what analysts pressed hardest on
   {{"topic": "<=10 words", "quote": "analyst's verbatim words <=30", "page": int}}
 ],
 "tone": "<=14 words describing management's stance this call"
}}"""

AR_SYS = "You describe a company's business from its own annual report. " + GROUND
AR = """Company: {name}. Annual report {year}, selected pages with [pN] markers:
<<<
{text}
>>>
Return JSON:
{{
 "what_it_does": "<=30 words, plain English, no adjectives",
 "segments": [ {{"name": str, "revenue": number as printed for the latest year or null,
                "unit": "crore"|"million"|"lakh"|null, "what": "<=12 words: what this segment sells, to whom",
                "page": int}} ],
 "mix_note": "<=20 words on geography/customer/product concentration with the % if stated, else null",
 "risks_mgmt_flags": [ "<=14 words each, max 3: risks SPECIFIC to this business that management names
                        (e.g. 'top-10 clients are 40% of revenue'), never generic categories like 'market risk'" ]
}}
Copy segment revenue figures exactly as printed; do not compute percentages."""

SYNTH_SYS = ("You are a sceptical buy-side analyst. You compare what management promised with what the "
             "reported numbers show. " + GROUND + " Numbers come only from the REPORTED NUMBERS block. "
             "Be terse and specific; every sentence should contain a number or a concrete fact.")
SYNTH = """Company: {name}

GUIDANCE GIVEN ON THE LAST {n} CALLS (oldest first; ids like g2.3 = call 2, item 3):
{guidance}

MANAGEMENT'S CLAIMS & ANALYST PUSHBACK:
{claims}

REPORTED NUMBERS (₹ crore; quarterly, then annual):
{numbers}

RULE-BASED OBSERVATIONS FROM THE FINANCIALS:
{flags}

Return JSON:
{{
 "promise_ledger": [ // 4-7 rows. Order of preference: (1) promises whose deadline has passed and that the
                     // reported numbers can confirm or refute; (2) promises management itself later updated,
                     // raised or cut on a subsequent call (compare calls!); (3) at most 2 still-open promises
                     // that matter most. Never list the same promise twice — if repeated across calls, use
                     // the earliest ref and mention the repeat in "delivered".
   {{"ref": "g-id it comes from", "promise": "<=16 words", "made_in": "quarter label",
     "delivered": "<=22 words: what the numbers show, with figures; or what is still pending",
     "verdict": "delivered"|"partly"|"missed"|"too_early"|"walked_back",
     "checked_against": "which reported figure(s), e.g. 'Q2 FY27 revenue YoY'"}}
 ],
 // RULES FOR VERDICTS
 // - "ref" must be one of the g-ids listed above, exactly. Claims are context, not refs.
 // - delivered = met or beat. partly = some of it, or ahead on one part and behind on another.
 //   missed = fell short of a deadline that has passed. Beating a growth range is "delivered", never "missed".
 // - walked_back = management itself lowered/withdrew the target on a later call (cite both calls).
 // - Constant-currency or USD growth guidance CANNOT be checked against ₹ revenue (currency moves and
 //   acquisitions distort it). Judge it only from management's own later statements of cc/USD growth;
 //   if there are none, use "too_early". Same for EBIT vs EBITDA, segment vs company, volume vs value,
 //   and "pre-Ind AS" / "adjusted" / "ex-one-offs" margins vs the reported (post-Ind AS) figures.
 // - Never convert currencies or compute new numbers.
 // ALL SIX KEYS ARE REQUIRED.
 "credibility": {{"score": 0-10, "line": "<=20 words: does this team do what it says?"}},
 "narrative_vs_numbers": [ // 3-4
   {{"agree": true|false, "point": "<=24 words, the story vs the figure"}}
 ],
 "bull": [ "<=20 words each, 3 items, each anchored in a number or a promise" ],
 "bear": [ "<=20 words each, 3 items" ],
 "watch": [ "<=14 words each, 2-3 things to check next quarter" ]
}}"""


def _valid_synthesis(dossier: dict):
    """Schema + citation check. An answer missing sections, or whose rows mostly cite promises
    that don't exist, is rejected and the next model in the chain gets a turn."""
    ids = {g["id"] for c in dossier["calls"] for g in c.get("guidance") or []}

    def ok(d) -> bool:
        if not isinstance(d, dict):
            return False
        if not all(d.get(k) for k in ("promise_ledger", "credibility", "bull", "bear")):
            return False
        if not isinstance(d["credibility"], dict) or "score" not in d["credibility"]:
            return False
        rows = d["promise_ledger"]
        good = sum(1 for r in rows if isinstance(r, dict) and r.get("ref") in ids)
        return good >= max(2, len(rows) // 2) or (len(ids) < 2 and good >= 1)
    return ok


def _norm(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", (s or "").lower()).strip()


def verify_quote(quote: str, pages: list[str], page_hint: int | None) -> tuple[bool, int | None]:
    """Is the quote really in the transcript? Checks the hinted page first, then all pages."""
    words = _norm(quote).split()
    if len(words) < 4:
        return False, page_hint
    probes = [" ".join(words[i:i + 6]) for i in range(0, max(1, len(words) - 5), 3)][:5]
    order = list(range(len(pages)))
    if page_hint and 1 <= page_hint <= len(pages):
        order.remove(page_hint - 1)
        order.insert(0, page_hint - 1)
    for i in order:
        p = _norm(pages[i])
        hits = sum(1 for pr in probes if pr in p)
        if hits >= max(1, len(probes) // 2):
            return True, i + 1
    return False, page_hint


def _ar_pages(pages: list[str], k: int = 12) -> list[tuple[int, str]]:
    kw = re.compile(r"segment|revenue (mix|share|from)|business (overview|model|segments)|our business|"
                    r"product portfolio|contribution|% of (total )?revenue|geograph|verticals|"
                    r"management discussion", re.I)
    scored = []
    for i, t in enumerate(pages):
        if len(t) < 400:
            continue
        s = len(kw.findall(t)) + (2 if re.search(r"management discussion and analysis", t, re.I) else 0)
        s += 2 * bool(re.search(r"segment (revenue|information|results)|operating segments", t, re.I))
        s += 1 * bool(re.search(r"top (5|10|five|ten) (clients|customers)|client concentration", t, re.I))
        s -= 3 * bool(re.search(r"notice is hereby|form no\. mgt|secretarial audit|independent auditor", t, re.I))
        if s > 0:
            scored.append((s, i))
    top = sorted(i for _, i in sorted(scored, reverse=True)[:k])
    return [(i + 1, pages[i][:3500]) for i in top]


def _numbers_block(an: dict) -> str:
    lines = ["Definitions: EBITDA% = operating profit BEFORE depreciation / revenue. EBIT% = AFTER depreciation "
             "(this is what IT-services and many companies mean by 'operating margin' or 'EBIT margin' guidance). "
             "All figures are company-level; segment-level promises can only be checked against what management "
             "later reported on a call, and you must say so. Quarter labels are calendar quarter-ends "
             "(Sep 2026 = Q2 FY27).",
             "Quarter | Revenue | Rev YoY% | EBITDA | EBITDA% | EBIT% | PAT | PAT YoY%"]
    for q in an["quarters"]:
        lines.append(f"{q['q']} | {q['revenue']} | {q['rev_yoy']} | {q['op_profit']} | {q['margin']} | "
                     f"{q.get('ebit_margin')} | {q['pat']} | {q['pat_yoy']}")
    lines.append("Year | Revenue | EBITDA | EBITDA% | PAT")
    for y in an["years"]:
        lines.append(f"{y['y']} | {y['revenue']} | {y['op_profit']} | {y['margin']} | {y['pat']}")
    if an.get("cash"):
        lines.append("Year | Operating cash flow | Free cash flow | OCF/PAT")
        for c in an["cash"]:
            lines.append(f"{c['y']} | {c['cfo']} | {c['fcf']} | {c['conv']}")
    if an.get("balance_sheet"):
        lines.append("Year-end | Borrowings | Equity | D/E")
        for b in an["balance_sheet"]:
            lines.append(f"{b['y']} | {b['debt']} | {b['equity']} | {b['de']}")
    s = an["snapshot"]
    lines.append(f"ROCE {s['roce']}% · ROE {s['roe']}% · D/E {s['de']} · 5y cash conversion {an['cash_conv_5y']}")
    return "\n".join(lines)


def run(symbol: str, max_calls: int = 4, force: bool = False):
    """Generator of progress events; final event has type 'done' with the dossier."""
    symbol = symbol.upper().strip()
    t0 = time.time()
    path = OUT / f"{symbol}.json"
    yield {"type": "step", "text": f"Looking up {symbol}"}
    co = screener.company(symbol)
    latest_call = co["concalls"][0]["date"] if co["concalls"] else None

    old = json.loads(path.read_text(encoding="utf-8")) if path.exists() else None
    if old and not force:
        fresh = (dt.datetime.now() - dt.datetime.fromisoformat(old["generated_at"])).days < 7
        # a ledger written by the fallback model is upgraded once the strong model is back
        upgradable = old.get("ledger_fallback") and llm.model_for("strong") != old.get("ledger_model")
        if fresh and not upgradable and old.get("latest_call") == latest_call and \
                (old.get("ledger_ok") or not llm.available()):
            yield {"type": "step", "text": "Using cached dossier (no new filings since last run)"}
            yield {"type": "done", "dossier": old}
            return

    yield {"type": "step", "text": f"Read reported financials ({co['basis']}): "
                                   f"{len(co['quarters']['cols']) if co['quarters'] else 0} quarters, "
                                   f"{len(co['pl']['cols']) if co['pl'] else 0} years"}
    an = N.analyse(co)
    yield {"type": "step", "text": f"Computed trajectory, balance sheet & cash quality · {len(an['flags'])} flags"}

    usage = []
    dossier = {"symbol": symbol, "name": co["name"], "basis": co["basis"],
               "generated_at": dt.datetime.now().isoformat(timespec="seconds"),
               "latest_call": latest_call, "numbers": an, "sources": [], "calls": [],
               "business": None, "ledger": None, "ledger_ok": False, "usage": usage}
    # numbers need no model: show them now, the ledger streams in behind them
    yield {"type": "partial", "dossier": {k: v for k, v in dossier.items() if k != "usage"}}

    if not llm.available():
        dossier["note"] = ("No model key configured: showing the numbers-only dossier. Add GEMINI_API_KEY "
                           "to enable the Promise Ledger.")
        yield {"type": "step", "text": "No model key: numbers-only dossier"}
        _save(path, dossier, t0)
        yield {"type": "done", "dossier": dossier}
        return

    # ---- transcripts
    # only recent calls: a promise from three years ago says little about this management today
    recent = [c for c in co["concalls"] if (N.months_ago(c["date"]) or 99) <= 15]
    if co["concalls"] and not recent:
        yield {"type": "warn", "text": f"Latest transcript on record is from {co['concalls'][0]['date']} — too old to "
                                       f"judge current management; using the annual report instead"}
    calls = recent[:max_calls]
    seen_q = {}
    for c in calls:
        q = N.call_quarter(c["date"])
        seen_q[q] = seen_q.get(q, 0) + 1
    for ci, c in enumerate(reversed(calls), 1):  # oldest first, so ids read chronologically
        qlabel = N.call_quarter(c["date"])
        if seen_q[qlabel] > 1:  # two calls about the same quarter (e.g. an analyst day): label by month
            qlabel = f"{qlabel} ({c['date'].split()[0]})"
        yield {"type": "step", "text": f"Reading {qlabel} earnings call transcript"}
        try:
            pgs = pdfs.pages(c["transcript"])
        except Exception as e:
            yield {"type": "warn", "text": f"Couldn't open {qlabel} transcript ({e.__class__.__name__})"}
            continue
        text = pdfs.with_markers(pgs)
        scanned = len(text) < 2000
        try:
            if scanned:  # no text layer: hand the PDF itself to the model (it reads page images)
                yield {"type": "step", "text": f"{qlabel} transcript is a scanned image — reading it visually"}
                raw = get_bytes(c["transcript"], ttl=None)
                if len(raw) > 18_000_000:
                    raise ValueError("scanned PDF too large to send")
                data, meta = llm.ask_json("cheap", EXTRACT_SYS, EXTRACT.format(
                    name=co["name"], quarter=qlabel, date=c["date"],
                    text="(the transcript is the attached PDF; use its page numbers for [pN])"),
                    task="extract_call_scan", tag=f"{symbol} {qlabel}", pdf=raw)
            else:
                data, meta = llm.ask_json("cheap", EXTRACT_SYS, EXTRACT.format(
                    name=co["name"], quarter=qlabel, date=c["date"], text=text[:220000]),
                    task="extract_call", tag=f"{symbol} {qlabel}")
        except Exception as e:
            yield {"type": "warn", "text": f"Couldn't read {qlabel} transcript: {e}"}
            continue
        usage.append(meta)
        ok = tot = 0
        for kind in ("guidance", "claims", "pushback"):
            for j, g in enumerate(data.get(kind) or [], 1):
                if scanned:  # nothing to string-match against: say so instead of claiming either way
                    v, pg = None, g.get("page")
                else:
                    v, pg = verify_quote(g.get("quote", ""), pgs, g.get("page"))
                g["verified"], g["page"] = v, pg
                if kind == "guidance":
                    g["id"] = f"g{ci}.{j}"
                ok += bool(v)
                tot += 1
        dossier["calls"].append({"n": ci, "quarter": qlabel, "date": c["date"], "url": c["transcript"],
                                 "pages": len(pgs), "scanned": scanned, **data})
        dossier["sources"].append({"kind": "transcript", "label": f"{qlabel} call", "url": c["transcript"]})
        check = "scanned: quotes can't be string-checked" if scanned else f"{ok}/{tot} quotes verified in source"
        yield {"type": "step", "text": f"{qlabel}: {len(data.get('guidance') or [])} commitments · "
                                       f"{check} · {meta['model']}{' (cached)' if meta.get('cached') else ''}"}

    # ---- no earnings calls (some companies never hold them): promises from last year's annual report
    if not dossier["calls"] and len(co["annual_reports"]) >= 2:
        ar = co["annual_reports"][1]  # a year old, so its outlook can be checked against the latest year
        label = f"AR {ar['year']}"
        yield {"type": "step", "text": f"No recent earnings-call transcripts — reading the outlook in "
                                       f"Annual Report {ar['year']} instead"}
        try:
            pgs = pdfs.pages(ar["url"])
            kw = re.compile(r"outlook|going forward|we expect|we plan|we aim|we intend|target|guidance|"
                            r"next (financial )?year|capacity|expansion|new stores|capex", re.I)
            scored = sorted(((len(kw.findall(t)), i) for i, t in enumerate(pgs) if len(t) > 400), reverse=True)[:16]
            sel = sorted(i for s, i in scored if s >= 1)
            if sel:
                text = "\n".join(f"[p{i + 1}]\n{pgs[i][:4000]}" for i in sel)
                prompt = EXTRACT.format(name=co["name"], quarter=f"FY{str(ar['year'])[-2:]} (annual report outlook)",
                                        date=str(ar["year"]), text=text).replace(
                    "Transcript with [pN] page markers", "Annual report pages with [pN] page markers")
                data, meta = llm.ask_json("cheap", EXTRACT_SYS.replace("earnings-call transcript", "annual report"),
                                          prompt, task="extract_ar_outlook", tag=symbol)
                usage.append(meta)
                for j, g in enumerate(data.get("guidance") or [], 1):
                    g["verified"], g["page"] = verify_quote(g.get("quote", ""), pgs, g.get("page"))
                    g["id"] = f"g1.{j}"
                for kind in ("claims", "pushback"):
                    data[kind] = []  # an annual report has no Q&A
                dossier["calls"].append({"n": 1, "quarter": label, "date": str(ar["year"]), "url": ar["url"],
                                         "pages": len(pgs), "scanned": False, "source": "annual_report", **data})
                dossier["sources"].append({"kind": "annual_report", "label": f"Annual Report {ar['year']} (outlook)",
                                           "url": ar["url"]})
                dossier["promise_source"] = "annual_report"
                yield {"type": "step", "text": f"{label}: {len(data.get('guidance') or [])} forward-looking statements"}
        except Exception as e:
            yield {"type": "warn", "text": f"Couldn't read the {label} outlook ({e.__class__.__name__})"}

    # ---- annual report -> business snapshot
    if co["annual_reports"]:
        ar = co["annual_reports"][0]
        yield {"type": "step", "text": f"Scanning Annual Report {ar['year']} for the business mix"}
        try:
            pgs = pdfs.pages(ar["url"])
            sel = _ar_pages(pgs)
            if sel:
                text = "\n".join(f"[p{n}]\n{t}" for n, t in sel)
                data, meta = llm.ask_json("cheap", AR_SYS, AR.format(name=co["name"], year=ar["year"], text=text),
                                          task="annual_report", tag=symbol)
                usage.append(meta)
                segs = data.get("segments") or []
                revs = [s.get("revenue") for s in segs]
                if segs and all(isinstance(r, (int, float)) and r > 0 for r in revs) and \
                        len({s.get("unit") for s in segs}) == 1:
                    tot = sum(revs)  # arithmetic stays in code
                    for s in segs:
                        s["share_pct"] = round(s["revenue"] / tot * 100)
                data["year"], data["url"], data["pages_read"] = ar["year"], ar["url"], [n for n, _ in sel]
                dossier["business"] = data
                dossier["sources"].append({"kind": "annual_report", "label": f"Annual Report {ar['year']}",
                                           "url": ar["url"]})
                yield {"type": "step", "text": f"Read {len(sel)} of {len(pgs)} AR pages · "
                                               f"{len(data.get('segments') or [])} segments"}
        except Exception as e:
            yield {"type": "warn", "text": f"Annual report unavailable ({e.__class__.__name__})"}

    if not dossier["calls"]:
        dossier["note"] = ("No earnings-call transcripts or annual-report outlook could be read for this company, "
                           "so there are no promises to check. The numbers below are from its reported financials.")

    # ---- synthesis
    if dossier["calls"]:
        yield {"type": "step", "text": "Checking promises against reported numbers"}
        g_lines, c_lines = [], []
        for c in dossier["calls"]:
            for g in c.get("guidance") or []:
                g_lines.append(f"{g['id']} [{c['quarter']} call] {g.get('promise')} (horizon {g.get('horizon')}) "
                               f"— \"{(g.get('quote') or '')[:200]}\"")
            for cl in c.get("claims") or []:
                c_lines.append(f"[{c['quarter']}] mgmt claim: {cl.get('claim')}")
            for p in c.get("pushback") or []:
                c_lines.append(f"[{c['quarter']}] analysts pressed on: {p.get('topic')}")
        flags = "\n".join(f"- {f['text']}" for f in an["flags"]) or "- none"
        try:
            data, meta = llm.ask_json("strong", SYNTH_SYS, SYNTH.format(
                name=co["name"], n=len(dossier["calls"]), guidance="\n".join(g_lines) or "none",
                claims="\n".join(c_lines) or "none", numbers=_numbers_block(an), flags=flags),
                task="synthesis", tag=symbol, validate=_valid_synthesis(dossier))
            usage.append(meta)
            ids = {g["id"]: (c, g) for c in dossier["calls"] for g in c.get("guidance") or []}
            rows, seen, seen_words = [], set(), []
            for r in data.get("promise_ledger") or []:
                src = ids.get(r.get("ref"))
                if not src or r.get("ref") in seen:  # citation check + no duplicate refs
                    continue
                # same promise restated on a later call: keep the first. Two signals: identical set of
                # numbers ("20,000 graduates" / "$2B by FY27"), or heavy word overlap.
                p = r.get("promise", "")
                w = {x for x in _norm(p).split() if len(x) > 2 and not x.isdigit()}
                p_no_dates = re.sub(r"\b(FY|Q[1-4]\s*FY|CY|H[12]\s*FY)\s?\d{2,4}\b|\b20\d\d\b", " ", p, flags=re.I)
                nums = {n.replace(",", "").rstrip(".") for n in re.findall(r"\d[\d,.]*", p_no_dates)} - {""}
                if any((nums & on) or len(w & ow) / max(1, len(w | ow)) > 0.45 for ow, on in seen_words):
                    continue
                seen.add(r["ref"])
                seen_words.append((w, nums))
                c, g = src
                r["quote"], r["page"], r["verified"] = g.get("quote"), g.get("page"), g.get("verified")
                r["url"] = c["url"]
                rows.append(r)
            data["promise_ledger"] = rows
            judged = sum(1 for r in rows if r.get("verdict") in ("delivered", "partly", "missed", "walked_back"))
            if judged < 2 and isinstance(data.get("credibility"), dict):  # a score must be earned
                data["credibility"] = {"score": None, "line": (
                    "None of these promises can be checked against reported numbers yet — no score."
                    if judged == 0 else "Only one promise can be checked against reported numbers so far — too few to score.")}
            dossier["ledger"] = data
            dossier["ledger_ok"] = True
            dossier["ledger_model"] = meta["model"]
            dossier["ledger_fallback"] = meta.get("fallback", False)
            note = " (strong model's free quota used up — fallback model)" if meta.get("fallback") else ""
            yield {"type": "step", "text": f"Promise Ledger: {len(rows)} promises traced · {meta['model']}{note}"}
        except Exception as e:
            if old and old.get("ledger"):  # keep the last good ledger rather than show none
                for k in ("ledger", "ledger_ok", "ledger_model", "ledger_fallback"):
                    dossier[k] = old.get(k)
                yield {"type": "warn", "text": f"Synthesis unavailable ({e.__class__.__name__}); kept the previous ledger"}
            else:
                yield {"type": "warn", "text": f"Synthesis failed: {e}"}

    _save(path, dossier, t0)
    yield {"type": "done", "dossier": dossier}


def _save(path: Path, d: dict, t0: float):
    u = d.get("usage") or []
    d["cost"] = {"calls": len(u), "fresh_calls": sum(not x.get("cached") for x in u),
                 "tokens_in": sum(x.get("in", 0) for x in u), "tokens_out": sum(x.get("out", 0) for x in u),
                 "models": sorted({x["model"] for x in u}), "usd": 0.0, "secs": round(time.time() - t0, 1)}
    path.write_text(json.dumps(d, ensure_ascii=False, indent=1), encoding="utf-8")


# ------------------------------------------------------------------ ask ---

ASK_SYS = ("You answer an investor's question using ONLY the excerpts from the company's own transcripts and "
           "annual report below. Cite pages like [Q2 FY27 call p4] or [AR 2026 p88]. If the excerpts don't "
           "answer it, say so plainly. <=90 words. Return JSON only.")


def ask(symbol: str, question: str) -> dict:
    """Follow-up Q&A: keyword retrieval over cached primary docs, then a cheap-model answer."""
    path = OUT / f"{symbol.upper()}.json"
    if not path.exists():
        raise LookupError("run research first")
    d = json.loads(path.read_text(encoding="utf-8"))
    docs = []
    for c in d.get("calls", []):
        for i, t in enumerate(pdfs.pages(c["url"]), 1):
            docs.append((f"{c['quarter']} call p{i}", t, c["url"], i))
    if d.get("business"):
        b = d["business"]
        for i, t in enumerate(pdfs.pages(b["url"]), 1):
            docs.append((f"AR {b['year']} p{i}", t, b["url"], i))
    stop = set("the a an of to in and or is are was what how did does do for on with about it its this that "
               "they their management company".split())
    terms = [w for w in _norm(question).split() if w not in stop and len(w) > 2]
    if not terms:
        return {"answer": "Ask about something specific: margins, capex, a segment, debt...", "cites": []}

    def sc(t):
        n = _norm(t)
        return sum(n.count(w) for w in terms) * (1 + sum(1 for w in terms if w in n)) / (1 + len(n) / 4000)

    top = sorted(docs, key=lambda x: -sc(x[1]))[:6]
    top = [x for x in top if sc(x[1]) > 0]
    if not top:
        return {"answer": "Nothing in the transcripts or annual report mentions that.", "cites": []}
    ctx = "\n\n".join(f"[{lab}]\n{t[:3000]}" for lab, t, _, _ in top)
    data, meta = llm.ask_json("cheap", ASK_SYS, f"Company: {d['name']}\nQuestion: {question}\n\nExcerpts:\n{ctx}\n\n"
                              'Return {"answer": str, "cited": [labels used]}', task="ask", tag=symbol)
    used = set(data.get("cited") or []) | set(re.findall(r"\[([^\]]+)\]", data.get("answer") or ""))
    cites = [{"label": lab, "url": u, "page": p} for lab, _, u, p in top if lab in used]
    return {"answer": data.get("answer"), "cites": cites, "model": meta["model"], "cached": meta.get("cached")}
