"""End-of-day price & volume from Yahoo Finance (yfinance), one batched call."""
from __future__ import annotations

import math

import yfinance as yf


def eod(symbols: list[str], period: str = "6mo") -> dict[str, list[dict]]:
    out = _eod(symbols, period, threads=True)
    # yfinance's parallel mode occasionally drops a ticker ("database is locked"): retry those one by one
    missing = [s for s in symbols if not out.get(s)]
    for s in missing:
        out.update({k: v for k, v in _eod([s], period, threads=False).items() if v})
    return out


def _eod(symbols: list[str], period: str, threads: bool) -> dict[str, list[dict]]:
    tickers = [f"{s}.NS" for s in symbols]
    if len(tickers) == 1:
        tickers = tickers + ["^NSEI"]  # keep yfinance's multi-ticker column layout for single symbols
    df = yf.download(tickers, period=period, interval="1d", group_by="ticker",
                     auto_adjust=False, progress=False, threads=threads)
    out: dict[str, list[dict]] = {}
    for s, t in zip(symbols, tickers):
        try:
            sub = df[t].dropna(subset=["Close"])
        except KeyError:
            continue
        rows = []
        for idx, r in sub.iterrows():
            if idx.weekday() >= 5:  # Yahoo occasionally emits a weekend stub row
                continue
            vol = r.get("Volume")
            rows.append({"d": idx.date().isoformat(), "c": round(float(r["Close"]), 2),
                         "v": int(vol) if vol is not None and not math.isnan(vol) else 0})
        # Yahoo sometimes emits a zero-volume stub for a non-trading day; drop it
        rows = [x for x in rows if x["v"] > 0] or rows
        out[s] = rows
    return out
