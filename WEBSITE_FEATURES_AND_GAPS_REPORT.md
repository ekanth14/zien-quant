# Zien Quant / MarketScope — Complete Features and Completion Report

**Audit date:** 30 October 2026, India Standard Time  
**Main database evidence snapshot:** 2026-10-30T13:40:24.186114+05:30  
**Project:** `/Users/chethan/Documents/finance/zien-quant`  
**Branch:** `feat/paper-watchlist`  
**Report type:** Current source-code, stored-data and selected live-interface audit. This is a completion assessment, not a profitability or security certification.

## 1. Overall position

The website is an implemented personal stock-research terminal with public-market candles, technical analysis, stock search, company-result collection, local AI assistance and two separate paper-trading systems. The running server and AI service are available locally.

The main unfinished work is **data completeness and integration**. Some substantial features exist in code but cannot populate until their provider or data is available. Other features are usable through direct links while a navigation bug hides their sidebar buttons. Neither the technical scores nor the earnings models establish reliable future trading accuracy.

The earlier statement that the backend still needed a restart is no longer the current runtime situation: the server process has changed, migration 006 is present, paper events are being recorded, and the new paper view loaded during this audit.

### Status meanings

- **Implemented:** supported by the source code; not every control was exercised in this audit.
- **Live verified:** observed in the current local browser or database.
- **Partial / data-limited:** functionality exists, but important inputs, coverage or integration remain incomplete.
- **Blocked:** a configuration or workflow requirement currently prevents operation.
- **Not built / placeholder:** no complete usable workflow was found.
- **Expected empty:** there is a valid reason to show no values; filling the space with demo data would be incorrect.

## 2. Current runtime and data inventory

Counts describe raw local database records at the snapshot time. They are not counts of fully verified quarters, independent trading samples or completed trades. Collectors continue running, so later counts will differ.

| Item | Current evidence | Meaning |
|---|---|---|
| Website | Python process listening on `127.0.0.1:8766`; dashboard loaded | Local website is running |
| Local AI service | Ollama listening on `127.0.0.1:11434`; `/api/ai/status` returned online | AI daemon is reachable |
| Models | `gemma4:12b` and `laya:latest` installed; target and decision models available | Models are installed; this audit did not submit an inference prompt |
| Generative-model residency | `model_loaded=false` in inspected status | Available on disk; not proof of failure or successful generation |
| Model training | `weights_trained=false`, `knowledge_mode=document_grounding` | Website context retrieval, not automatic fine-tuning on all daily data |
| Company catalogue | 2,613 entries, with the generic database label `NSE/BSE` | Search universe; this label alone does not certify complete coverage of both exchanges |
| Current feed universe | 24 companies in the inspected dashboard | Catalogue size does not equal simultaneously monitored stocks |
| Candle archive | 535,089 rows | Real stored bars across different intervals and date ranges |
| Result-announcement records | 47,597 across 2,339 symbols | Large announcement ledger; numerical enrichment is much smaller |
| Records with both revenue and profit QoQ comparisons | 461 — 0.97% of result rows | Most result rows cannot yet support the requested financial comparison |
| Result-news/disclosure records | 5,389 | Stored company context; not an exhaustive market-news service |
| Account setup | One owner account exists | Another owner account does not need to be created |
| Saved configuration versions | 0 | Default configuration is in use unless other runtime settings apply; no saved version history yet |
| Completed stored market snapshots | Four date/interval combinations | Useful archive, not every trading day |
| FYERS credentials | App/client ID and access token not configured in inspected local environment | FYERS-dependent views remain blocked |
| Kite credentials | Not configured | Kite integration is also a placeholder, rather than a completed feed |
| Memberships/payment events | 0 / 0 | Billing/subscriber operation is not active |

**AI verification scope:** the status endpoint reported both models available and after-hours chat supported by the backend. Successful model generation, response quality and latency were not tested by sending a prompt.

## 3. What you can do in the website

### 3.1 Market overview and animated stock terminal

