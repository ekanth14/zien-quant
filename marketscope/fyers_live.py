"""Read-only REST polling adapter. Never routes orders or substitutes public prices."""
import json
import math
import time
import threading
import urllib.parse
import urllib.request
from datetime import datetime, timedelta
try:
    from .fyers_history import get_fyers_history_provider, IST
except ImportError:
    from fyers_history import get_fyers_history_provider, IST


_lock = threading.Lock()
_cache = {}
_last_request = 0


def fetch_stock(symbol, timeframe=5, exchange='NSE'):
    global _last_request
    key = (symbol, timeframe, exchange)
    with _lock:
        cached = _cache.get(key)
        if cached and time.monotonic()-cached[0] < 5:
            return cached[1]
        time.sleep(max(0, .25-(time.monotonic()-_last_request)))
        result = _fetch_stock(symbol, timeframe, exchange)
        _last_request = time.monotonic()
        _cache[key] = (time.monotonic(), result)
        if len(_cache) > 500:
            _cache.pop(next(iter(_cache)))
        return result


def _fetch_stock(symbol, timeframe=5, exchange='NSE'):
    now = time.time()
    try:
        provider = get_fyers_history_provider()
        if exchange != 'NSE':
            raise ValueError('FYERS adapter supports NSE instruments only')
        clean = symbol.removesuffix('.NS').upper()
        matches = provider.search_instruments(clean)['instruments']
        instrument = next(r['symbol'] for r in matches if r['short_symbol'] == clean and r['kind'] == 'equity')
        app, token = provider._credentials()
        request = urllib.request.Request('https://api-t1.fyers.in/data/quotes?' + urllib.parse.urlencode({'symbols': instrument}),
                    headers={'Authorization': f'{app}:{token}'})
        with urllib.request.urlopen(request, timeout=12) as response:
            payload = json.loads(response.read(65536))
        row = next(r for r in payload.get('d', []) if r.get('n') == instrument and r.get('s') == 'ok')
        values = row['v']; price = float(values['lp']); stamp = float(values['tt'])
        if not all(math.isfinite(v) and v > 0 for v in (price, stamp)) or stamp > now + 5:
            raise ValueError('Invalid quote')
        today = datetime.now(IST).date()
        # The chart's ten-minute cache is unsuitable for live indicator updates.
        with provider._lock:
            provider._history_cache.clear()
        candles = provider.get_candles(instrument, (today-timedelta(days=7)).isoformat(), today.isoformat(), str(timeframe))['candles']
        if not candles:
            raise ValueError('No historical candles')
        for candle in candles:
            candle['source'] = 'FYERS'
        try:
            from .candle_archive import get_candle_archive
        except ImportError:
            from candle_archive import get_candle_archive
        get_candle_archive().archive_closed_candles(clean, timeframe, candles, source='FYERS')
        session_candles=[c for c in candles if datetime.fromtimestamp(c['t'],IST).date()==today]
        if not session_candles:
            raise ValueError('No current-session candles; use stored session view')
        previous = values.get('prev_close_price')
        return dict(status='ok' if now-stamp <= 30 else 'stale', error=None if now-stamp <= 30 else 'FYERS quote older than 30 seconds',
                    source='FYERS', quote_timestamp=stamp, delay_status='Measured quote age; REST polling',
                    fetched_at=now, symbol=clean, ticker=instrument, exchange='NSE', currency='INR',
                    price=price, previous_close=float(previous) if previous is not None else None,
                    day_high=values.get('high_price'), day_low=values.get('low_price'), timeframe=timeframe, candles=session_candles)
    except Exception:
        return dict(status='failed', error='FYERS data unavailable; check authorization, entitlement or rate limit',
                    source='FYERS', fetched_at=now, symbol=symbol, exchange=exchange,
                    price=None, timeframe=timeframe, candles=[])
