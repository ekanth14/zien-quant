# Independent model and dataset review

Reviewed 27 October 2026. Scope: `model/positive_path_model.py`, `model/build_local_model_dataset.py`, the expanded local financial/candle archive, and inference/release claims. This is an implementation review; it does not establish market forecasting accuracy.

## Current empirical conclusion

The locally recovered positive-result cohort contains **17 unique company/date mornings on 3 dates**. Nine finished above and eight below their 09:15 opening reference. All are included because their financial results were positive; a rising stock price is not a selection requirement. Four prediction landmarks produce 68 correlated rows, not 68 independent events.

Original public financial vintages, earliest source timestamps, price adjustment/instrument provenance and feature definitions remain unverified in this archive. The real-data training attempts therefore correctly refuse to fit or publish accuracy. No complete 2023–2025 company intraday mornings are available locally. Current results are descriptive candidates, not a completed full-history study.

## Correct design elements checked

1. **Own-company labels.** The builder's closing return, future maximum upside and future maximum downside use the selected company's candles. NIFTY prices are optional, separate conditioning features; index prices are never substituted for stock outcomes.
2. **Information and targets are separated.** Features use candles closed by decision time plus a registered availability lag. Excursion labels use bars whose starts are at or after the decision, excluding the completed feature candle. Later checkpoint targets are included only when strictly later than the landmark.
3. **Public and operational clocks differ.** Public reconstruction uses candidate source/financial-version clocks; current acquisition is provenance. Operational replay additionally requires app first-seen, extraction and arrival clocks. Later acquisition alone does not invalidate a hypothetical public-information study; original public availability still must be verified.
4. **Chronological date partitions.** Fit, calibration and test partitions group whole session dates. Inner hyperparameter selection uses a later portion of the fit period, never the outer final test. Training-only scales and ridge fits are retained in the artifact.
5. **Outcome availability is checked.** The training process removes rows whose labels were unavailable at the declared training time and purges labels not known before later partition boundaries.
6. **Fitting-artifact lookahead fails closed.** `raw_prediction` now rejects the entire fitted artifact when any fitting date or target lies at/after the query. This is necessary because masking future neighbor weights cannot undo future-dependent scaling, ridge coefficients or priors.
7. **Magnitude validation has separate gates.** The current code checks return, maximum-upside and maximum-downside MAE against simple baselines, rather than allowing a good closing-return score alone to authorize all magnitude claims.
8. **No execution claim.** Outputs describe changes from an available reference price. The model does not place orders or assume fills, fees, price-limit access or intrabar stop/target ordering.

## Findings sent to the model author

These concern future model activation. The current actual-data release is already blocked, so they do not change an existing published forecast.

### A. Explicit benchmark lineage must be enforced

At the initial review, `row_reasons` checked shared stock quality flags but did not enforce `provenance.benchmark.provider_verified` or its completed-bar clock when market features were present. A row could therefore carry verified stock flags while still explicitly carrying an unverified or stale benchmark.

Requested correction: require benchmark provenance and verified provider/instrument/timestamp/adjustment lineage for the market-feature variant, confirm the same session and expected prefix/end time, and reject mixed or stale benchmark feature snapshots. A distinct reduced-feature model should remain available when benchmark inputs are missing; missing market data must not silently become zero.

### B. Artifact availability and landmark need integrity binding

The initially inspected integrity hash bound coefficients/calibration/configuration but omitted `created_at` and `landmark_ist`. Backdating the former or relabelling the latter could change inference eligibility without a hash mismatch. Fitting-label checks alone do not protect against future-dependent calibration or test-based release decisions.

Requested correction: bind availability time, landmark, model version and feature variant into artifact integrity. Record the latest information-used time across fitting, calibration and validation, and require it to precede the future query. A self-hash detects accidental changes; it is not a cryptographic signature or protection against a user intentionally recomputing it.

### C. Stale completed-bar snapshots need an explicit bound

The initial row check accepted a completed feature-bar end anywhere after 09:15 if it preceded decision time. A 10:30 query could therefore carry a stale 09:20 snapshot while a generic `bars_complete` flag remained true.

