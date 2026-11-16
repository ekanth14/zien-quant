# Supplied updates: comparison and integration

Reviewed on 16 November 2026. The existing localhost application is the integration target.

| Supplied item | Comparison and action |
|---|---|
| `finance-20261116T111504Z-1-001.zip` | Older standalone MarketScope, snapshots and working databases. Retained the current authentication/data-provider implementation and current databases. |
| `finance V2-20261116T111503Z-1-001.zip` | Older standalone outputs plus an earlier `zien-quant` checkout. Of the checkout's source candidates, 64 match, 44 differ and none are missing. Most other entries are runtime/cache material. Retained later current source; no duplicate server or database replacement. |
| `update till 31_10_26.zip` | All 128 update paths exist: 113 match after line-ending normalization and 15 differ. Preserved later IPO support, full-catalogue details, database cleanup, Windows test repairs, research catalogue changes and wording. |
| `Company_Positive_Earnings_Research_Pipeline_2026-10-27.zip` | Missing research package. All 76 manifest entries verified against supplied SHA256 hashes. Imported to `research/company-positive-earnings-study/` and integrated below. |
| `STUDY_STATUS_AND_FORMULAS.md` | Dated methods and explicit coverage limits. Existing project already contains these notes; preserved their limits and the packaged source copy. |

## Website additions

- `/company-research`: search, complete/missing coverage filters, financial comparison, observed morning chart, four research checkpoints, source references, year coverage and model readiness.
- `/company-research/charts`: original detailed explorer with line/candlestick modes, checkpoint review, separate benchmark context and keyboard OHLC inspection.
- `/api/company-study`: read-only response accepting only `id`, `search`, `coverage` and `landmark`. No client paths, quality flags or snapshots.
- “Earnings study” navigation in the workspace and Results & reactions desk.

The adapter verifies critical imported data against the original manifest and caches it until restart. Future labels are removed from inference input; only the completed company prefix reaches the bridge. Imported records/candles are not inserted into production results, price or paper stores. Counts describe the supplied October snapshot, not newly collected live coverage. Owner/session/broker databases from the finance archives were not imported.

## Pipeline and adaptations

The imported package includes collectors, causal features, the conditional-path model, training/calibration/testing code, original exports, provenance, schema, tests and docs. Its SQLite research snapshots remain separate and Git-ignored.

- Financial-document and dashboard tests now discover this repository instead of a Mac path.
- Registry JSON explicitly uses UTF-8.
- Local-history reconstruction accepts `--project`, `--calendar`, `--output-dir` and `--until`. A verified calendar and separate output directory are required. The original calendar was absent from the archives; dates have not been invented.
- The server-owned `model/registry/` is initialized with the four supplied disabled attempts, named `09-30.json`, `09-40.json`, `10-00.json` and `10-30.json`. Browsers cannot choose paths or install artifacts.
- Optional NumPy is declared in `requirements-research.txt`. Model tests passed on installed 1.26.4; the source package reports testing on 2.3.5. The page and disabled-model status work without NumPy. Parquet collection separately requires optional PyArrow.

`SOURCE_ARCHIVE_SHA256SUMS.json` retains the original archive hashes. The published
`SHA256SUMS.json` records adapted source and data hashes. Publication replaces
machine-specific home-directory prefixes with `[source-home]`; financial figures,
price observations, timestamps, quality flags and study counts are unchanged.
Original files remain in the supplied ZIP and private local review directory.
Deliberate snapshot replacement requires reviewed provenance and updated adapter
expectations.
Git preserves the bundle's exact bytes across Windows and Linux checkouts so
line-ending conversion cannot invalidate the data hashes. Imported test
transcripts have trailing blank lines normalized in the publication manifest.

## Actual readiness

The supplied data has 14,796 company periods, 62 known positive events and 17 complete company mornings on three dates: nine above the open and eight below. Forty-five known positive events lack complete price windows. Benchmark prices do not increase the company sample count.

Re-running the real 09:40 training command returned `INSUFFICIENT_VERIFIED_DATA`: 17 raw sessions, three dates and zero rows passing original-vintage/price-lineage gates. Validated forecasts, probabilities and accuracy remain unavailable. Full May 2023 onward collection is incomplete.

## PowerShell commands, repository root

```powershell
python server.py --host 127.0.0.1 --port 8765
python -m pip install -r requirements-research.txt  # optional model dependency
python -X utf8 -B -m unittest marketscope.tests.test_company_study marketscope.tests.test_results_integration
python -X utf8 -B -m unittest discover -s research/company-positive-earnings-study/model -p 'test_*.py'
python -X utf8 -B -m unittest discover -s research/company-positive-earnings-study/collector -p 'test_*.py'
python -X utf8 -B -m unittest discover -s research/company-positive-earnings-study/website -p 'test_*.py'
npm test
node --test research/company-positive-earnings-study/test_dashboard.cjs
```

Public-history planning, with no downloads by default:

```powershell
python -X utf8 -B research/company-positive-earnings-study/collector/collect_public_archive.py --max-shards 1
```

Use `--db` and `--raw-dir` under `work/` for new collection. The publisher's full archive is approximately 10.4 GB and was not downloaded during integration. `--execute` explicitly starts bounded downloading. Follow the collector docs for provenance and financial verification; advertised source coverage is historical metadata pending fresh checks.

Reproduce eligibility without overwriting supplied artifacts:

```powershell
python -X utf8 -B research/company-positive-earnings-study/model/positive_path_model.py train research/company-positive-earnings-study/model/local_model_dataset_with_market.json --landmark 09:40 --output work/repeated-training-attempt.json
```

The source README describes the pre-integration delivery. This guide supersedes its statements that the website was unchanged and its old Mac commands.

## Verification performed

- 87 targeted Python tests passed: 33 model, 28 collector, nine bridge, eight new application-adapter and nine existing results-integration tests. The collector parser test now runs on this Windows checkout instead of skipping its old Mac path.
- All 61 frontend tests passed, including three new study-page cases. The imported explorer's DOM test additionally passed 680 company/mode/checkpoint/reveal combinations.
- The downloaded study notes, existing project notes and packaged study notes have identical SHA256 hashes.
- Homepage, results page, study page, detailed explorer, research API and health endpoint returned HTTP 200 on localhost. Browser checks confirmed navigation, filtering, missing prices and candlestick checkpoint changes.
- The real-data training eligibility result was reproduced, and the public collector's default command was confirmed to remain `PLAN_ONLY`.
- Source syntax and whitespace checks passed. The initial repository-wide scanner reported 94 potential findings. Publication cleanup removed the imported personal home paths and preserved original hash provenance. The remaining scanner findings are existing documentation paths, fixture email/credential-URL examples and authentication-URI construction code. Scanning the working tree and four pending commits found no password, API-token, private-key, session-cookie or TOTP-secret values. The strict repository scan still does not pass these path/example checks; no claim of fully passing CI is made.

Full historical data collection, fitted-model validation and the complete unrelated backend suite were not certified by these checks.
