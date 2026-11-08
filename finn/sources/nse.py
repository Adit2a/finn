"""NSE: corporate announcements + integrated-filing XBRL (quarterly results).

Results numbers are read from the XBRL the company itself files, not from a
model reading a PDF. That makes the "first cut" of a results announcement
exact, instant and free.
"""
from __future__ import annotations

import datetime as dt
import re

from finn.net import get_json, get_text

API = "https://www.nseindia.com/api"


def announcements(symbol: str, start: dt.date, end: dt.date) -> list[dict]:
    rows = get_json(
        f"{API}/corporate-announcements",
        {"index": "equities", "symbol": symbol,
         "from_date": start.strftime("%d-%m-%Y"), "to_date": end.strftime("%d-%m-%Y")},
        ttl=1800,
    )
    out = []
    for r in rows or []:
        ts = dt.datetime.strptime(r["sort_date"], "%Y-%m-%d %H:%M:%S")
        out.append({
            "id": r["seq_id"],
            "symbol": r["symbol"],
            "company": r.get("sm_name"),
            "subject": (r.get("desc") or "").strip(),
            "text": (r.get("attchmntText") or "").strip(),
            "pdf": r.get("attchmntFile"),
            "ts": ts.isoformat(),
        })
    return out


# ---------------------------------------------------------------- results ---

def result_filings(symbol: str) -> list[dict]:
    """Integrated-filing financials, newest first. Consolidated preferred."""
    data = get_json(f"{API}/integrated-filing-results",
                    {"index": "equities", "symbol": symbol,
                     "type": "Integrated Filing- Financials"}, ttl=3600)
    rows = (data or {}).get("data", [])
    by_q: dict[str, dict] = {}
    for r in rows:
        filed = r.get("broadcast_Date") or r.get("creation_Date")
        if not (r.get("qe_Date") and r.get("xbrl") and filed):
            continue
        q = dt.datetime.strptime(r["qe_Date"], "%d-%b-%Y").date()
        filed_dt = dt.datetime.strptime(filed, "%d-%b-%Y %H:%M:%S")
        key = q.isoformat()
        cur = by_q.get(key)
        # prefer consolidated, then the latest revision
        rank = (r["consolidated"] == "Consolidated", r.get("type_Sub") != "Original", filed_dt)
        if cur is None or rank > cur["_rank"]:
            by_q[key] = {"quarter_end": q, "basis": r["consolidated"], "xbrl": r["xbrl"],
                         "filed": filed_dt, "audited": r.get("audited"), "_rank": rank}
    return sorted(by_q.values(), key=lambda x: x["quarter_end"], reverse=True)


_CTX = re.compile(r'<xbrli:context id="([^"]+)">(.*?)</xbrli:context>', re.S)


def _quarter_context(xml: str, q_end: dt.date, lo: int = 80, hi: int = 100) -> str | None:
    best = None
    for cid, body in _CTX.findall(xml):
        if "<xbrli:segment" in body or "<xbrli:scenario" in body:
            continue
        s = re.search(r"<xbrli:startDate>([\d-]+)", body)
        e = re.search(r"<xbrli:endDate>([\d-]+)", body)
        if not (s and e) or e.group(1) != q_end.isoformat():
            continue
        days = (q_end - dt.date.fromisoformat(s.group(1))).days
        if lo <= days <= hi:
            best = cid
    return best


def _ytd_months(q_end: dt.date) -> int:
    return {6: 3, 9: 6, 12: 9, 3: 12}[q_end.month]


def _val(xml: str, tag: str, ctx: str) -> float | None:
    m = re.search(rf'<[\w-]+:{tag}\b[^>]*contextRef="{re.escape(ctx)}"[^>]*>([^<]*)<', xml)
    if not m or not m.group(1).strip():
        return None
    try:
        return float(m.group(1))
    except ValueError:
        return None


def headline_numbers(xbrl_url: str, q_end: dt.date) -> dict | None:
    """Revenue / EBITDA / PBT / PAT in ₹ crore for one quarter.

    EBITDA = PBT - exceptional + finance costs + D&A — the definition Indian
    companies use in their own press releases (it reconciles exactly to e.g.
    Reliance's reported consolidated EBITDA). Banks don't have EBITDA; we show
    pre-provision operating profit instead and say so.
    """
    xml = get_text(xbrl_url, ttl=None)
    ctx = _quarter_context(xml, q_end)
    if not ctx:
        return None
    return _numbers(xml, ctx)


def ytd_numbers(xbrl_url: str, q_end: dt.date) -> dict | None:
    """Financial-year-to-date numbers (some filers only tag the YTD period)."""
    xml = get_text(xbrl_url, ttl=None)
    n = _ytd_months(q_end)
    ctx = _quarter_context(xml, q_end, n * 30 - 12, n * 30 + 12)
    return _numbers(xml, ctx) if ctx else None


