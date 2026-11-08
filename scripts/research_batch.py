"""Pre-build research dossiers so the demo opens instantly.

    python scripts/research_batch.py               # research_demo set from config
    python scripts/research_batch.py DIXON TITAN   # specific symbols
"""
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from finn.pull import research  # noqa: E402

if __name__ == "__main__":
    FORCE = "--force" in sys.argv
    syms = [a for a in sys.argv[1:] if a != "--force"] or json.loads((ROOT / "config" / "portfolio.json").read_text())["research_demo"]
    for s in syms:
        print(f"== {s}", flush=True)
        try:
            for ev in research.run(s, force=FORCE):
                if ev["type"] != "done":
                    print(f"   {ev['type']}: {ev.get('text')}", flush=True)
                else:
                    c = ev["dossier"].get("cost", {})
                    print(f"   done · {c.get('calls')} calls · {c.get('tokens_in')} tok in · {c.get('secs')}s", flush=True)
        except Exception as e:
            print(f"   FAILED: {e}", flush=True)