Requested correction: enforce the most recent expected five-minute bar end under the registered lag, or a separately pre-registered maximum staleness tolerance. Apply the same rule to optional benchmark inputs. The provided builder currently generates correct prefixes; this finding concerns standalone inference input validation.

### D. Simultaneous interval release claims must match validation

The conformal calibration score is a maximum across all issuers and targets on one date, but the initial release gate inspected per-target row-weighted coverage only. Whole-date simultaneous coverage was reported separately and could still be weak while every individual component passed.

Requested correction: either gate release on held-out whole-date simultaneous coverage consistent with the registered claim, or narrow the released coverage claim to the actually validated marginal/component target. Dependent financial dates and a small test period cannot establish guaranteed future conditional coverage.

## Interpretation limits

The minimum-count settings, model family, bandwidths, movement band and coverage tolerances are registered engineering/research choices. Passing them would not prove a unique market law or guarantee future returns. Multiple model variants or repeated inspection of the final test must be tracked; an untouched final period is only untouched once.

Lagged references can precede the nominal decision timestamp. Future percentage and rupee outputs must retain that actual available-reference definition rather than being described as executable changes from the exact current market price.

The present feature variants use comparable financial growth and opening price geometry, with an optional separately identified benchmark. Pre-announcement daily controls are retained as provenance but are not yet quantitative features in the current builder. Timestamped analyst consensus, verified news, spread/depth and reliable same-clock volume are not supplied. They should not be described as inputs already used by this model.

Two additional consistency observations were sent during the author's fixes: every published optional checkpoint magnitude needs its own validation gate; and an explicit feature-variant/lag/band/benchmark contract should be identical between dataset rows, trained artifacts and inference rows. Unsupported artifact schema/model versions should fail closed rather than being interpreted by a changed numerical kernel.

## Final resolution pass

All implementation findings above are resolved in the inspected final source. The independent follow-up checked the relevant code paths and the regenerated row contracts rather than repeating the entire suite.

| Finding | Final inspected behavior |
|---|---|
| Benchmark lineage | Market-feature rows require separate verified benchmark provider, source-time, adjustment and instrument flags; five-minute interval, registered lag and same-session aligned endpoint are enforced. The trained instrument is bound into the feature contract. |
| Artifact timing and integrity | Integrity binds creation/build times, latest information used across fit/calibration/test, landmark, model version, dataset hash, feature variant/contract and prediction-release flag, alongside numerical state and calibration. Inference checks both creation and latest-information availability; operational mode also checks actual build time. |
| Stale snapshots | Expected latest five-minute feature endpoints are calculated under the registered lag. Missing, stale or misaligned stock and benchmark prefixes are rejected. Contradictory benchmark and stale-stock audit inputs were independently confirmed to be rejected. |
| Simultaneous intervals | Held-out whole-date simultaneous coverage below registered coverage minus the disclosed 0.10 tolerance blocks release, in addition to component coverage checks. |
| Optional checkpoint magnitudes | Every published target, including optional 10:00/10:30 returns, must improve MAE over the best simple mean/ridge baseline; the gate is no longer restricted to the three mandatory targets. |
| Feature variants and changes | All 68 regenerated core rows explicitly carry `core_financial_opening_price_v1`; all 68 market rows carry `core_financial_opening_price_with_market_context_v1`. Variant, lag, movement band and benchmark instrument must match the trained contract. Unsupported model/schema versions fail closed. |
| Offline/live feature consistency | Both builders share the new standard-library `feature_snapshot.py` prefix extractor; the recorded regression suite checks live-prefix/offline feature parity. |

Reviewed final hashes:

* `model/positive_path_model.py`: `d2ca1ef85c8f10f626a31873f1a9106d6885153f84057673e24021bad4eff409`
* `model/feature_snapshot.py`: `31628451ca7d09332dbcd0f3c47e84d1502029dc6b18eee30393de82e4fd2f4e`

The recorded author regression log reports **33 passing tests**. The parent task owns the final combined-suite run. These fixes validate implementation contracts; they do not validate prediction accuracy. The regenerated real-data artifacts still contain no fitted model, calibration or test performance, and predictions remain disabled because the archive has insufficient verified observations and independent dates. No real-browser deployment verification is claimed.
