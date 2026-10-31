# Results & Reactions

The dashboard's **Results & reactions** navigation opens `/results.html` (also available at `/results` after restart). It lists published financial results only for symbols currently in the website's stock master database (`MS_STOCK_DB`). The installed catalogue currently contains 2,613 companies, but the code reads the actual catalogue instead of hard-coding that count. It does not create entries for companies that have not announced results, and it excludes companies outside that catalogue. Removing a company from the catalogue also hides its cached results, research and disclosures. An empty or unavailable catalogue fails closed.

## Automatic updates

Starting the normal website server also starts one background results collector. By default it checks recent NSE filings every five minutes, including after the trading day closes. The page checks the local store every minute. An evening filing can therefore appear on the next successful collection and page refresh; this is polling, not a guaranteed instant exchange push feed. Closing the server or sleeping the laptop stops collection. No Codex automation or operating-system scheduler is installed.

The collector stores original announcement time, source document, period, reporting basis and immutable revisions in `work/runtime/results.db`. Changed documents are queued for re-reading. Pending revisions are not used to create directional research signals. Cached records remain visible with a stale/error label when a source fails. An upcoming board meeting is not represented as an already declared result.

One bounded 30-day historical batch is collected per cycle, subject to the selected post-COVID boundary of 6 May 2023. The lookback setting is an upper bound and cannot make earlier periods visible. Numerical documents and candles are enriched in bounded batches of six companies per cycle. The page exposes actual coverage and outstanding batches: discovering metadata does not mean all financial documents and price history have already been downloaded.

## Financial comparisons

- The public NSE integrated financial-filings endpoint is used for modern filings, with the legacy financial-results endpoint for older periods. Public endpoint availability and payload formats can change; unsupported formats or access failures are reported.
- Supported XBRL/iXBRL facts are read only from matching whole-company, quarterly duration contexts. Revenue from operations, profit and EPS remain missing when a supported comparable fact is absent. Segment figures, annual and cumulative half-year values are not silently substituted.
- Standalone and consolidated records remain separate. Quarter-on-quarter comparisons require matching basis, units and revenue definition, with a previous quarter roughly 70–110 days earlier. Year-on-year comparisons use a corresponding quarter roughly 350–380 days earlier.
- For a positive comparison base, growth is `(current - previous) / previous × 100`. Zero bases produce no percentage. Negative bases and loss-to-profit turnarounds are labelled explicitly rather than presented as ordinary growth percentages.
- NSE's comparison endpoint supplies the latest five **standalone** quarters in **INR lakh**. Its total-income field is labelled as total income, and its current-vintage values are excluded from historical forecasts when their original availability cannot be established.
- Company disclosures link to NSE documents. Keyword tags identify potential catalysts, neutral announcements and risk items; these tags do not establish that an acquisition or dividend is beneficial. Readers can inspect the original filing.

## Testing the opening-rise, pullback and recovery observation

This is a hypothesis, not an assumed market rule. Normal NSE equity trading begins at 09:15 IST; pre-opening price discovery is a separate session. The study maps an exact pre-open filing to that day's regular session, and an after-close/weekend filing to the next eligible regular session. Intraday announcements and date-only filings are excluded from this overnight-results pattern.

The engine requires actual, closed five-minute observations covering the following consecutive windows:

| Window | Observation |
| --- | --- |
| 09:15–09:30 | Opening rise, measured from the first open to the 09:25 candle close |
| 09:30–09:40 | Pullback, measured to the 09:35 candle close |
| 09:40–10:00 | Recovery, measured to the 09:55 candle close |

The three-stage pattern requires a rise of at least 0.10%, a pullback of at least 0.10%, and a recovery of at least 0.10%. No missing bars are interpolated. Corporate-action sessions are excluded when identified; overnight gaps and close-to-close returns require a verified price-adjustment basis. Daily candles provide a separate open-to-close study and never stand in for 09:30/09:40 evidence.

Historical matches must be the same company, reporting basis, financial units and revenue definition, with similar revenue and profit growth. Each tolerance is the larger of five percentage points and 25% of the target growth magnitude. The target quarter, future announcements, late-known revisions, incomplete outcomes and incomparable negative-base percentages are excluded. Historical facts must have been known before their own reaction session opened and before the target announcement. The engine reports excluded observations and the actual usable sample dates.

