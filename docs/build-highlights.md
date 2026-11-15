# FINN: AI build highlights

I built FINN in one week (8 to 15 Nov 2026) in a single long Claude Code session in the desktop app. Claude Opus 5.5 did the main build, Fable 5.2 a final review and polish pass, and Sonnet 5.5 the wrap-up. The product itself runs on free-tier APIs (Gemini, Groq). About 75 messages passed between us, and these are a few of the moments that show how the work went. My own messages are quoted exactly, typos included.

## How we worked
I set the constraints and judged the result. Claude proposed, built, and checked its own work: it opened the running app at phone size, compared the figures on screen with the reported numbers, and re-checked the live site after every push. I pushed back whenever something looked or behaved wrong, and most of the changes below came from that.

## 1. Cost shaped the architecture
The brief rewards spending little, so I chose free tiers from the start, and that decided the design. A regex classifies filings from NSE's own subject lines, with no model. Exact results numbers come from the XBRL filing, also with no model. A cheap model reads only the filings whose summary is unclear, and a stronger model handles the one step that needs reasoning, the Promise Ledger. Every call is cached by content. Total spend: ₹0.

## 2. Pushing until it looked like a real product
Early on I asked for instant search that understands company names, because *"people remember company name often then stock name"*. That produced a type-ahead over all 2,600+ NSE companies, so "Zomato" finds Eternal. Then, on the design:

> "right now it looks so cluttered … make it more responsive , sleek , useful features more visible"

> "still there is a lot of clutter like about 25 minutes , 143 filled away and stuff like they dont exist in real apps right … act like i am making a professional app which could be deployed right now"

The first redesign still had counters and stats nobody needs. The second removed them and added a collapsible sidebar and a better, bundled font.

On Telegram alerts: *"why you wrote like see it in readme , i said keep it professional , i should have the option to add bot from here only"*. A setup step that lived in the README became a one-tap Connect Telegram button at the top of the Alerts screen, and each visitor gets alerts for their own portfolio.

## 3. Density, tested against the brief
I sent a screenshot of the brief's "The bar — no AI slop" section ("Density and design both count") and asked whether the app *"blasted"* a phone user with too much. Claude measured it: a company page ran to 5.5 screens and about 600 words, with the same facts repeated in three sections. The fix was a 30-second summary at the top (score, strongest bull and bear point, what to watch) with the detail folded behind it, plus one card per piece of news. A second pass cut Alerts from 9.6 screens to 3.1 and the Research landing page from 7.4 to 1.3.

## 4. Checking what the models produced
We ran the same Promise Ledgers through three models (Gemini Flash-Lite, Groq's GPT-OSS-120B, Gemini Flash). Each failed in a different way, so the fixes went into code; longer prompts would not have caught them.
- One ledger compared an EBIT promise with EBITDA, and constant-currency guidance with rupee revenue. EBIT and EBITDA are now computed separately, with explicit like-for-like rules.
- Promises repeated and evidence was weak. Duplicates are now merged by shared figures, and every quote is string-matched back to the transcript page.
- A stale 2023 earnings call slipped into a ledger (I spotted it from a screenshot). Only calls from the last 15 months count now.
- Scores were awarded on one or zero judged promises. Now there is no score unless at least two can be judged.

A later Groq re-run of 20 ledgers looked like an upgrade, but Claude compared it with the previous version and found real errors: a margin taken from the wrong quarter, a beaten lower-is-better target marked "missed", and dropped promises. We discarded all 20, and the upgrade script now accepts only Gemini Flash's verdicts. In another bug, a compliance officer's appointment was shown as the "cause" of a 3.7% price move. Now only price-sensitive filings can be named as a cause.

## 5. Free tiers as they really behave
Gemini Flash's free quota was about 10 to 20 calls a day, newer models were overloaded at peak hours, and Groq capped tokens per day. Hugging Face Docker Spaces turned out to need a paid plan, so the app moved to Render's free tier. The router therefore discovers models at run time, records each model's health, and tells per-minute limits from daily ones. It never stores a fallback answer as if the strong model had given it, so ledgers upgraded themselves as quota returned. All 56 ledgers ended up on the strong model.

## 6. Shortcuts we turned down
- BSE's API sits behind bot protection (HTTP 403). We attempted a workaround, it was blocked as bot-check circumvention, and we dropped it. The app uses NSE's public endpoints instead.
- We ruled out a second Google account to multiply free quota, because Google's terms don't allow spreading requests across accounts to get around limits. Groq stayed the second free provider.

## 7. Reliability problems found by looking
When I asked Claude to *"check my project and improve it as much as you can"*, it found that GitHub's scheduled keep-alive ran only a few times a day, so the free server kept falling asleep. The job had also been failing since the evening of 8 Nov on a script bug: a grep that matched "unchanged" as "changed". The server now pings itself, and the job is fixed. Claude also closed a quota leak (one analysis per company at a time), made the app re-add stocks the free host forgets, and added 20 offline tests that run on every change. They cover the XBRL arithmetic, filing rules, materiality scoring, and alert thresholds and de-duplication.

Live at https://finn-dq90.onrender.com, code at https://github.com/Adit2a/finn.
