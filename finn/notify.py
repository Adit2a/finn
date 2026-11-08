"""Telegram alerts: anyone can connect from the app; after each market close every subscriber gets
one message with what crossed *their* rules for *their* portfolio.

Connect flow (no setup for users):
  app makes a random link code -> opens t.me/<bot>?start=<code> -> the user taps Start ->
  Telegram calls our webhook with "/start <code>" -> code is linked to that chat.
The app then syncs the device's portfolio + rules to that code whenever they change.

Storage: data/subscribers.enc — Fernet-encrypted with a key derived from the bot token, so the file
can live in a public repo (that's how it survives free-host restarts) without exposing chat ids.
Each alert is sent once per chat (data/notified.json).
"""
from __future__ import annotations

import base64
import datetime as dt
import hashlib
import html
import json
import os
import threading
import time
from pathlib import Path

import httpx
from cryptography.fernet import Fernet, InvalidToken

ROOT = Path(__file__).resolve().parent.parent
STORE = ROOT / "data" / "subscribers.enc"
SENT = ROOT / "data" / "notified.json"
_lock = threading.Lock()

TOKEN = lambda: os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
OWNER_CHAT = lambda: os.getenv("TELEGRAM_CHAT_ID", "").strip()  # optional: the owner, before connecting in-app
URL = lambda: os.getenv("FINN_PUBLIC_URL", "").strip().rstrip("/")

CAT = {"results": "Results", "M&A": "M&A", "fund_raise": "Fund raise", "management_change": "Management",
       "litigation": "Legal", "credit_rating": "Credit rating", "pledging": "Pledge", "capex": "Capex",
       "insider_trading": "Insider trade", "dividend": "Dividend", "bonus_split": "Bonus/split",
       "press_release": "Press release"}


def configured() -> bool:
    return bool(TOKEN())


def sync_key() -> str:
    """Shared secret for webhook + subscriber export, derived from the bot token."""
    return hashlib.sha256(f"finn-sync:{TOKEN()}".encode()).hexdigest()[:48]


def _fernet() -> Fernet:
    return Fernet(base64.urlsafe_b64encode(hashlib.sha256(f"finn-store:{TOKEN()}".encode()).digest()))


# ------------------------------------------------------------------ subscriber store ---

def load() -> dict:
    """{code: {chat_id, name, symbols, rules, since}}"""
    if not (STORE.exists() and TOKEN()):
        return {}
    try:
        return json.loads(_fernet().decrypt(STORE.read_bytes()))
    except (InvalidToken, ValueError):
        return {}


def save(subs: dict) -> None:
    STORE.parent.mkdir(exist_ok=True)
    STORE.write_bytes(_fernet().encrypt(json.dumps(subs, sort_keys=True).encode()))


def link(code: str, chat_id: int, name: str) -> None:
    with _lock:
        subs = load()
        subs[code] = {**subs.get(code, {}), "chat_id": chat_id, "name": name, "since": int(time.time())}
        save(subs)


def unlink(code: str | None = None, chat_id: int | None = None) -> int:
    with _lock:
        subs = load()
        drop = [c for c, s in subs.items() if c == code or (chat_id and s.get("chat_id") == chat_id)]
        for c in drop:
            subs.pop(c)
        save(subs)
        return len(drop)


def set_prefs(code: str, symbols: list[str], rules: dict) -> bool:
    with _lock:
        subs = load()
        if code not in subs:
            return False
        subs[code].update(symbols=symbols[:100], rules=rules)
        save(subs)
        return True


def status(code: str) -> dict:
    s = load().get(code)
    return {"connected": bool(s), "name": s.get("name") if s else None}


# ------------------------------------------------------------------ telegram api ---

_bot: dict = {}


def bot_username() -> str | None:
    if "username" not in _bot and TOKEN():
        try:
            _bot["username"] = httpx.get(f"https://api.telegram.org/bot{TOKEN()}/getMe", timeout=20).json()["result"]["username"]
        except Exception:
            return None
    return _bot.get("username")


def send(chat_id, text: str) -> dict:
    r = httpx.post(f"https://api.telegram.org/bot{TOKEN()}/sendMessage", timeout=20,
                   json={"chat_id": chat_id, "text": text, "parse_mode": "HTML", "disable_web_page_preview": True})
    return r.json()


