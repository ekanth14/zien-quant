# Zien Quant - Configuration Reference

This document provides a comprehensive reference for all environment variables and dynamic configuration options.

---

## 1. Environment Variables

All environment variables can be set in the system environment or defined in a local `.env` file at the repository root.

| Variable | Type | Default | Allowed Values | Description |
|:---|:---|:---|:---|:---|
| `HOST` | String | `127.0.0.1` | Valid IPv4 / hostname | Network interface for the HTTP server to bind to. |
| `PORT` | Integer | `8765` | `1024` - `65535` | TCP port for the HTTP server. |
| `MS_ENV` | String | `production` | `production`, `development` | Operating environment mode. |
| `MS_DB_PATH` | String | `work/runtime/marketscope.db` | Filesystem path | Path to primary SQLite database (users, sessions, signals). |
| `MS_STOCK_DB` | String | `work/runtime/stock_master.db` | Filesystem path | Path to equity master catalog SQLite database. |
| `MS_CANDLES_DB` | String | `work/runtime/candles.db` | Filesystem path | Path to persistent historical candle archive SQLite database. |
| `OLLAMA_HOST` | String | `http://127.0.0.1:11434` | Valid HTTP URL | Address of the local Ollama LLM API service. |
| `OLLAMA_MODEL` | String | `gemma4:12b` | Ollama model tag | Model identifier to use for Laya AI quantitative commentary. |
| `OLLAMA_TIMEOUT` | Float | `35.0` | Positive float | Maximum seconds to wait for an LLM response before timing out. |
| `OLLAMA_KEEP_ALIVE` | String | `15m` | Time duration string | Duration to keep LLM model weights loaded in system RAM/VRAM. |
| `OLLAMA_CONTEXT_LENGTH`| Integer | `4096` | `2048` - `32768` | Context window length for LLM trade analysis. |
| `LAYA_ALLOW_RULES_SUMMARY` | Boolean | `true` | `true`, `false`, `1`, `0` | If `true`, returns rules-based commentary when LLM is unavailable. |
| `OLLAMA_INTEGRATION` | Integer | `0` | `0`, `1` | Enable live Ollama network calls in automated integration tests. |
| `KITE_API_KEY` | String | `""` | Valid API key or empty | Optional Zerodha Kite Connect API key. |
| `KITE_ACCESS_TOKEN` | String | `""` | Valid token or empty | Optional Zerodha Kite Connect access token. |

---

## 2. Dynamic Database Settings (`config_versions`)

Owner administrators can tune operational parameters at runtime via the Owner Settings Dashboard or `/api/config` without restarting the server.

| Parameter | Type | Default | Range | Description |
|:---|:---|:---|:---|:---|
| `scoring.ema_fast` | Integer | `9` | `3` - `50` | Period for the fast Exponential Moving Average. |
| `scoring.ema_slow` | Integer | `21` | `10` - `200` | Period for the slow Exponential Moving Average. |
| `scoring.rsi_period` | Integer | `14` | `5` - `30` | Rolling period for the Relative Strength Index. |
| `scoring.momentum_bars` | Integer | `5` | `1` - `20` | Number of bars for momentum slope calculation. |
| `gating.min_rr_ratio` | Float | `1.5` | $\ge 1.0$ | Minimum required Risk-to-Reward ratio for actionable trade setups. |
| `gating.min_conviction` | Float | `65.0` | `50.0` - `90.0` | Minimum composite score threshold for Bullish conviction signals. |
| `gating.max_bear_conviction` | Float | `35.0` | `10.0` - `50.0` | Maximum composite score threshold for Bearish conviction signals. |
| `scraper.concurrency` | Integer | `6` | `1` - `20` | Number of concurrent symbol worker threads in scraper pool. |
| `scraper.max_retries` | Integer | `3` | `1` - `10` | Maximum retries before a symbol request is marked failed. |
| `scraper.circuit_breaker_trips` | Integer | `5` | `2` - `20` | Consecutive global failures required to trip the circuit breaker. |
| `scraper.circuit_breaker_cooldown` | Integer | `60` | `10` - `300` | Seconds the circuit breaker remains open before attempting a probe. |

