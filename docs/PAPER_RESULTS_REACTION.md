# Results & Reaction paper lab

Implemented 30 October 2026. This is a single shared local-workspace virtual ledger, **not brokerage execution**. No order placement, modification or cancellation integration exists in the feature.

## Inspection and integration

- `marketscope/static/workspace.html`, `workspace.js`, `workspace-api.js`: existing dashboard, hash navigation and authenticated/CSRF-aware client. New view `/#paper`, no separate dashboard page.
- `marketscope/server.py`: existing threaded HTTP server; adds GET `/api/paper/status`, GET `/api/paper/export`, POST `/api/paper/control`. Control requires owner/admin and existing CSRF protection. Existing results ledger links to the embedded view.
- `marketscope/paper_trading.py`: persistent ledger, market-calendar gate, quote freshness, sizing, cost assumptions, simulation and server-owned monitor.
- `marketscope/static/paper-trading.js` and `.css`: responsive allocation ribbon, ₹3 lakh portfolio, stock table, rule review and event ledger. Values are escaped, CSV text formula-protected.
- `marketscope/earnings_research.py`: **reused without changing the strategy**. Existing `analyze_event` / `_setup` produces conditional BUY_WATCH / SELL_WATCH indications, not validated trade executions.
- `marketscope/results_service.py`: actual local filing history, point-in-time comparison availability and stored historical observations. If records or evidence are missing, no signal is invented.
- `marketscope/fyers_history.py`: existing local credentials and read-only history. The scanner's public Yahoo/NSE feed is not silently substituted for authenticated FYERS prices.

## Data source

Only authenticated, allowlisted FYERS GET `/data/quotes` is used for paper prices. The adapter checks per-instrument `s`, `lp` and `tt`, and shows the source, provider timestamp, measured age and delay statement. Provider documentation: https://fyers.in/api-docs/v3/api/market-data/quotes/

Freshness defaults to 15 seconds, configurable 1–30 seconds. Future-dated, missing, nonfinite, nonpositive, prior-session and expired prices cannot authorize fills. Provider real-time availability does not guarantee end-to-end exchange latency. No token enters the browser, responses or logs. Credentials are read from the existing server environment (`FYERS_APP_ID` or `FYERS_CLIENT_ID`, `FYERS_ACCESS_TOKEN`). During inspection these were **not configured**; authentication and live price reception could not be verified.

Current closed five-minute candles use the existing FYERS history request helper directly, bypassing its ten-minute UI cache. Current-day public/archive candles are removed before the merge; the entire closed FYERS prefix from 09:15 through the most recently completed five-minute interval must be present and nonconflicting. Historical daily and intraday rows remain separate even if their timestamps coincide. Historical local observations remain subject to the existing earnings engine's quality, cohort and point-in-time filters; no additional historical validation is claimed. API/network errors and insufficient cohorts block signals.

## Calendar and lifetime

NSE source inspected 30 October 2026:
https://www.nseindia.com/resources/exchange-communication-holidays?apiversion=v2
https://www.nseindia.com/static/market-data/market-timings

The existing broad hard-coded calendar contains discrepancies with the published holiday list. The new paper monitor therefore uses a separate **bounded verified regular-date window: 30 Oct–6 Nov 2026**, weekdays only, opening 09:15 IST. There are no listed equity holidays within that window. A conservative monitoring boundary of 15:15 accommodates the earliest continuous-session closing boundary; it does not assert that all instruments' exchange trading ends then. Simulator cutoff defaults to **11:00 IST** and can be set 10:00–15:10, always before this boundary.

The November 8 special session is announced on the annual calendar but its actual hours are **not installed/verified here**. It is blocked, as are all dates outside the covered window. No weekend or special hours are guessed. Future operation needs maintained official dates/hours; this feature is not an automatically refreshed full-year calendar.

Start chooses the next covered session with a future simulator cutoff. Starting before today's opening selects today. Starting after cutoff selects the next covered session. The server-owned worker polls every five seconds plus provider request time, only inside the armed session. It releases the state lock during network I/O so Pause/End and status remain responsive; returned data is discarded if a control changes the session. An independent one-second cutoff watchdog closes positions even while a provider call is blocked. Keeping the browser open is unnecessary; the server and computer must remain running and awake. There is no external scheduler or live broker socket. Backend changes require a server restart.

## Review gate and exact experimental rules

Initial state: **No strategy configured**. Start cannot enable entries until an owner/admin reviews and explicitly acknowledges version `earnings-watch-paper-v1` and saves assumptions. No live session has been reviewed, armed or started by this implementation.

The UI lists the complete rules. They reuse the existing earnings cohort tests:

