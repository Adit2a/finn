"""Upcoming events for tracked stocks: results board meetings, dividend/record dates, earnings calls.

Sources (all official NSE feeds):
  - event-calendar        board meetings scheduled across the market (~4 weeks ahead)
  - corporate actions     ex-dates / record dates per symbol (dividends, splits, bonuses)
  - announcements         earnings-call schedules announced in filings ("call on November 12, 2026")
"""
from __future__ import annotations

import datetime as dt
import re

from finn.net import get_json

API = "https://www.nseindia.com/api"
MONTHS = "january|february|march|april|may|june|july|august|september|october|november|december"
DATE_RX = [
    (re.compile(rf"\b({MONTHS})\s+(\d{{1,2}}),?\s+(20\d\d)\b", re.I), "mdy"),
    (re.compile(rf"\b(\d{{1,2}})(?:st|nd|rd|th)?\s+({MONTHS}),?\s+(20\d\d)\b", re.I), "dmy"),
    (re.compile(r"\b(\d{1,2})[-/.](\d{1,2})[-/.](20\d\d)\b"), "num"),
]


def _d(s: str) -> dt.date | None:
    for fmt in ("%d-%b-%Y", "%d-%B-%Y"):
        try:
            return dt.datetime.strptime(s.strip(), fmt).date()
        except (ValueError, AttributeError):
            pass
    return None


def _dates_in(text: str) -> list[dt.date]:
    out = []
    for rx, kind in DATE_RX:
        for m in rx.finditer(text or ""):
            try:
                if kind == "mdy":
                    out.append(dt.datetime.strptime(f"{m.group(1)} {m.group(2)} {m.group(3)}", "%B %d %Y").date())
                elif kind == "dmy":
                    out.append(dt.datetime.strptime(f"{m.group(1)} {m.group(2)} {m.group(3)}", "%d %B %Y").date())
                else:
                    out.append(dt.date(int(m.group(3)), int(m.group(2)), int(m.group(1))))
            except ValueError:
                continue
    return out


def _purpose(p: str) -> tuple[str, str]:
    p = p or ""
    if re.search(r"result", p, re.I):
        extra = " + dividend" if re.search(r"dividend", p, re.I) else ""
        return "results", f"Results board meeting{extra}"
    if re.search(r"fund rais|qip|preferential|rights", p, re.I):
        return "fund_raise", "Board meeting: fund raise"
    if re.search(r"buyback", p, re.I):
        return "fund_raise", "Board meeting: buyback"
    if re.search(r"dividend", p, re.I):
        return "dividend", "Board meeting: dividend"
    if re.search(r"bonus|split", p, re.I):
        return "bonus_split", "Board meeting: bonus / split"
    return "agm_egm", "Board meeting"


def upcoming(symbols: list[str], today: dt.date, announcements: list[dict] | None = None,
             horizon: int = 60) -> list[dict]:
    want, end, out = set(symbols), today + dt.timedelta(days=horizon), {}

    def add(sym, date, kind, title, detail=None, src=None):
        if not date or not (today <= date <= end):
            return
        key = (sym, date.isoformat(), kind)
        if key not in out:
            out[key] = {"symbol": sym, "date": date.isoformat(), "kind": kind, "title": title,
                        "detail": detail, "source": src}

    # market-wide board-meeting calendar (one call)
    try:
        for r in get_json(f"{API}/event-calendar", {"index": "equities"}, ttl=6 * 3600) or []:
            if r.get("symbol") in want:
                kind, title = _purpose(r.get("purpose"))
                add(r["symbol"], _d(r.get("date")), kind, title, (r.get("bm_desc") or "")[:160])
    except Exception:
        pass

    # per-symbol corporate actions: ex-dividend, record dates, splits
    for s in symbols:
        try:
            rows = get_json(f"{API}/corporates-corporateActions", {"index": "equities", "symbol": s}, ttl=12 * 3600)
        except Exception:
            continue
        for r in rows or []:
            subj = (r.get("subject") or "").strip()
            kind = "dividend" if re.search(r"dividend", subj, re.I) else \
                   "bonus_split" if re.search(r"bonus|split|sub-?division", subj, re.I) else "corporate_action"
            ex = _d(r.get("exDate"))
            label = re.sub(r"\s+", " ", subj)
            add(s, ex, kind, f"Ex-date: {label}", f"Record date {r.get('recDate')}" if r.get("recDate") not in (None, "-") else None)

    # earnings calls / investor meets announced in filings
    for a in announcements or []:
        if a.get("symbol") not in want:
            continue
        text = f"{a.get('subject', '')} {a.get('text', '')}"
        if not re.search(r"conference call|earnings call|analyst|investor", text, re.I):
            continue
        # NSE's subject line says "Con. Call Updates" for any analyst meet, so judge by the filing text
        is_call = bool(re.search(r"conference call|earnings call|concall|con\. call", a.get("text") or "", re.I))
        for d in _dates_in(text):
            if d > today:
                add(a["symbol"], d, "earnings_call" if is_call else "investor_meetings",
                    "Earnings call" if is_call else "Investor meeting", (a.get("text") or "")[:160], a.get("pdf"))
                break

    return sorted(out.values(), key=lambda e: (e["date"], e["symbol"]))
