# Local financial/history expansion

This is an actual read-only re-extraction of the existing website archives, not a new downloaded full-history market feed. It expands the previous positive research cohort by mining all retained financial versions and comparable quarter facts, rather than using only the prior selected event labels.

## Results

* Website catalogue: 2,613 companies. Cached current Zerodha MIS leverage strictly greater than 3×: 1,216 companies. Current cached eligibility is not proof of historical eligibility.
* Eligible announcement scope: 14,796 unique company/reporting-period clusters, first stored disclosure on or after 6 May 2023. Reporting-period ends are not subject to that cutoff.
* Recovered financial fact variants: 281; comparable quarter pairs: 126; algebraically positive pairs: 62.
* Complete positive five-minute mornings: **17 unique company/dates across 3 dates**: 3 August, 5 August and 26 October 2026. No complete 2023–2025 intraday mornings were recovered locally.
* Two prior announcement-date daily controls exist for 61 of the 62 positive financial events. These are stored raw daily OHLCV, with adjustment and arrival provenance unverified.
* Missing financial-document requests: 27,154 unique official NSE XBRL URLs. They are collection targets, not downloaded files or verified positive events.

The three newly identified descriptive positive windows are APOLLO on 26 October (consolidated), GAEL on 3 August (consolidated), and CASTROLIND on 5 August (standalone). Some companies' standalone and consolidated directions differ. The new extraction prefers a comparable consolidated pair and exports all available scope alternatives, so these definitions are reviewable. Different accounting scopes are not treated as conflicting revisions. No conflicting values within the selected scope were found in this local pool.

## Clocks and vintage

`public_time_claims` and `collection_time_claims` remain separate for every fact's provenance. Downloading an old filing today does **not** by itself invalidate a hypothetical public-information backtest. The question is whether the particular original public financial vintage and its publication/revision time were actually available before the historical prediction.

The existing local metadata does not independently certify the original public vintage, earliest disclosure across venues, price adjustment basis, source price response, or bar arrival. Therefore `original_public_vintage_verified` and `public_backtest_ready` are false. An actual operational website replay would additionally require its app acquisition/extraction clocks. These 17 cases are descriptive research candidates, not 17 validated forecasts.

## Files

* `eligible_event_coverage.csv`: all 14,796 issuer-period clusters, selected financial direction, price coverage and alternative scope labels.
* `financial_fact_variants.jsonl`: valid matching-definition quarter facts, source URLs, roles and separate clocks.
* `comparable_pairs.jsonl`: all 126 selected pairs plus every available alternate pair.
* `positive_events.jsonl`: 62 positive pairs, available candles, and two preceding trading-date daily controls.
* `positive_candles_available.csv`: available five-minute rows for those positive events; incomplete windows remain incomplete.
* `positive_preannouncement_daily_controls.csv`: stored daily controls.
* `financial_document_requests.csv`: unique official-document collection targets for missing financial comparisons.
* `cached_xml_inventory.json`: 49 locally cached XML source hashes and linkage counts.
* `local_expansion_summary.json`: exact coverage and per-year counts.
* `local_validation_results.json`: independently checked ledger/arithmetic/time contracts.

Run `python3 -B expand_local_history.py`, then `python3 -B validate_local_expansion.py`. The script reads the original renamed project and existing cached official parser; it does not edit the app, download market data, place orders, or write to source databases. Nonempty SQLite journals cause a fail-closed snapshot request. All outputs stay in this task's authorized artifact directory.
