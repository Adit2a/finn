"""FINN API + static web app. Run:  uvicorn server:app --reload"""
from __future__ import annotations

import json
import re
import threading
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from finn import llm, notify
from finn.pull import research
from finn.push import digest

ROOT = Path(__file__).resolve().parent
DATA = ROOT / "data"
WEB = ROOT / "web"

app = FastAPI(title="FINN")
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


@app.get("/api/status")
def status():
    cfg = json.loads((ROOT / "config" / "portfolio.json").read_text())
    ready = []
    for p in sorted((DATA / "research").glob("*.json")):
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


@app.get("/api/research/{symbol}/stream")
def research_stream(symbol: str, force: bool = False):
    sym = _sym(symbol)

    def gen():
        try:
            for ev in research.run(sym, force=force):
                yield f"data: {json.dumps(ev, ensure_ascii=False, default=str)}\n\n"
        except LookupError:
            yield f"data: {json.dumps({'type': 'error', 'text': f'{sym} not found. Use the NSE symbol, e.g. INFY.'})}\n\n"
        except Exception as e:
            yield f"data: {json.dumps({'type': 'error', 'text': f'{e.__class__.__name__}: {e}'})}\n\n"

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


@app.get("/api/track/{symbol}")
def track_status(symbol: str):
    sym = _sym(symbol)
    st = _building.get(sym)
    if st:
        return {"symbol": sym, "status": st}
    feed = json.loads((DATA / "feed.json").read_text(encoding="utf-8"))
    return {"symbol": sym, "status": "ready" if any(h["symbol"] == sym for h in feed["holdings"]) else "unknown"}


# ------------------------------------------------------------------ telegram alerts ---

class NotifySettings(BaseModel):
    pin: str
    symbols: list[str]
    rules: dict


def _check_pin(pin: str):
    if not notify.PIN() or pin != notify.PIN():
        raise HTTPException(403, "Wrong PIN")


@app.get("/api/notify/status")
def notify_status():
    return {"configured": notify.configured(), "pin_required": bool(notify.PIN())}


@app.put("/api/notify/settings")
def notify_settings(s: NotifySettings):
    _check_pin(s.pin)
    notify.save_settings([_sym(x) for x in s.symbols][:100], s.rules)
    return {"ok": True}


class Pin(BaseModel):
    pin: str


@app.post("/api/notify/test")
def notify_test(p: Pin):
    _check_pin(p.pin)
    if not notify.configured():
        raise HTTPException(503, "Telegram isn't set up on the server")
    feed = json.loads((DATA / "feed.json").read_text(encoding="utf-8"))
    text, _ = notify.digest(feed, force=True)
    res = notify.send(text or "<b>FINN</b>\nConnected. Nothing crossed your rules in the last session.")
    if not res.get("ok"):
        raise HTTPException(502, res.get("description", "Telegram error"))
    return {"ok": True}


@app.get("/")
def index():
    return FileResponse(WEB / "index.html")


app.mount("/", StaticFiles(directory=WEB), name="web")
