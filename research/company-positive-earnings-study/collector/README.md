# Resumable research-price collector

`collect_history.py` collects/imports prices into a **separate research SQLite database**. It has no order route, does not edit the website, and does not train or assert a successful prediction model.

The initialized `research_prices.sqlite` currently has **62 unique next-day company sessions following comparable positive financial results**. It reuses **357 existing local five-minute candles**, covering **17 complete mornings**. These are not new external downloads. The remaining 45 requested mornings are missing in this store. Financial classes, original vintages and provider arrival/adjustment quality remain unverified. Further financial extraction can identify additional positive events; these 62 are not asserted to exhaust the complete May 2023–present universe.

## Initialize and inspect the queue

Run from this directory:

```sh
python3 -B collect_history.py init --events ../local_data/eligible_event_coverage.csv --db research_prices.sqlite --positive-only
python3 -B collect_history.py coverage --db research_prices.sqlite --output missing_requests.csv
```

`--positive-only` screens the company's **financial result**, regardless of whether that company's following morning rose or fell. Omitting that option initializes all eligible continuous event-day windows for broader collection; it does not turn negative results into positive-model training events. Requests are deduplicated by exact company symbol and literal next trading date. The supplied ledger has already resolved official calendar sessions; the collector does not guess exchange holidays. Announcements before 6 May 2023 and future incomplete mornings are excluded.

## Import public files you have downloaded

CSV and CSV.GZ require only Python's standard library. The field aliases include symbol/ticker, timestamp/datetime/date/time/ts, open/high/low/close/volume and o/h/l/c/v. Separate Date and Time columns are supported. A per-file symbol can be supplied with `--symbol INFY`. Headers are required; unknown schemas fail visibly rather than being guessed.

```sh
python3 -B collect_history.py import '/path/to/INFY_1m.csv.gz' --db research_prices.sqlite --source 'Publisher and dataset version' --symbol INFY --interval 1 --timezone UTC --bar-stamp start
```

For documented Indian local timestamps use `--timezone IST`. A naive timestamp without an explicit convention is rejected. A timezone-aware timestamp or ten-digit Unix epoch already identifies UTC time; millisecond epochs are supported too. `--bar-stamp start|end` is required; end-stamped inputs are shifted by the declared interval, never by a guessed convention. Off-grid five-minute timestamps are rejected.

Native five-minute inputs use `--interval 5`. One-minute inputs yield a five-minute bar **only when all five consecutive minutes are present**. Open is the first open; high/low are the extrema; close is the fifth close; volumes are summed only if every minute's volume is known. Missing index volume stays null, not zero. Missing company volume also stays unknown and cannot establish a verified VWAP input. No candles are interpolated or invented.

Only exact requested symbol/date/09:15–11:00 windows are retained. Every retained price must be finite, positive and satisfy OHLC ordering; known volume must be finite and nonnegative. A complete morning requires 21 aligned five-minute bars. Price completeness is separate from financial-vintage and provider-arrival verification.

### Hugging Face Parquet shards

The public source inventory is in `PUBLIC_SOURCES.md` and `public_download_manifest.json`. Optional Parquet decoding uses PyArrow, which was **not installed in the inspected Python runtime**. On a runtime with internet access, install that optional dependency privately in an environment:

```sh
python3 -m venv .collector-venv
.collector-venv/bin/python -m pip install pyarrow
.collector-venv/bin/python -B collect_history.py import '/path/to/train-00000.parquet' --db research_prices.sqlite --source 'xxparthparekhxx Indian minute dataset / exact downloaded version' --interval 1 --timezone UTC --bar-stamp start
```

The reader streams bounded Arrow batches. It pushes symbol and timezone-aware global date bounds into the Parquet scanner when the schema permits, then retains only the exact requested windows. It does not load 713 million rows into a Pandas frame. Each shard is hashed and imported independently. Full-file hashing and resume scanning still require sequential disk reads, so the operation can take substantial time; the 10+ GB archive is not embedded in this package.

If interrupted, rerun the same import: the file hash/declared conventions identify the job and the persisted row cursor resumes progress. `--max-rows 1000000` limits additional scanned rows per invocation. Changing the source file or its conventions creates a different source identity. Completed identical imports are idempotent. Invalid rows are counted; incomplete windows stay missing. Conflicting duplicate prices are retained as conflict evidence and their affected windows withheld from complete exports; different providers are never silently blended.

## Public HTTPS downloader

The downloader is credential-free and bounded by a byte budget. It retains `.part` and `.download.json` files after interruption and resumes only with the matching original URL and strong ETag. Range and ETag responses are validated; supplied SHA256 checksums must match before completion. A publisher checksum is preferable when one exists. New downloads go only to the explicitly supplied output directory.

