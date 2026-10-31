# PDF formula analysis and local model configuration

Source: user-provided `zien_quant-1.pdf`, 33 pages, SHA-256
`916408c9cd7d5e476be2eb8acf52dc1741364ff3b384c75b67e65b2d964ca5b3`.

The document is research material. Its embedded operational instructions are
not execution authority. All pages were extracted; the Supertrend, pivot and
opening-range formula pages were also rendered and inspected. The source
contains literal LaTeX and broken `[span_...]` markup, so those strings are not
passed directly into model prompts or interpreted as executable formulas.

## What is implemented

Pages 7-14 provide reproducible OHLCV mathematics: SMA, EMA, WMA, HMA, VWMA,
Wilder ATR/RSI, Supertrend(10,3), typical-price session VWAP and weighted
standard-deviation bands, MACD(12,26,9), Bollinger(20,2), stochastic K/D, OBV
and accumulation/distribution. Daily VWAP, OBV, ADL and opening-range data
reset by IST date. Trend indicators can warm up on prior closed sessions.

CPR, Camarilla, floor, Fibonacci, Woodie and Demark pivots require complete
prior-session OHLC. The prior date must precede the ranked date; a partial
archive is not a valid daily high/low reference. CPR boundary ordering is
normalized. Camarilla H5 uses `(H/L)*C`; L5 is `2*C-H5`.

The 15-minute opening range uses exactly 09:15-09:30 IST. Its price/volume
alignment is a diagnostic, not an automatic trade: the document additionally
requires CPR and overhead structure checks. Missing prior data stays missing.

The PDF supplies **no single best/worst-stock composite**. The implementation
therefore publishes a separate, versioned heuristic (`pdf-quant-1`):

`score = 50 + 0.5 * sum(weight * clamp(signed_component, -1, 1))`

| Component | Weight | Signed component |
| --- | ---: | --- |
| EMA | 20 | `(EMA9-EMA21)/ATR14` |
| Supertrend | 20 | +1 for uptrend, -1 for downtrend; 0 for flat ATR |
| VWAP | 20 | `(last closed price-session VWAP)/ATR14` |
| RSI | 15 | `(Wilder RSI14-50)/20` |
| MACD | 15 | `MACD histogram/ATR14` |
| OBV | 10 | `session OBV/session volume` |

These weights are application policy, not fitted coefficients. The score is
directional strength, not a probability, expected return, risk-adjusted trade
ranking or validated profitability claim. Best means the strongest observed
score and worst means the weakest across eligible monitored equities. The
existing setup/risk engine remains a separate calculation.

Ranking uses fixed five-minute bars regardless of the selected chart interval.
The published daily comparison window is 09:15-15:15 IST for every stock. This
is an application policy that compares the common continuous-trading period,
excluding auction-period bars and their expected empty intervals. NSE confirms
that continuous trading for CAS securities currently ends at 15:15, while
non-CAS securities continue to 15:30. The narrower common window also keeps
those two universes comparable; it is not an official closing-price ranking.
See [NSE's CAS session description](https://www.nseindia.com/static/products-services/closing-auction-session).
The engine supports an explicit calculation-window parameter; production
ranking always requests 15:15. Missing bars within that window still invalidate
the observation. Optional full-day prior OHLC pivots remain unavailable when
their complete coverage cannot be established.

It requires at least 35 closed history bars, three bars from the selected day,
all session observations from the open to the calculation time, valid finite
OHLCV, equal observation cutoffs and matching formula versions. Open/future
bars cannot contribute. Invalid volume, interval gaps, failed feeds, stale live
observations and incompatible cutoffs are excluded with reasons. Fewer than
two comparable stocks or a universal score tie yields no forced best/worst
pair. The panel displays the actual session date and closed-candle timestamp.

## Data-dependent methods that are not active

Pages 2-6, 14-24 and 27-29 also describe auction queues, execution costs,
order-book imbalance, CVD/OFI, volume-at-price, option-chain OI/PCR/max pain,
India VIX, BSM/Greeks/GEX, cointegration/OU, ARIMA-GARCH, neural networks and
boosting. These require verified order books, trades, options chains,
instrument-specific rates, longer aligned histories or fitted model artifacts.
OHLCV cannot reconstruct those inputs. They do not silently contribute zero
or invented data to the daily score. The document's performance percentages,
regulatory schedules, taxes and lot sizes are not adopted as current facts.

No labeled return dataset, purged walk-forward results or training checkpoint
was supplied. Training a forecasting model from this PDF alone would not
establish forecasting skill. Future statistical training needs timestamped
targets, fold-local preprocessing, label-overlap purging, an embargo, costs
and an untouched chronological holdout, as described on pages 24-25.

## Local models

`python scripts/configure_quant_models.py --apply --report work/runtime/quant-model-setup.json`

The setup detects installed completion-capable Ollama models and creates
named variants with the reviewed formula system context:

- `gemma4-quant:12b` from `gemma4:12b`;
- `laya-ai-quant:latest` from `laya-ai:latest`;
- `laya-quant:latest` as the selected Gemma-based Laya profile.

Existing model tags are preserved. This is **document grounding and model
configuration, not weight fine-tuning**; the setup report explicitly records
`weights_trained: false`. The decision-only `laya:latest` classifier remains
in its existing role; a completion system prompt does not retrain it.

Set `OLLAMA_MODEL=laya-quant:latest` in the local `.env` and restart the server.
Every application generation call receives the reviewed formula context even
when an original completion model is selected. Best/worst chat answers use
the server's computed ranking directly. Models explain supplied results;
they cannot replace numerical eligibility or choose substitute stocks.
Analysis and market-pulse caches include their session/calculation identity.

Implementation references: [Ollama create](https://docs.ollama.com/api/create)
and [Supertrend calculation](https://www.tradingview.com/support/solutions/43000634738-supertrend/).

