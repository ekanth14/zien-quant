# NSE data collection and model evidence

Scope: website catalogue, from 6 May 2023, simulation only.

## Collection commands

Run from the repository on the laptop with working network access and a current
FYERS token. Start with the four research companies:

```sh
python3.12 scripts/collect_fyers_dataset.py --max-requests 20
python3.12 scripts/collect_research_data.py --batches 120 --enrichment-cycles 10
```

The price collector supports `--all-companies`, `--interval 1|3|5|15|D`,
`--from-date`, `--to-date` and `--max-requests`. Run identical arguments to resume.
Successful nonempty chunks are skipped; blocked and empty chunks remain retryable.
30-day requests use the official API's date-string format. Symbols come from the
FYERS master and must belong to the website catalogue. A provider rejection stops
the run rather than repeatedly probing expired credentials or inaccessible history.

Snapshots and retrieval metadata are in `work/fyers-datasets`; collection progress
is in `collection.sqlite`. The website candle archive receives validated rows.
Keep immutable snapshots: the main archive upserts rows and does not preserve all
provider revisions. Source availability and usage entitlement remain account-specific.
The existing five-minute validator rejects weekends; announced special sessions
need separate verified calendar handling before collection acceptance.

## What this does not establish

Nonempty responses do not certify complete session grids. Retrieval time does not
prove historical information availability. Financial parser success does not prove
matching accounting basis, comparable quarters or historical public availability.
Corporate actions, delistings, historical membership, news timestamps, historical
spreads and historical fee schedules remain separate data requirements.

## Training and validation gates

1. Audit missing bars by symbol/date and verified session, including previous-session
   warm-up and ten matched opening-volume reference windows for the RVOL model.
2. Freeze source vintages, dataset hashes, strategy rules, labels and trial count.
3. Divide by chronological trading sessions; purge overlapping label horizons.
4. Fit thresholds/models only on training data, choose settings on validation data,
   then evaluate once on untouched later sessions. Keep no-trade and original ORB controls.
5. Re-run at 1x/1.5x/2x costs and disclose sample concentration, drawdown, net
   expectancy, exposure and missing-data exclusions. No best-run-only reporting.
6. Keep candidates disabled until owner review and adequate forward paper evidence.

No retrained parameters or profitable model are delivered by these collectors.
No account/order/holding APIs or broker execution are used.