The output includes sample size, median returns, observed positive-return frequency, the user's three-stage pattern frequency, 95% Wilson intervals and an all-prior-eligible-results baseline for that company. These are descriptive historical frequencies. They are not calibrated probabilities of an individual future trade and do not isolate earnings from other news, valuation, market expectations, sector effects or market-wide movement.

## Conditional buy/sell watches

A directional watch requires at least ten comparable completed intraday observations. In addition, its historical direction must have a 95% Wilson bound beyond 50%, an improvement of at least ten percentage points over the baseline, and a median return greater than the illustrative 20-basis-point round-trip cost assumption in the proposed direction.

The current reaction session must then show a fresh closed candle after 09:40 confirming VWAP and an opening-range break. Entry comes from the observed closed price; invalidation uses an observed session extreme. A displayed target is an illustrative 1.5R calculation, not an independently forecast price. Missing/stale observations or excessive stop distance result in WAIT or INSUFFICIENT EVIDENCE. No brokerage orders are submitted and no strategy execution win rate is claimed.

## History, calendars and current evidence limits

Public Yahoo five-minute retention is limited; a request for 60 days does not provide years of intraday evidence. Previously archived observations are retained. Optional existing FYERS credentials can collect bounded historical event windows, subject to the account's plan and access. Configure `FYERS_APP_ID` (or `FYERS_CLIENT_ID`) and `FYERS_ACCESS_TOKEN` privately on the server; never put them in frontend files or share them in chat. This addition is read-only historical data access, not a broker login flow or a live FYERS quote adapter.

The repository's configured exchange holiday years are 2025–2027. Other years are excluded from reaction inference. Special sessions and historical amendments need an independently verified calendar before extending coverage. **As of October 2026, that calendar range alone cannot supply ten previous quarterly events for one company.** Consequently the research engine will normally withhold a directional watch until more verified historical calendar and intraday coverage exists. The feature does not claim that the proposed pattern has already been validated for the 2,613-company universe.

Actual sourced price datasets can also be imported:

```sh
python manage.py results-import-candles historical-candles.csv --source "Your licensed data provider" --timeframe 5
```

The CSV requires `symbol,t,o,h,l,c,v`. Time `t` must be Unix seconds or an ISO timestamp including its timezone. Invalid OHLCV, future/incomplete bars, off-grid five-minute rows and conflicting duplicates reject the file before data is written. Imports do not bypass the company-universe or calendar filters.

## Operations

```sh
python manage.py results-status
python manage.py results-refresh
```

`results-refresh` runs one bounded collection batch; the website already does this automatically while running. Settings are documented in `.env.example`: `MS_RESULTS_DB_PATH`, `MS_RESULTS_POLL_SECONDS`, `MS_RESULTS_BACKFILL_DAYS` and `MS_RESULTS_SYMBOL_BATCH`. The existing website catalogue remains the only company-universe source.

Read-only APIs:

- `GET /api/results?from=YYYY-MM-DD&to=YYYY-MM-DD&search=&tone=all&limit=100&offset=0`
- `GET /api/results/detail?id=FILING_ID`
- `GET /api/results/status`

All source documents and outbound requests are validated; credentials stay server-side. Public-source failures, unknown schemas, missing figures, date precision, revisions and evidence coverage remain visible.

## Research and source references