**Status: implemented; current tracked-universe display live verified.**

You can view company prices, changes, technical scores, advancing/declining counts, sector summaries, bullish/bearish classifications and detailed stock panels. Blue/red trajectories visualize movement relative to session open. Hovering or selecting plotted companies gives additional context.

The terminal has pause/resume, theme and reduced-motion controls. Animation pauses or changes behavior when data is unavailable, stale, paused or hidden. It should not be interpreted as evidence that a fresh exchange tick arrived.

**Limits:** inspected breadth covered 24 received companies, not all 2,613 catalogue companies. Sector performance is based on the tracked subset. “Order blocks” and volume profiles are candle-derived heuristics, not a live institutional order book.

### 3.2 Company search and stock scanner

**Status: implemented.**

You can search the company catalogue by name/symbol, open company details, filter received stocks by sector and sort by score, price change, bearish score or symbol. Owner-authenticated tracking can add symbols to the backend feed, subject to the configured limit; the default cap is 100.

**Limits:** the scanner's displayed catalogue count can be 2,613 while the populated live cards cover a much smaller monitored list. The add/remove tracking handlers change the scraper's in-memory list; persistence across restarts needs to be made explicit or completed. A saved backend symbol configuration and browser favourites are different things.

### 3.3 Personal Watchlist

**Status: implemented.**

You can favourite companies and return to their cards. This list is stored in browser local storage.

**Limits:** it is not automatically synchronized across devices or users. Adding a favourite is not a trade and does not necessarily start backend collection for a previously untracked instrument.

### 3.4 Charts and technical overlays

**Status: implemented; provider/history dependent.**

The dashboard supports candle intervals such as 1, 5, 15 and 60 minutes, volume, VWAP, order-block zones and price levels. Detailed charts use received bars, with explicit unavailable-data behavior.

The backend includes EMA, RSI, ATR, momentum, RVOL, support/resistance, Supertrend, CPR/Camarilla and VWAP-band calculations. Not every backend field has an equally prominent control in every layout.

**Limits:** changing interval can produce an empty chart when the source/archive lacks that interval. A daily bar cannot reconstruct a missing historical 09:15–11:00 path.

### 3.5 Embedded historical price view

**Status: implemented, currently blocked for FYERS candle retrieval.**

The side panel supports stock/index search, date ranges from 6 May 2023, 1D/5D/1M/6M/YTD/custom controls, a lightly filled historical line chart, selected price/time, expansion into a wider view, synchronized day selection and an intraday candlestick chart. Responsive resizing and explicit loading/empty/error states are implemented.

**Current blocker:** the FYERS app/client ID and access token are absent locally. A cached instrument master may support search while historical prices remain unavailable. This is a configured-data problem, not an absence of chart code.

### 3.6 Flow River, funnel and alternate layouts

**Status: implemented; individual layouts need broader end-to-end QA.**

Routes include `/flow`, `/matrix`, `/console`, `/bloomberg` and `/mobile`. These provide different arrangements of trajectories, candle-derived volume profiles, funnel filtering and setup cards.

**Limits:** these are alternate interfaces over the application's sources. The Bloomberg-style layout does not provide Bloomberg data, subscriptions, analytics coverage or an execution connection.

### 3.7 Technical scoring and trade-setup analysis

**Status: implemented and experimental.**

The main composite uses normalized EMA trend, momentum, VWAP distance and RSI. Default weights are 35/30/20/15. Directional setup logic evaluates breakout/pullback long and breakdown/pullback short cases, entry zones, invalidation stops, targets, R:R, grades and explanatory factors. The default minimum R:R hurdle is 1.5.

Optional Laya decision output can modify conviction and promote certain grades. That model confidence is not a measured trading win probability. Core level calculations remain rule-based.

**Limits:** there is no established live profitability, calibrated probability or guaranteed risk limit. The backend does not expose independently calculated confirmed setup plans; the frontend deliberately withholds those plans when independent confirmation is unavailable.

