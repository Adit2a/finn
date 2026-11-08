"""Model router.

Two tiers, picked per task:
  cheap  -> bulk extraction (classify filings, pull guidance out of a transcript)
  strong -> reasoning (reconcile promises vs delivery, bull/bear)

Providers: Google Gemini (free tier, long context: a whole transcript fits in
one call) or any OpenAI-compatible endpoint (Groq, OpenRouter, local Ollama).
Every call is cached on disk by (model, prompt) and logged to a usage ledger,
so re-running the pipeline costs nothing and we can show what each answer cost.
FINN still works with no key at all: callers fall back to deterministic paths.
"""
from __future__ import annotations

import base64
import hashlib
import json
import os
import re
import time
from pathlib import Path

import httpx
from dotenv import load_dotenv

from finn.net import CACHE

load_dotenv(Path(__file__).resolve().parent.parent / ".env")

GEMINI_KEY = os.getenv("GEMINI_API_KEY", "").strip()
# Groq (or any OpenAI-compatible endpoint). With Gemini also set, it slots into the STRONG tier
# as the backup reasoner: the synthesis prompt is small (~8k tokens), which fits Groq's free limits.
OAI_KEY = (os.getenv("GROQ_API_KEY") or os.getenv("OPENAI_COMPAT_API_KEY") or "").strip()
OAI_BASE = os.getenv("OPENAI_COMPAT_BASE_URL", "https://api.groq.com/openai/v1").rstrip("/")
OAI_MODEL = os.getenv("OPENAI_COMPAT_MODEL", "").strip()
OAI_PREFERRED = ["openai/gpt-oss-120b", "llama-3.3-70b-versatile", "qwen/qwen3-32b", "openai/gpt-oss-20b"]
_oai_model: list[str] = []


def oai_model() -> str:
    """Env override, else the best preferred model the endpoint actually serves."""
    if OAI_MODEL:
        return OAI_MODEL
    if not _oai_model:
        pick = OAI_PREFERRED[1]
        try:
            ids = {m["id"] for m in httpx.get(f"{OAI_BASE}/models", timeout=20,
                                               headers={"Authorization": f"Bearer {OAI_KEY}"}).json()["data"]}
            pick = next((m for m in OAI_PREFERRED if m in ids), pick)
        except Exception:
            pass
        _oai_model.append(pick)
    return _oai_model[0]

LEDGER = CACHE / "llm_ledger.jsonl"
_models: dict[str, str] = {}


class LLMUnavailable(RuntimeError):
    pass


def available() -> bool:
    return bool(GEMINI_KEY or OAI_KEY)


def _ver(name: str) -> tuple:
    return tuple(int(x) for x in re.findall(r"\d+", name.split("gemini-")[-1])[:2]) or (0,)





def _pick_gemini_models() -> dict[str, list[str]]:
    """Fallback chains per tier. Env overrides go first; then the newest stable models
    discovered from the API (model names get retired, so nothing is hardcoded)."""
    if _models:
        return _models
    oai = [f"oai:{oai_model()}"] if OAI_KEY else []
    if not GEMINI_KEY:
        _models.update(cheap=oai, strong=oai)
        return _models
    lites, flashes = [], []
    try:
        r = httpx.get("https://generativelanguage.googleapis.com/v1beta/models",
                      params={"key": GEMINI_KEY, "pageSize": 200}, timeout=30).json()
        names = [m["name"].split("/")[-1] for m in r.get("models", [])
                 if "generateContent" in m.get("supportedGenerationMethods", [])]
        ok = [n for n in names if re.fullmatch(r"gemini-\d+(\.\d+)?-flash(-lite)?", n)]
        lites = sorted([n for n in ok if n.endswith("-lite")], key=_ver, reverse=True)
        flashes = sorted([n for n in ok if not n.endswith("-lite")], key=_ver, reverse=True)
    except Exception:
        pass
    env_c, env_s = os.getenv("FINN_CHEAP_MODEL"), os.getenv("FINN_STRONG_MODEL")
    cheap = ([env_c] if env_c else []) + lites[:3]
    strong = ([env_s] if env_s else []) + flashes[:4] + oai  # Flash first, Groq as backup reasoner
    dedupe = lambda xs: list(dict.fromkeys(x for x in xs if x))
    _models.update(cheap=dedupe(cheap) or ["gemini-flash-lite-latest"],
                   strong=dedupe(strong) or ["gemini-flash-latest"])
    return _models


# Model health, persisted across runs so we never waste scarce free-tier requests re-checking.
#   ok    -> trusted for 6h, no probing
#   slow  -> overloaded/timeouts, benched 15 min
#   quota -> daily free quota spent, benched until Google's reset (midnight Pacific ≈ 08:00 UTC)
HEALTH = CACHE / "model_health.json"