1. Eligible latest company filing assigned to the same reaction session, with point-in-time financial comparisons and at least ten complete comparable observations.
2. Entry assessment 09:40–10:00, fresh closed five-minute candle beginning at/after 09:40 (first possible close 09:45), contiguous intraday candles, full first three opening-range candles and positive volume for VWAP.
3. LONG requires Wilson lower 95% bound above 50%, cohort upside frequency at least ten percentage points above baseline, median return above 20 bps and close above both VWAP and opening high.
4. SHORT requires Wilson upper bound below 50%, baseline upside frequency at least ten points above cohort, median return below −20 bps and close below both VWAP and opening low.
5. Existing stop uses the observed session extreme; risk must be positive and at most 5%. Existing target is illustrative 1.5R. Existing split/dividend flags exclude confounded observations; no new complete corporate-action feed is claimed.
6. Signal rule inputs, cohort summaries, levels and reasons are logged and exported. WAIT/INSUFFICIENT_DATA maps to NO TRADE. No backfilled entry. Current quote must remain fresh and postdate the trigger candle close.
7. Entries fill at observed quote adjusted adversely by assumed spread/slippage, **not at the historical watch entry level**. If observed quote is outside the inherited stop/target bracket, ignore the signal.
8. One whole-share round trip per stock per experiment. A completed experiment with trades is immutable; export/archive it before implementing a fresh experiment. There is no silent reset or capital replenishment control.

These are experimental conditions and assumptions. Passing tests does not measure a strategy win rate, probability calibration or out-of-sample profitability.

## Capital, costs and P&L

Defaults: capital ₹300,000; max allocation 1/3 per stock; price-to-stop risk cap 0.5% of initial capital; flat ₹20 per side; all-in turnover fee 5 bps per side; full spread 4 bps (half on each side); adverse slippage 3 bps per side. Fees are configurable assumptions, **not exact Zerodha/FYERS tax schedules**. Stops may gap and fee/gap losses can exceed the sizing risk cap.

For side d=+1 LONG, d=−1 SHORT:

- adverse entry = quote × (1 + d × (spread/2 + slippage)/10000)
- side fee = flat + quantity × price × fee_bps/10000
- whole-share quantity = max(0, min(floor((allocation_budget−flat)/(entry×(1+fee_bps/10000))), floor(initial_capital×risk_pct/100/|entry−stop|)))
- reserved = notional + entry fee for open positions of either side
- spendable = initial capital + priced realized net P&L − reserved; short proceeds never increase it
- gross = d × quantity × (exit−entry)
- net = gross − entry fee − exit fee
- return = net / actual entry notional ×100

No intraday leverage is borrowed. Equity includes available valid marked net P&L; mark values estimate liquidation with assumed costs, explicitly unrealized. No trades means zero allocation and no profit made. A missing/stale mark yields unknown combined P&L, not a carried-forward live profit assertion.

Stops/targets trigger only on observed fresh quotes, never invented intrabar paths. Pause disables entries but keeps risk exits/cutoff active. End and configured cutoff close positions by the simulator. If a fresh quote is unavailable, the position becomes `CLOSED_UNPRICED`: an administrative closure **without any exit fill**, exit/P&L unknown, and available capital unknown. This is the honest limitation when a simulator cannot obtain a closing price. No stale-price execution is fabricated. After a server outage, closure is recorded when the worker resumes, without backdating a fill to the cutoff. Closure timing depends on server availability and request latency.

## Persistence and export

`work/runtime/paper_session.json` is atomically replaced under an in-process lock; trades, assumptions and all emitted signal transitions, ignored assessments, entries, exits and controls persist. Corrupt/incompatible storage blocks the session rather than resetting money or dropping positions. One process per workspace is required; this is not a multi-process execution engine. Repeated unchanged conditions are deduplicated; assessment runs once per five-minute bucket. No backfill/catch-up simulated fills are produced after restart.

CSV contains portfolio, all three company rows, and the full event log with observed rule values and trade records. The UI displays the last 100 events; export preserves all. Existing authentication protects controls; read-only status/export follow this personal local dashboard's public-read model. Do not expose the local workspace publicly without broader application access control.

## Verification and deployment limits

- 28 ledger/source/deadline tests pass, including empty/partial FYERS rejection and concurrent blocked-history cutoff handling.
- Four socket-free tests invoke the real GET/POST dispatcher for owner/CSRF/origin gates and CSV routing.
- 26 existing earnings research tests and 8 existing FYERS history tests pass. All 23 frontend tests pass, including three new paper-view tests and an offline-dashboard navigation test; included in `npm test`.
- Sandbox disallows local socket binding and shell DNS. Full suite attempted but stopped because network-backed tests retried unavailable external feeds. No claim that the whole Python suite passed, or that live FYERS was tested.
- Existing server was left running; do not stop it from a sandbox that cannot bind a replacement. Run the checkout's `./scripts/restart-local.sh` in your Terminal, then visit `http://127.0.0.1:8766/#paper`.
