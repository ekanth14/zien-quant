# Positive financial results → next-session conditional path model

This is **executable training, validation and inference code**, rather than another descriptive score. It models a company's next trading session after a comparable positive financial result, including sessions where that company's price fell. Direction of other companies' results or the broad market does not determine whether its outcome is retained.

The current local dataset cannot fit or validate this model: 17 complete next-day company sessions following positive financial results span three dates, and original point-in-time financial vintages and price adjustments are not verified. `actual_data_training_attempt.json` records that result. No market accuracy or fitted market coefficients are fabricated.

## Dependencies and commands

Python 3.10+ and NumPy are sufficient; no sklearn/scipy, network calls, broker orders, model downloads or unsafe pickle loading are used. The bundled local runtime already contains NumPy:

```sh
PYTHON='[source-home]/.cache/codex-runtimes/codex-primary-runtime/dependencies/python/bin/python3'
"$PYTHON" -B -m unittest -v test_positive_path_model.py
"$PYTHON" -B build_local_model_dataset.py
"$PYTHON" -B positive_path_model.py train local_model_dataset.json --landmark 09:40 --output actual_data_training_attempt.json
"$PYTHON" -B build_local_model_dataset.py --benchmark ../public_data/benchmark_mornings.json --output local_model_dataset_with_market.json
"$PYTHON" -B run_all_landmarks.py
```

When independently verified history is collected, provide the same schema and run `train`. To obtain a forecast from a fitted, validated artifact:

```sh
"$PYTHON" -B positive_path_model.py forecast trained_artifact.json current_company_row.json --output forecast.json
```

An insufficient or failed artifact returns abstention and null predictions. `--diagnostic` may expose explicitly unvalidated candidate diagnostics for research; it does not activate them as a validated forecast. Source revisions, unverifiable original figures and missing data remain recorded rather than silently filled. A dataset flagged `synthetic_mechanics_only` cannot enable forecasts even if synthetic test metrics look excellent.

## Exact targets: rise, fall and remaining checkpoints

At a registered landmark t ∈ {09:30,09:40,10:00,10:30}, Pₜ is the last completed close actually usable under the registered data-availability convention. It is a reference price, not an assumed executable fill. For the same company's next live trading date after its announcement:

    Yclose = 10,000 log(C11:00 / Pₜ)
    Yup    = 10,000 log(max(Pₜ, future highs) / Pₜ)
    Ydown  = 10,000 log(min(Pₜ, future lows) / Pₜ)

The excursion targets use bars whose **start is at or after the decision time**; a completed feature candle's high/low cannot enter a future excursion. Including Pₜ ensures Yup ≥ 0 and Ydown ≤ 0. A future final close is between the future extrema. Optional future 10:00 and 10:30 close returns form a checkpoint vector, never a claim to know every minute's path.

Direction labels use a separately registered, known-at-decision movement band bₜ:

    DOWN when Yclose < −bₜ; FLAT when |Yclose| ≤ bₜ; UP when Yclose > bₜ.

The local export uses an illustrative fixed **10 log-basis-point** band. This is not an optimized trading threshold or an estimate of brokerage costs. Direction accuracy depends on this definition and must be compared with the corresponding class-prior baseline. It is not trading win rate.

All target distributions are in log basis points. Inference also converts interval endpoints to percent and reference-price levels:

    percent = 100 expm1(y / 10,000)
    price   = Pₜ exp(y / 10,000).

The converted interval median is a median price level. Exponentiating the mean log return is not silently labelled expected price. No fees, spreads, latency fills, MIS profit or order placement are inferred from these targets.

## The actual mathematical estimator

Each event has an as-of feature vector x: financial growth, margin changes and observed opening price geometry in the current core variant. The training-only transform is:

    zⱼ = (xⱼ − median_train,j) / scale_train,j
    scale_train,j = 1.4826 MAD_train,j

Near-constant scales have a disclosed training-only standard-deviation/one fallback. Nothing is fitted on calibration or test features. Missing feature families require a separate named variant; consensus, news, benchmarks, relative volume and prior sessions are not replaced by zeros.

For historical training event i, let dᵢ be its date, n(dᵢ) the number of training companies on that date, and aᵢ the number of calendar days before the query date. The pooled weight is:

    Kᵢ(x) = exp[−||z − zᵢ||² / (2 p h²)] × 2^(−aᵢ/H) / n(dᵢ)
    wglobal,i = Kᵢ / Σ Kⱼ.