### 3.8 Best/worst observed daily ranking

**Status: implemented; data-dependent.**

A separate closed-five-minute calculation ranks eligible monitored equities using EMA, Supertrend, session VWAP, RSI, MACD and OBV. It displays strongest/weakest observed scores, tier counts, formula weights and excluded instruments/reasons.

**Limits:** this ranking has a different formula and purpose from the main four-component score. It requires sufficient warm-up, session volume and synchronized observations. “Best observed” does not predict the eventual closing winner. Its combined weights have not been calibrated to future returns.

### 3.9 Previous sessions and persistent candles

**Status: implemented; partial historical coverage.**

You can inspect received prior sessions and use real archived bars when the market is closed. The archive persists candle data and stored session snapshots rather than generating replacement prices.

At the snapshot:

| Interval | Rows | Symbols | Earliest bar | Latest bar |
|---|---:|---:|---|---|
| 1 minutes | 13,096 | 27 | 2026-10-26T09:15:00+05:30 | 2026-10-29T15:29:00+05:30 |
| 5 minutes | 358,128 | 112 | 2026-07-30T09:15:00+05:30 | 2026-10-30T13:35:00+05:30 |
| 1440 minutes | 163,865 | 89 | 2016-10-26T09:15:00+05:30 | 2026-10-26T09:15:00+05:30 |

Stored completed snapshots were: 23 October 2026 (5m), 27 October (5m), and 29 October (1m and 5m).

**Important:** the oldest daily data goes back much further than the intraday archive. Neither this table nor the catalogue establishes complete intraday history from May 2023.

### 3.10 Company Results & Reactions

**Status: implemented, with major enrichment gaps.**

The separate results section lists website-company announcements by date, supports filters/search, links source documents, shows available financial figures and previous-quarter/year comparisons, and attaches company disclosure/news context. Its configured history floor is 6 May 2023. Background collection/enrichment runs while the server is running.

At the snapshot:

- 47,597 raw result records; 2,339 company symbols.
- 814 records marked financial data available; 15 revision-pending; 46,768 awaiting documents.
- Revenue QoQ present in 488 records; profit QoQ in 492; both in 461.
- Revenue YoY present in 119; profit YoY in 125.
- 2,345 symbol jobs; 80 have a financial-processing timestamp and 89 a candle-processing timestamp. These are activity indicators, not proof of complete history.
- Eleven jobs have recorded errors: nine unsupported comparison schemas, one document-size limit, one HTTP 404.
- Raw announcement range: 4 July 2023 through 27 October 2026.

**Limits:** many announcements are visible while numerical figures are pending. These rows cannot honestly support QoQ analysis or result-conditioned forecasting yet. Current collection also needs freshness investigation because the latest locally stored announcement at this audit precedes 30 October.

### 3.11 Earnings-reaction studies and offline research

**Status: research code/reports exist; website-wide prediction capability is incomplete.**

The earnings engine matches comparable point-in-time results with recorded intraday observations, calculates cohort summaries, uses Wilson bounds and can expose conditional watch setups with VWAP/opening-range requirements. Missing or insufficient samples block the setup.

Separate research packages exist for TCS, Asian Paints, Coal India and Trent under the finance project's research directory. They include event studies, charts, source records and exploratory mathematical models.

**Limits:** those offline packages have not been comprehensively imported into the website's runtime data. The latest local result record for each of Asian Paints, Trent and Coal India still has period end **30 June 2026**, with no usable growth values in that latest record. The main candle archive has Asian Paints bars but no Trent or Coal India rows at the snapshot. Separate offline research files do not automatically fill those application gaps.

A complete earnings/news/peer-conditioned forecast of the next morning's rise, fall and recovery is not established. Analyst-expectation surprise, comprehensive historical news context, peer-result effects, source-vintage certification and sufficient out-of-sample evaluation remain unfinished.

### 3.12 Laya AI research assistant

**Status: backend online; user-interface integration partial.**

