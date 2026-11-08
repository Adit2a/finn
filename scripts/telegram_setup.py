"""Find your Telegram chat id: create a bot with @BotFather, put TELEGRAM_BOT_TOKEN in .env,
send your bot any message, then run this script."""
import os
import sys
from pathlib import Path

import httpx
from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent
load_dotenv(ROOT / ".env")
token = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
if not token:
    sys.exit("Add TELEGRAM_BOT_TOKEN=... to .env first (from @BotFather).")
updates = httpx.get(f"https://api.telegram.org/bot{token}/getUpdates", timeout=60).json()
chats = {u["message"]["chat"]["id"]: u["message"]["chat"].get("first_name", "")
         for u in updates.get("result", []) if "message" in u}
if not chats:
    sys.exit("No messages yet. Open your bot in Telegram, press Start / send 'hi', then run this again.")
for cid, name in chats.items():
    print(f"Found chat with {name or 'you'}. Add this line to .env:\nTELEGRAM_CHAT_ID={cid}")
