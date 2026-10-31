# Website cleanup review

This pass fixed verified data-integrity defects while preserving local records.

- FYERS fetches several days for archive storage, but now supplies only current-date
  candles to session VWAP, opening-price, volume and intraday calculations.
- Frontend normalization preserves FYERS source and exchange quote timestamp instead
  of labeling every live observation as public data and showing processing time.
- Feed metadata now identifies FYERS polling or the public Yahoo candle route.
- Candle archive rejects malformed, nonfinite, negative-volume, boolean-price and
  inconsistent OHLC records before insertion.

Frontend suite: 29 passed. FYERS focused suite: 10 passed. Broad Python discovery:
433 cases, two failed assertions, twelve errors and one skip. Several errors are
socket/network restrictions; the full suite is not certified passing. Remaining
assertion failures: older Laya test expects a 503 despite document-grounded fallback;
the banned-word scanner flags explanatory text saying no artificial positions exist.
These failures were retained for explicit review rather than hidden.

Outstanding: live FYERS quote reception, historical plan coverage, calendar
unification, complete earnings comparisons, after-hours AI frontend behavior,
deployment context and full backup/restore. No public deployment or browser-wide
visual acceptance is claimed. Start/restart is restricted in this execution
environment. Source changes require the normal website restart.

This review does not certify absence of all bugs or profitable strategies.
