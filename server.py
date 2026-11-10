"""FINN API + static web app. Run:  uvicorn server:app --reload"""
from __future__ import annotations

import json
import logging
import os
import re
import threading
import time
from collections import defaultdict
from pathlib import Path

import httpx

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse, Response, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from finn import llm, notify
from finn.pull import research
from finn.push import digest

ROOT = Path(__file__).resolve().parent
DATA = ROOT / "data"
WEB = ROOT / "web"

app = FastAPI(title="FINN")
log = logging.getLogger("finn")
SYM = re.compile(r"^[A-Z0-9&\-]{1,20}$")


@app.middleware("http")
async def revalidate(request, call_next):
    resp = await call_next(request)
    if not request.url.path.startswith("/api/"):
        resp.headers["Cache-Control"] = "no-cache"  # always revalidate the app shell (ETag keeps it cheap)
    return resp


def _sym(s: str) -> str:
    s = s.upper().strip()
    if not SYM.match(s):
        raise HTTPException(400, "not an NSE symbol")
    return s


_status_cache: dict = {}


def _ready() -> list[dict]:
    """One summary line per saved dossier; re-read only when a dossier file changes."""
    files = sorted((DATA / "research").glob("*.json"))
    key = tuple((p.name, p.stat().st_mtime_ns) for p in files)
    if _status_cache.get("key") == key:
        return _status_cache["ready"]
    ready = []
    for p in files:
        try:
            d = json.loads(p.read_text(encoding="utf-8"))
        except Exception:
            continue
        L = d.get("ledger") or {}
        verdicts = [r.get("verdict") for r in L.get("promise_ledger") or []]
        ready.append({"symbol": d["symbol"], "name": d.get("name"),
                      "score": (L.get("credibility") or {}).get("score"),
                      "line": (L.get("credibility") or {}).get("line"),
                      "missed": verdicts.count("missed") + verdicts.count("walked_back"),
                      "delivered": verdicts.count("delivered"), "promises": len(verdicts)})
    _status_cache.update(key=key, ready=ready)
    return ready


@app.get("/api/status")
def status():
    cfg = json.loads((ROOT / "config" / "portfolio.json").read_text())
    ready = _ready()
    return {"llm": llm.available(),
            "research_demo": cfg["research_demo"],
            "cached": [r["symbol"] for r in ready], "ready": ready}


@app.get("/api/feed")
def feed():
    p = DATA / "feed.json"
    if not p.exists():
        raise HTTPException(503, "feed not built yet: run scripts/refresh.py")
    return FileResponse(p, media_type="application/json")


@app.get("/api/research/{symbol}")
def research_cached(symbol: str):
    p = DATA / "research" / f"{_sym(symbol)}.json"
    if not p.exists():
        raise HTTPException(404, "not researched yet")
    return FileResponse(p, media_type="application/json")


_research_locks: dict[str, threading.Lock] = defaultdict(threading.Lock)
WAITING = "Someone else is analysing this company right now; waiting for their result"
FAILED = "The analysis could not be completed. The data sources may be busy; please try again in a minute."


