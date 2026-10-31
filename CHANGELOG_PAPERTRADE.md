# Paper watchlist implementation — 30 October 2026

## Preservation and Git

- Branch: `feat/paper-watchlist`. Origin: `https://github.com/ekanth14/zien-quant.git`.
- Restored a **shallow** Git checkout at the installed upstream commit `568f97e8b018b5584b90a6e45983e317cc12d1a9`. Shell DNS prevented normal fetch, so approved GitHub GET endpoints supplied missing objects. Every restored blob, tree and the exact unsigned commit was checked against its Git SHA-1. `git fsck --no-reflogs` passed.
- No checkout/reset replaced the working tree. A SHA-256 comparison confirmed all 144 pre-existing source files were unchanged by Git restoration. Existing local modifications remain unstaged, including those installed before this task. This is not a claim to have fetched the owner's latest branch head or all parent history. Use `git fetch --unshallow origin` later to obtain full ancestry.
- Source backup, including local environment configuration, was saved privately before implementation: `../update-backups/paper-watchlist-20261030-042840/source-before-watchlist.tar.gz`, with a source hash manifest. Directory mode 0700; archive mode 0600. Nothing was uploaded.
- `.env` and runtime data remain ignored by Git. Existing account, market, results and candle databases were not replaced. Verification tests use temporary application databases and read-only candle access. The new schema migration runs on normal server startup.
- No commit, push, PR, real order or broker execution connection was made.

## New files

- `config/papertrade.yaml`: one JSON-compatible YAML configuration with capital, risk, leverage, entry policy/validity, ATR multipliers, trailing, reversal, fees, slippage, clocks, replay variants and installed 2026 NSE regular calendar.
- `strategy/papertrade_math.py`: pure validated ATR, whole-share sizing, adverse fills, charges, gross/net/return/R, MFE/MAE and portfolio statistics.
- `strategy/papertrade_engine.py`: deterministic pending/open/closed/expired lifecycle; conservative collision/entry-bar rules; frozen per-trade assumptions; hindsight reports and independent alternative/gate-bypass replays.
- `strategy/papertrade_replay.py`: causal shared-capital offline replay, prefix-only engine inputs and explicit coverage audit.
- `marketscope/paper_watchlist.py`: existing archive/decision/journal adapters; separate SQLite persistence; source freshness checks; snapshot integration; data worker plus independent cutoff/report watchdog; CSV/JSON exports.
- `marketscope/migrations/006_paper_watchlist.sql`: three new tables and an active-symbol partial unique index. Existing `signals` schema/outcomes are unchanged.
- `marketscope/static/paper-watchlist.js`: embedded watchlist/day report, timestamps, source/delay labels, countdown, realized-equity curve, costs and separated hindsight.
- `scripts/replay_paper_watchlist.py`: scratch-ledger CLI, never writes the website's live ledger.
- `tests/test_papertrade_watchlist.py`: hand-calculated math, lifecycle/property, source/time, persistence, real archive and deterministic report tests.
- `tests/test_papertrade_routes.py`: socket-free real server dispatch/export/error checks.
- `tests/paper-watchlist-ui.test.mjs`: empty/source-error and closed-session report display tests.

## Thin integration changes

- `marketscope/server.py`: adds GET `/api/paper-watchlist`, `/api/paper-watchlist/report?day=YYYY-MM-DD`, and `/api/paper-watchlist/export?day=...&format=csv|json`; connects existing snapshot/journal flow and server worker lifetime.
- `marketscope/static/workspace.html`, `workspace.js`, `workspace-api.js`: add hash views and API allowlist; original personal watchlist, terminal, results and Results Lab remain available.
- `tests/workspace-ui.test.mjs`: module-loader support for the new view.
- `package.json`: include three new frontend checks.
- `requirements-dev.txt`: declare optional pytest runner. No new production dependency.
- `README.md`: operating rules, source/cost assumptions, reports, exports and replay instructions.
- `marketscope/tests/test_audit_fixes.py`: updated one pre-existing stale Laya assertion to match the already implemented, explicitly labelled website-context fallback. It checks the actual retrieved unavailable-data message and `backend=rules`; no production AI code changed. The prior expectation incorrectly required an Ollama error despite the existing fallback behavior.

## Explicit model assumptions and limits