You can request market/stock explanations and website-data summaries. The backend retrieves bounded context from snapshots, saved candles, results, the older signal journal, formulas and margin information. When Ollama is unavailable, it can return an explicitly labelled deterministic website-context fallback.

The local status endpoint confirms installed `gemma4:12b` and `laya:latest`, an available decision model, and backend after-hours chat capability.

**Remaining issues:**

1. `sendAiMessage()` uses a local brief whenever the market is not LIVE, the selected interval is not five minutes, the user is signed out or no snapshot exists. Thus backend after-hours capability is not fully exposed by the main UI.
2. The context builder does not currently retrieve `paper_watch_trades`, `paper_watch_events` or `paper_watch_reports`. Laya cannot reliably answer all questions about the new paper ledger without that connection.
3. Retrieval is bounded and covers recorded bars/setups, not every exchange tick or everything that happened in every company.
4. There is no automatic daily weight training/fine-tuning. Installed models and document grounding do not imply a trained market forecasting model.

### 3.13 Three-company Results & Reaction Paper Lab

**Status: implemented, currently blocked/unreviewed.**

Direct route: `/#paper`.

This is the earlier, fixed-stock laboratory for Asian Paints, Trent and Coal India: ₹3 lakh virtual capital, exact earnings-watch conditions, fresh FYERS quotes, review acknowledgement, configurable allocation/risk/costs, Start/Pause/End, trade/event ledger and CSV export. It uses 1× notional reservation by default and an 11:00 cutoff.

Live `/api/paper/status` verification returned **No strategy configured**, `reviewed=false`, `data_configured=false`, no quotes and no trades. Its paper calendar currently extends only through 6 November 2026.

This blank state is expected until the rules are reviewed and FYERS/data requirements are met. It must not be filled with invented signals or profit.

### 3.14 Automatic ATR Paper-Trading Watchlist

**Status: implemented and live activity verified.**

Direct route: `/#paperwatch`.

The separate simulator watches eligible technical setups across tracked stocks. It has pending entries, 15-minute validity, long/short-from-zero simulation, ATR(14) plans, R:R gates, cost-aware sizing, assumed leverage, reserved margin, stop/target/trailing/time/reversal exits, 15:15 square-off, source checks and a persistent event ledger.

It uses existing public-source closed candles and engine/journal integration; FYERS is not a prerequisite for this separate simulator. It preserves the original Results Lab.

**Snapshot state at 2026-10-30T13:40:24.186114+05:30:** one **POWERGRID SHORT PENDING** paper record, planned entry ₹264.69, 1,886 shares, approximately ₹99,958.15 reserved virtual margin. It had no entry fill, exit or net P&L. Reservation is not an investment or an executed position. The remaining virtual funds were approximately ₹2,00,041.85.

The event table contained 420 GATE_BLOCKED; 30 IGNORED; 1 PENDING. Repeated records are not independent research samples.

**Limits:** source candles model hypothetical fills at candle-end observation times. They do not provide exact intrabar execution. The 5× setting is an assumption, not verified broker margin eligibility. Repeated gate-blocked logging was observed within the same candle period; deduplication/storage behavior should be reviewed.

### 3.15 Paper day reports and hindsight

**Status: implemented; current report table expected empty.**

Direct route: `/#paper-report`.

The new simulator can store day reports at 15:35 IST with simulated trade rows, gross/net P&L, costs, returns, R-multiples, win rate, average win/loss, profit factor, expectancy, realized-equity drawdown, daily Sharpe-like calculations and an equity curve. CSV/JSON export is implemented. Complete-window MFE/MAE and alternative-parameter/gate-bypass replays are explicitly hypothetical.

**Current evidence:** `paper_watch_reports` contained 0 rows before today's configured report time. No saved report is currently available; this does not establish a report-generator failure. Deadline generation, post-close retrieval and exports still need a real end-to-end session acceptance check.

MFE/MAE deliberately exclude uncertain entry/exit-bar movement. Missing boundary prices produce an unpriced administrative closure. The application must retain unknown P&L rather than fabricate a successful exit.

