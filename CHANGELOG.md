# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [2.1.0] - 2026-10-26

### Added
- **Accurate previous close resolution**: Day % change resolves against real previous closes from Yahoo Finance and candle archive; returns explicit `unavailable` status when no previous close is recorded instead of inventing numbers.
- **Dynamic matrix flow canvas**: Replaced static matrix placeholders with an active HTML5 2D canvas displaying real candle-derived trajectory swarms from `/api/flow`.
- **Design preview notices**: Added prominent disclaimer banners to static HTML mockups (`Bloomberg`, `Mobile`, `Console`).
- **Regression test suite**: Added `test_missing_prior_close.py` testing prior close precedence, unpriced breadth isolation, and archive fallbacks.

### Changed
- Flow and Matrix terminals upgraded to 1-minute default candle resolution with 5-second live polling.
- Matrix terminal calculations now derive purely from verified candle metrics (RVOL, breadth, session moves from open).
- Account link on flow terminal routes directly to home workspace `/`.

### Fixed
- Fixed matrix page JavaScript crash by handling null/undefined metric scores and unpriced stocks safely.

## [2.0.0] - 2026-10-26

Development checks include Node DOM/API regression tests in CI. Trajectory gaps stay gaps, selected live candle history has a bounded refresh cache, and confirmed charts exclude open candles.

### Added
- **Reference workspace design**: A responsive Zien Quant frontend based on `finance reff/outputs/marketscope/static/`, with sidebar navigation, market overview, scanner, watchlist, stock details, owner controls, and the Laya assistant.
- **Dedicated workspace assets**: The new interface uses `workspace.html`, `workspace.css`, `workspace.js`, and `workspace-api.js`, allowing the previous terminals to remain available.

### Changed
- `/` and `/index.html` now open the new workspace. The existing Flow River stays at `/flow` and `/flow.html`; legacy terminal routes and backend APIs are preserved.
- The reference design is connected to the repository's existing real-data APIs. Auto and previous-session views display received or archived observations, with explicit unavailable states when data is missing.

### Fixed
- Unknown analysis symbols return HTTP 404 before live-feed retrieval, so an upstream outage cannot incorrectly report a missing symbol as a feed failure.

## [0.1.0] - 2026-10-26

### Added
- **Flow River Canvas Terminal**: Canvas visualization of intraday price and volume flows across 4 algorithmic funnel stages (Movers, Volume, Pressure, Setups).
- **Setup Cards with Strict R:R Gating**: Quantitative trade setups with mandatory Risk:Reward >= 1.5 requirement. Setups failing R:R or missing valid levels are strictly rejected.
- **Previous Session Playback**: Deterministic off-market replay powered entirely by real persisted Yahoo Finance chart candles from the most recent trading session, with automatic weekend and NSE holiday detection.
- **Laya AI Quantitative Commentary**: Background synthesis worker integration with local Ollama (`gemma4:12b`), with 0ms deterministic rules fallback and strict numerical validator.
- **Yahoo Finance Scraper & Circuit Breaker**: Resilient scraper engine with exponential backoff, jitter, and automatic circuit breaker tripping on consecutive upstream errors.
- **Deterministic Session State Machine**: `LIVE`, `CLOSED` (previous session), and `FEED_DOWN` states with honest status banners.
- **Security & Single-Owner Administration**: PBKDF2 HMAC-SHA256 password hashing, TOTP multi-factor authentication, secure cookie session manager, CSRF protection, and endpoint rate limiting.
- **Standard Library Zero-Pip Architecture**: Pure Python 3.12+ stdlib backend with zero third-party pip dependencies required for runtime.
- **CLI & Diagnostic Suite**: Built-in CLI commands `init-owner`, `doctor`, `import-stock-master`, and `diagnose-scraper`.
- **Automated Test Suite**: 128 comprehensive unit and integration tests covering security, algorithmic scoring, flow calculations, and session replay.