def set_webhook(base_url: str) -> dict:
    return httpx.post(f"https://api.telegram.org/bot{TOKEN()}/setWebhook", timeout=20, json={
        "url": f"{base_url}/api/telegram/webhook", "secret_token": sync_key(),
        "allowed_updates": ["message"], "drop_pending_updates": False}).json()


def handle_update(update: dict) -> None:
    """Bot commands: /start <code> links this chat; /stop unsubscribes."""
    msg = update.get("message") or {}
    chat = msg.get("chat") or {}
    text = (msg.get("text") or "").strip()
    if not chat.get("id"):
        return
    name = chat.get("first_name") or chat.get("title") or "there"
    if text.startswith("/start"):
        code = text[6:].strip()
        if 16 <= len(code) <= 64 and code.replace("-", "").replace("_", "").isalnum():
            link(code, chat["id"], name)
            send(chat["id"], f"<b>Connected to FINN ✓</b>\n\nHi {html.escape(name)}. After each market close you'll get "
                             "one message with anything in your portfolio that crosses your alert rules. "
                             "Change rules in the app; send /stop to unsubscribe.")
        else:
            send(chat["id"], "Open FINN and tap <b>Connect Telegram</b> to link this chat to your portfolio.")
    elif text.startswith("/stop"):
        n = unlink(chat_id=chat["id"])
        send(chat["id"], "Unsubscribed. You won't get FINN alerts any more." if n else "You weren't subscribed.")


# ------------------------------------------------------------------ alert digest ---

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


def digest(feed: dict, symbols: list[str], rules: dict, sent: set) -> tuple[str | None, list[str]]:
    """One subscriber's message: alerts not yet sent to them. Returns (text or None, ids)."""
    syms, rules = set(symbols), {**feed["rules"], **(rules or {})}
    since = _session_start(feed)
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
            link_ = f' <a href="{x["pdf"]}">filing</a>' if x.get("pdf") else ""
            lines.append(f"• <b>{e(x['symbol'])}</b> · {e(CAT.get(x['category'], x['category']))}: {e(x['headline'][:140])}{link_}")
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


def _recipients(feed: dict) -> list[dict]:
    default = feed.get("default_portfolio") or [h["symbol"] for h in feed["holdings"]]
    out, seen = [], set()
    for s in load().values():
        if s.get("chat_id") and s["chat_id"] not in seen:
            seen.add(s["chat_id"])
            out.append({"chat_id": s["chat_id"], "symbols": s.get("symbols") or default, "rules": s.get("rules") or {}})
    if OWNER_CHAT() and int(OWNER_CHAT()) not in seen:  # owner configured by env, not yet connected in-app
        out.append({"chat_id": int(OWNER_CHAT()), "symbols": default, "rules": {}})
    return out


def test(feed: dict, code: str) -> dict:
    s = load().get(code)
    if not s:
        return {"ok": False, "description": "not connected"}
    text, _ = digest(feed, s.get("symbols") or feed.get("default_portfolio") or [], s.get("rules") or {}, set())
    return send(s["chat_id"], text or "<b>FINN</b>\nYou're connected. Nothing in your portfolio crossed your rules last session.")


def run(feed: dict) -> str:
    if not configured():
        return "telegram not configured"
    sent_all = json.loads(SENT.read_text()) if SENT.exists() else {}
    if isinstance(sent_all, list):  # older single-recipient format
        sent_all = {OWNER_CHAT() or "owner": sent_all}
    n_msgs = n_alerts = 0
    for r in _recipients(feed):
        key = str(r["chat_id"])
        text, ids = digest(feed, r["symbols"], r["rules"], set(sent_all.get(key, [])))
        if not text:
            continue
        if send(r["chat_id"], text).get("ok"):
            sent_all[key] = (sent_all.get(key, []) + ids)[-1500:]
            n_msgs += 1
            n_alerts += len(ids)
    SENT.parent.mkdir(exist_ok=True)
    SENT.write_text(json.dumps(sent_all))
    return f"sent {n_msgs} message(s), {n_alerts} alerts" if n_msgs else "nothing new to send"