### 3.16 Zerodha exact leverage reference

**Status: parser/cache implemented; browser presentation needs checking.**

The display layer can attach exact leverage values to recognized company labels whose verified source leverage exceeds 3×. This is a display reference; it does not borrow money or authorize trades.

The local cache was ready/fresh, dated 30 October 2026, with **1,624 source instruments**, of which **1,354** met the >3× criterion. This is not a claim that all 2,613 catalogue companies have a verified margin value.

**Observed discrepancy:** the inspected browser's margin legend still said margin data unavailable while the local cache was fresh. Fetch delivery, browser clock/freshness validation, rendering selectors and coverage across new paper tables need checking. An absent badge does not prove a stock's actual leverage is ≤3×.

### 3.17 Accounts, owner settings and auditability

**Status: implemented personal-workspace controls; broader administration partial.**

The application has password hashing, owner sign-in, session expiry, CSRF checks, optional TOTP flows, role checks, audit records and rate limits. The owner console can save score/EMA/RSI/timeframe/refresh settings, inspect configuration history, create an application-database backup and inspect recorded signal statistics.

**Limits:** user-management UI, plan management and payment/subscription workflows are not complete. Zero configuration versions means version history is currently empty, not that saving is unimplemented. This audit did not perform a penetration test or alter account credentials.

### 3.18 Exports, backups and diagnostics

**Status: implemented, with operational scope limits.**

Market/session export, paper CSV/JSON, health/source diagnostics, AI status and CLI diagnostics are available in code. The owner backup endpoint uses SQLite's backup API.

**Important limit:** the owner backup action copies the application database. It is not a complete disaster-recovery backup of `candles.db`, `results.db`, the stock master, local configuration, cached source files and research packages. A complete restore procedure and automated backup policy still need implementation/testing.

### 3.19 Git, deployment and public access

**Status: local Git restored; production deployment incomplete.**

The project is on `feat/paper-watchlist`, with origin configured and verified shallow Git history at the installed upstream base. Local changes remain uncommitted/unpushed. Full ancestry and the current remote head have not been fetched through shell Git.

Docker/Caddy deployment files exist. The current deployment context is the `marketscope` directory, while the new `strategy/` and `config/` directories are at repository root. That packaging needs revision and a clean-container startup test. No public deployment was verified in this audit.

The current application is bound to localhost. Public hosting would require access-control review of read endpoints, secure credential handling, HTTPS/domain setup, appropriate data redistribution rights, deployment validation and monitoring. Membership/payment tables alone do not create a subscriber product.

## 4. Why sections may look empty

| Empty area | Current explanation | What completes it |
|---|---|---|
| Historical FYERS chart | No configured FYERS app/token | Configure and verify server-side market-data authorization |
| Results revenue/profit/growth cells | Most records await documents or supported comparisons | Parser/enrichment work, retries, normalized comparable quarterly figures |
| Latest focus-company results | Runtime ledger still has June 2026 period-end records for the three companies | Ingest and verify newer source filings, retaining timestamps and basis |
| Historical next-morning charts | Intraday archive lacks the requested company/date windows | Authorized historical intraday collection with coverage checks |
| Trent/Coal India runtime candle charts | No main candle-archive rows for those symbols at the snapshot | Explicit tracked-data collection/backfill and source checks |
| Results Lab quotes/trades | No strategy review and no FYERS credentials | Review rules, configure source and satisfy evidence gates |
| ATR paper P&L | Pending order has not filled/exited | Valid subsequent source bars and actual simulator lifecycle transitions |
| Day report | No saved reports yet; audit precedes 15:35 | Run and verify the full cutoff/report cycle |
| Main journal closed statistics | All recorded journal rows are still OPEN | Repair/verify candle delivery to outcome updater and session-close handling |
| Configuration history | No version has been saved | Owner saves supported settings |
| Leverage badge | Could be stale/missing source, selector mismatch or truly ineligible | Verify source delivery and company-label integration |
| Best/worst ranking | Can fail minimum history, alignment or eligible-stock requirements | Complete synchronized closed bars; retain exclusions when unavailable |

