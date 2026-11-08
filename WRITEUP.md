# FINN — covering note

**Prototype (open on a phone):** https://finn-dq90.onrender.com · **Repository:** https://github.com/Adit2a/finn · **AI build history:** <Claude Code transcript>

## What I built
FINN treats a portfolio like an inbox you clear in a few minutes, and treats management commentary as a set of promises to be checked.

**Push — the Brief.** Every NSE filing across your portfolio, for a window you pick (last session / 24h / 7d / 30d / new since your last look), sorted into *Needs attention*, *Worth a glance* and routine filings that stay hidden. The default is *last session*, not 24 hours: opened on a Monday morning, "24h" shows an empty weekend, while "since the close before the latest trading day" always shows the session you missed plus its evening filings. Each item shows why it ranked where it did ("₹13,000 cr ≈ 3.2% of market cap", "3 exits in one go", "PAT +114% YoY").
- **Results** arrive as exact Revenue / EBITDA / PBT / PAT figures, QoQ and YoY, read from the company's own XBRL filing rather than parsed from a PDF by a model. Reliance's EBITDA comes out at exactly the ₹62,409 cr its CFO quotes on the call. Banks show pre-provision operating profit; NBFCs mark EBITDA as not meaningful; companies that only tag year-to-date figures get the quarter derived by subtraction.
- **Management changes** carry the incoming person's background from the "brief profile" SEBI requires in the appointment filing itself (a primary source), plus a LinkedIn link. Several departures on one day merge into one higher-priority event.
- **Unusual moves** (your thresholds) are linked to the filing that most likely caused them, or say plainly that nothing disclosed explains the move.
- **Related filings fold together:** a results day is one card, not six.
- **You train it:** ✓ (or swipe left) clears an item; ⊖ files that kind of filing for that stock as routine from then on.
- **Portfolio heatmap** for the day at a glance; tap a stock to see only its news.
- **Coming up:** results board meetings, ex-dividend dates and announced earnings calls for your holdings, from NSE's event calendar and corporate-actions feeds.

**Portfolio.** Add any NSE stock by name or symbol (2,600+ companies, with suggestions as you type, including names people actually use — "zomato" finds Eternal); optional quantity and cost show value and returns. Each person's list lives on their own device. A stock no one has added before is fetched in the background in under a minute and joins the daily refresh.

**Alerts.** Sliders for price move and volume spike plus the filing types you care about; the app re-evaluates 30 days of history live and tells you if your rules are noisy or too quiet. After each market close FINN sends **one Telegram message** with anything new that crossed your rules — each alert once, never repeated.

**Pull — Research.** Any NSE company; the Nifty 50 and the demo set open instantly, anything else shows its financials in seconds while the ledger streams in. FINN reads the last four earnings-call transcripts (only calls from the last 15 months) and the latest annual report, and produces:
- a **Promise Ledger**: each commitment management made, when, and what the reported numbers show (delivered / partly / missed / walked back / too early), with the verbatim quote, its page, and a ✓ when the quote was found word-for-word in the transcript;
- a credibility score — only when at least two promises can actually be judged — story-vs-numbers checks, bull/bear, and what to check next quarter;
- trajectory, leverage, working capital and cash conversion as charts plus rule-based flags — arithmetic, not model output;
- *Ask the filings*: follow-up questions answered from transcript and annual-report pages, with page citations.
Companies that hold no earnings calls fall back to promises in last year's annual-report outlook; scanned transcripts are read visually and labelled as such.

## Key decisions and trade-offs
1. **Numbers never come from a model.** Results come from XBRL; trajectory and cash quality from reported tables; the model only reads and compares. This removes the most damaging failure mode — a confidently wrong number — at zero cost.
2. **Rules first, model last.** NSE's own subject lines categorise most filings for free; the cheap model reads only the PDFs whose summary is unclear *and* whose category could matter (~10–20% of filings). Routine paperwork never touches a model.
3. **Model routing by job.** Gemini Flash-Lite does bulk extraction (a whole transcript fits in one call, no chunking); the one reasoning step goes to Gemini Flash, then Groq's GPT-OSS-120B, then Flash-Lite as a last resort. Every call is cached by content. Spend: ₹0.
4. **Built for free tiers as they really behave.** On build day the newest Flash was overloaded, `gemini-2.5-flash` had been retired, and Flash's free quota ran out after ~10 calls. The router discovers models at runtime, records each model's health until its quota resets, rejects malformed answers, and never caches a fallback answer as if the strong model gave it — so ledgers upgrade themselves when quota returns.
5. **Model output is checked, not trusted.** Running the same ledgers through three models showed each fails differently — repeated promises, weak evidence, constant-currency guidance compared with rupee revenue, segment promises judged on company totals. The fixes went into code rather than prompts alone: EBIT and EBITDA computed separately, explicit like-for-like rules, schema and citation validation, duplicate merging by shared figures, quotes string-matched to the source, a recency window on transcripts (a stale 2023 call once slipped through), and no score unless it's earned.
6. **Privacy and safety by design.** Portfolios and rules stay on each device; the shared server can't be changed by visitors; Telegram settings are PIN-protected; keys live only in host secrets.
7. **Allowed sources only.** BSE's API sits behind bot protection, so I used NSE's public endpoints rather than working around it. I also chose not to use extra Google accounts to multiply free quota.

## How it compares
Screener.in and Trendlyne list announcements and summarise concalls; AlphaSense and Fiscal.ai search transcripts with AI. FINN's difference is that it **ranks** filings instead of listing them, reads results from XBRL instead of a PDF, links price moves to their cause, and — the part the brief values most — **checks management's promises against delivery** rather than summarising them.

## Deliberately left out
- Intraday prices (EOD is what the brief specifies) and a stock-chart page.
- News, broker notes and the model's own knowledge on the research side, by design.
- Accounts and cross-device sync: each device keeps its own portfolio.
- Peer comparison.

## Where I'd take it next
- Carry the Promise Ledger forward: when a new transcript lands, re-score open promises and alert "Persistent just missed its FY27 margin promise".
- Per-holding materiality learned from ✓ / ⊖ history instead of fixed weights.
- Annual-report deep checks: auditor qualifications, contingent liabilities, related-party transactions, year-on-year changes.
- Licensed data feeds and accounts for a multi-user product.
