# Experimental research additions

Implemented as original code using public concepts; no third-party source code copied.

- Donchian20 + session VWAP: previous 20 contiguous same-session highs/lows, excluding current bar. LONG/SHORT require crossing the channel with matching VWAP direction. Existing replay supplies 1 ATR stop, 2 ATR target, timeout and session cutoff. Available in research catalogue and offline CLI as `donchian20_atr`. Disabled for live and automatic paper trading.
- `strategy/research_validation.py`: whole-session rolling training/test folds with configurable embargo; disjoint test windows; deterministic training-only selection using equal-coverage mean net returns, including no-trade days. These are primitives, not an automated optimizer or a completed walk-forward study. Embargo must be increased for overlapping label horizons.
- Regression checks cover channel exclusion, both directions, future-data rejection, chronological folds and invalid samples.

References:
- https://www.quantconnect.com/docs/v2/writing-algorithms/indicators/supported-indicators/donchian-channel
- https://www.quantconnect.com/docs/v2/writing-algorithms/optimization/walk-forward-optimization
- https://www.freqtrade.io/en/stable/lookahead-analysis/

The future-data regression is not a full Freqtrade-style differential lookahead audit. No profitability, prediction accuracy, or complete-platform equivalence is claimed. No new training dataset was collected. Run historical studies only after checking data coverage, availability vintages, licensing, NSE calendar, and applicable costs. Existing cost stress and isolated replay remain unchanged. Running server needs a restart to expose the new Python catalogue entry.