## 5. Specific completion issues found

### Priority 1 — Make existing functionality reachable and trustworthy

1. **Paper navigation buttons are hidden.** `normalizeConfig()` supplies a navigation list containing only overview/scanner/watchlist/methodology/admin. `applyBrand()` hides other navigation buttons. The new direct paper route loaded, but its sidebar buttons were absent. Update the allowlist and add a visibility regression test.
2. **After-hours AI UI bypasses the capable backend.** Remove the unnecessary LIVE/five-minute restriction for authenticated research questions while continuing to label archived context.
3. **Legacy journal outcomes are not progressing.** At the snapshot every journal row was OPEN: 40 non-gated; 124 gated. The server builds outcome-update inputs from `stock.candles`, but the successful scraper payload does not include that field. The candle archive should be connected explicitly, with time and session-close handling tested.
4. **Unify calendar behavior.** The main schedule, bounded Results Lab and new paper calendar are separate implementations. Their holiday lists disagree internally, including November dates. Reconcile them against published exchange calendars and handle announced special sessions explicitly.
5. **Make source timestamps consistent.** Some display `updated_at` values represent processing/fetch time, and archived bars may carry the generic source label `exchange`. Preserve provider, actual bar/quote timestamp, arrival time, delay and adjustment basis separately.

### Priority 2 — Complete the inputs required by your earnings-reaction goal

6. Configure FYERS; verify actual response freshness and historical availability for all three focus stocks.
7. Complete financial extraction/comparison coverage, including schema errors, document size limits, revisions and stale collection.
8. Connect verified offline research records to runtime stores instead of leaving two disconnected datasets.
9. Collect the actual historical intraday windows needed for the requested post-May-2023 research. Daily candles cannot substitute for morning paths.
10. Establish adequate point-in-time samples and chronological validation before publishing any next-result accuracy or price-move forecast.
11. Build the missing historical news/consensus/peer-results feature dataset if those effects are to enter a model. Current headlines and ad hoc observations do not establish numeric causal weights.

### Priority 3 — Finish paper-trading and AI integration

12. Observe one complete live paper session through expiry/fill, exits, 15:15 closure and the 15:35 stored report; reconcile UI/CSV/JSON totals with SQLite.
13. Fix repeated gate-event logging so storage and displayed rejection counts are intelligible.
14. Connect the new paper ledger/reports to Laya's bounded website-context retrieval.
15. Verify margin badges throughout all new and alternate layouts. Keep source age visible and separate assumed simulator leverage from actual broker reference values.
16. Persist backend tracking intentionally and distinguish it from browser favourites.

### Priority 4 — Operational completion

17. Build and test complete multi-database backup and restore, including source/configuration and research evidence.
18. Install the optional pytest runner and run the entire permitted integration suite. Earlier focused test passes do not prove every live screen or production deployment works.
19. Correct deployment context/packaging and validate a clean installation with migrations and all new modules present.
20. Complete hosting/security/data-rights work before public or subscriber use. Add billing and user administration only if public subscriptions remain a product goal.

## 6. Features that should not be described as completed

- A universal profitable BUY/SELL prediction engine, calibrated success percentage or guaranteed morning price target.
- Complete historical intraday coverage for all 2,613 companies since 6 May 2023.
- Automatic fine-tuning of Laya on everything recorded each day.
- A unified live FYERS + public-source feed with selectable source comparison/failover in the main terminal. The main adapter currently rejects broker-source switching; FYERS has separate historical/Results Lab paths.
- Complete BSE-specific filing coverage or source-verified coverage of both exchanges merely because database labels say NSE/BSE.
- A complete options-chain, Greeks, live OI, exchange depth/order-book, broker holdings or broker account portfolio terminal.
- A finished paid subscriber, billing, multi-user administration or publicly hosted product.
- Exact real execution times, guaranteed limit/stop fills, or complete tick-level MFE/MAE derived from OHLC bars.

