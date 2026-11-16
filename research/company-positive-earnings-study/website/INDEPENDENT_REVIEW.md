# Independent bridge review · 27 October 2026

Reviewed `forecast_bridge.py`, `test_forecast_bridge.py`, the real model/feature interfaces and `STUDY_STATUS_AND_FORMULAS.md`. No bridge source was edited by this reviewer.

The initial review found a real module-interface defect: the statistical model module did not expose the feature-builder method called by the bridge. The author corrected the loader to load the sibling `feature_snapshot.py` explicitly. The review also requested typed financial/candle snapshot checks, computed feature-variant propagation and fail-closed handling of malformed model/snapshot exceptions. Those corrections are present in the reviewed version.

Confirmed behavior:

- Registry and engine locations are server configuration, not company/browser-selected paths. Company symbols cannot choose an artifact filename.
- Absent or disabled artifacts return unavailable/abstention before importing NumPy.
- The feature builder recomputes features from completed own-company bars, rather than trusting supplied feature values or later targets. Targets and target-availability fields are removed before inference.
- Computed feature/reference definitions replace caller labels. Matching benchmark prefix clocks are propagated without inventing source-verification attestations.
- Actual inference calls the real model's clock, source-quality, model-integrity, support and validation gates. Registry status alone does not publish a probability.
- Malformed financial/candle structures and arithmetic/interface errors return a missing-snapshot result. The bridge forces `orders_enabled=false` and contains no network, credentials, training or trade calls.

Validation run independently: **9 bridge tests passed in the bundled NumPy research runtime**, including the real statistical-model plus real shared-feature-module interface. The real-module test deliberately uses an unsupported test artifact and correctly receives an abstention, not a market prediction. The existing app virtual environment was also checked separately: **8 tests passed and the NumPy-dependent real-model test was skipped**. No trained model or live website deployment is established by these software checks.

Report-data spot check: **17 complete company mornings, 357 five-minute candles, three dates, 62 currently known positive financial events and 45 missing positive windows** agree with the collection artifacts. The Hugging Face public-source manifest lists **eight** minute Parquet shard URLs; their approximately 10.4 GB aggregate size and January 2026 maximum remain publisher/viewer claims, not downloaded company-history completeness. Public benchmark prices remain separate from company targets. The report appropriately states zero newly downloaded online company price history and unavailable validated accuracy.

No additional blocking issue was found in this scoped review after the corrections. Production integration, complete licensed/public-source verification, original financial vintages, broader training data and a validated forecasting artifact remain outstanding.
