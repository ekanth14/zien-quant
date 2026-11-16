# Company reaction research and trainable forecasting pipeline

**Status: collection incomplete; no validated market accuracy or trained market forecast.**

Start with `STUDY_STATUS_AND_FORMULAS.md`, then open `COMPANY_REACTION_DASHBOARD.html`. The dashboard studies the selected company's own next-day chart after its **financial result** is positive. Falling price paths are included. It is an offline review, not the deployed website.

## Actual work completed

- Expanded the local financial/event ledger to 14,796 company-period records, 126 comparable pairs and 62 currently known positive financial events.
- Recovered 17 complete next-day company sessions on three dates, containing 9 rises and 8 falls from 09:15 to 11:00. Older years still lack complete company reaction prices.
- Downloaded 22,500 public NIFTY minute prices for separate context, verified exact source-file hashes, timestamps and OHLC consistency, and matched context to all 17 company sessions.
- Implemented resumable public-price import/download, optional broker collection, official financial-document fetching, source hashes, conflict quarantine and missing-data queues.
- Implemented a nonlinear date-balanced conditional-path model with actual fitting, chronological selection, calibration, held-out testing, magnitude intervals and inference. Actual training attempts on the available unverified sample remain disabled.
- Supplied a causal training/inference feature builder, a read-only website bridge and an inspectable company-chart dashboard.

The full May 2023–present company dataset has not been downloaded. Public bulk sources were found, but this session's shell cannot resolve their hosts and the web/GitHub readers cannot retrieve the large archives. A normal network-capable runtime can run the prepared collector. Source coverage/rights/adjustments and original financial vintages still need verification; the downloader does not certify them.

## Start the public collection in a normal Terminal

From this extracted package directory, prepare a private research environment:

```sh
python3 -m venv .study-venv
.study-venv/bin/python -m pip install 'numpy==2.3.5' pyarrow
.study-venv/bin/python -B collector/collect_public_archive.py --max-shards 8
```

The last command prints the plan and performs no downloads. To start the public Parquet download/import into a separate research database:

```sh
.study-venv/bin/python -B collector/collect_public_archive.py --max-shards 8 --execute
```

The publisher advertises about 10.4 GB across eight shards. Files are retained for provenance and resume; allow storage for the raw files and research database. `--max-shards 1` starts with one shard, and `--max-rows-per-shard` bounds scanning. None of these commands obtains broker credentials or places trades. The source's advertised January 2026 endpoint leaves newer periods missing, so completion of this batch is not completion of the full study.

`collector/README.md` documents CSV/GZ imports, exact timestamps, source conflicts, missing-window reports, verified-checksum public ZIP download, optional FYERS access, and official NSE document collection. The wider ledger includes unknown financial classifications so future positive events are not lost merely because their documents have not been parsed yet. Only positive financial events enter this model.

## Reproduce local research

The original source databases remain outside this package and were read only. Re-extract and validate their snapshot:

```sh
python3 -B local_data/expand_local_history.py
python3 -B local_data/validate_local_expansion.py
python3 -B analyze_benchmark_context.py
```

The bundled research runtime has NumPy:

```sh
study_python='[source-home]/.cache/codex-runtimes/codex-primary-runtime/dependencies/python/bin/python3'
"$study_python" -B model/build_local_model_dataset.py
"$study_python" -B model/positive_path_model.py train model/local_model_dataset.json --landmark 09:40 --output model/repeated_training_attempt.json
```

The expected current result is `INSUFFICIENT_VERIFIED_DATA`, not a newly trained market model. The source-public historical clock and the app's actual operational clock are different: collecting a file today can support a historical public-information study if its original version/time is verified. All candidate source and acquisition clocks are retained separately.

See `model/README.md` for exact mathematics, the dataset contract, all registered landmarks, held-out metrics, release gates and forecast conversion into percentages/rupee levels. `website/README.md` documents the read-only bridge and the missing NumPy dependency in the actual app environment. No original website, credentials, GitHub branch or pull request was changed.

## Checks and navigation

Use the tested NumPy runtime for all model/real-engine tests:

```sh
"$study_python" -B -m unittest discover -s model -p 'test_*.py' -v
python3 -B -m unittest discover -s collector -p 'test_*.py' -v
"$study_python" -B -m unittest discover -s website -p 'test_*.py' -v
node --test test_dashboard.cjs
```

The DOM test reuses the original project's existing `linkedom` dependency. Browser pixel-layout verification and deployment were not performed. Software test success is not market prediction accuracy. `validation_results.json` records the final checks; `SHA256SUMS.json` records file integrity.

Key folders: `local_data/` contains the financial/event ledger and actual stored company candles; `public_data/` contains the downloaded benchmark and source hashes; `collector/` contains the resumable data workers; `model/` contains training/calibration/testing/inference; `website/` contains the safe bridge. Full original data stores, credentials, environments, large third-party archives and proprietary broker data are not embedded in this package.
