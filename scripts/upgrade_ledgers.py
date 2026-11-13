"""Re-judge Promise Ledgers with the strongest model currently available.

Only the final reasoning step runs again (transcript extraction stays cached). A ledger is replaced
only if Gemini Flash wrote the new one; otherwise the previous ledger is kept. (Groq's verdicts were
reviewed against Flash-Lite's and were not reliably better, e.g. a margin from the wrong quarter, a
"lower is better" target marked missed, so a fallback model never replaces an existing ledger.)

    python scripts/upgrade_ledgers.py                    # every ledger not written by Gemini Flash
    python scripts/upgrade_ledgers.py INFY DIXON         # specific companies
    python scripts/upgrade_ledgers.py TCS --rest         # TCS first, then all the others
    python scripts/upgrade_ledgers.py --limit=12         # leave some of the day's quota for live use

Stops by itself when Flash stops answering (daily quota spent, or overloaded at US peak hours;
FINN_PROBE_TIMEOUT=60 makes the run more patient).
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
    return bool(d.get("ledger")) and RANK(d.get("ledger_model")) < 3


if __name__ == "__main__":
    args = sys.argv[1:]
    limit = next((int(a.split("=")[1]) for a in args if a.startswith("--limit=")), 99)
    want = [a.upper() for a in args if not a.startswith("--")]
    have = {}
    for p in sorted(OUT.glob("*.json")):
        d = json.loads(p.read_text(encoding="utf-8"))
        have[d["symbol"]] = d
    # named companies first, in the order given; then every other ledger not written by Flash
    todo = [s for s in want if s in have and weak(have[s])]
    if not want or "--rest" in args:
        todo += [s for s, d in have.items() if weak(d) and s not in todo]
    todo = todo[:limit]
    print(f"{len(todo)} ledgers to re-judge: {' '.join(todo)}", flush=True)
    misses = 0
    for i, s in enumerate(todo, 1):
        path = OUT / f"{s}.json"
        before = path.read_text(encoding="utf-8")
        old = json.loads(before).get("ledger_model")
        new = None
        try:
            for ev in research.run(s, force=True):
                if ev["type"] == "done":
                    new = ev["dossier"].get("ledger_model")
        except Exception as e:  # one company failing must not stop the batch
            new = f"error {e.__class__.__name__}"
        better = RANK(new) == 3 and "error" not in str(new)
        if not better:
            path.write_text(before, encoding="utf-8")  # keep the previous ledger
        print(f"[{i}/{len(todo)}] {s}: {old} -> {new} {'(kept new)' if better else '(kept previous)'}", flush=True)
        misses = 0 if better else misses + 1
        if misses >= 2:
            print("Gemini Flash is not answering (daily quota spent, or overloaded); stopping. Try again later, with FINN_PROBE_TIMEOUT=60 at busy hours.", flush=True)
            break
        time.sleep(8)
