"""Build web/symbols.json: every NSE-listed company for instant type-ahead search.

Source: NSE's official equity list + Nifty 50 constituents. Run occasionally (listings change).
Each row: [symbol, display name, rank flag, aliases]
"""
import csv
import io
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from finn.net import get_text  # noqa: E402

# Names people actually use that don't appear in the registered company name
ALIASES = {
    "ETERNAL": "zomato blinkit", "SBIN": "sbi state bank", "HINDUNILVR": "hul unilever",
    "LT": "l&t larsen", "BHARTIARTL": "airtel", "NYKAA": "nykaa", "POLICYBZR": "policybazaar",
    "PAYTM": "paytm", "DMART": "dmart", "M&M": "mahindra", "MARUTI": "maruti suzuki",
    "BAJFINANCE": "bajaj finance", "KOTAKBANK": "kotak", "HDFCLIFE": "hdfc life",
    "ICICIBANK": "icici", "TATAMOTORS": "tata motors", "TMPV": "tata motors passenger",
    "TMCV": "tata motors commercial", "INFY": "infosys", "HCLTECH": "hcl", "TECHM": "tech mahindra",
    "ULTRACEMCO": "ultratech", "SUNPHARMA": "sun pharma", "DRREDDY": "dr reddy", "ADANIENT": "adani",
    "COALINDIA": "coal india cil", "ONGC": "oil and natural gas", "NTPC": "ntpc", "HAL": "hal",
    "BEL": "bel bharat electronics", "IRCTC": "irctc railway", "JUBLFOOD": "dominos jubilant",
    "TRENT": "zudio westside", "NAUKRI": "info edge naukri", "SWIGGY": "swiggy instamart",
}


def clean(name: str) -> str:
    n = re.sub(r"\s+(Limited|Ltd\.?)$", "", name.strip(), flags=re.I)
    if n.isupper() and len(n) > 4:  # "360 ONE WAM" -> "360 One Wam"; keep short acronyms
        n = " ".join(w if len(w) <= 3 else w.title() for w in n.split())
    return n


if __name__ == "__main__":
    eq = get_text("https://nsearchives.nseindia.com/content/equities/EQUITY_L.csv", ttl=86400)
    n50 = get_text("https://nsearchives.nseindia.com/content/indices/ind_nifty50list.csv", ttl=86400)
    nifty = {r["Symbol"].strip() for r in csv.DictReader(io.StringIO(n50))}
    cfg = json.loads((ROOT / "config" / "portfolio.json").read_text())
    mine = {h["symbol"] for h in cfg["holdings"]} | set(cfg["research_demo"])
    rows = []
    for r in csv.reader(io.StringIO(eq)):
        if len(r) < 3 or r[0] == "SYMBOL" or r[2].strip() not in ("EQ", "BE", "BZ"):
            continue
        sym = r[0].strip()
        rank = 2 if sym in mine else 1 if sym in nifty else 0
        rows.append([sym, clean(r[1]), rank, ALIASES.get(sym, "")])
    rows.sort(key=lambda x: (-x[2], x[0]))
    out = ROOT / "web" / "symbols.json"
    out.write_text(json.dumps(rows, separators=(",", ":"), ensure_ascii=False), encoding="utf-8")
    (ROOT / "config" / "nifty50.json").write_text(json.dumps(sorted(nifty)))
    print(f"wrote {out} · {len(rows)} companies · {out.stat().st_size // 1024} KB · nifty50={len(nifty)}")