1. This is experimental candle-based simulation, not a validated strategy or actual execution record. Original engine levels and the existing three-stock earnings simulator are preserved. New levels are independently labelled ATR simulation plans, not silently substituted terminal recommendations.
2. Live pending decisions are stamped at actual observation time. A candle starting before that time cannot fill the order. Matching closed-prefix confirmation is required; forming/stale/history-mode snapshots cannot silently enter a position.
3. Planned entry is the engine entry-zone midpoint. Pending lifetime is 15 minutes. A favorable opening gap uses the better raw opening price but rechecks margin/risk and the plan bracket. Adverse slippage applies before accepting the fill. No validity-window interpolation or exact intrabar fill time is claimed.
4. Stops win OHLC collisions. Entry-bar targets are withheld; stops may fire conservatively. Gap stops use the worse opening price. Trailing levels use completed prior-bar data. Time stops use completed candle observations.
5. Quantity includes estimated stop slippage and charges within the configured planned risk cap. Unexpected gap losses can exceed it. Reserve margin on pending and open long/short trades; never spend short proceeds. 5× is a user-configured ceiling, not a verified broker entitlement.
6. Gross uses slipped fills; net=gross−fees. The slippage benchmark is separately disclosed and never double-counted. Costs approximate NSE equity intraday; instrument/exchange eligibility, IPFT, contract-note rounding, special charges and tariff changes are not certified. User must verify the configuration against the broker schedule.
7. Boundary candle means **ending at 15:15**, avoiding a 15:20 lookahead. Missing boundary data closes administratively without a priced fill; P&L and available capital become unknown. Server/sleep outages cannot be represented as timely executions. Reports recover from recorded activity after restart, without fabricating missing trades.
8. Live calendar is published 2026 NSE regular sessions, with January election closure and updated holidays. Unknown years and the unconfigured November 8 special session are blocked. Historical replay does not certify calendar/source vintage.
9. MFE/MAE exclude uncertain entry/exit boundary-bar movement and require complete windows. Thus the metric is a conservative fully held interior-bar observation, not a tick-perfect maximum. No complete window means no MFE number.
10. What-if and blocked-gate replays are separate same-signal hypotheticals with independent sizing, not a shared executable hindsight portfolio or future prediction. A one-day equity curve measures realized trade-close drawdown, not full intratrade drawdown. Daily Sharpe-like needs multiple complete daily returns; observed/saved flat days are included.
11. Reports are immutable once saved. Late source revisions do not silently rewrite a published day. SQLite is single-application-process persistence, not a multi-process execution platform. Read endpoints follow the existing personal localhost dashboard's public-read model.

## Verification

- New backend: 40 math/lifecycle/property/persistence/archive checks and 5 socket-free route checks passed.
- Existing Results Lab: 32 focused engine/route tests passed.
- Existing earnings research: 26 passed; FYERS history: 8 passed.
- Existing audit/security/journal checks: 22 ran, 21 passed, one optional real-Ollama integration check skipped. The stale Laya assertion above was corrected without altering AI code.
- Frontend: all 26 API/DOM regression checks passed.
- Pytest is not installed in the provided Python runtimes; the pytest-collectable cases were executed via standard-library unittest. No claim that pytest or the entire network/socket-dependent project suite passed in this restricted environment.
- Compilation/syntax and Git object verification passed. Full live browser integration and source reception remain unverified because the running local server still needs a normal Terminal restart; the sandbox cannot bind its replacement. Existing server was kept available.

## Stored archive reconciliation run

The first eight alphabetic symbols having complete archived 26 October 2026 mornings/full cutoff windows were selected without optimizing for profit: ABDL, AEGISVOPAK, APLAPOLLO, APOLLO, ATHERENERG, ATUL, AUBANK, AVANTEL. There were 72 five-minute candles per symbol through the cutoff (576 rows). Using default costs/slippage and shared capital, the frozen causal replay produced 2 priced closed trades and 6 expired entries. Net simulated result: **−₹2,932.24 (−0.9774%)**. Both closed trades lost; report totals equal the sum of priced trade rows, and repeated replay was identical.

The detailed local JSON is in ignored `work/papertrade-validation/2026-10-26-offline-replay.json`. It was not inserted as a live website day report. This is an accounting/replay demonstration on one archived day, **not research validation, live profit, or evidence of predictive accuracy**.