h is bandwidth, H is the recency half-life, p the feature count. The complete artifact is rejected if any fitting label/date is not strictly earlier than the query; masking such a row's weight cannot undo its effect on preprocessing or a baseline. Same-event labels are excluded as well. Busy earnings dates therefore do not gain weight simply by contributing many correlated companies. The feature median/MAD and constant-feature standard-deviation fallback use date-balanced training weights.

Known-sector analogs can add a shrunk component:

    ESSsector = (Σwsector)² / Σwsector²
    ρ = ESSsector / (ESSsector + κ)
    wᵢ = (1 − ρ) wglobal,i + ρ wsector,i.

Unknown sectors use the pooled model. κ controls shrinkage; the code selects h, H and κ inside an earlier chronological selection block. This model is nonlinear: similar market contexts receive exponentially greater weight, sector contributions shrink when evidence is sparse, and recent examples may receive more weight. These are standard statistical building blocks assembled into this specific research system; there is no claim that no other researcher could design them.

The future joint distribution is an empirical mixture of observed outcome vectors:

    F̂(y | x) = Σᵢ wᵢ(x) 1{Yᵢ ≤ y}.

Means and inverse empirical-CDF quantiles are evaluated directly from that distribution. This retains associations between final return, future high/low excursion and remaining checkpoints instead of inventing interpolated candle prices. A raw historical fraction is not presented as already calibrated confidence.

Class frequencies receive weak pooled-prior pseudocounts with local event effective sample size n_eff = 1/Σwᵢ². Temperature T is then selected on the later calibration block:

    pₖ = softmax(log(praw,k) / T).

The implementation separately computes effective **date** sample size from weights aggregated by session. Hundreds of same-day companies cannot masquerade as hundreds of independent market dates. Query forecasts abstain when local event/date support is too weak or the standardized input lies outside the registered distribution limit.

## Magnitude intervals and validation

Raw q10/q50/q90 conditional quantiles are expanded using date-block conformal nonconformity. For calibration row i and target k:

    sᵢ = maxₖ max((Lᵢₖ − Yᵢₖ)/sₖ, (Yᵢₖ − Uᵢₖ)/sₖ, 0).

sₖ is a training-only target scale. One maximum score is retained per calibration date, across that day's companies and targets. A finite-sample order statistic expands the interval endpoints. Nonnegative-up and nonpositive-down constraints are retained.

Nominal 80% coverage relies on assumptions about exchangeable date blocks that financial data may violate. The artifact explicitly reports untouched-date empirical coverage and widths. It does not promise guaranteed conditional coverage or a future price boundary.

Every issuer/event landmark for the same session is placed in one date partition. The chronological order is 60% fit, 20% calibration, 20% final test. Within fit, the last selection dates choose hyperparameters using registered log loss plus normalized return MAE. Training labels not known before the next partition's decision boundary are purged. Every hyperparameter trial is saved.

Only fit data establish scales, examples, class priors and the regularized linear-ridge baseline. The ridge penalty is independently selected on the same inner chronological selection block so that the comparison does not rely on an arbitrarily weak baseline. Calibration data choose temperature and interval expansion; final test data never tune a feature, kernel parameter or temperature. Date-balanced log loss, multiclass Brier score, direction accuracy, return/excursion/checkpoint MAE, coverage and interval width are reported. The probability baseline is a frozen training class prior. Magnitude baselines include a training-only mean and regularized ridge regression.

The pre-registered release screen requires classification improvements and MAE improvements for **every published target**, including final return, maximum upward/downward excursion and optional future checkpoints, against the better mean/ridge baseline; a positive moving-date-block uncertainty interval for log-loss improvement; and acceptable untouched-test marginal and simultaneous whole-date interval coverage. It reports joint all-target row coverage and simultaneous whole-date coverage across every company/target, with the test-date count and small/dependent-sample caveat; marginal coverage is not passed off as simultaneous coverage. Both marginal and whole-date empirical coverage must reach the registered coverage less a disclosed0.10 tolerance. A failed model is saved as a trained research candidate with predictions disabled. Passing this screen supplies limited held-out evidence, not a guarantee of profitable future trading. Drift, new data and feature-variant changes require new validation rather than automatic confidence escalation.

Default screening counts are 80 distinct dates, 300 verified rows, 10 symbols, sufficient earlier-fit/selection/calibration/test dates and class support. These are conservative engineering gates, not a statistical theorem that 80 dates always suffices. Counts alone cannot override failed provenance, chronology or test performance.

## Public-information study versus app replay

These are separate research claims:

