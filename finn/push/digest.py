"""Build the push feed: filings -> events (clustered, scored, enriched) + EOD series."""
from __future__ import annotations

import datetime as dt
import json
import re
import time
from collections import defaultdict
from pathlib import Path

from finn import llm
from finn.push import classify as C
from finn.push import enrich as E
from finn.push import events as EV
from finn.sources import nse, prices, screener

ROOT = Path(__file__).resolve().parents[2]

CLUSTER = {"results", "management_change", "litigation", "M&A", "fund_raise"}
RESULTS_FAMILY = {"results", "earnings_call"}  # supporting docs fold into the results event


def _log(msg):
    print(f"[{time.strftime('%H:%M:%S')}] {msg}", flush=True)


UNIVERSE = ROOT / "data" / "universe.json"  # stocks users added in the app (shared tracking list)
READS = ROOT / "data" / "reads.json"        # model reads of filings, kept so no filing is read twice


def tracked() -> list[dict]:
    """Default portfolio plus every stock a user has added in the app."""
    cfg = json.loads((ROOT / "config" / "portfolio.json").read_text())
    out = {h["symbol"]: h for h in cfg["holdings"]}
    if UNIVERSE.exists():
        for h in json.loads(UNIVERSE.read_text(encoding="utf-8")):
            out.setdefault(h["symbol"], h)
    return list(out.values())


def build(days: int = 30, read_pdfs: bool = True, today: dt.date | None = None,
          holdings: list[dict] | None = None) -> dict:
    cfg = json.loads((ROOT / "config" / "portfolio.json").read_text())
    holdings = holdings or tracked()
    syms = [h["symbol"] for h in holdings]
    today = today or dt.date.today()
    start = today - dt.timedelta(days=days)

    _log("prices")
    px = prices.eod(syms, period="6mo")

    _log("market caps")
    mcap = {}
    for s in syms:
        try:
            mcap[s] = screener.company(s)["ratios"].get("Market Cap")
        except Exception:
            mcap[s] = None

    _log("announcements")
    items = []
    for s in syms:
        try:
            items += nse.announcements(s, start, today)
        except Exception as e:
            _log(f"  {s}: {e}")
    _log(f"  {len(items)} filings")

    # Every filing is read by a model at most once, ever: results live in the repo (data/reads.json),
    # so the daily cloud job only reads filings it has never seen.
    memory = json.loads(READS.read_text(encoding="utf-8")) if READS.exists() else {}
    reads = 0
    usage = {"calls": 0, "cached": 0, "in": 0, "out": 0, "models": set(), "remembered": 0}
    for it in items:
        it["category"] = C.classify(it)
        it["headline"] = E.clean_text(it)
        it["people"] = E.people_fallback(it) if it["category"] == "management_change" else []
        if read_pdfs and E.needs_reading(it, it["category"]) and (it["id"] in memory or llm.available()):
            r = None
            if it["id"] in memory:
                r = dict(memory[it["id"]])
                usage["remembered"] += 1
            else:
                try:
                    r = E.read_filing(it)
                except Exception as e:
                    _log(f"  read failed {it['symbol']}: {e}")
                if r:
                    m = r.pop("_meta")
                    usage["calls"] += 1; usage["cached"] += m["cached"]
                    usage["in"] += m["in"]; usage["out"] += m["out"]; usage["models"].add(m["model"])
                    r["model"], r["ts"] = m["model"], it["ts"]
                    memory[it["id"]] = r
            if r:
                reads += 1
                if reads % 10 == 0:
                    _log(f"  read {reads} filings so far")
                m = {"model": r.get("model")}
                if r.get("category"):
                    it["category"] = r["category"]
                it["headline"] = r.get("headline") or it["headline"]
                it["why"] = r.get("why")
                it["hint"] = r.get("materiality")
                it["amount_cr"] = r.get("amount_cr")
                it["summary"] = f"{r.get('headline') or ''} {r.get('why') or ''}"
                if r.get("people"):
                    it["people"] = r["people"]
                it["read_by"] = m["model"]
        it["score"], it["reasons"] = C.score(it, it["category"], mcap.get(it["symbol"]))
    _log(f"  {reads} filings with model reads ({usage['remembered']} remembered, {usage['calls']} new)")
    cutoff = (today - dt.timedelta(days=120)).isoformat()  # keep the memory small
    memory = {k: v for k, v in memory.items() if (v.get("ts") or "9") >= cutoff}
    READS.parent.mkdir(exist_ok=True)
    READS.write_text(json.dumps(memory, ensure_ascii=False, indent=0), encoding="utf-8")

    events = _cluster(items)

    _log("public profiles for appointees without one in the filing")
    for ev in events:
        for p in ev.get("people") or []:
            if p.get("change") in ("appointed", "redesignated") and not p.get("background"):
                prof = E.public_profile(p.get("name"), ev.get("company"))
                if prof:
                    p["background"], p["profile_url"] = prof["text"], prof["url"]
                    p["profile_source"] = "Wikipedia" + (" (name match)" if prof["match"] == "name" else "")

    _log("results cards")
    for ev in events:
        if ev["category"] == "results" and ev["score"] >= 60:
            try:
                ev["results"] = nse.results_card(ev["symbol"], near=dt.date.fromisoformat(ev["ts"][:10]))
            except Exception as e:
                _log(f"  results {ev['symbol']}: {e}")
            if ev.get("results"):
                r = ev["results"]
                ev["headline"] = _results_headline(r)
                # Rank results by surprise: a big swing either way matters more than an in-line print
                pat = next(m for m in r["metrics"] if m["key"] == "pat")
                swing = abs(pat["yoy"] or 0)
                ev["score"] = min(99, 80 + round(min(swing, 60) / 60 * 19))
                if swing >= 25:
                    ev["reasons"] = (ev.get("reasons") or []) + [f"PAT {pat['yoy']:+.0f}% YoY"]

    for ev in events:
        ev["tier"] = C.tier(ev["score"])
        ev["label"] = C.LABELS[ev["category"]]
    events.sort(key=lambda e: (e["score"], e["ts"]), reverse=True)

    names = {h["symbol"]: h for h in holdings}
    out_hold = []
    for s in syms:
        ser = px.get(s, [])[-90:]
        out_hold.append({**names[s], "mcap_cr": mcap.get(s), "series": ser})

    _log("upcoming events")
    try:
        upcoming = EV.upcoming(syms, today, items)
    except Exception as e:
        _log(f"  upcoming failed: {e}")
        upcoming = []

    usage["models"] = sorted(usage["models"])
    return {"generated_at": dt.datetime.now().isoformat(timespec="seconds"),
            "window_days": days, "holdings": out_hold, "events": events, "upcoming": upcoming,
            "default_portfolio": [h["symbol"] for h in cfg["holdings"]],
            "rules": cfg["default_rules"], "llm": usage}