@app.get("/api/research/{symbol}/stream")
def research_stream(symbol: str):
    sym = _sym(symbol)

    def gen():
        # one build per company at a time: a second visitor waits, then gets the saved dossier
        # instead of spending the free model quota twice
        lock = _research_locks[sym]
        if not lock.acquire(blocking=False):
            yield f"data: {json.dumps({'type': 'step', 'text': WAITING})}\n\n"
            lock.acquire()
        try:
            for ev in research.run(sym):
                yield f"data: {json.dumps(ev, ensure_ascii=False, default=str)}\n\n"
        except LookupError:
            yield f"data: {json.dumps({'type': 'error', 'text': f'{sym} not found. Use the NSE symbol, e.g. INFY.'})}\n\n"
        except Exception:
            log.exception("research failed for %s", sym)
            yield f"data: {json.dumps({'type': 'error', 'text': FAILED})}\n\n"
        finally:
            lock.release()

    return StreamingResponse(gen(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


class Ask(BaseModel):
    symbol: str
    question: str


@app.post("/api/ask")
def ask(q: Ask):
    if not llm.available():
        raise HTTPException(503, "no model key configured")
    try:
        return research.ask(_sym(q.symbol), q.question[:400])
    except LookupError as e:
        raise HTTPException(404, str(e))


# ------------------------------------------------------------------ portfolio tracking ---
# Each person's portfolio lives on their own device. The server only keeps the shared list of
# stocks anyone has added, so the daily refresh covers them; adding one builds its data now.

MAX_TRACKED = 60
_lock = threading.Lock()
_building: dict[str, str] = {}  # symbol -> "building" | "failed: ..."


def _symbol_names() -> dict[str, str]:
    rows = json.loads((WEB / "symbols.json").read_text(encoding="utf-8"))
    return {r[0]: r[1] for r in rows}


def _build_one(sym: str, name: str) -> None:
    try:
        part = digest.build(holdings=[{"symbol": sym, "name": name, "sector": ""}])
        with _lock:
            p = DATA / "feed.json"
            feed = json.loads(p.read_text(encoding="utf-8"))
            p.write_text(json.dumps(digest.merge(feed, part), ensure_ascii=False, indent=1, default=str), encoding="utf-8")
        _building.pop(sym, None)
    except Exception as e:  # keep the reason so the app can say what happened
        _building[sym] = f"failed: {e.__class__.__name__}"


class Track(BaseModel):
    symbol: str


@app.post("/api/track")
def track(t: Track):
    sym = _sym(t.symbol)
    names = _symbol_names()
    if sym not in names:
        raise HTTPException(404, f"{sym} isn't an NSE-listed symbol")
    feed = json.loads((DATA / "feed.json").read_text(encoding="utf-8"))
    if any(h["symbol"] == sym for h in feed["holdings"]):
        return {"symbol": sym, "status": "ready"}
    if _building.get(sym) == "building":
        return {"symbol": sym, "status": "building"}
    uni = digest.UNIVERSE
    tracked = json.loads(uni.read_text(encoding="utf-8")) if uni.exists() else []
    if sym not in {h["symbol"] for h in tracked}:
        if len(tracked) >= MAX_TRACKED:
            raise HTTPException(429, "Tracking list is full on this demo server")
        tracked.append({"symbol": sym, "name": names[sym], "sector": ""})
        uni.write_text(json.dumps(tracked, indent=1), encoding="utf-8")
    _building[sym] = "building"
    threading.Thread(target=_build_one, args=(sym, names[sym]), daemon=True).start()
    return {"symbol": sym, "status": "building"}


@app.get("/api/universe")
def universe():
    """Stocks visitors have added. The GitHub jobs copy this into the repo so the daily refresh covers them."""
    uni = digest.UNIVERSE
    return JSONResponse(json.loads(uni.read_text(encoding="utf-8")) if uni.exists() else [])


@app.get("/api/track/{symbol}")
def track_status(symbol: str):
    sym = _sym(symbol)
    st = _building.get(sym)
    if st:
        return {"symbol": sym, "status": st}
    feed = json.loads((DATA / "feed.json").read_text(encoding="utf-8"))
    return {"symbol": sym, "status": "ready" if any(h["symbol"] == sym for h in feed["holdings"]) else "unknown"}


# ------------------------------------------------------------------ telegram alerts ---

# Anyone can connect: the app holds a random link code; tapping Start in Telegram links the chat.
CODE = re.compile(r"^[A-Za-z0-9_-]{16,64}$")


def _code(c: str) -> str:
    if not CODE.match(c or ""):
        raise HTTPException(400, "bad link code")
    return c


@app.on_event("startup")
def _telegram_webhook():
    # Register this server as the bot's webhook so "/start <code>" reaches us (public deployments only)
    base = notify.URL()
    if notify.configured() and base.startswith("https://"):
        threading.Thread(target=lambda: print("telegram webhook:", notify.set_webhook(base)), daemon=True).start()


@app.on_event("startup")
def _keep_awake():
    """Free hosting sleeps after 15 idle minutes (a ~40 s cold start, and files written since the last
    deploy are lost). A request to our own public URL every 10 minutes keeps the one free instance up;
    a month of that fits inside the free allowance."""
    base = notify.URL()
    if not (os.environ.get("RENDER") and base.startswith("https://")):
        return

    def loop():
        while True:
            time.sleep(600)
            try:
                httpx.get(f"{base}/api/status", timeout=30)
            except httpx.HTTPError:
                pass

    threading.Thread(target=loop, daemon=True).start()


@app.get("/api/telegram/status")
def telegram_status(code: str = ""):
    out = {"available": notify.configured(), "bot": notify.bot_username() if notify.configured() else None}
    if code:
        out.update(notify.status(_code(code)))
    return out


@app.post("/api/telegram/webhook")
async def telegram_webhook(request: Request):
    if request.headers.get("X-Telegram-Bot-Api-Secret-Token") != notify.sync_key():
        raise HTTPException(403, "forbidden")
    update = await request.json()
    threading.Thread(target=notify.handle_update, args=(update,), daemon=True).start()
    return {"ok": True}


class Prefs(BaseModel):
    code: str
    symbols: list[str]
    rules: dict


@app.put("/api/telegram/prefs")
def telegram_prefs(p: Prefs):
    ok = notify.set_prefs(_code(p.code), [_sym(s) for s in p.symbols][:100], p.rules)
    if not ok:
        raise HTTPException(404, "not connected")
    return {"ok": True}


class Code(BaseModel):
    code: str


@app.post("/api/telegram/test")
def telegram_test(c: Code):
    feed = json.loads((DATA / "feed.json").read_text(encoding="utf-8"))
    res = notify.test(feed, _code(c.code))
    if not res.get("ok"):
        raise HTTPException(404 if res.get("description") == "not connected" else 502, res.get("description", "Telegram error"))
    return {"ok": True}


@app.post("/api/telegram/disconnect")
def telegram_disconnect(c: Code):
    notify.unlink(code=_code(c.code))
    return {"ok": True}


@app.get("/api/telegram/export")
def telegram_export(request: Request):
    """Encrypted subscriber list for the GitHub jobs, which save it to the repo so it survives restarts."""
    if not notify.configured() or request.headers.get("X-Finn-Sync") != notify.sync_key():
        raise HTTPException(403, "forbidden")
    return Response(notify.STORE.read_bytes() if notify.STORE.exists() else b"", media_type="application/octet-stream")


@app.get("/")
def index():
    return FileResponse(WEB / "index.html")


app.mount("/", StaticFiles(directory=WEB), name="web")
