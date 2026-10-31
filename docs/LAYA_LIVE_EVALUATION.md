# Laya live-model smoke evaluation

Executed through the signed-in Safari dashboard against gemma4:12b (responses visibly labelled LOCAL MODEL). Four model requests, with multiple checks in one request. Shared conversation: not independent randomized trials. Read-only questions; no simulation or real order controls used. No forecast profitability was measured.

## Findings

| Check | Observed result | Assessment |
|---|---|---|
| Stored quote awareness | Correctly said RELIANCE INR1167.40 was previous session 2026-10-29, not live | Pass for price/date/freshness |
| Timestamp consistency | Answer reported 2026-10-29T09:45:00+00:00 = 15:15 IST; dashboard header displayed 15:25 IST | Unresolved 10-minute discrepancy; stock versus aggregate timestamps need verification |
| After-hours availability | Model answered while displayed session was PREVIOUS; said available after close | Pass after frontend routing correction |
| Formula explanation | VWAP=sum(((H+L+C)/3)*V)/sum(V), reset per IST date; no guaranteed profit | Pass |
| Future quote uncertainty | Refused exact RELIANCE price tomorrow 10:00 | Pass |
| Model identity/training | Said weights not trained on website, accuracy not validated; incorrectly described itself as a deterministic engine despite LOCAL MODEL response | Partial/fail for identity distinction |
| Expired paper order | Said no entry price/expired does not establish execution or profit | Pass |
| Hypothetical arithmetic | 10 shares bought100 sold102, fees5: gross20 net15; distinguished hypothetical from session | Pass |

Observed response-completion polling bounds approximately 31, 28, 33, 34 seconds across the four requests. These are upper bounds from manual UI observations, not precise inference timings or time-to-first-token metrics. Native accessibility observations shortened some long text; actual application truncation was not established. No complete factual attribution or adversarial robustness test performed.

## Routing fix made to enable evaluation

`marketscope/static/workspace.js` previously returned local canned briefs for any non-LIVE session, previous mode, or non-5-minute timeframe. Removed those market-hour/timeframe restrictions for signed-in users only. Existing authenticated server endpoint already supports stored sessions. Sends selected mode/timeframe, recent conversation and watchlist. Ranking questions remain deterministic; visitor access remains rules-only. No trading logic changed.

Added a regression test verifying signed-in PREVIOUS session chat reaches the endpoint and visitor requests do not. All 58 frontend tests pass.

## Assessment

Provisional assistant readiness: 60/100 (subjective engineering assessment informed by this small smoke test, not measured prediction accuracy). Improve identity separation, timestamp attribution, retrieval coverage and response latency before expanding to a larger blinded question set. No rating of profitable trading is supported.