Example of a known public release, **not automatically downloaded here**:

```sh
python3 -B collect_history.py download 'https://github.com/voletiramu/nse-fno-1min-data/releases/download/v1.0.0/stocks_1m_csvs.zip' --output-dir raw_downloads --sha256 20024713c455cc16b5daae91e06991d57a1acfa6a30c77bb7d5a742ee1789ab2 --max-bytes 500000000
```

That release is advertised for April 2024–April 2026 and 214 stocks, so it does **not** establish complete requested coverage. Extract only expected data files with ordinary archive tools after verifying the checksum, then import CSV.GZ files individually. Import does not execute any dataset-supplied code. Large public downloads were not attempted through a shell whose official-host DNS checks had failed.

## Optional FYERS collection

The user can continue with public datasets; broker collection is an optional additional source. It requires private valid authorization and an actual entitlement returning the requested 2023 session. Source credentials never go into URLs, CSV exports or logs. The current project has no usable saved access token.

```sh
python3 -B collect_history.py fyers --db research_prices.sqlite --master '/path/to/verified/NSE_CM.csv' --env-file '/path/to/private/.env' --plan prime --max-jobs 5
```

The worker reads the official instrument master rather than guessing ticker suffixes. It sends only History GET requests, uses a no-redirect authenticated transport, persists request budgets, and enforces the documented second/minute/day ceilings. It stops on authentication or rate-limit rejection; it does not repeatedly hammer the endpoint. There is no automatic long retry loop. Received raw successful responses are hashed and preserved in `raw_fyers/` beside the research database, with symbol-master hash metadata. This records collection time; it does not certify historical live arrival or original financial knowledge.

Provider no-data/partial results are persisted so later batches advance. Add `--retry-incomplete` only when new entitlement/data availability justifies retrying them. The `--plan` argument chooses rate limits; it does not verify or purchase a subscription. Standard's current one-year intraday coverage is insufficient to establish 2023 access in October 2026.

## Export and validate

```sh
python3 -B collect_history.py export --db research_prices.sqlite --output collected_complete_candles.csv
python3 -B -m unittest -v test_collect_history.py
```

The exporter includes source ID, raw-file hash and acquisition time, and explicitly false historical-arrival/financial-vintage verification flags. When two unconflicted sources are complete, their source IDs remain separate. The parent study must register a source policy and deduplicate event units before model fitting. A complete price window is not an accurate forecast, an executable fill, or a verified original financial vintage.

The database stores `requests`, `sources`, `bars`, `conflicts`, `import_jobs`, `api_attempts` and `api_jobs`. `missing_requests.csv` is regenerated from current coverage, not silently marked fulfilled by a successful HTTP status. Software tests cover time conventions, exact resampling, missing data, conflict quarantine, resume, source hashes, byte limits and minute/day API budgets. Network downloads and PyArrow decoding need independent runtime checks when available.

## Official financial documents

The price queue is complemented by `collect_financial_documents.py`, which reads the separate source-linked financial-document request ledger:

```sh
python3 -B collect_financial_documents.py --requests ../local_data/financial_document_requests.csv --db financial_documents.sqlite --output-dir raw_documents --project '[source-home]/Documents/ finance V2/zien-quant' --limit 5
python3 -B -m unittest -v test_financial_documents.py
```

This worker allows only exact official NSE HTTPS hosts, uses at least one second between network downloads and an explicit 1–100-document batch limit, and stops after a DNS, HTTP blocking/rate/authentication error or detected block page. It does not evade a gate or retry the whole ledger after failure. One actual one-document run here stopped on DNS/network failure, downloading zero documents; that result is stored in `financial_documents.sqlite`.

Raw XML/PDF bytes receive SHA256 and acquisition metadata. Existing identical downloaded URLs are not fetched again. `--retry-errors` explicitly revisits previously failed requests after access changes; `--reparse` reprocesses existing raw files after a parser update. The current project's standard-library `parse_financial_document` is loaded read-only when `--project` is supplied. It extracts exact supported quarterly XBRL contexts rather than subtracting annual figures or guessing table positions. Unsupported XML taxonomies and PDFs remain saved/unparsed; zero parsed rows are not interpreted as zero revenue/profit.

Parsed facts include raw-file hash, acquisition time, the request's claimed announcement time and the response's declared metadata. None of these certifies original publication/vintage. The worker never derives a historical publication time from today's download. Parsed fact files are inputs for a subsequent audited financial-vintage join, not automatic activation of forecasts. Unknown original disclosures, company-period conflicts and late revised comparatives must still be resolved.
