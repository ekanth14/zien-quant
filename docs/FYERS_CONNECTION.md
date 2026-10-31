# FYERS market-data connection

Use the existing **MarketScope Personal non-trading app**. No order API is used.
Its registered callback must remain `http://127.0.0.1:8765/api/fyers/callback`.
The temporary callback listener runs on 8765; the website stays on 8766.

Run locally:

```sh
cd /Users/chethan/Documents/finance/zien-quant
python3.12 scripts/connect_fyers.py
```

Enter the App ID and App Secret at the hidden terminal prompts. Complete the
official FYERS browser authorization. The callback checks a random one-time
state and exchanges the code server-side. It stores App ID/access token and
`MS_MARKET_PROVIDER=fyers` atomically in the ignored `.env` with mode 0600.
The entered app secret is not persisted. Callback URLs are not logged.
Restart the website normally to load the new environment.

The main feed then polls authenticated FYERS quotes and fetches historical
candles for indicators; existing historical charts and Results Lab use the same
credentials. This is REST polling, not a WebSocket implementation. A quote older
than 30 seconds is excluded from scoring. Closed markets can therefore show
stored sessions instead of fresh live signals. Provider failures produce missing
data, with no silent substitution of public prices. Quote timestamps/source/delay
metadata are included in stock payloads. Request serialization and five-second
per-instrument cache limit polling; large universes may refresh slowly.

Historical depth depends on FYERS entitlement; token storage does not certify
access back to May 2023. Tokens require renewal under FYERS's current daily expiry
policy. Run the connector again when authorization expires. Do not commit `.env`.

Implementation is locally testable; actual authorization and end-to-end provider
reception still require your credentials and successful browser login.
