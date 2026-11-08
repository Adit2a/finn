"""Deterministic analysis from the company's reported financials (no model).

Everything here is arithmetic on screener's standardised copy of the filed
statements. The model never computes a number; it only reads these.
"""
from __future__ import annotations

import re


def _row(table: dict | None, *names: str) -> list | None:
    if not table:
        return None
    for n in names:
        for k, v in table["rows"].items():
            if k.lower() == n.lower():
                return v
    for n in names:
        for k, v in table["rows"].items():
            if k.lower().startswith(n.lower()):
                return v
    return None


def _series(table, *names, ttm=False):
    """[(col, value)] skipping TTM unless asked."""
    r = _row(table, *names)
    if not r:
        return []
    out = []
    for c, v in zip(table["cols"], r):
        if c == "TTM" and not ttm:
            continue
        out.append((c, v))
    return out


def cagr(a, b, years):
    if not a or not b or a <= 0 or b <= 0 or years <= 0:
        return None
    return round(((b / a) ** (1 / years) - 1) * 100, 1)


def _pct(a, b):
    if a in (None, 0) or b is None:
        return None
    return round((b - a) / abs(a) * 100, 1)


def analyse(co: dict) -> dict:
    q, pl, bs, cf, eff = co["quarters"], co["pl"], co["bs"], co["cf"], co["eff"]
    is_fin = _row(pl, "Financing Profit") is not None
    rev_names = ("Sales", "Revenue")
    op_names = ("Financing Profit",) if is_fin else ("Operating Profit",)
    margin_names = ("Financing Margin %",) if is_fin else ("OPM %",)

    # ---- quarterly (evidence for the promise ledger)
    qrev, qop, qm, qpat = (_series(q, *rev_names), _series(q, *op_names),
                           _series(q, *margin_names), _series(q, "Net Profit"))
    qdep = _series(q, "Depreciation")
    quarters = []
    for i, (c, v) in enumerate(qrev):
        yo = qrev[i - 4][1] if i >= 4 else None
        pat = qpat[i][1] if i < len(qpat) else None
        pat_y = qpat[i - 4][1] if i >= 4 and i - 4 < len(qpat) else None
        op = qop[i][1] if i < len(qop) else None
        dep = qdep[i][1] if i < len(qdep) else None
        # EBIT margin (after depreciation): what IT companies call "operating margin" in guidance
        ebit_m = round((op - dep) / v * 100, 1) if op is not None and dep is not None and v else None
        quarters.append({"q": c, "revenue": v, "rev_yoy": _pct(yo, v), "op_profit": op,
                         "margin": qm[i][1] if i < len(qm) else None, "ebit_margin": ebit_m,
                         "pat": pat, "pat_yoy": _pct(pat_y, pat)})
    quarters = quarters[-8:]

    # ---- annual trajectory
    arev, aop, am, apat = (_series(pl, *rev_names), _series(pl, *op_names),
                           _series(pl, *margin_names), _series(pl, "Net Profit"))
    years = [{"y": c, "revenue": v, "op_profit": aop[i][1] if i < len(aop) else None,
              "margin": am[i][1] if i < len(am) else None,
              "pat": apat[i][1] if i < len(apat) else None} for i, (c, v) in enumerate(arev)][-7:]
    ttm = dict(zip(pl["cols"], _row(pl, *rev_names) or [])).get("TTM") if pl else None
    ttm_pat = dict(zip(pl["cols"], _row(pl, "Net Profit") or [])).get("TTM") if pl else None

    def ycagr(key, n):
        if len(years) <= n:
            return None
        return cagr(years[-1 - n][key], years[-1][key], n)

    traj = {"rev_cagr_3y": ycagr("revenue", 3), "rev_cagr_5y": ycagr("revenue", 5),
            "pat_cagr_3y": ycagr("pat", 3), "pat_cagr_5y": ycagr("pat", 5),
            "margin_change_3y_bps": (round((years[-1]["margin"] - years[-4]["margin"]) * 100)
                                     if len(years) >= 4 and years[-1]["margin"] is not None
                                     and years[-4]["margin"] is not None else None)}

    # ---- balance sheet
    borrow = _series(bs, "Borrowings", "Borrowing")
    eq = _series(bs, "Equity Capital")
    res = _series(bs, "Reserves")
    bsrows = []
    for i, (c, b) in enumerate(borrow):
        e = (eq[i][1] or 0) + (res[i][1] or 0) if i < len(eq) and i < len(res) else None
        bsrows.append({"y": c, "debt": b, "equity": e, "de": round(b / e, 2) if b is not None and e else None})
    bsrows = bsrows[-6:]
    wc = {k: _series(eff, k)[-6:] for k in ("Debtor Days", "Inventory Days", "Days Payable",
                                             "Cash Conversion Cycle", "Working Capital Days")}
    wc = {k: v for k, v in wc.items() if v}

    # ---- cash quality
    cfo = dict(_series(cf, "Cash from Operating Activity"))
    fcf = dict(_series(cf, "Free Cash Flow"))
    patd = {y["y"]: y["pat"] for y in years}
    cash = []
    for y, c in list(cfo.items())[-6:]:
        p = patd.get(y)
        cash.append({"y": y, "cfo": c, "pat": p, "fcf": fcf.get(y),
                     "conv": round(c / p, 2) if c is not None and p and p > 0 else None})
    tot_cfo = sum(x["cfo"] or 0 for x in cash[-5:])
    tot_pat = sum(x["pat"] or 0 for x in cash[-5:])
    conv5 = round(tot_cfo / tot_pat, 2) if tot_pat > 0 else None

    if is_fin:  # "financing margin" on screener is not a meaningful operating margin for lenders
        for x in quarters + years:
            x["margin"] = None
            x["op_profit"] = None
            x["ebit_margin"] = None
        traj["margin_change_3y_bps"] = None

    snap = {"market_cap_cr": co["ratios"].get("Market Cap"), "pe": co["ratios"].get("Stock P/E"),
            "roce": co["ratios"].get("ROCE"), "roe": co["ratios"].get("ROE"),
            "ttm_revenue": ttm, "ttm_pat": ttm_pat,
            "margin_latest_q": quarters[-1]["margin"] if quarters else None,
            "de": bsrows[-1]["de"] if bsrows else None, "is_financial": is_fin,
            "margin_label": "Financing margin" if is_fin else "Operating margin"}

    return {"snapshot": snap, "quarters": quarters, "years": years, "trajectory": traj,
            "balance_sheet": bsrows, "working_capital": wc, "cash": cash, "cash_conv_5y": conv5,
            "flags": _flags(traj, bsrows, wc, conv5, quarters, is_fin)}