- **PUBLIC_INFORMATION_BACKTEST:** source-original financial versions were public before the decision; prices use completed bars under the recorded lag convention. A file downloaded today may legitimately contain historical public information. Its acquisition timestamp remains provenance, not a pretend earlier publication time.
- **OPERATIONAL_APP_REPLAY:** the website itself must also have received the figures, completed extraction, and received the price bar before the decision. Actual app clocks are required.

The model checks original publication, original-version availability, the feature clock, feature-bar end plus registered lag, and original-vintage quality flags. In operational mode it checks app first-seen, extraction and bar-arrival clocks as well. A midday revision cannot become a morning feature merely because an older original filing had been public.

The local builder selects the earliest direct-financials-role candidate clock separately for current and previous facts. Within that record it retains its dependency claims; it does **not** take the maximum over every later year-ago mention or import of the same fact. All candidate provenance records remain saved. Because none has been independently certified as the original public vintage, the quality gate remains false. This avoids both look-ahead and the opposite mistake of treating every historical public datum as unusable merely because it was downloaded later.

## Current real-data result

The expanded local export contains **68 landmark rows from 17 complete next-day company sessions following positive financial results**, on 3 August, 5 August and 26 October 2026. All stock paths are retained irrespective of whether their price rose or fell. Zero original financial vintages and price adjustment records are verified to the model's input standard. The default training attempt returns `INSUFFICIENT_VERIFIED_DATA`, null model/calibration/test metrics and disabled predictions.

The original request begins on 6 May 2023. The current export is not that full dataset. Additional prices, original point-in-time financial versions, provider conventions and adjustment/instrument evidence are required. A local acquisition script and missing-data ledger in the parent package support that work; changing this model's quality flags manually is not data collection.

The exported core intentionally excludes unverified volume, absent expectations/news and incomplete benchmark features. A separately named `local_model_dataset_with_market.json` variant adds aligned NIFTY50 opening returns, ranges and stock-minus-market movement for the same known prefix. Its community benchmark source remains explicitly unverified, no index volume is treated as company volume, and market direction never excludes a company's observed outcome. The benchmark is only available for three downloaded 2026 months, not the requested whole 2023+ period.

Pre-announcement daily controls remain in provenance for a future separately validated feature variant. Run `build_local_model_dataset.py --bar-lag-seconds 60 --output lag60_dataset.json` to inspect a more conservative bar-availability convention; reference prices may then be older than the decision, and are labelled accordingly. When a benchmark is included, exactly the same lag applies to it. The 09:30 landmark is withheld under this lag because the third opening candle is not yet available. Neither convention establishes executable entry prices.

## Website interface

`feature_snapshot.py` is a standard-library-only shared builder. Its stable entry point is:

```python
build_feature_snapshot(current, previous, bars, decision_at,
                       lag_seconds=0, benchmark_bars=None)
```

It returns the feature dictionary, reference price, latest bar end, prefix count, registered lag, benchmark bar end and explicit feature variant. It requires adjacent compatible positive quarters and an exact available contiguous morning prefix. It accepts a live prefix without any future11:00 data, ignores extra future bars before inspecting their OHLC, and respects an optional actual `available_at` field as well as registered lag. The offline exporter calls this same function, preventing train/serve feature drift.

Every row stamps its feature variant. Training refuses mixed lag, movement-band, benchmark-instrument or variant contracts; inference must match the learned contract exactly. Market features require their own verified benchmark provenance and same-date, same-lag, aligned bar-end metadata. Verifying stock prices does not silently certify a community benchmark.

Artifact self-hashes bind model/calibration parameters, dataset/version, configuration, feature contract, landmark, created/built timestamps, latest information used across fitting/calibration/testing and release state. Self-hashes detect accidental inconsistent edits; they are not signatures or protection against someone deliberately rewriting a whole artifact. Unknown model/schema versions are rejected. The fitting-information cutoff and true build timestamp are both recorded. Public historical simulation can use a disclosed earlier logical cutoff with verified earlier public inputs; operational replay additionally requires the artifact actually to have been built before the decision.

`forecast(artifact, current_company_row)` returns `predictions: null` on missing lineage, a wrong landmark, a model created after the decision, inadequate local support, out-of-distribution inputs or a failed release gate. A valid result includes:

* DOWN/FLAT/UP probabilities for the registered material movement band;
* conditional mean log-return/excursion/checkpoint targets;
* calibrated interval lower/median/upper values, converted percent and reference-price levels;
* local event and date effective sample sizes;
* model version, horizon, source assumptions and coverage caveat.

`orders_enabled` remains false. This can sit behind the existing dashboard's read-only positive-results panel after the data and model pass their gates. JSON artifacts are auditable, versionable and safe to inspect without executable object deserialization.
