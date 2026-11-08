# FINN — a portfolio inbox that tells you what changed, and whether management keeps its word

**Live app:** https://finn-dq90.onrender.com — best on a phone (add it to your home screen). Free hosting: if it has been idle, the first load can take up to a minute.

FINN is a mobile-first investing agent for Indian equities. It does two things:

- **Push — the Brief.** Every exchange filing across an 18-stock portfolio, ranked by how much it could matter. Results arrive as exact Revenue / EBITDA / PBT / PAT tables (QoQ, YoY) read from the company's XBRL filing. Unusual price/volume days are linked to the filing that probably caused them. Routine paperwork is filed away. You clear items like an inbox, and "Less" teaches it what you don't care about.
- **Pull — Research.** Type any NSE symbol. FINN reads the last four earnings-call transcripts and the latest annual report and builds a **Promise Ledger**: every commitment management made, checked against what the reported numbers show. Every quote is verified against the transcript text and linked to its page.

## Run it locally

```bash
python -m venv .venv
.venv/Scripts/pip install -r requirements.txt     # macOS/Linux: .venv/bin/pip
cp .env.example .env                              # add free Gemini + Groq keys (both optional)
python scripts/refresh.py                         # build data/feed.json (~2 min)
uvicorn server:app --port 8000                    # open http://localhost:8000 on your phone/laptop
```

With no key, everything except the Promise Ledger, filing reads and Q&A still works (rules + XBRL + arithmetic).

## Your portfolio, alerts and events

- **Portfolio tab** — add any NSE stock by name or symbol, remove stocks, optionally enter quantity and average cost for value and returns. The list lives on your device; a stock nobody has added before is fetched by the server in the background (~15–60 s) and joins the daily refresh.
- **Coming up** — results board meetings, ex-dividend / record dates and announced earnings calls for your holdings, from NSE's event calendar and corporate-actions feeds.
- **Telegram alerts** — connect in one tap from the app; after each market close, one message per person with anything that crossed *their* rules for *their* portfolio. Each alert is sent once.

### Telegram alerts
Users connect from the app: **Alerts → Connect Telegram** opens FINN's bot, one tap on *Start* links the chat, and the device's portfolio and rules sync automatically (`/stop` in Telegram unsubscribes). The server needs `TELEGRAM_BOT_TOKEN` (a free bot from @BotFather) and `FINN_PUBLIC_URL`; on start it registers itself as the bot's webhook. Subscribers are stored encrypted (key derived from the bot token) in `data/subscribers.enc`, which the GitHub jobs copy into the repo so a restart of the free host never loses them.

## How it is put together

```
scripts/refresh.py   batch, after market close (GitHub Actions cron)
  ├─ sources/prices.py     yfinance EOD, one batched call
  ├─ sources/nse.py        announcements API + integrated-filing XBRL → exact results numbers
  ├─ push/classify.py      NSE subject line + regex → category → materiality score with reasons
  ├─ push/enrich.py        cheap model reads only opaque, possibly-material PDFs (people, order values)
  └─ push/digest.py        cluster related filings into one event → data/feed.json

server.py            FastAPI: serves the app + /api/feed, /api/research/{sym}/stream (SSE), /api/ask
  └─ pull/research.py      screener index → numbers.py (no model) → cheap model ×4 transcripts
                           → quote verification → cheap model on AR pages → strong model synthesis
web/                 Preact + htm, no build step, installable PWA
```

### Model routing (the cost story)

| Step | Who does it | Why |
|---|---|---|
| Category of 250+ filings | regex on NSE's own subject line | free, instant, ~90% right on its own |
| Results numbers | XBRL arithmetic | exact; reconciles to company-reported EBITDA |
| Trajectory, leverage, cash conversion, flags | arithmetic | a model should never compute a number |
| Reading opaque filings (≈10% of them) | Gemini Flash-Lite | bulk extraction, cheap tier |
| Guidance extraction per transcript | Gemini Flash-Lite, whole transcript in one call | long context, no chunking needed |
| Scanned transcripts (no text layer) | Gemini Flash-Lite reading the PDF directly | quotes labelled "read visually", not "verified" |
| Quote verification | string matching | catches hallucinated quotes |
| Promise Ledger, bull/bear | Gemini Flash → Groq GPT-OSS-120B → Flash-Lite | the only step that needs reasoning; input is small (~8k tokens), so Groq's free tier fits |

**Running on free tiers, for real.** Gemini Flash's free daily quota is small (it ran out after ~10 calls on build day), and the newest Flash models were overloaded. So the router:
- discovers model names from the API (on build day `gemini-2.5-flash` had been retired), never hardcodes them;
- records each model's health on disk (`ok` / `slow` / `quota` with an expiry at Google's daily reset), so it never wastes requests re-probing;
- walks a fallback chain per tier, and **never caches a fallback answer as if the strong model gave it**, so ledgers upgrade automatically when quota returns;
- validates the reasoning step's JSON (all sections present, rows cite real promise ids) and moves to the next model if not;
- caps Groq output so requests fit its 8k-tokens-per-minute free budget.

Every call is cached by content and the token cost is shown on each dossier. Total spend: ₹0.

### Grounding rules on the pull side
- Inputs are only the company's transcripts, annual report and reported financials. Prompts forbid outside knowledge.
- The model never does arithmetic: "delivered" values come from the reported-numbers block.
- Every ledger row must cite an extracted promise id; rows that don't are dropped.
- Every quote is string-matched back to the transcript and shown as ✓ verified or ⚠ not matched.
- Margins are compared like for like: FINN computes EBIT% (after depreciation) as well as EBITDA%, because IT companies' "operating margin" guidance means EBIT. Constant-currency and USD guidance are never checked against ₹ revenue.
- Duplicate promises restated across calls are merged in code (same figures or heavy word overlap), not left to the model.

## Data sources
- NSE public APIs: corporate announcements, integrated-filing financial results (XBRL)
- Yahoo Finance via `yfinance`: end-of-day price and volume
- screener.in: standardised financial tables and links to annual reports and transcripts (PDFs hosted on BSE / company sites)

Everything is fetched with plain, rate-limited requests and cached on disk.