**Real-order placement is intentionally absent.** It is not an unfinished requirement for this project. Both paper systems should remain simulations under the current user instructions.

## 7. The four different “lists” in the product

| Name | Purpose | Persistence/source |
|---|---|---|
| Company catalogue | Find companies among 2,613 entries | Stock-master database |
| Tracked scanner universe | Decide which companies receive routine market collection and display | Scraper tracking/configuration; 24 observed in view |
| Personal Watchlist | Save favourite company cards | Browser local storage |
| Paper watchlist | Hold automatically evaluated hypothetical trading plans/positions | Separate SQLite paper ledger |

The three-company Results Lab is another separate experiment. It should not be confused with the automatic ATR watchlist or their performance combined without explaining the different rules.

## 8. Recommended completion sequence

1. Fix navigation, after-hours chat routing and the journal-to-candle integration.
2. Verify FYERS and consistent source timestamps; centralize calendar handling.
3. Prioritize complete financial and intraday data for Asian Paints, Trent and Coal India.
4. Verify one complete paper session and its saved report/exports without modifying gate rules just to generate trades.
5. Integrate paper records into Laya retrieval and improve tracking persistence/label coverage.
6. Extend research to a sufficiently large, clean, point-in-time dataset; report honest out-of-sample errors.
7. Finish backup/restore, clean deployment and public-access controls if public hosting is still wanted.

## 9. Verification, evidence and scope

This audit reviewed the frontend/backend modules, provider adapters, calendars, paper configuration, schema/migrations, deployment files and live local data. Browser checks observed the AI status endpoint, the direct ATR paper view and the three-company Results Lab status. It did not alter application settings, start/end a paper session, change model weights, fix code or place real orders.

The running dashboard and model availability were checked now. Historical testing statements in `CHANGELOG_PAPERTRADE.md` describe earlier implementation checks; the full test suite and every UI interaction were not re-run for this feature inventory. Pytest remains absent in the inspected Python environment.

Key evidence:

- [Frozen runtime snapshot](/Users/chethan/Documents/finance/zien-quant/work/feature-audit/2026-10-30/runtime_snapshot.json)
- [Workspace controller](/Users/chethan/Documents/finance/zien-quant/marketscope/static/workspace.js)
- [Workspace adapter and navigation allowlist](/Users/chethan/Documents/finance/zien-quant/marketscope/static/workspace-api.js)
- [Server routes and service integration](/Users/chethan/Documents/finance/zien-quant/marketscope/server.py)
- [Public candle adapter](/Users/chethan/Documents/finance/zien-quant/marketscope/scraper.py)
- [Technical decision engine](/Users/chethan/Documents/finance/zien-quant/marketscope/decision_engine.py)
- [Daily formula ranking](/Users/chethan/Documents/finance/zien-quant/marketscope/daily_quant.py)
- [Results collector](/Users/chethan/Documents/finance/zien-quant/marketscope/results_service.py)
- [Earnings reaction engine](/Users/chethan/Documents/finance/zien-quant/marketscope/earnings_research.py)
- [Laya retrieval context](/Users/chethan/Documents/finance/zien-quant/marketscope/laya_context.py)
- [Three-company Results Lab](/Users/chethan/Documents/finance/zien-quant/marketscope/paper_trading.py)
- [Automatic paper watchlist](/Users/chethan/Documents/finance/zien-quant/marketscope/paper_watchlist.py)
- [Paper configuration](/Users/chethan/Documents/finance/zien-quant/config/papertrade.yaml)
- [Paper implementation record](/Users/chethan/Documents/finance/zien-quant/CHANGELOG_PAPERTRADE.md)
- [Separate research packages](/Users/chethan/Documents/finance/research)

The report records a moving system at a stated time. A pending trade can later fill or expire, collectors can add records, and a report can be generated after its deadline. Those later changes do not make an earlier empty-state observation a permanent feature status.
