# Public historical-source inventory

Checked 27 October 2026. Publisher coverage descriptions are claims to verify against downloaded rows, not measured completeness for the website's universe. No public company-price rows after 6 May 2023 were successfully retrieved through these web-only probes. The parent study has separately collected some NIFTY benchmark rows; those are not company earnings-event price histories.

## Hugging Face minute dataset

[Original publisher](https://huggingface.co/datasets/xxparthparekhxx/indian-stock-market-minute-data), [inspected duplicate](https://huggingface.co/datasets/GalacticWanderer/indian-stock-market-minute-data), [shard listing](https://huggingface.co/datasets/xxparthparekhxx/indian-stock-market-minute-data/tree/main/minute).

The card claims 2,500+ NSE stocks/indices with minute history from 2022; the viewer describes roughly 713 million minute rows and shows an aggregate latest timestamp of 21 January 2026. Its schema has symbol, UTC timestamp, float32 OHLC, integer volume and OI. Eight minute Parquet shards are listed, roughly 10.4 GB collectively. The publisher labels the dataset MIT and intended for research/backtesting. The card does not independently establish broker redistribution permission, corporate-action policy or original tick provenance. Upstox is a tag, not a verified lineage chain.

Actual numeric viewer rows for 20MICRONS on 3 January 2022 were readable, establishing that the viewer contains price rows. They precede the requested cutoff. Public `/rows`, `/filter`, `/parquet`, Hub API and later viewer-page requests were inaccessible to the current web tool; no claimed 2023 company window was extracted. The documented filter API can partially index datasets above 5 GB, so a filter response must not be assumed to cover all symbols. [Official filter documentation](https://huggingface.co/docs/dataset-viewer/filter).

Direct Parquet URLs are recorded in the download manifest. Full downloads/imports require a network-capable runtime and optional PyArrow. Even a fully validated copy would still need newer company data after its actual last dates and the separate original financial-result disclosures.

## NSE F&O stock release

[Publisher repository](https://github.com/voletiramu/nse-fno-1min-data), [release metadata](https://github.com/voletiramu/nse-fno-1min-data/releases/tag/v1.0.0).

The release advertises 214 stocks, April 2024–April 2026, one-minute spot OHLCV derived from Zerodha Kite. The published GitHub asset is 485,923,642 bytes with SHA256 `20024713c455cc16b5daae91e06991d57a1acfa6a30c77bb7d5a742ee1789ab2`. Its README describes research/educational use and excludes redistribution as a commercial product. Binary download was not supported by the available GitHub connector; no company candles were imported from it. Its stated range misses the requested 2023 start and later 2026 months. The README's total-bar count and multi-year/uncompressed-size claims require validation after extraction.

## bh1rg1v repository

[Current equity dataset README](https://github.com/bh1rg1v/algorithmic-trading/blob/main/data/storage/raw/equity/README.md).

The older `historicaldata/equity` link returns 404. Current main tree revision `7a8eb320a5e7e25a65e52d85ebfb087b03c089e5` contains the dataset README and fetching scripts, not stock OHLC CSV files. The README links externally hosted Jio Cloud archives, advertising a 2025 market-cap-selected universe of 1,852 stocks and 2015–March 2025 coverage. It lists 52 GB one-minute and 12 GB five-minute downloads. The files were not downloaded, their schema/rights/contents are unverified, and that stated end date leaves newer history missing.

## Other checks

[gsidhu/nse-intraday-data](https://github.com/gsidhu/nse-intraday-data/tree/main/2023) has public text data, but its inspected 2023 directory contains only January/February, preceding the cutoff. It was not treated as requested-range evidence.

[DataCedar's INFY page](https://datacedar.com/markets/nse/infy) distinguishes long daily history from minute history starting 17 September 2026. Its documented minute coverage therefore does not answer the requested 2023 study, regardless of the length of its daily-price chart.

These checks distinguish visible historical charts, downloadable numeric intraday bars and sufficient event coverage. A plotted line or long daily history cannot supply unobserved 09:15–11:00 candles.
