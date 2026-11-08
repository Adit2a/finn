"""Read the PDF behind an opaque-but-possibly-material filing with the cheap model.

Only a small subset of filings reach this step (see needs_reading). For
appointments, SEBI's LODR requires the filing to carry a "brief profile" of the
person, so the bio comes from the company's own disclosure.
"""
from __future__ import annotations

import re

from finn import llm
from finn.push.classify import CATEGORIES
from finn.sources import pdfs

OPAQUE = re.compile(r"has (informed|submitted to) the exchange (about|regarding)?\s*[^.]{0,60}\.?$|^outcome of|"
                    r"^please find|^in continuation|^the newspaper", re.I)
READ_CATS = {"management_change", "M&A", "litigation", "press_release", "fund_raise", "capex",
             "credit_rating", "insider_trading", "pledging"}

SYSTEM = """You read Indian stock-exchange filings for a busy portfolio manager.
Use ONLY the filing text provided. Never use outside knowledge about the company or people.
Be specific and numeric. No adjectives, no hype, no filler like "significant" or "strategic".
Return JSON only."""

PROMPT = """Company: {company} ({symbol})
Exchange subject line: {subject}
Exchange summary: {text}

Filing text (first pages):
<<<
{body}
>>>

Return JSON:
{{
 "category": one of {cats},
 "headline": "<=16 words. What happened, with the key number if any. e.g. 'Wins ₹2,400 cr metro rail order from MMRDA'",
 "why": "<=18 words on why an investor should care, or null if routine",
 "materiality": 0-100 (0 routine paperwork, 50 worth knowing, 80+ could move the stock),
 "amount_cr": rupee amount involved in crore or null,
 "people": [ only for appointments/resignations/retirements:
   {{"name": str, "role": str, "change": "appointed"|"resigned"|"retired"|"re-appointed"|"redesignated",
     "effective": str|null,
     "background": "<=45 words from the filing's brief profile: past roles, employers, education. null if absent",
     "previous": "most recent prior role and employer, or null",
     "reason": "stated reason for leaving, or null"}} ]
}}"""


def clean_text(item: dict) -> str:
    t = item["text"]
    t = re.sub(r"^.*? has (informed|submitted to) the Exchange,?\s*(regarding|about|that)?\s*", "", t, flags=re.I)
    t = t.strip(" '\".")
    return (t[:1].upper() + t[1:]) if t else item["subject"]


def needs_reading(item: dict, cat: str) -> bool:
    if cat not in READ_CATS or not item.get("pdf"):
        return False
    if cat == "management_change":
        return True  # always: we want the person's profile
    return len(item["text"]) < 140 or bool(OPAQUE.search(item["text"].strip()))


def read_filing(item: dict) -> dict | None:
    try:
        pgs = pdfs.pages(item["pdf"], max_pages=4)
    except Exception:
        return None
    body = "\n".join(pgs)[:12000]
    if len(body.strip()) < 80:  # scanned image PDF; nothing to read
        return None
    data, meta = llm.ask_json("cheap", SYSTEM, PROMPT.format(
        company=item.get("company") or item["symbol"], symbol=item["symbol"], subject=item["subject"],
        text=item["text"][:400], body=body, cats=CATEGORIES), task="enrich", tag=item["symbol"])
    if not isinstance(data, dict):
        return None
    if data.get("category") not in CATEGORIES:
        data["category"] = None
    data["_meta"] = meta
    return data


def public_profile(name: str, company: str | None) -> dict | None:
    """Free, keyless enrichment for people whose filing carries no profile: a Wikipedia
    summary, accepted only if it mentions the company (guards against namesakes)."""
    import os

    import httpx

    from finn.net import memo_json
    contact = os.getenv("FINN_CONTACT", "").strip()  # Wikimedia's bot policy asks for a contact URL
    clean = re.sub(r"^(mr|ms|mrs|dr|shri|smt)\.?\s+", "", name or "", flags=re.I).strip()
    if not contact or len(clean.split()) < 2 or not company:
        return None
    ua = {"User-Agent": f"FINN/0.1 ({contact}) research prototype"}

    def get_json(url, params=None, ttl=None):
        return memo_json("wiki", url + str(sorted((params or {}).items())),
                         lambda: httpx.get(url, params=params, headers=ua, timeout=20).raise_for_status().json(),
                         ttl=ttl)
    key = re.sub(r"\b(limited|ltd|india|the|company|co|bank|industries|technologies)\b", "",
                 company, flags=re.I).strip().split()
    key = key[0].lower() if key else ""
    try:
        hits = get_json("https://en.wikipedia.org/w/api.php",
                        {"action": "query", "list": "search", "srsearch": f'"{clean}"', "srlimit": 3,
                         "format": "json"}, ttl=7 * 86400)["query"]["search"]
        norm = lambda x: re.sub(r"[^a-z ]", "", x.lower()).split()
        for h in hits:
            title = re.sub(r"\s*\(.*\)$", "", h["title"])
            # accept: exact full-name title AND (mentions the company OR is plainly a business biography)
            if norm(title) != norm(clean):
                continue
            s = get_json(f"https://en.wikipedia.org/api/rest_v1/page/summary/{h['title'].replace(' ', '_')}",
                         ttl=7 * 86400)
            text = s.get("extract") or ""
            mentions = bool(key and key in text.lower())
            biz = re.search(r"banker|business(man|woman|person)|executive|chief executive|managing director|"
                            r"chairman|chairperson|industrialist|entrepreneur|economist|civil servant|"
                            r"chartered accountant|ceo|cfo", text, re.I)
            if mentions or biz:
                return {"text": " ".join(text.split()[:55]), "url": s.get("content_urls", {}).get("desktop", {}).get("page"),
                        "match": "company" if mentions else "name"}
    except Exception:
        return None
    return None


NAME = re.compile(r"(?:of|that)\s+((?:Mr\.?|Ms\.?|Mrs\.?|Dr\.?|Shri|Smt\.?)\s+[A-Z][\w.]*(?:\s+[A-Z][\w.]*){0,3})"
                  r"\s*(?:\(DIN[^)]*\))?\s+as\s+([^.]*?)(?:\s+of the company|\s+w\.?e\.?f|\.|$)")


def people_fallback(item: dict) -> list[dict]:
    """No-LLM path: NSE's summary often names the person and role."""
    out = []
    for m in NAME.finditer(item["text"]):
        change = "resigned" if re.search(r"cessation|resign", item["text"], re.I) else \
                 "retired" if "retire" in item["text"].lower() else "appointed"
        out.append({"name": m.group(1).strip(), "role": m.group(2).strip(), "change": change,
                    "effective": None, "background": None, "previous": None, "reason": None})
    return out
