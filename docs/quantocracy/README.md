# Quant research delivery

Read [coverage](RESEARCH_COVERAGE.md), [formulas and evidence](FORMULAS_AND_EVIDENCE.md) and [architecture](PAPER_TRADING_ARCHITECTURE.md).

Implemented: embedded `/#research` catalogue, read-only registry/document APIs, eight disabled candidates, cash/ORB15/EMA9-VWAP offline evaluators, causal scratch replay and data coverage labels. Existing paper views are now visible in navigation. No newly activated strategies or real execution.

Verification: 31 research logic tests, 3 new HTTP cases (plus inherited checks) and 29 frontend tests passed. Full archive research, licensed historical-data validation, walk-forward comparisons and multi-strategy live paper coordination remain unfinished. The running server was not restarted; restart normally to load the new API before visiting `/#research`.

Offline command (use authorized input candles; output must not already exist):

```sh
python3 scripts/replay_research_strategy.py --input /absolute/path/candles.json --day 2026-10-30 --strategy orb15_atr --output /absolute/path/new-run.json
```

Input is `{symbol: [candles]}`. Candles require `t,o,h,l,c,v,source,closed:true,available_at`. Explicit `--retrospective` assumes availability at close for unversioned archives and cannot establish historical prediction accuracy. The configured calendar must verify the date. Runs do not write to the website's paper ledger.
