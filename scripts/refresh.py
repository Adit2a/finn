"""Batch job: rebuild data/feed.json. Run after market close (GitHub Actions cron).

    python scripts/refresh.py            # 30-day window, reads opaque PDFs if a key is set
    python scripts/refresh.py --no-pdf   # rules only, zero model calls
"""
import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from finn.push.digest import build  # noqa: E402

if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=int, default=30)
    ap.add_argument("--no-pdf", action="store_true")
    a = ap.parse_args()
    feed = build(days=a.days, read_pdfs=not a.no_pdf)
    out = ROOT / "data" / "feed.json"
    out.parent.mkdir(exist_ok=True)
    out.write_text(json.dumps(feed, ensure_ascii=False, indent=1, default=str), encoding="utf-8")
    tiers = {}
    for e in feed["events"]:
        tiers[e["tier"]] = tiers.get(e["tier"], 0) + 1
    print(f"wrote {out} · {len(feed['events'])} events {tiers} · llm {feed['llm']}")

    from finn import notify  # noqa: E402
    print("telegram:", notify.run(feed))