def _numbers(xml: str, ctx: str) -> dict:
    v = lambda t: _val(xml, t, ctx)
    cr = lambda x: None if x is None else round(x / 1e7, 2)

    if v("InterestEarned") is not None and v("OperatingProfitBeforeProvisionAndContingencies") is not None:
        rev = v("InterestEarned")
        ebitda = v("OperatingProfitBeforeProvisionAndContingencies")
        pbt = v("ProfitLossFromOrdinaryActivitiesBeforeTax")
        pat = v("ProfitLossForThePeriod") or v("ProfitLossAfterTaxesMinorityInterestAndShareOfProfitLossOfAssociates")
        return {"kind": "bank", "revenue": cr(rev), "ebitda": cr(ebitda), "pbt": cr(pbt), "pat": cr(pat),
                "labels": {"revenue": "Interest earned", "ebitda": "Op. profit (pre-prov.)"}}

    rev = v("RevenueFromOperations")
    pbt = v("ProfitBeforeTax")
    pat = v("ProfitLossForPeriod")
    fin = v("FinanceCosts") or 0.0
    dep = v("DepreciationDepletionAndAmortisationExpense") or 0.0
    exc = v("ExceptionalItemsBeforeTax") or 0.0
    lender = rev and fin / rev > 0.25  # NBFCs: interest is cost of goods, EBITDA is meaningless
    ebitda = None if (pbt is None or lender) else pbt - exc + fin + dep
    return {"kind": "lender" if lender else "corporate",
            "revenue": cr(rev), "ebitda": cr(ebitda), "pbt": cr(pbt), "pat": cr(pat),
            "labels": {"revenue": "Revenue", "ebitda": "EBITDA" if not lender else "EBITDA (n.m. for lenders)"}}


def _prev_quarter_end(d: dt.date, n: int) -> dt.date:
    y, m = d.year, d.month - 3 * n
    while m <= 0:
        m += 12
        y -= 1
    nxt = dt.date(y + (m == 12), m % 12 + 1, 1)
    return nxt - dt.timedelta(days=1)


def results_card(symbol: str, near: dt.date | None = None) -> dict | None:
    """Headline numbers for the latest quarter (optionally the filing nearest a date),
    with QoQ and YoY comparisons."""
    filings = result_filings(symbol)
    if not filings:
        return None
    by_q = {f["quarter_end"]: f for f in filings}
    cur = filings[0]
    if near:
        cands = [f for f in filings if f["filed"].date() <= near + dt.timedelta(days=1)]
        cur = cands[0] if cands else cur
    q = cur["quarter_end"]
    derived = []

    def quarter(qe):
        f = by_q.get(qe)
        if not f:
            return None
        n = headline_numbers(f["xbrl"], qe)
        if n:
            return n
        # Fallback: quarter = YTD(q) - YTD(previous quarter in the same fiscal year)
        ytd = ytd_numbers(f["xbrl"], qe)
        if not ytd:
            return None
        if _ytd_months(qe) == 3:
            return ytd
        pq = _prev_quarter_end(qe, 1)
        pf = by_q.get(pq)
        prev = pf and ytd_numbers(pf["xbrl"], pq)
        if not prev:
            return None
        derived.append(fy_quarter(qe))
        out = dict(ytd)
        for k in ("revenue", "ebitda", "pbt", "pat"):
            out[k] = None if ytd.get(k) is None or prev.get(k) is None else round(ytd[k] - prev[k], 2)
        return out

    now = quarter(q)
    if not now:
        return None

    def compare(prev_q):
        return quarter(prev_q)

    qoq, yoy = compare(_prev_quarter_end(q, 1)), compare(_prev_quarter_end(q, 4))
    metrics = []
    for k in ("revenue", "ebitda", "pbt", "pat"):
        def chg(base):
            if not base or base.get(k) in (None, 0) or now.get(k) is None:
                return None
            return round((now[k] - base[k]) / abs(base[k]) * 100, 1)
        metrics.append({"key": k, "label": now["labels"].get(k, k.upper()),
                        "value": now[k], "qoq": chg(qoq), "yoy": chg(yoy),
                        "prev_q": qoq and qoq.get(k), "prev_y": yoy and yoy.get(k)})
    corp = now["kind"] == "corporate"
    margin = (now["ebitda"] / now["revenue"] * 100) if corp and now.get("ebitda") and now.get("revenue") else None
    margin_y = (yoy["ebitda"] / yoy["revenue"] * 100) if yoy and yoy.get("ebitda") and yoy.get("revenue") else None
    return {"symbol": symbol, "quarter": fy_quarter(q), "quarter_end": q.isoformat(),
            "basis": cur["basis"], "kind": now["kind"], "source": cur["xbrl"],
            "derived": derived,  # quarters computed as YTD minus prior YTD
            "metrics": metrics,
            "ebitda_margin": margin and round(margin, 1),
            "ebitda_margin_bps_yoy": round((margin - margin_y) * 100) if margin and margin_y else None}


def fy_quarter(q_end: dt.date) -> str:
    """Indian fiscal label: quarter ending 30 Sep 2026 -> Q2 FY27."""
    fy = q_end.year + (1 if q_end.month > 3 else 0)
    qn = {6: 1, 9: 2, 12: 3, 3: 4}[q_end.month]
    return f"Q{qn} FY{str(fy)[-2:]}"
