# Historical data access check · 27 October 2026

This check concerns the requested complete positive-results study from **6 May 2023**, using five-minute prices during the literal next trading date's **09:15–11:00 IST** window. It establishes accessible sources and blockers; it does not certify historical prices or model accuracy.

## Actual local access

The current app is at `[source-home]/Documents/ finance V2/zien-quant`. Its `.env` has no populated FYERS app ID/client ID or access token. The running agent process also has no FYERS credential variables.

The legacy app at `[source-home]/Documents/ finance V2/outputs/marketscope` has private app configuration (app ID, app secret and redirect configuration), but **no access token**. These values were not printed, copied into this report or sent to web tools. App configuration alone cannot authenticate the History API.

The legacy authorization code stores its session in `data_directory()/fyers-session.json`. Its default resolves to `[source-home]/Documents/ finance V2/work/runtime/fyers-session.json`; that file is absent. No FYERS token/session file was found in a filename scan of the finance V2 tree excluding installed dependency directories. The active project's runtime directory also has no FYERS session file. This was a scoped project check, not a search of unrelated user folders, browser storage or system Keychain.

Three bounded unauthenticated HTTPS read probes from the shell—to the FYERS NSE symbol master, FYERS History endpoint and official NSE equity catalogue—all failed with DNS error `[Errno 8] nodename nor servname provided, or not known`. No elevated access, repeated retries, orders, account calls, authentication exchanges or paid purchases were attempted. The current web tool can read public documentation, but it is not an authenticated broker transport and must not receive secrets in URLs.

**New historical market-price responses collected through these probes: zero.** Existing local data remain available for read-only inspection. A public documentation page being readable does not establish that authenticated bulk collection is accessible.

## FYERS documented historical coverage

The official History documentation currently distinguishes:

| Access | Standard | Prime |
|---|---|---|
| Daily prices | Last five years | Entire available history, described as 25+ years |
| Intraday prices | Last one year | Entire available history, described as 5+ years |
| Seconds prices | Unavailable | Available |

Source: [FYERS History API](https://fyers.in/api-docs/v3/api/data-api).

A Standard entitlement therefore does not establish access to five-minute 2023 prices in October 2026. The user's actual entitlement has **not** been checked because there is no authenticated session. Prime's broad advertised history also does not guarantee every instrument, renamed/delisted issuer or requested day is available. The collector must verify actual returned dates and record gaps rather than infer completeness from the subscription name. No subscription purchase is authorized or performed by this check.

## Documented rate limits matter

The official plans page, updated 14 October 2026, lists Data API limits:

| Plan | Per second | Per minute | Per day |
|---|---:|---:|---:|
| Standard | 5 | 50 | 5,000 |
| Prime | 10 | 500 | 500,000 |

Source: [FYERS API plans and limits](https://fyers.in/api-docs/v3/getting-started/plans-and-limits/).

The existing `marketscope/fyers_history.py` spaces calls by 0.22 seconds. That respects a five-per-second ceiling but **does not by itself respect the Standard 50-per-minute ceiling** during sustained backfill. A bulk collector needs a limiter for all applicable windows, bounded exponential backoff/jitter for retryable failures, stop-on-authentication-error, daily budgets, a persisted queue and resume support. The published page says repeated minute-limit breaches can block the user for the rest of the day.

History requests use the read-only endpoint `https://api-t1.fyers.in/data/history` and request attributes `symbol`, `resolution`, `date_format`, `range_from`, `range_to`, and `cont_flag`. Five-minute resolution is `"5"`. FYERS returns `[epoch, open, high, low, close, volume]` rows. Credentials belong in the Authorization header, never logs or output datasets. Symbol identities should come from a verified instrument master, preserving its download time/hash; guessed suffixes and today's symbols alone cannot prove historical issuer identity.

## What is needed to collect the requested history

1. Establish a valid FYERS market-data authorization privately through the app's normal login/setup flow or the server's private local environment. Do not paste tokens or app secrets into chat.
2. Verify that the account's entitlement actually returns a known 2023 NSE five-minute session. A recent-session success is insufficient proof of the required coverage.
3. Run the resumable collector from an environment that can reach official endpoints. This shell currently cannot resolve their hosts. Authenticate only to the intended FYERS host, with no cross-host redirects carrying Authorization headers.
4. Keep raw response files/hashes, request ranges, collection time, instrument mapping and provider metadata. Verify contiguous session bars and never fill missing historical prices. Separate collection/import time from original public availability; historical reconstruction and an actually recorded live app prediction are different evidence.

The full study cannot honestly be labeled collected or backtested until these external access conditions are resolved and coverage is measured. More execution time does not replace an absent authenticated source or unavailable historical entitlement.
