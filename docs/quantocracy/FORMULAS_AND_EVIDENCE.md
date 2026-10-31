# Quantocracy research: formulas, evidence and NSE experiments

Research date: **30 October 2026**. Scope: cash-equity intraday **simulation only**. This document is a research design, not a claim that these strategies work on NSE. It does not enable any strategy or change the running paper ledger. The accompanying crawl manifest is the authority for archive coverage; this document does not claim every linked paper has been read.

## 1. What the inspected research actually supports

Quantocracy is a discovery index. Its excerpts and inclusion of an article are not independent validation. We followed the following original-author articles from [page 1](https://quantocracy.com/?pg=1), and inspected their methodology and limitations. Dates below are original-publication/compiled dates shown by the author, not necessarily Quantocracy listing dates. Summaries are deliberately short; the linked articles remain the source of the reported numbers.

| ID | Article and date | Original experiment and findings | Evidence limit and NSE implication |
|---|---|---|---|
| Q1 | [EMA9 Crossing the VWAP](https://kruegeralgorithms.com/en/research/ema9-vwap-cross-index-cfds-costs-eat-the-edge), 20 October 2026; addendum 23 October | Index CFD minute data, four markets, 2015–2022; first cross on 5-minute bars versus repeated reversals. Primary first-cross result: net 1.35 bp/day, t=1.12, q=0.83. Subsequent high-volatility holdout failed the author's confirmation gate. | CFD tick activity is not NSE share volume. No confirmed net edge; useful negative control and cost-sensitivity experiment. |
| Q2 | [OU mean reversion on point-in-time S&P 500 constituents](https://quanterlab.com/research/ornstein-uhlenbeck-mean-reversion-process-applied-on-s-p-500-point-in-time), compiled 24 October 2026 | Forty half-year daily-equity windows, 2006–2025; five short-half-life stocks versus trending stocks, same long-only OU rules. Short-half-life selection performed better within that comparison. | Costs were applied outside the run; signal-close fills, selection confounding and unknown prior search remain concerns. This is daily US-stock evidence, not an intraday NSE result. |
| Q3 | [HMM versus VIX regime detection](https://quanterlab.com/research/hidden-markov-model-against-the-vix-on-a-regime-aware-factor-portfolio), compiled 19 August 2026 | Same quarterly factor portfolio, different regime detectors; yearly forward windows over 2006–2025. The HMM advantage was concentrated and not statistically decisive. | This does not justify an HMM by default. Compare a lagged realized-volatility rule before adding a fitted latent-state model. |
| Q4 | [TTM/Bollinger squeeze versus volatility clustering](https://quanterlab.com/research/the-calm-before-more-calm-the-ttm-squeeze-the-bollinger-squeeze-and-volatility), compiled 11 October 2026 | Daily US equities, 2006–2025, 36 parameter combinations and sector checks; squeeze added little to ordinary volatility clustering, and its specified long strategy lost money. | The source uses same-close entries and its own band conventions. Treat squeeze as a candidate feature; do not label it a reliable breakout predictor. |
| Q5 | [Volatility regimes: momentum versus fade](https://kruegeralgorithms.com/en/research/volatility-regime-momentum-vs-fade), 28 August 2026, corrections September/October | This is a linked follow-up from Q1. The author explicitly withdrew support for older R-multiple results after finding bias in the previous simulation chain. Corrected breakouts lacked an advantage over random-market controls; the candidate failed holdout. | Preserve corrections and article versions in the registry. The earlier attractive results must not be presented as validated evidence. |

Additional primary implementation reference, located through search rather than a verified Quantocracy path: [QuantConnect, Opening Range Breakout for Stocks in Play](https://www.quantconnect.com/research/18444/opening-range-breakout-for-stocks-in-play/), December 2024. Its own replication uses a 5-minute opening range, relative opening volume and daily ATR on US stocks. It reports a 2.396 Sharpe for **2016**, with parameter sensitivity checks. That is an author-reported, one-year replication, not our reproduction or an NSE result. The article's discussion also identifies indicator-resolution differences between implementations. The underlying Zarattini–Barbon–Aziz paper is linked there; its full text was not reviewed in this pass.

**Research decision:** prioritize transparent ORB, VWAP and mean-reversion baselines for falsifiable experiments; preserve failures as well as successes. No source above establishes an NSE profit forecast or a probability suitable for the dashboard.

## 2. Timing, units and availability contracts

Let each bar have start/end timestamps, instrument, venue, provider, retrieval time, `available_at`, OHLC, volume, adjustment basis and quality flags. A strategy at decision time `t` can use only complete bars whose `end <= t` and `available_at <= t`. A late-arriving corrected bar must not silently replace the version used by an earlier simulated decision.

Use IST for sessions and UTC instants for storage. The normal NSE cash-equity session is **09:15–15:30**, per [NSE market timings](https://www.nseindia.com/static/market-data/market-timings). T+0, auctions and special sessions have different rules; the cash-equity simulator must not infer their eligibility from weekday alone. Installed holiday/circular records must define the exact session. Unknown sessions fail closed.

One-minute bars can be aggregated to 3, 5 or 15 minutes **anchored at 09:15** using first open, maximum high, minimum low, last close and summed volume. A bucket with a missing required source bar is incomplete. Never derive 3-minute candles from 5-minute bars. Never fill missing prices with invented bars. Warm-up bars are permitted from previous sessions only where the feature contract says so; session VWAP and opening range reset each day.

Throughout, `C,H,L,O,V` are sourced close, high, low, open and share volume; `n` is an integer lookback; `d=+1` for LONG and `−1` for SHORT. Percentages and decimal fractions are distinguished explicitly. Missing volume makes VWAP/relative-volume strategies unavailable, not equal-weight substitutes.

## 3. Formula catalogue

These are explicit implementation conventions and standard mathematical definitions. They are not claimed as novel discoveries or as verbatim formulas extracted from every Quantocracy article.

### Indicators and price features

| Family | Formula and convention | Required data / limitation |
|---|---|---|
| SMA | `SMA_n(t) = (1/n) Σ[j=0..n−1] C_(t−j)` | Exactly n valid closes; no partial-window output. |
| EMA | `EMA_t = α C_t + (1−α) EMA_(t−1)`, `α=2/(n+1)` | Specify seed and warm-up. Existing `market.py` seeds first close; retain that for compatibility and report it. |
| Momentum / ROC | `ROC_n(t)=100(C_t/C_(t−n)−1)`; log return `r_t=ln(C_t/C_(t−1))` | Split-adjustment consistency matters; reject zero prices. |
| Session / anchored VWAP | `TP_t=(H_t+L_t+C_t)/3`; `VWAP_(a,t)=Σ[u=a..t] TP_u V_u / Σ V_u` | Bar-based proxy, not exact trade VWAP. Anchor `a` must be selected with information already available. No hindsight swing-low anchors. |
| VWAP dispersion | `σ²_V = Σ V_u(TP_u−VWAP)^2/Σ V_u`; `z_V=(C_t−VWAP)/σ_V` | Same anchor; zero variance means unavailable. Historical fit and online statistic need separate labels. |
| True range / ATR | `TR_t=max(H_t−L_t, |H_t−C_(t−1)|, |L_t−C_(t−1)|)`; seed ATR with mean of n TRs, then `ATR_t=((n−1)ATR_(t−1)+TR_t)/n` | n+1 bars needed for full n-period ATR. Existing generic helper permits partial warm-up; research strategy must reject it. Daily ATR and intraday ATR are different inputs. |
| RSI | `g=max(ΔC,0)`, `l=max(−ΔC,0)`; Wilder-smoothed `G,L`; `RSI=100−100/(1+G/L)` | Full seed of n changes. If L=0 and G>0 return100; if both zero return50, matching existing helper. |
| MACD | `M=EMA_12−EMA_26`; signal `S=EMA_9(M)`; histogram `M−S` | Seed/warm-up fixed and versioned; crossover is an indicator event, not a strategy by itself. |
| ADX | `u=H_t−H_(t−1)`, `v=L_(t−1)−L_t`; `+DM=u` if `u>v,u>0`, otherwise0; `−DM=v` if `v>u,v>0`, otherwise0. `±DI=100 RMA_n(±DM)/ATR_n`; `DX=100|+DI−−DI|/(+DI+−DI)`; `ADX=RMA_n(DX)` | RMA uses α=1/n with SMA seed. Ties produce zero DM. Zero denominator gives zero DX only for a fully valid flat window. ADX measures strength, not direction. |
| Bollinger | `mid=SMA_n(C)`, `upper/lower=mid±kσ_n(C)`; population σ uses denominator n. Width `(upper−lower)/mid`. | n=20,k=2 is a baseline convention, not optimized evidence. |
| Keltner | `mid=EMA_20(C)`, bounds `mid±1.5 ATR_20` | Proposed convention differs from Q4's SMA-centered/simple-TR version; name/version must distinguish them. |
| Squeeze | `BB_upper<KC_upper AND BB_lower>KC_lower`; release is true→false transition on closed bars. | No directional forecast follows without a separately specified entry rule and test. |
| Opening range | `ORH=max H`, `ORL=min L` across complete first N minutes, N∈{5,15,30}; freeze once window ends. | Pre-open auction prints excluded; later highs never alter the range. |
| Gap | `gap=100(O_today/C_previous_regular_session−1)` | Use corporate-action-consistent prior close; event/distribution gaps need flags. |
| Relative volume | `RVOL_(day,slot)=V_(day,slot)/mean(V_(previous D days,same slot))`; opening-volume ratio uses sums over identical opening windows | Full same-slot history only. Comparing opening volume to midday volume biases seasonality. |
| Volatility / regime | `RV_n=sqrt(Σ r²)` within defined past window; lagged daily range average `A_d=mean[(H−L)/C]_(d−5..d−1)`; classify A using only previous training distribution quantiles | Proposed simple regime baseline. Do not substitute today's full-day high/low at09:30. |

### Mean reversion, dependence and state estimates

**Rolling z-score:** `z_t=(x_t−μ_(t−1,n))/s_(t−1,n)`; mean and sample standard deviation are estimated on the n observations strictly before t. This convention makes the compared shock explicit and avoids a subtly different self-normalized statistic. For price mean reversion, use a justified stationary spread/residual where possible; raw trending price is not automatically stationary.

**Ornstein–Uhlenbeck:** `dX=κ(μ−X)dt+σdW`. Fit the exact discrete AR(1) `X_(t+Δ)=a+b X_t+ε`, with `0<b<1`. Then `κ=−ln(b)/Δ`, `μ=a/(1−b)`, `σ=s_ε sqrt(2κ/(1−b²))`, equilibrium deviation `σ_eq=s_ε/sqrt(1−b²)`, half-life `ln(2)/κ`. If b is outside (0,1), do not manufacture a positive half-life. Confidence intervals and stability over successive training windows matter more than a single estimate. Q2 motivates the investigation, but its selected daily US stocks do not establish these parameters for NSE intraday bars.

**Correlation and cointegration:** `ρ=Cov(r_i,r_j)/(s_i s_j)` describes return co-movement. A candidate log-price spread is `X_t=ln P_i−a−β ln P_j`; estimate a,β on a training window. Correlation alone is not cointegration. [Statsmodels' Engle–Granger documentation](https://www.statsmodels.org/stable/generated/statsmodels.tsa.stattools.coint.html) tests a **no-cointegration null**, assumes I(1) inputs, and warns about gaps/near-collinearity. Require multiple-testing correction across screened pairs, stable hedge ratios and both legs' tradability. A spread signal is not safe to execute through a single-stock ledger without atomic simulated two-leg accounting.

**Kalman filter, proposed state-space specification:** `x_t=F x_(t−1)+w_t`, `y_t=H_t x_t+v_t`, covariances Q,R. Predict `x⁻=F x`, `P⁻=FPFᵀ+Q`; gain `K=P⁻Hᵀ(HP⁻Hᵀ+R)⁻¹`; update `x=x⁻+K(y−Hx⁻)`. Use Joseph covariance update `P=(I−KH)P⁻(I−KH)ᵀ+KRKᵀ`. An adaptive spread can use x=(intercept,hedge ratio). This is a standard model proposal, **not a reviewed Quantocracy profitability result**; the attempted introductory UNC document returned404. Fit Q/R without future observations, avoid full-sample smoothing, and benchmark fixed-coefficient regression.

**HMM:** latent state `S_t`, transition `A_ij=P(S_t=j|S_(t−1)=i)`, emissions `f_j(y_t)`. Online filter `π_t(j) ∝ f_j(y_t) Σ_i π_(t−1)(i) A_ij`, normalized to sum1. Use filtered states, never future-informed smoothed states. [hmmlearn documentation](https://hmmlearn.readthedocs.io/en/stable/tutorial.html) supplies model/estimation concepts; Q3 supplies a cautionary comparison, not a case for automatic adoption. Assign state names using training-period volatility; state numeric IDs can permute after refitting.

### Forecasting, machine learning and aggregation

Forecast a clearly defined, executable **future** target, such as net return from the next eligible bar to a fixed horizon, not a vaguely defined “stock will rise.” Alternative targets are first stop/target hit or quantiles of future maximum favorable/adverse excursion; ambiguous OHLC first-hit paths must be marked or scored conservatively. Features include lagged returns, ATR/price, gap, VWAP distance, same-slot RVOL, time-of-day, sector/index residual returns and point-in-time news/earnings availability.

| Model | Mathematical specification | Evidence needed / decision |
|---|---|---|
| Ridge regression | Minimize `Σ(y−β₀−xᵀβ)²+λ||β||²`; preprocess using training-only statistics. | First continuous baseline; compare zero-return and historical same-slot forecasts. [Official linear-model docs](https://scikit-learn.org/stable/modules/linear_model.html). |
| Logistic regression | `p=1/(1+exp(−β₀−xᵀβ))`; minimize binary log loss plus regularization. | First classification baseline; raw p is not automatically calibrated. Same source. |
| Random forest | `p=(1/B)Σ_b p_b(y=1|x)` across randomized trees. | Time-ordered evaluation despite internally randomized tree fitting; limit depth/minimum leaf size. [Official ensemble docs](https://scikit-learn.org/stable/modules/ensemble.html). |
| Gradient boosting | `F_m=F_(m−1)+η h_m(x)` fitted to negative loss gradients; logistic link for classification. | Bound tuning budget and report every attempted configuration. Same ensemble source. |
| XGBoost | `ŷ=Σ_m f_m(x)`; minimize loss plus `Σ_m [γT_m + λ||w_m||²/2]`. | Optional research dependency only; original [XGBoost model documentation](https://xgboost.readthedocs.io/en/stable/tutorials/model.html). Not an edge by itself. |
| LightGBM | Histogram-based tree boosting; leaf-wise expansion maximizes objective reduction. | Sparse small event samples need strict leaf/depth controls; [official documentation](https://lightgbm.readthedocs.io/en/stable/Features.html) explicitly discusses small-data overfitting. |
| Calibration | Fit sigmoid or monotonic isotonic map `p_cal=g(score)` on temporally separate calibration data. `Brier=mean[(p_cal−y)²]`; also log loss and reliability bins. | [Official calibration documentation](https://scikit-learn.org/stable/modules/calibration.html). Evaluate on a later untouched test period; Brier alone mixes calibration and discrimination. |
| Ensemble | `p_ens=Σ w_k p_k`, `w_k≥0,Σw_k=1`; weights fixed or trained on out-of-fold predictions. | Compare equal weights first; calibrate ensemble again. Opposing unvalidated models do not become valid by voting. |

If gain G and loss L are **positive gross rupee outcomes** and round-trip cost is c, `EV=pG−(1−p)L−c`, so break-even `p*=(L+c)/(G+L)`. If G,L are already net of fees, do not subtract c again. This two-outcome formula is only appropriate when the label truly has two priced outcomes; time exits require their own conditional outcome distribution. Abstain when uncertainty/estimated cost erases the edge. No arbitrary confidence score should be displayed as this probability.

## 4. Executable baselines versus future experiment proposals

### 4.1 Implemented offline research versions

The executable contract is `strategy/research_lab.py`, version `research-v1`, with the website catalogue in `marketscope/static/data/quant-research.json`. These are **offline, isolated-capital experiments**, not enabled strategies in the automatic paper watchlist. The catalogue reports `approved=false`, `enabled=false`, and no calibrated probability. The following describes that code, not the future variants in §4.2.

| Executable ID | Actual entry rule | Actual indicator and execution conventions |
|---|---|---|
| `cash_baseline` | Always NO TRADE; no interest assumed. | Zero simulated exposure; replay still checks the configured regular calendar and supplied input schema. |
| `orb15_atr` | After a complete09:15–09:30 range, LONG when `previous_close ≤ ORH < current_close` and `current_close > session_VWAP`; SHORT when `previous_close ≥ ORL > current_close` and `current_close < session_VWAP`. | No opening-candle direction condition, no RVOL filter and no09:30–11:00-only restriction. Current close is a proposed limit price, not an executed fill. |
| `ema9_vwap` | LONG on `previous_EMA9 ≤ previous_VWAP` and `current_EMA9 > current_VWAP`; SHORT on the reverse inequalities. | EMA9 has an **SMA of its first9 supplied closes as seed**, followed by α=2/10. This is deliberately separate from production `market.py`'s first-close seed. Session VWAP uses typical-price×volume and resets at09:15. |

Both active candidate implementations use **intraday SMA ATR14**: the arithmetic mean of the latest14 true ranges from15 supplied bars. This is neither prior-day daily ATR nor Wilder's recursive ATR. Indicators may warm up from earlier supplied bars; their source, timing and availability must pass validation. Strategy input supports1/3/5/15-minute bars, but does not itself manufacture or resample bars. The observation window is **09:15 inclusive to15:15 exclusive**; opening-range completion and indicator warm-up can delay eligibility further. The EMA crossing does not have an additional15-minute-after-open gate.

A signal creates a **next-eligible-bar limit at the signal close**, valid for15minutes. The shared `PaperEngine` evaluates subsequent complete OHLC bars for a touch, permits a favorable opening-gap raw fill, and then applies the configured adverse slippage approximation. Fill timestamps are bar-end simulation timestamps; they are not exact exchange executions. It rejects invalid entry gaps or a slipped fill beyond the target. Stops/targets are fixed from the signal-close plan at1×/2× that intraday SMA ATR. A stop takes priority if OHLC touches both barriers; an entry bar cannot book a target profit. Remaining positions have a90-minute timeout or15:15 cutoff. Missing cutoff data can produce an unpriced administrative close, never an invented exit price.

Only **one pending/open position per symbol/day at a time** is permitted. That is **not one trade per day**: after a position closes or a pending entry expires, a later qualifying crossing can create another attempt. Duplicate same-event signals remain deduplicated. Reversal, flip and trailing features are disabled in research replay; leverage is overridden to1×. Capital, risk fraction, per-trade capital cap and fee/slippage assumptions are copied from the supplied configuration and recorded in the output. Simultaneous allocation follows symbol ordering and must not be mistaken for an optimized portfolio.

The adapter rejects unclosed, unordered, duplicate, future/unavailable or mixed-provider input and incomplete opening/session prefixes for candidate generation. `available_at` must be supplied unless the caller explicitly selects retrospective availability-at-close assumptions; that mode is clearly labeled and is not point-in-time evidence. Actual source licensing and instrument identity/adjustment-basis quality still require a separate audit. `evaluate()` alone relies on the caller's calendar certification; the `replay()` wrapper checks the configured regular-session calendar. No NSE predictive performance follows from passing these software gates.

### 4.2 Future specifications to preregister, not implemented rules

The following are **proposed NSE adaptations**, not faithful replications or optimized recommendations. They are alternative experiment specifications awaiting an immutable preregistration; they are **not the currently executed `research-v1` rules**. Parameters must be locked before their backtests. All candidates remain **research-only / disabled for live paper allocation** until a reviewed strategy version, data entitlement and validation gates exist. The existing automatic ATR watchlist is a separate legacy experiment; this document does not switch it off or change its rules.

Shared baseline: 5-minute closed bars; complete source session; sourced daily ATR(14) through the prior trading day for brackets; at most one accepted entry per instrument per day; entry observation after opening window through11:00; no entry at/after11:00; next eligible bar open for market-style simulated fills; planned stop one **prior daily ATR** away, target two daily ATR away; maximum90-minute holding period and forced exit at installed session cutoff (default15:15 for regular sessions), whichever arrives first. The wide daily-ATR bracket is an explicit baseline, not a claim that it is optimal intraday. Alternative intraday-ATR brackets are separate experiment IDs and must not be silently substituted. Reject missing/full-warm-up ATR. Stop-first treatment for ambiguous same-bar barriers; gap stops use the worse observed opening price. No opposite-signal reversals in these first baselines.

| Candidate | Exact entry event | Additional exit / assumptions | Why research it and what would falsify it |
|---|---|---|---|
| ORB-15 | Freeze09:15–09:30 range. LONG on first later close crossing from `C_previous≤ORH` to `C_t>ORH`, provided opening close>opening open. SHORT symmetric below ORL and opening close<opening open. Doji opening range: no trade. | Shared bracket/time exits. First signal and one daily attempt only. Optional RVOL>1 using prior14 same-window volumes is a **separate** variant, unavailable without those days. | Transparent opening-momentum baseline inspired by ORB literature. Compare matched same-time always-long, random-direction and no-breakout controls after costs. |
| EMA9-VWAP-first | Starting09:30, LONG when `EMA9_previous≤VWAP_previous` and `EMA9_t>VWAP_t`; SHORT with reverse inequalities. First cross only. | Session VWAP; continuous regular-session EMA warm-up. Shared bracket differs from Q1's hold-to-close rule. No cross if either previous statistic missing. | Q1 is a negative result. This intentionally tests whether local economics differ; reject if costs or simpler timed-direction controls explain performance. |
| Z-reversion-20 | On full prior20 session bars, LONG when `−3<z_t≤−2` and `z_previous>−2`; SHORT when `2≤z_t<3` and `z_previous<2`. | LONG exits next eligible open after `z≥0` or `z≤−3`; SHORT after `z≤0` or `z≥3`; shared price-stop/time exit remains. Cross detection requires previously valid z. | Own simple baseline, not Q2 reproduction. Compare OU-conditioned variant only after stability checks; reject on trending-regime losses or unstable fit. |
| Squeeze release | Prior bar squeeze true, current squeeze false; LONG if close crosses above prior20-bar high, SHORT below prior20-bar low. Range excludes current bar. | Shared brackets and cutoff; no claim squeeze predicts direction. Use the catalogue's EMA-centered Keltner convention. | Q4 motivates a matched plain-breakout control. Reject if squeeze does not improve net out-of-sample results over that control. |

For 1/3/15-minute candidates, create separate registered versions with complete resampling and proper warm-up. The 20-bar reversion baseline naturally produces later signals on slower intervals; do not relax its warm-up merely to generate trades. Alternative5/30-minute opening ranges count as separate trials. Never select the best range on the final test period and report that period as untouched.

## 5. Indian friction, risk and portfolio mathematics

The following **current cash-equity intraday** assumptions were checked on30 October2026 against [Zerodha's official charges](https://zerodha.com/charges/); the sell-side STT is independently consistent with [NSE's levy table](https://www.nseindia.com/static/invest/first-time-investor-sebi-turnover-fees-stt-other-levies). They match the existing paper configuration's core rates. They are not a historical fee schedule back to2023.

| Item | Rate / basis |
|---|---|
| Brokerage | Minimum of ₹20 and0.03% of each executed order's turnover |
| STT | 0.025% of sell turnover |
| NSE transaction charge | 0.00307% of turnover on both sides |
| SEBI turnover fee | ₹10/crore =0.0001% on both sides |
| GST |18% of brokerage + exchange charges + SEBI fees |
| Stamp duty |0.003% of buy turnover |

Store effective-dated rates and source versions; historical backtests require historical schedules or an explicit “current-cost sensitivity” label. Contract-note rounding, IPFT/other applicable charges, special brokerage conditions and penalties require separate verification before claiming exact billing. Spread and slippage are market friction, not taxes.

For an order with turnover `T=qP`, fee `F=min(20,0.0003T)+0.0000307T+0.000001T+0.18[brokerage+exchange+SEBI]+0.00025T I_sell+0.00003T I_buy`. Cash shorts pay sell STT on entry and buy stamp on covering. A simulation should not count short-sale proceeds as new freely spendable capital.

With midpoint m and observed full spread s, approximate adverse market fill is `P_fill=m+d(s/2+slippage+impact)`, where d=+1 for a buy order and−1 for a sell order, independently of position direction. If sourced OHLC is used instead of quotes, an assumed basis-point friction must be labeled, and the app must avoid double-counting an already included spread. Suggested stress scenarios are total friction1×,1.5×,2×, with one-bar latency; these are experiment choices, not measured impact estimates. True depth/queue execution cannot be inferred from OHLC volume.

Whole-share sizing solves for the largest integer q such that **both** `q|entry_fill−stop_fill|+estimated entry/stop fees≤equity×risk_fraction` and `q×entry_fill/eligible_leverage+entry_fee≤available_margin`, with an independent per-symbol cap. Add a maximum participation constraint `q≤η×expected executable volume`, where η is declared and estimated volume uses only past information. Do not use a candle's eventual volume to size at its opening. A5× simulator ceiling does not establish broker eligibility for every instrument.

`Gross=d q(exit_fill−entry_fill)`, `Net=Gross−fees`, `return_on_equity=Net/starting_equity`, `return_on_margin=Net/reserved_margin`; display both bases explicitly. Slippage embedded in fills is not deducted again. Correlated simultaneous positions need shared capital, sector/net/gross exposure and summed stop-risk caps. A proposed optimization is `maximize wᵀμ − λwᵀΣw − estimated_cost(w−w_previous)` subject to cash, gross, single-stock and sector bounds. μ and covariance must come from training-only estimates; equal-risk allocation is the comparison baseline, and optimizer output is not evidence of an edge.

## 6. Validation and promotion gates

1. **Data gate:** record counts by symbol/day/timeframe, missing-bar masks, legal source entitlement, corporate-action handling and point-in-time universe membership. Current constituents cannot stand in for all historical members. Require event/news publication and ingestion timestamps before using earnings features.
2. **Causality gate:** each decision must reproduce from its historical input version. Perturbing future bars must not change an earlier signal. Future daily high/low, later financial revisions and same-close execution are forbidden shortcuts.
3. **Experiment registry:** freeze hypothesis, parameter grid, code hash, dataset manifest, costs, benchmark, cutoff and rejection rule. Track failed runs and all attempted variants, including manual changes.
4. **Chronological evaluation:** fit only on earlier sessions; tune/calibrate on later training partitions; evaluate on still-later untouched blocks. Purge training labels whose information interval overlaps test labels. For any cross-validation using both sides of a test block, also embargo adjacent samples; choose span at least the maximum label horizon. Never use shuffled train/test splitting for this problem.
5. **Inference unit:** cluster/block-bootstrap by trading day and earnings event, not individual minute bars. Thousands of correlated bars are not thousands of independent earnings observations. Report sample size, exposure, uncertain intervals and instrument/regime concentration.
6. **Compare like with like:** same universe, entry timestamps, costs, capital and exposure for always-long, random-direction, simpler-rule and cash baselines. Report net expectancy, drawdown including marked open positions, turnover, unfilled/ignored signals and sensitivity to worse fills. Accuracy alone can prefer a losing asymmetric strategy.
7. **Multiple testing:** use a declared familywise/FDR procedure and retain the full search size; optional deflated-Sharpe/PBO diagnostics require independent implementation verification. The attempted author-hosted deflated-Sharpe PDF timed out in this pass, so no numerical DSR implementation or result is asserted here.
8. **Promotion:** approve only a frozen version after adequate independent days/events, stable chronological net performance, adverse-cost survival, calibrated probability where claimed and forward shadow observations. There is no universal minimum sample count or guaranteed confidence threshold. Choose and preregister the economic/statistical requirements before looking at the final test. Failed/insufficient experiments remain research-only.

The last reviewed website audit recorded substantial historical coverage gaps, especially long intraday history and complete point-in-time financial comparisons. Those gaps must be measured afresh and repaired before a claim of full May2023-onward NSE validation. This document supplies testable hypotheses and specifications; it supplies **no manufactured backtest, probability, target price or expected profit**.

## 7. Integration boundaries and implementation inventory

Keep production `marketscope/market.py`'s existing EMA, Wilder RSI, candle VWAP and ATR definitions unchanged. The implemented `research_lab.py` explicitly versions its distinct SMA-seeded EMA9 and intraday SMA ATR14 conventions; tests and UI must preserve those differences. Future candidates can reuse production helpers only when their declared indicator specification actually matches. Reuse `strategy/papertrade_math.py` for validated sizing, side-aware fees and P&L, and the existing closed-bar lifecycle concepts in `strategy/papertrade_engine.py`. A research strategy adapter must preserve explicit entry type: a next-open market candidate is **not** equivalent to the current midpoint-limit entry plan.

New source-driven candidates need distinct strategy IDs, immutable parameter versions, their own isolated experiment ledgers and explicit `research_only` status. Do not overwrite the original decision engine or commingle comparative experiments with the user's existing ₹300,000 paper account. Any live paper promotion must consume only market data, never an order-execution client. Source HTML is untrusted data; a research link or embedded code cannot authorize actions, data sharing, credential access or strategy activation.

Read-only source inspection for this document covered `market.py`, `paper_watchlist.py`, `papertrade_math.py`, `papertrade_engine.py`, `research_lab.py`, `static/data/quant-research.json`, and `config/papertrade.yaml`. The document itself adds no installed HMM/ML package, no model training, no new licensed feed, no enabled strategy and no live orders. The architecture and implementation deliverables alongside it should report their own actual status separately.
