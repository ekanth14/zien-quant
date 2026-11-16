# Company chart reactions after positive financial results

Research update: 27 October 2026, India time. This continues the requested study from **6 May 2023 onward**. The code and collection pipeline are implemented; the full requested historical dataset has **not** been obtained, so no validated market prediction accuracy is reported.

## Exact meaning of the study

Select one company because its comparable quarterly **financial result** is positive. Then observe **that company's own price chart** on its literal next trading date, from 09:15 to 11:00. Its price can rise, fall, reverse several times, or remain unchanged. Other companies' results and the broad market can be negative on that date; this does not remove the observation.

The current ordinary positive definition requires revenue and profit after tax to improve over immediately preceding comparable positive-base quarterly figures, using the same units, accounting basis and metric definitions. Turnarounds, zero bases and mixed financial changes remain separate. A positive financial result is not an assertion that the stock chart will be positive or that the company beat analysts' expectations.

The study retains the previously requested universe: existing website companies with the frozen cached Zerodha MIS leverage screen strictly above 3×. This is 1,216 companies out of the website's 2,613. It is a research-cohort screen, not proof of historical or currently executable leverage for every date.

## What was actually collected and recovered

| Item | Actual state |
|---|---|
| Eligible company/reporting-period ledger | 14,796 records |
| Financial fact variants with usable stored figures | 281 |
| Comparable quarterly financial pairs | 126 |
| Known ordinary positive financial events | 62 |
| Complete own-company next-day price windows in that positive subset | 17, containing 357 five-minute candles |
| Distinct dates represented by those complete company windows | 3 August, 5 August and 26 October 2026 |
| Their own prices above/below the 09:15 open at 11:00 | 9 above, 8 below; all retained |
| Known positive events with both earlier daily control observations | 61 |
| Missing price windows among the 62 currently known positives | 45 |
| Further unique official financial-document URLs queued | 27,154 |
| New public index prices downloaded online | 22,500 one-minute NIFTY benchmark candles, July/August/October 2026 |
| Complete benchmark mornings from those downloads | 60; all 17 available company windows can be paired with context |
| New company price history obtained online | 0 |
| Trained and validated market forecast | Unavailable |

The three extra company observations recovered beyond the earlier 14 are APOLLO (Apollo Micro Systems), GAEL and CASTROLIND. Recovery came from additional saved financial versions and same-scope comparisons. It did not come from inventing missing prices.

The index download is separately labelled market context. It never replaces the selected company's price, result, future rise/fall target or sample count. Downloaded files were checked against their exact Git blob hashes and SHA256 hashes; every epoch was checked against the publisher's stated IST text timestamp, and OHLC ordering was validated. These checks verify file integrity and internal consistency, not independent NSE certification.

### Coverage starts in 2023; price completeness does not

| Announcement year | Company/reporting-period records | Comparable financial pairs | Known positive pairs | Complete company reaction windows for those positives |
|---|---:|---:|---:|---:|
| 2023 | 2,598 | 0 | 0 known | 0 |
| 2024 | 4,039 | 5 | 1 | 0 |
| 2025 | 4,399 | 19 | 9 | 0 |
| 2026 | 3,760 | 102 | 52 | 17 |

“0 known” is missing classification coverage, not proof that no company announced a positive result. Most reporting periods still require their original financial documents. No complete local company intraday reaction windows for 2023–2025 have been established.

## Public-source investigation

