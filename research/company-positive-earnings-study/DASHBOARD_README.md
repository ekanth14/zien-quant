# Own-company reaction review

Open `COMPANY_REACTION_DASHBOARD.html` locally. It is a self-contained historical review artifact and makes no network requests. It has not been installed in the running website.

The selection criterion is a **positive financial result**: matching-scope revenue and PAT growth over the previous quarter, with ordinary positive bases. The price chart is that company's own next-trading-day chart. It includes every observed direction: 9 higher and 8 lower closes relative to the 09:15 open. Stock charts are never replaced by the NIFTY index.

The 17 complete company mornings currently span 3 August, 5 August and 26 October 2026. The coverage table shows the requested announcement scope from 6 May 2023 and the missing financial/price history. It does not claim collection of the full 2023–present dataset.

Interactions:

- Company search and mobile company selection.
- 09:30, 09:40, 10:00, 10:30 and 11:00 information landmarks.
- Line and OHLC candlestick views using completed candles only.
- Pointer or left/right keyboard inspection of candle OHLC and stored volume.
- Optional reveal of later recorded prices and realized upside/downside in rupees, explicitly excluded from earlier model inputs.
- A separate optional NIFTY context plot, clearly labelled as an unverified community benchmark. No index volume is assumed.
- Source-public timestamp claims and current dataset acquisition time shown separately.

The future probability, expected return, upside and downside fields remain unavailable. The current training attempt has insufficient verified data and only three observed dates; there is no measured model accuracy to display. The 68 company/landmark rows are correlated and do not establish 68 independent experiments. Later collection alone is not a reason to reject historical public-information research; original public financial vintages, revisions and source-price provenance still need verification.

Build from the package root:

```sh
python3 -B build_dashboard.py
node test_dashboard.cjs
```

The build reads the portable files under `local_data/`, `model/` and `public_data/`; it does not read credentials or original app files. The DOM test reuses the existing project's `linkedom` development dependency. Set `ZIEN_PROJECT` to another checked-out project root if necessary. No new browser library or frontend runtime dependency is added.

`dashboard_validation.json` records successful JavaScript parsing, company identity, 680 render combinations across all 17 companies, five landmarks, two chart modes, revealed/hidden later outcomes and separate market context. It also checks search, 350-pixel SVG sizing and keyboard candle inspection. These are DOM/SVG checks, not a real-browser screenshot or full-server deployment test.
