"""Telegram alerts: after each refresh, message the owner about anything that crossed their rules.

Setup (free, ~2 minutes):
  1. In Telegram, message @BotFather -> /newbot -> copy the token into .env as TELEGRAM_BOT_TOKEN
  2. Send any message to your new bot, then run:  python scripts/telegram_setup.py
     (it reads the chat id from the bot's updates and prints the line to add: TELEGRAM_CHAT_ID=...)
  3. Optional: FINN_PUBLIC_URL=https://your-deployed-app  (links in messages)
             FINN_OWNER_PIN=1234  (lets the app sync your portfolio + rules to the notifier)

Each alert is sent once (ids remembered in data/notified.json).
"""
from __future__ import annotations

import datetime as dt
import html
import json
import os
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parent.parent
SETTINGS = ROOT / "data" / "notify.json"     # owner's portfolio + rules, synced from the app
SENT = ROOT / "data" / "notified.json"

TOKEN = lambda: os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
CHAT = lambda: os.getenv("TELEGRAM_CHAT_ID", "").strip()
URL = lambda: os.getenv("FINN_PUBLIC_URL", "").strip().rstrip("/")
PIN = lambda: os.getenv("FINN_OWNER_PIN", "").strip()

CAT = {"results": "Results", "M&A": "M&A", "fund_raise": "Fund raise", "management_change": "Management",
       "litigation": "Legal", "credit_rating": "Credit rating", "pledging": "Pledge", "capex": "Capex",
       "insider_trading": "Insider trade", "dividend": "Dividend", "bonus_split": "Bonus/split",
       "press_release": "Press release"}


def configured() -> bool:
    return bool(TOKEN() and CHAT())


def settings(feed: dict) -> dict:
    s = json.loads(SETTINGS.read_text(encoding="utf-8")) if SETTINGS.exists() else {}
    return {"symbols": s.get("symbols") or feed.get("default_portfolio") or [h["symbol"] for h in feed["holdings"]],
            "rules": {**feed["rules"], **(s.get("rules") or {})}}


def save_settings(symbols: list[str], rules: dict) -> None:
    SETTINGS.parent.mkdir(exist_ok=True)
    SETTINGS.write_text(json.dumps({"symbols": symbols, "rules": rules}, indent=1), encoding="utf-8")


def _session_start(feed: dict) -> dt.datetime:
    days = sorted({p["d"] for h in feed["holdings"] for p in h.get("series") or []})
    d = days[-2] if len(days) >= 2 else (dt.date.today() - dt.timedelta(days=1)).isoformat()
    return dt.datetime.fromisoformat(d + "T15:30:00")


def price_alerts(holdings: list[dict], rules: dict, since: dt.datetime) -> list[dict]:
    """Same rules as the app: day move >= ±X% or volume >= N× its trailing average."""
    out, L = [], int(rules.get("volume_lookback_days", 20))
    for h in holdings:
        s = h.get("series") or []
        for i in range(max(1, L), len(s)):
            t = dt.datetime.fromisoformat(s[i]["d"] + "T15:30:00")
            if t <= since:  # the session-start close itself belongs to the previous session
                continue
            ret = (s[i]["c"] / s[i - 1]["c"] - 1) * 100
            win = s[i - L:i]
            avg = sum(x["v"] for x in win) / len(win) if win else 0
            mult = s[i]["v"] / avg if avg else 0
            if abs(ret) >= rules["price_move_pct"] or mult >= rules["volume_multiple"]:
                out.append({"id": f"px-{h['symbol']}-{s[i]['d']}", "symbol": h["symbol"], "ret": ret, "mult": mult,
                            "vol": mult >= rules["volume_multiple"], "date": s[i]["d"]})
    return out


def digest(feed: dict, force: bool = False) -> tuple[str | None, list[str]]:
    """Build the message for alerts not yet sent. Returns (text or None, ids)."""
    st = settings(feed)
    syms, rules = set(st["symbols"]), st["rules"]
    since = _session_start(feed)
    sent = set(json.loads(SENT.read_text())) if SENT.exists() and not force else set()

    px = [a for a in price_alerts([h for h in feed["holdings"] if h["symbol"] in syms], rules, since)
          if a["id"] not in sent]
    fl = [e for e in feed["events"] if e["symbol"] in syms and e["category"] in rules["categories"]
          and e["score"] >= 60 and dt.datetime.fromisoformat(e["ts"]) >= since and e["id"] not in sent]
    tomorrow = (dt.date.today() + dt.timedelta(days=1)).isoformat()
    up = [u for u in feed.get("upcoming", []) if u["symbol"] in syms and u["date"] <= tomorrow
          and f"up-{u['symbol']}-{u['date']}-{u['kind']}" not in sent]
    if not (px or fl or up):
        return None, []

    e = html.escape
    day = dt.datetime.fromisoformat(feed["generated_at"]).strftime("%a %d %b")
    lines = [f"<b>FINN · {day}</b>"]
    if fl:
        lines.append("\n<b>Needs attention</b>")
        for x in sorted(fl, key=lambda x: -x["score"])[:8]:
            link = f' <a href="{x["pdf"]}">filing</a>' if x.get("pdf") else ""
            lines.append(f"• <b>{e(x['symbol'])}</b> · {e(CAT.get(x['category'], x['category']))}: {e(x['headline'][:140])}{link}")
    if px:
        lines.append("\n<b>Unusual moves</b>")
        for a in sorted(px, key=lambda a: -abs(a["ret"]))[:8]:
            vol = f" on {a['mult']:.1f}× volume" if a["vol"] else ""
            lines.append(f"• <b>{e(a['symbol'])}</b> {a['ret']:+.1f}%{vol}")
    if up:
        lines.append("\n<b>Coming up</b>")
        for u in up[:6]:
            when = "Today" if u["date"] == dt.date.today().isoformat() else "Tomorrow"
            lines.append(f"• {when}: <b>{e(u['symbol'])}</b> {e(u['title'])}")
    if URL():
        lines.append(f'\n<a href="{URL()}/#brief">Open your brief</a>')
    ids = [a["id"] for a in px] + [x["id"] for x in fl] + [f"up-{u['symbol']}-{u['date']}-{u['kind']}" for u in up]
    return "\n".join(lines), ids


def send(text: str) -> dict:
    r = httpx.post(f"https://api.telegram.org/bot{TOKEN()}/sendMessage", timeout=20,
                   json={"chat_id": CHAT(), "text": text, "parse_mode": "HTML", "disable_web_page_preview": True})
    return r.json()


def run(feed: dict, force: bool = False) -> str:
    if not configured():
        return "telegram not configured"
    text, ids = digest(feed, force=force)
    if not text:
        return "nothing new to send"
    res = send(text)
    if not res.get("ok"):
        return f"telegram error: {res.get('description')}"
    if not force:
        prev = json.loads(SENT.read_text()) if SENT.exists() else []
        SENT.write_text(json.dumps((prev + ids)[-2000:]))
    return f"sent {len(ids)} alerts"