def _health() -> dict:
    try:
        return json.loads(HEALTH.read_text())
    except Exception:
        return {}


def _set_health(model: str, state: str) -> None:
    now = time.time()
    if state == "quota" and model.startswith("oai:"):
        until = now + 1800  # Groq's limits are rolling windows: retry in 30 min, not tomorrow
    elif state == "quota":
        reset = (int(now // 86400) * 86400) + 8 * 3600  # Google resets at midnight Pacific
        until = reset if reset > now else reset + 86400
    else:
        until = now + (6 * 3600 if state == "ok" else 900)
    h = _health()
    h[model] = {"state": state, "until": until}
    HEALTH.write_text(json.dumps(h))


def _state(model: str) -> str | None:
    s = _health().get(model)
    return s["state"] if s and s["until"] > time.time() else None


def _probe(model: str) -> bool:
    """One tiny request, only for a model we know nothing about. New models are often
    overloaded on the free tier; this keeps a real call from hanging for minutes."""
    try:
        r = httpx.post(f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent",
                       params={"key": GEMINI_KEY}, timeout=20,
                       json={"contents": [{"parts": [{"text": "Reply with the word ok"}]}]})
        state = "ok" if r.status_code == 200 else "quota" if r.status_code == 429 else "slow"
    except httpx.HTTPError:
        state = "slow"
    _set_health(model, state)
    return state == "ok"


def chain(tier: str) -> list[str]:
    """Usable models for a tier, best first. The strong tier always ends with a cheap model
    as a last resort, so a spent quota degrades quality instead of failing."""
    if not GEMINI_KEY:
        return _pick_gemini_models()[tier]
    out = []
    for m in _pick_gemini_models()[tier]:
        st = _state(m)
        # Groq models aren't probed (no wasted requests); they're benched only after a real failure
        if st == "ok" or (st is None and (m.startswith("oai:") or _probe(m))):
            out.append(m)
            if len(out) == 2:
                break
    if tier == "strong":
        out += [m for m in chain("cheap")[:1] if m not in out]
    return out or _pick_gemini_models()[tier][-1:]


def tier_of(model: str) -> str:
    return "cheap" if "lite" in model else "strong"


def label(model: str) -> str:
    return model.removeprefix("oai:")


def model_for(tier: str) -> str:
    return label(chain(tier)[0])


def _log(entry: dict) -> None:
    with LEDGER.open("a", encoding="utf-8") as f:
        f.write(json.dumps(entry) + "\n")


def _parse_json(text: str):
    text = text.strip()
    text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text)
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        m = re.search(r"[\[{].*[\]}]", text, re.S)
        if m:
            return json.loads(m.group(0))
        raise


_last_call: dict[str, float] = {}
MIN_GAP = {"lite": 4.5, "flash": 6.5}  # seconds between calls: stays under free per-minute limits


def _pace(model: str) -> None:
    gap = MIN_GAP["lite"] if "lite" in model else MIN_GAP["flash"]
    wait = _last_call.get(model, 0) + gap - time.time()
    if wait > 0:
        time.sleep(wait)
    _last_call[model] = time.time()


def _gemini(model: str, system: str, prompt: str, pdf: bytes | None = None) -> tuple[str, dict]:
    url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
    parts = [{"text": prompt}]
    if pdf:  # scanned filings: let the model read the page images directly
        parts.insert(0, {"inline_data": {"mime_type": "application/pdf",
                                         "data": base64.b64encode(pdf).decode()}})
    body = {
        "systemInstruction": {"parts": [{"text": system}]},
        "contents": [{"role": "user", "parts": parts}],
        "generationConfig": {"temperature": 0.1, "responseMimeType": "application/json"},
    }
    for attempt in range(5):
        _pace(model)
        try:
            r = httpx.post(url, params={"key": GEMINI_KEY}, json=body, timeout=180)
        except httpx.TimeoutException:
            _set_health(model, "slow")
            break
        if r.status_code == 429:
            daily = bool(re.search(r"PerDay|per day|per_day", r.text, re.I))
            m = re.search(r'"retryDelay":\s*"(\d+)', r.text)
            wait = int(m.group(1)) + 1 if m else 15
            if daily:  # the day's free quota is spent: bench until Google's reset
                _set_health(model, "quota")
                break
            if attempt == 4:  # still throttled after ~a minute of waiting: bench briefly, not all day
                _set_health(model, "slow")
                break
            time.sleep(min(wait, 60))  # per-minute limit: wait as told, then retry
            continue
        if r.status_code in (500, 503, 504):  # overloaded: quick retries, then bench briefly
            if attempt == 2:
                _set_health(model, "slow")
                break
            time.sleep(3 * (attempt + 1))
            continue
        if r.status_code == 404:
            _set_health(model, "slow")
            break
        r.raise_for_status()
        j = r.json()
        cand = (j.get("candidates") or [{}])[0]
        parts = cand.get("content", {}).get("parts", [])
        text = "".join(p.get("text", "") for p in parts if not p.get("thought"))
        u = j.get("usageMetadata", {})
        return text, {"in": u.get("promptTokenCount", 0),
                      "out": u.get("candidatesTokenCount", 0) + u.get("thoughtsTokenCount", 0)}
    raise LLMUnavailable(f"{model}: unavailable")


def _openai_compat(model: str, system: str, prompt: str) -> tuple[str, dict]:
    name = model.removeprefix("oai:")
    body = {"model": name, "temperature": 0.1, "response_format": {"type": "json_object"},
            "max_completion_tokens": 3000,  # Groq reserves this against the 8k/min free budget
            "messages": [{"role": "system", "content": system + " Respond with a single JSON object."},
                         {"role": "user", "content": prompt}]}
    if "gpt-oss" in name:  # its hidden reasoning counts against the token cap: keep it short
        body["reasoning_effort"] = "low"
    for attempt in range(3):
        try:
            r = httpx.post(f"{OAI_BASE}/chat/completions", timeout=120, json=body,
                           headers={"Authorization": f"Bearer {OAI_KEY}"})
        except httpx.TimeoutException:
            _set_health(model, "slow")
            break
        if r.status_code == 429:  # Groq: per-minute token limits; long waits mean daily cap
            wait = float(r.headers.get("retry-after", 10))
            if wait > 60 or attempt == 2:
                _set_health(model, "quota" if wait > 600 else "slow")
                break
            time.sleep(wait + 1)
            continue
        if r.status_code == 413:  # prompt bigger than this model's per-minute budget
            raise LLMUnavailable(f"{name}: prompt too large for free tier")
        if r.status_code == 400 and "response_format" in r.text:
            body.pop("response_format")
            continue
        r.raise_for_status()
        j = r.json()
        u = j.get("usage", {})
        return j["choices"][0]["message"]["content"], {"in": u.get("prompt_tokens", 0),
                                                       "out": u.get("completion_tokens", 0)}
    raise LLMUnavailable(f"{model}: rate limited")


def ask_json(tier: str, system: str, prompt: str, task: str = "", tag: str = "", pdf: bytes | None = None,
             validate=None):
    """Run a JSON-returning prompt on the given tier. Cached forever by content.

    Returns (parsed_json, meta) where meta = {model, in, out, cached}.
    pdf: optional raw PDF (Gemini only) for scanned documents with no text layer.
    validate: optional fn(data) -> bool; a malformed answer moves on to the next model, uncached.
    """
    if not available():
        raise LLMUnavailable("no API key configured")
    if pdf and not GEMINI_KEY:
        raise LLMUnavailable("reading scanned PDFs needs Gemini")
    # cache by tier, not model: a re-run reuses whichever model answered last time
    pdf_id = hashlib.sha1(pdf).hexdigest() if pdf else ""
    key = hashlib.sha1(f"{tier}\n{system}\n{prompt}\n{pdf_id}".encode()).hexdigest()
    path = CACHE / "llm" / f"{key}.json"
    path.parent.mkdir(exist_ok=True)
    redo = task and task in os.getenv("FINN_REDO_TASKS", "").split(",")  # deliberate re-judging
    if path.exists() and not redo:
        hit = json.loads(path.read_text(encoding="utf-8"))
        if validate is None or validate(hit["data"]):
            return hit["data"], {**hit["meta"], "cached": True}

    t0 = time.time()
    last = None
    models = chain(tier)
    if pdf:  # only Gemini reads PDFs
        models = [m for m in models if not m.startswith("oai:")]
    for model in models:  # walk the fallback chain: newest -> older -> other provider -> cheaper
        try:
            if model.startswith("oai:"):
                text, usage = _openai_compat(model, system, prompt)
            else:
                text, usage = _gemini(model, system, prompt, pdf) if pdf else _gemini(model, system, prompt)
            data = _parse_json(text)
            if validate and not validate(data):
                _log({"ts": time.time(), "task": task, "tag": tag, "model": label(model), "rejected": "schema"})
                raise ValueError(f"{label(model)} returned an incomplete answer")
            break
        except (LLMUnavailable, json.JSONDecodeError, httpx.HTTPError, ValueError) as e:
            last = e
    else:
        raise LLMUnavailable(f"all models failed for {tier}: {last}")
    fallback = bool(GEMINI_KEY) and tier_of(model) != tier
    meta = {"model": label(model), **usage, "secs": round(time.time() - t0, 1), "cached": False, "fallback": fallback}
    if not fallback:  # never let a degraded answer stand in for the real tier on later runs
        path.write_text(json.dumps({"data": data, "meta": meta}, ensure_ascii=False), encoding="utf-8")
    _log({"ts": time.time(), "task": task, "tag": tag, **meta})
    return data, meta