def merge(feed: dict, part: dict) -> dict:
    """Fold a build for a few new symbols into the existing feed."""
    syms = {h["symbol"] for h in part["holdings"]}
    feed["holdings"] = [h for h in feed["holdings"] if h["symbol"] not in syms] + part["holdings"]
    feed["events"] = sorted([e for e in feed["events"] if e["symbol"] not in syms] + part["events"],
                            key=lambda e: (e["score"], e["ts"]), reverse=True)
    feed["upcoming"] = sorted([u for u in feed.get("upcoming", []) if u["symbol"] not in syms] + part["upcoming"],
                              key=lambda u: (u["date"], u["symbol"]))
    return feed


def _cluster(items: list[dict]) -> list[dict]:
    """Fold related filings into one event so a results day is one card, not six."""
    by_sym = defaultdict(list)
    for it in items:
        by_sym[it["symbol"]].append(it)
    events = []
    for sym, its in by_sym.items():
        its.sort(key=lambda x: x["ts"])
        open_clusters: list[dict] = []
        for it in its:
            fam = "results" if it["category"] in RESULTS_FAMILY and it["score"] >= 20 else it["category"]
            t = dt.datetime.fromisoformat(it["ts"])
            host = None
            if fam in CLUSTER:
                for c in open_clusters:
                    if c["_fam"] == fam and (t - c["_t0"]).total_seconds() < 72 * 3600:
                        host = c
                        break
            if host:
                host["_items"].append(it)
            else:
                c = {"_fam": fam, "_t0": t, "_items": [it]}
                open_clusters.append(c)
        for c in open_clusters:
            members = sorted(c["_items"], key=lambda x: -x["score"])
            lead = members[0]
            ev = {k: lead.get(k) for k in ("id", "symbol", "company", "category", "headline", "why",
                                           "score", "reasons", "pdf", "people", "read_by", "subject")}
            ev["ts"] = max(m["ts"] for m in members)
            if c["_fam"] == "results" and any(m["category"] == "results" for m in members):
                ev["category"] = "results"
                lead_res = max((m for m in members if m["category"] == "results"), key=lambda m: m["score"])
                ev["score"] = max(ev["score"], lead_res["score"])
            people = [p for m in members for p in (m.get("people") or [])]
            ev["people"] = _dedupe_people(people)
            departures = [p for p in ev["people"] if p.get("change") in ("resigned", "retired")]
            if len(departures) >= 3:
                ev["score"] = min(100, ev["score"] + 25)
                ev["reasons"] = (ev["reasons"] or []) + [f"{len(departures)} exits in one go"]
                ev["headline"] = f"{len(departures)} directors exit: " + ", ".join(
                    p["name"].split()[-1] for p in departures[:4])
            ev["related"] = [{"id": m["id"], "headline": m["headline"], "subject": m["subject"],
                              "pdf": m["pdf"], "ts": m["ts"], "category": m["category"]}
                             for m in members[1:]]
            events.append(ev)
    return events


def _dedupe_people(ps):
    seen, out = set(), []
    for p in ps:
        k = re.sub(r"^(mr|ms|mrs|dr|shri|smt)\.?\s+", "", (p.get("name") or "").lower().strip())
        k = re.sub(r"[^a-z ]", "", k).strip()
        if k and k not in seen:
            seen.add(k)
            out.append(p)
    return out


def _fmt_pct(x):
    return "n/a" if x is None else f"{x:+.0f}%"


def _results_headline(r: dict) -> str:
    m = {x["key"]: x for x in r["metrics"]}
    rev, pat = m["revenue"], m["pat"]
    return f"{r['quarter']}: revenue {_fmt_pct(rev['yoy'])} YoY, PAT {_fmt_pct(pat['yoy'])} YoY"