- [NSE published financial results](https://www.nseindia.com/companies-listing/corporate-filings-financial-results): official source documents and publication/revision fields.
- [NSE standalone comparison](https://www.nseindia.com/companies-listing/corporate-filings-financial-results-comparision): latest-five-quarter scope and INR-lakh convention.
- [NSE company disclosures](https://www.nseindia.com/companies-listing/corporate-filings-announcements).
- [NSE normal market timings](https://www.nseindia.com/static/market-data/market-timings) and [pre-open session](https://www.nseindia.com/static/products-services/equity-market-pre-open).
- [FYERS historical data reference](https://fyers.in/api-docs/v3/api/market-data/historical-data): historical candles, completed-bar requirements and account-plan limits.
- [NSE historical data products](https://www.nseindia.com/static/market-data/eod-historical-data-subscription): longer exchange-level histories are separate data products.
- [So and Wang, News-driven return reversals](https://eso.scripts.mit.edu/docs/News-Driven-Return-Reversals.pdf): research on liquidity around earnings supports testing reversal hypotheses, but does not establish the specific NSE 09:15–10:00 pattern or the effectiveness of this implementation.

## Verification

Tests use clearly isolated test observations and temporary databases. No example companies or invented prices are inserted into the running results section. The implementation has offline tests for ingestion, universe filtering, period/scope comparisons, revisions, missing windows, historical availability, conditional rules, endpoint wiring and safe historical imports. Live source access and browser rendering must be checked after restarting the website in a normal terminal when the Codex session cannot create a listening socket.

### Verification on 26 October 2026

- 60 targeted Python tests passed and 17 frontend/API tests passed. Syntax compilation passed.
- A scan of all 17 changed/new source files found no credential or private-path findings. The original workspace controller, including its company-filter behaviour, is unchanged.
- The actual catalogue was read locally: 2,613 symbols. An initial source refresh correctly reported an unavailable source when this session could not resolve NSE's hostname; no result records were substituted.
- The new sidebar link was clicked in the running website and opened its separate page. The running process still has the older backend loaded, so the page correctly displays a restart requirement. Live result collection has not yet been confirmed.
- The broader suite ran 196 test methods: 193 passed, two Laya endpoint tests failed because no live market snapshot was available, and one optional Ollama test was skipped. Additionally, the HTTP workspace-route class failed during setup because this session cannot bind a listening socket. This is not a fully passing broader-suite run.

Restart the existing website process to load the added service and background collector. The saved screenshot shows the new page before that restart, with its actual unavailable-service state.

## Historical data loaded and user-selected post-COVID boundary

On 26 October 2026, official NSE CSV exports were downloaded through NSE’s published results pages and imported. The integrated-filings export contained 27,574 source rows; the older quarterly-results export contained 54,958 source rows. Current catalogue membership, exact unambiguous legal-name matching, period/publication dates and duplicate/revision handling were applied. Unknown company identities were excluded rather than assigned a guessed ticker.

The user selected **6 May 2023 onward**. Both the declaration date and reporting-period end must meet this boundary. Earlier active records were moved into `result_scope_archive` for recovery, with their versions retained, and are excluded from list/detail/research APIs. The active ledger contains **47,354 filings across 2,337 matched companies** in the website’s 2,613-company catalogue. The first qualifying declaration in the loaded data is 4 July 2023. These are available matched filings, not a claim that the remaining companies never announced results.

At verification, 269 filings had parsed financial figures and 142 had comparable revenue/profit QoQ values. Those counts change as the running collector reads more source documents. Missing numerical figures remain visibly pending; a declared result is still listed. Separately filed previous quarters are joined to the current filing, while original availability timestamps and source links prevent a later restatement from becoming a supposedly known historical comparison.

NSE’s [official public RSS feeds](https://www.nseindia.com/static/rss-feed) are now a fallback for recent announcements when the main query feed fails. RSS has a bounded latest-items window and is never used to advance the historical backfill cursor. Independent historical collection can resume even when today’s query fails. Public RSS XML and official downloaded XBRL documents are cached with bounded memory and validated identities.

Additional commands:

```sh
python manage.py results-import-filings official-nse-export.csv --source https://www.nseindia.com/companies-listing/corporate-filings-financial-results
python manage.py results-backfill --from 2023-05-06 --batches 12
```

`MS_RESULTS_START_DATE=2023-05-06` controls the server-side boundary. The date inputs and post-COVID preset use the same selected date. This is an application research scope chosen by the user, using the day after WHO ended the COVID-19 global public-health emergency; it is not a claim that COVID ceased to exist. [WHO’s 5 May 2023 statement](https://www.who.int/news/item/05-05-2023-statement-on-the-fifteenth-meeting-of-the-international-health-regulations-%282005%29-emergency-committee-regarding-the-coronavirus-disease-%28covid-19%29-pandemic).

Final targeted verification: 77 Python tests and 23 frontend/API tests passed. Source provenance and hashes are retained locally under `work/results-post-covid-import-provenance.json`; the raw exports and downloaded XBRL documents are working data and remain excluded from source control. No owner credentials or company-filter behaviour were changed.
