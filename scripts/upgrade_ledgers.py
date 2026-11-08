"""Re-judge Promise Ledgers with the strongest model currently available.

Only the final reasoning step runs again (transcript extraction stays cached). A ledger is replaced
only if the new one comes from a model at least as strong as the old one; otherwise it is kept.

    python scripts/upgrade_ledgers.py                    # every ledger written by a fallback model
    python scripts/upgrade_ledgers.py INFY DIXON         # specific companies
    python scripts/upgrade_ledgers.py --flash INFY ...   # accept only Gemini Flash verdicts
"""
import json
import os
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
os.environ["FINN_REDO_TASKS"] = "synthesis"  # ask the model again instead of reusing the saved verdict

from finn.pull import research  # noqa: E402

OUT = ROOT / "data" / "research"
RANK = lambda m: 0 if not m else 1 if "lite" in m else 3 if "gemini" in m else 2  # lite < groq < flash


def weak(d: dict) -> bool:
    return bool(d.get("ledger")) and RANK(d.get("ledger_model")) < 2


if __name__ == "__main__":
    args = sys.argv[1:]
    flash_only = "--flash" in args
    want = [a for a in args if not a.startswith("--")]
    todo = []
    for p in sorted(OUT.glob("*.json")):
        d = json.loads(p.read_text(encoding="utf-8"))
        if (want and d["symbol"] in want) or (not want and weak(d)):
            todo.append(d["symbol"])
    print(f"{len(todo)} ledgers to re-judge: {' '.join(todo)}", flush=True)
    for i, s in enumerate(todo, 1):
        path = OUT / f"{s}.json"
        before = path.read_text(encoding="utf-8")
        old = json.loads(before).get("ledger_model")
        new = None
        for ev in research.run(s, force=True):
            if ev["type"] == "done":
                new = ev["dossier"].get("ledger_model")
        better = RANK(new) >= max(RANK(old), 3 if flash_only else 0) and RANK(new) > 1
        if not better:
            path.write_text(before, encoding="utf-8")  # keep the previous ledger
        print(f"[{i}/{len(todo)}] {s}: {old} -> {new} {'(kept new)' if better else '(kept previous)'}", flush=True)
        time.sleep(8)
