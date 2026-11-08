"""Copy the live server's (encrypted) Telegram subscriber list into data/subscribers.enc.

Free hosting forgets files on restart, so the GitHub jobs call this and commit the result; the next
deploy starts with every subscriber intact. Prints "changed" when the file was updated.
Needs TELEGRAM_BOT_TOKEN (to prove we're allowed) and FINN_PUBLIC_URL.
"""
import os
import sys
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from finn import notify  # noqa: E402

if __name__ == "__main__":
    base = notify.URL()
    if not (notify.configured() and base):
        sys.exit("skipped: TELEGRAM_BOT_TOKEN or FINN_PUBLIC_URL not set")
    try:
        r = httpx.get(f"{base}/api/telegram/export", headers={"X-Finn-Sync": notify.sync_key()}, timeout=120)
    except httpx.HTTPError as e:
        sys.exit(f"skipped: server unreachable ({e.__class__.__name__})")
    if r.status_code != 200:
        sys.exit(f"skipped: export returned {r.status_code}")
    if not r.content:  # nobody has connected yet; never wipe a saved list with an empty one
        print("unchanged (server has no subscribers)")
        sys.exit(0)
    old = notify.STORE.read_bytes() if notify.STORE.exists() else b""
    if r.content != old:
        notify.STORE.parent.mkdir(exist_ok=True)
        notify.STORE.write_bytes(r.content)
        print("changed")
    else:
        print("unchanged")
