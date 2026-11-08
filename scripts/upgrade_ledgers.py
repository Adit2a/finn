"""Re-judge Promise Ledgers that were written by a weaker model.

Only the final reasoning step runs again (transcript extraction is cached). Paced for Groq's
free tier (~8k tokens/minute), and retried once if a call still falls back to the cheap model.

    python scripts/upgrade_ledgers.py              # every ledger written by a fallback model
    python scripts/upgrade_ledgers.py INFY DIXON   # specific companies
"""
import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from finn.pull import research  # noqa: E402

OUT = ROOT / "data" / "research"


def weak(d: dict) -> bool:
    return bool(d.get("ledger")) and (d.get("ledger_fallback") or "lite" in (d.get("ledger_model") or ""))


if __name__ == "__main__":
    want = sys.argv[1:]
    todo = []
    for p in sorted(OUT.glob("*.json")):
        d = json.loads(p.read_text(encoding="utf-8"))
        if (want and d["symbol"] in want) or (not want and weak(d)):
            todo.append(d["symbol"])
    print(f"{len(todo)} ledgers to re-judge: {' '.join(todo)}", flush=True)
    for i, s in enumerate(todo, 1):
        for attempt in (1, 2):
            model = None
            for ev in research.run(s, force=True):
                if ev["type"] == "done":
                    model = ev["dossier"].get("ledger_model")
            ok = model and "lite" not in model
            print(f"[{i}/{len(todo)}] {s}: {model}{'' if ok else ' (fallback)'}", flush=True)
            if ok or attempt == 2:
                break
            time.sleep(70)  # let the per-minute budget refill, then retry once
        time.sleep(45)