Public downloadable intraday archives do exist. The [Hugging Face dataset publisher](https://huggingface.co/datasets/xxparthparekhxx/indian-stock-market-minute-data) advertises broad NSE minute history, while the inspected viewer shows a latest timestamp of January 2026. Its approximately 10.4 GB minute archive has not been downloaded here. The [214-stock GitHub release](https://github.com/voletiramu/nse-fno-1min-data) advertises April 2024–April 2026 coverage and a roughly 486 MB compressed download, which misses the requested 2023 start and newer months. The [bh1rg1v dataset README](https://github.com/bh1rg1v/algorithmic-trading/blob/main/data/storage/raw/equity/README.md) links 12 GB five-minute files hosted externally and advertises a 2015–March 2025 span.

Those are publisher claims pending per-symbol/session inspection, provenance and usage checks. Their files were not silently counted as collected data. The public [NIFTY repository](https://github.com/technovusin/nifty50-historical-data) did permit three smaller monthly CSVs to be fetched through the connected GitHub reader. They are the 22,500 benchmark rows actually downloaded.

The shell's bounded official-host requests failed DNS. The web reader could inspect source pages but could not retrieve the large Parquet/binary archives or the queried historical candle APIs; its raw-file request for a roughly 6 MB benchmark year was explicitly rejected for content length. An official NSE financial-document probe also stopped on network failure with zero files downloaded. The available GitHub connector rejects binary downloads. These are access limits of this session, not proof that the public datasets contain no useful data. See `collector/PUBLIC_SOURCES.md` and `collector/public_download_manifest.json` for exact checks and source URLs.

## The implemented mathematical model

This is an executable nonlinear conditional distribution model. It estimates a distribution of a company's possible later moves; it does not claim a deterministic law that every earnings result follows. The statistical building blocks are established methods combined for this research design; uniqueness against every other researcher is not claimed.

### 1. Positive financial features

For comparable current and prior quarter revenue R and PAT P:

    gR = log(Rcurrent / Rprevious)
    gP = log(Pcurrent / Pprevious)
    margin_change_bps = 10,000 × (Pcurrent/Rcurrent − Pprevious/Rprevious)

Quarter length, adjacent periods, units, consolidated/standalone scope, metric definitions, material bases and original version availability need verification. A later restatement cannot be used as if it had appeared in the original release.

### 2. Observed own-company chart features

At a registered decision time t ∈ {09:30,09:40,10:00,10:30}, compute only from the completed price prefix available under the declared candle-lag convention:

* Return from the 09:15 open and return during the first 15 minutes.
* Observed range, position inside/above/below the opening range, drawdown from the high already observed, and latest completed-close momentum.
* Comparable log revenue/PAT growth and PAT margin change.
* In a separately registered market-context variant, matching completed NIFTY returns and range. This is explanatory context; the prediction target is still the company's own chart.

Volume, news, analyst expectations and two-day prior controls are not silently replaced by zero when missing or unverified. The prior-day records have been collected where available and preserved for separately validated feature variants. The current core variant deliberately excludes unverified volume and missing expectations/news.

The same reusable causal feature function is used for historical export and website inference. It has no need to see the eventual 11:00 candle. Missing/nonconsecutive prefixes and stale clocks withhold a forecast.

### 3. Learn similarity and recency from earlier sessions

Each feature is normalized using training-period statistics only:

    z_j = (x_j − training_median_j) / training_scale_j

The scale is based on date-weighted median absolute deviation, with a disclosed training-only fallback for near-constant variables. For a historical event i:

    K_i(x) = exp(−||z − z_i||² / (2 p h²)) × 2^(−age_i/H) / n(date_i)

Here p is the feature count, h the similarity bandwidth, H the recency half-life, and n(date_i) the number of training companies on that date. This prevents a busy results day from dominating simply because many companies announced together. h, H and sector shrinkage are selected inside earlier chronological training data.

A sparse sector's local evidence is shrunk toward the pooled sample:

    rho = sector_effective_sample_size / (sector_effective_sample_size + kappa)
    w_i = (1 − rho) × pooled_weight_i + rho × sector_weight_i

Unknown sectors use the pooled component. Sector labels have not been invented for the current archive.

### 4. Estimate the joint future path distribution

For each comparable earlier company event, retain its later outcome vector Y_i. The model is:

    F_hat(y | x, t) = Σ_i w_i(x,t) × 1{Y_i ≤ y}

This weighted mixture keeps the relationship between final return, maximum subsequent rise, maximum subsequent fall and remaining checkpoint prices. It does not interpolate or fabricate future candle prices.

The primary targets in log basis points are:

    final_return = 10,000 × log(close_11:00 / reference_price_t)
    maximum_rise = 10,000 × log(max(reference_price_t, future_highs) / reference_price_t)
    maximum_fall = 10,000 × log(min(reference_price_t, future_lows) / reference_price_t)

The future high/low calculation excludes the already completed feature candle. Maximum rise is nonnegative; maximum fall is nonpositive. “How much it will fall” therefore concerns the selected company's possible later low, not whether another company's financial result was negative.

Raw direction frequencies are calibrated on a later, separate calibration period:

    probability_k = softmax(log(raw_probability_k) / temperature)

The local export registers an illustrative 10-log-basis-point material movement band for UP/FLAT/DOWN. It is neither a learned trading threshold nor a fee estimate. Nominal conditional q10/q50/q90 ranges are expanded using separate date-block conformal calibration. The calibration uses a maximum error across the companies and targets within a date, because observations on one date are correlated.

Prediction intervals depend on assumptions that financial regime shifts can violate. Their actual held-out coverage, width and errors must be shown. The [original conformalized quantile regression paper](https://proceedings.neurips.cc/paper/2019/hash/5103c3584b063c431bd1268e9b5e76fb-Abstract.html) motivates calibrated ranges, while [research on distribution shifts](https://www.jmlr.org/papers/v25/22-1218.html) explains why changing markets require continuing checks. Neither validates this NSE model's future accuracy.

### 5. Translate estimates into percentages and rupee levels

For any predicted log-basis-point endpoint y and a reference price P:

    percentage_move = 100 × expm1(y / 10,000)
    price_level = P × exp(y / 10,000)

These transformations can display a conditional median, lower/upper range, possible high and possible low in the website. They are not enabled for the current data. Reference prices are completed observed closes, not promised executable entry prices. Exponentiating a mean log return is not labelled an expected rupee price.

## What “accuracy” will mean

The implementation separates earlier fitting, later calibration and untouched final testing by session date. The same company's event and all companies from the same date stay in one date partition. Preprocessing and hyperparameter choices use earlier training data; future labels, same-day labels and later financial revisions cannot become earlier features.

It reports direction log loss, Brier score and direction accuracy against the training class-prior baseline, together with return/rise/fall magnitude errors against mean and regularized-ridge baselines. It reports interval coverage and width, date-block uncertainty and query effective sample sizes. Direction accuracy depends on the registered movement band; it is not a trading win rate.

Minimum history counts are engineering screens, not guarantees of accuracy. A richer model must improve held-out results over simpler baselines; more complexity alone does not activate forecasts. Weak local matches, unfamiliar inputs, failed calibration, missing lineage or a failed artifact check cause abstention.

The actual training attempts on the available company dataset return **INSUFFICIENT_VERIFIED_DATA**. There are only three dates, original financial vintages and source adjustment/instrument conventions are unverified, and one selected source-version clock is later than its morning decision. Test accuracy, calibrated market probabilities and learned market coefficients remain unavailable. Synthetic data exercises code mechanics only and is explicitly prevented from enabling market forecasts.

## Correction to the earlier timestamp explanation

A historical file imported today can be valid research data. For a **public-information historical study**, we need to establish which original financial version was public before the historical decision and use the appropriate historical price prefix. Acquisition time is retained as provenance.

To claim the **website itself made a real prediction at that past time**, we additionally need its actual first-seen, extraction and price-arrival clocks. Those clocks are a different question. The code implements both modes. Later acquisition alone does not invalidate the new retrospective research; unverified or later-revised source content still requires attention.

## Collection and continuation

The resumable price collector has its own SQLite database, no order paths and no modifications to the original application. It validates explicit UTC/IST conventions and start/end candle stamping; one-minute prices become five-minute candles only with all five consecutive minutes present. Missing prices remain missing. Sources are hashed and conflicts quarantined rather than blended. The financial-document worker preserves official raw files and parses supported XBRL contexts without guessing annual-to-quarter subtraction or unsupported PDF figures.

The queue initially contains the 62 **currently known** positive financial events. The 14,796-period ledger and 27,154 official URLs preserve the wider collection task; further financial extraction can add many more positive events. Neither 62 nor 17 is asserted to be the complete 2023–present positive-result population.

See `collector/README.md` for public-file import/download commands and the optional existing FYERS path. Downloadable sources require a network-capable runtime. Their raw files, dates, symbols, adjustment policy, instrument identities and original financial releases then need validation before model fitting. A full study cannot be completed by changing quality flags or repeatedly training on these same three dates.

`COMPANY_REACTION_DASHBOARD.html` is an offline review of the selected companies' own charts and the actual coverage limits. `website/forecast_bridge.py` supplies a read-only inference bridge, and `model/positive_path_model.py` supplies the actual training/forecasting engine. The existing app remains unchanged because its moved folder is outside this task's writable roots. NumPy is available in the bundled research runtime but absent from the website's current virtual environment; `website/README.md` documents the deployment dependency and model/data prerequisites.
