"""screener.in: standardised multi-year tables + links to primary documents.

We use screener only as (a) a tidy copy of the company's reported financials
and (b) an index of primary documents (annual reports, concall transcripts).
Its "About" blurb and AI summaries are secondary content and are ignored.
"""
from __future__ import annotations

import re

from bs4 import BeautifulSoup

from finn.net import get_text

BASE = "https://www.screener.in/company"


def _num(s: str):
    s = s.replace(",", "").replace("%", "").strip()
    try:
        return float(s)
    except ValueError:
        return None


def _table(soup, section: str) -> dict | None:
    t = soup.select_one(f"section#{section} table")
    if not t:
        return None
    cols = [th.get_text(strip=True) for th in t.select("thead th")][1:]
    rows = {}
    for tr in t.select("tbody tr"):
        tds = tr.select("td")
        if not tds:
            continue
        name = tds[0].get_text(" ", strip=True).replace("+", "").strip()
        if not name or name == "Raw PDF":
            continue
        rows[name] = [_num(td.get_text(strip=True)) for td in tds[1:]]
    return {"cols": cols, "rows": rows}


def company(symbol: str) -> dict:
    """Fetch consolidated view, falling back to standalone."""
    html = None
    for path in (f"{BASE}/{symbol}/consolidated/", f"{BASE}/{symbol}/"):
        try:
            html = get_text(path, ttl=6 * 3600)
        except RuntimeError:
            continue
        soup = BeautifulSoup(html, "lxml")
        q = _table(soup, "quarters")
        if q and any(any(v is not None for v in r) for r in q["rows"].values()):
            basis = "consolidated" if "consolidated" in path else "standalone"
            break
    else:
        raise LookupError(f"{symbol}: not found on screener")

    name = soup.select_one("h1")
    ratios = {}
    for li in soup.select("#top-ratios li"):
        k = li.select_one(".name")
        v = li.select_one(".number")
        if k and v:
            ratios[k.get_text(strip=True)] = _num(v.get_text(strip=True))

    concalls = []
    for li in soup.select(".documents.concalls li"):
        date = li.select_one("div")
        tr = li.find("a", string=re.compile("Transcript", re.I))
        ppt = li.find("a", string=re.compile("PPT", re.I))
        if date and tr and tr.get("href"):
            concalls.append({"date": date.get_text(strip=True), "transcript": tr["href"],
                             "ppt": ppt.get("href") if ppt else None})
    annual = []
    for a in soup.select(".documents.annual-reports a"):
        m = re.search(r"(\d{4})", a.get_text(" ", strip=True))
        if m and a.get("href", "").lower().endswith(".pdf"):
            annual.append({"year": int(m.group(1)), "url": a["href"]})
    annual.sort(key=lambda x: -x["year"])

    return {
        "symbol": symbol,
        "name": name.get_text(strip=True) if name else symbol,
        "basis": basis,
        "ratios": ratios,
        "quarters": _table(soup, "quarters"),
        "pl": _table(soup, "profit-loss"),
        "bs": _table(soup, "balance-sheet"),
        "cf": _table(soup, "cash-flow"),
        "eff": _table(soup, "ratios"),
        "concalls": concalls,
        "annual_reports": annual,
    }