def _flags(traj, bsrows, wc, conv5, quarters, is_fin) -> list[dict]:
    """Rule-based observations. tone: good | bad | neutral."""
    f = []
    if traj["rev_cagr_3y"] is not None and traj["pat_cagr_3y"] is not None:
        gap = traj["pat_cagr_3y"] - traj["rev_cagr_3y"]
        if gap > 5:
            f.append({"tone": "good", "text": f"Profit compounding faster than sales over 3y "
                                              f"({traj['pat_cagr_3y']}% vs {traj['rev_cagr_3y']}%): operating leverage"})
        elif gap < -5:
            f.append({"tone": "bad", "text": f"Profit lagging sales over 3y ({traj['pat_cagr_3y']}% vs "
                                             f"{traj['rev_cagr_3y']}%): margins under pressure"})
    m = traj.get("margin_change_3y_bps")
    if m is not None and abs(m) >= 200 and not is_fin:
        f.append({"tone": "good" if m > 0 else "bad",
                  "text": f"Operating margin {'up' if m > 0 else 'down'} {abs(m)} bps over 3 years"})
    if conv5 is not None and not is_fin:
        if conv5 < 0.7:
            f.append({"tone": "bad", "text": f"Only {conv5:.0%} of 5y profit showed up as operating cash"})
        elif conv5 > 1.0:
            f.append({"tone": "good", "text": f"Cash conversion strong: 5y operating cash = {conv5:.0%} of profit"})
    if len(bsrows) >= 4 and not is_fin:
        a, b = bsrows[-4], bsrows[-1]
        if a["debt"] and b["debt"] and b["debt"] > 1.8 * a["debt"] and b["debt"] > 100:
            f.append({"tone": "bad", "text": f"Borrowings {b['debt'] / a['debt']:.1f}x in 3 years "
                                             f"(₹{a['debt']:,.0f} → ₹{b['debt']:,.0f} cr)"})
        if b["de"] is not None and b["de"] < 0.1:
            f.append({"tone": "good", "text": f"Practically debt-free (D/E {b['de']})"})
        elif b["de"] is not None and b["de"] > 1.5:
            f.append({"tone": "bad", "text": f"Leveraged balance sheet (D/E {b['de']})"})
    ccc = wc.get("Cash Conversion Cycle") or wc.get("Working Capital Days")
    if ccc and len(ccc) >= 3 and ccc[-1][1] is not None and ccc[-3][1] is not None:
        d = ccc[-1][1] - ccc[-3][1]
        if abs(d) >= 15:
            f.append({"tone": "bad" if d > 0 else "good",
                      "text": f"Cash cycle {'stretched' if d > 0 else 'tightened'} by {abs(d):.0f} days in 2 years "
                              f"({ccc[-3][1]:.0f} → {ccc[-1][1]:.0f})"})
    if len(quarters) >= 4:
        ys = [x["rev_yoy"] for x in quarters[-4:] if x["rev_yoy"] is not None]
        if len(ys) >= 3 and all(ys[i] < ys[i - 1] for i in range(1, len(ys))):
            f.append({"tone": "bad", "text": "Revenue growth decelerated every quarter for the last "
                                             f"{len(ys)} quarters ({' → '.join(f'{y:.0f}%' for y in ys)})"})
        elif len(ys) >= 3 and all(ys[i] > ys[i - 1] for i in range(1, len(ys))):
            f.append({"tone": "good", "text": "Revenue growth accelerating "
                                              f"({' → '.join(f'{y:.0f}%' for y in ys)})"})
    return f


MONTH = {m: i for i, m in enumerate(
    ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"], 1)}


def months_ago(label: str, today=None) -> int | None:
    """'Oct 2026' -> months between that call and today."""
    import datetime as dt
    m = re.match(r"(\w{3})\w* (\d{4})", label or "")
    if not m:
        return None
    today = today or dt.date.today()
    return (today.year - int(m.group(2))) * 12 + today.month - MONTH.get(m.group(1), 1)


def call_quarter(label: str) -> str:
    """'Oct 2026' (call month) -> 'Q2 FY27' (the quarter being discussed)."""
    m = re.match(r"(\w{3})\w* (\d{4})", label)
    if not m:
        return label
    mon, yr = MONTH.get(m.group(1), 1), int(m.group(2))
    if mon <= 3:
        q, fy = 3, yr
    elif mon <= 6:
        q, fy = 4, yr
    elif mon <= 9:
        q, fy = 1, yr + 1
    else:
        q, fy = 2, yr + 1
    return f"Q{q} FY{str(fy)[-2:]}"
