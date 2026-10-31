"""Persistent single-workspace simulator. No order client, routes or broker execution.

Prices: authenticated FYERS GET market-data only. Existing earnings research
watch rules are reused, never treated as a validated trading strategy.
"""
from __future__ import annotations
import copy
import csv
import io
import json
import math
import threading
import urllib.parse
import urllib.request
from datetime import datetime, date, time, timedelta
from pathlib import Path

try:
    from .market_schedule import IST
    from .fyers_history import get_fyers_history_provider
except ImportError:
    from market_schedule import IST
    from fyers_history import get_fyers_history_provider

SYMBOLS = {'ASIANPAINT': 'Asian Paints', 'TRENT': 'Trent', 'COALINDIA': 'Coal India'}
VERSION = 'earnings-watch-paper-v1'
CALENDAR_SOURCE = 'https://www.nseindia.com/resources/exchange-communication-holidays?apiversion=v2'
# Deliberately bounded: do not infer hours for the announced Nov 8 special session.
CALENDAR_FROM, CALENDAR_TO = date(2026, 10, 30), date(2026, 11, 6)
RULES = [
    'Experimental: reuse earnings_research.analyze_event and its exact BUY_WATCH / SELL_WATCH rules. No validated strategy win rate.',
    'Only the latest eligible company result assigned to this reaction session; at least 10 comparable point-in-time complete observations.',
    '09:40–10:00 IST only (first qualifying 09:40 candle closes at 09:45). Complete 09:15–09:30 opening range, contiguous closed 5-minute candles and positive volume for typical-price VWAP.',
    'LONG: historical 95% Wilson lower bound >50%, upside frequency ≥10 percentage points above baseline, median return >0.20%, and close above both VWAP and opening high.',
    'SHORT: historical 95% Wilson upper bound <50%, downside edge ≥10 points vs baseline, median return <−0.20%, and close below both VWAP and opening low.',
    'Stop: observed session extreme; positive risk no more than 5% of signal price. Target: illustrative 1.5R. Split/dividend sessions excluded by research engine.',
    'Entry uses the next received fresh FYERS quote after the closed-candle signal, never the historical signal price. Reject if quote has already crossed stop or target.',
    'One simulated round trip per stock per session, whole shares, price-to-stop risk budget (fees and gap losses may exceed it), 1× notional reservation for LONG and SHORT; short-sale proceeds never increase spendable capital.',
    'Exit on the first observed fresh quote crossing stop/target (gaps fill at observed quote), or configured cutoff, or End. Intrabar paths are not inferred.',
    'Pause prevents entries; stop/target and cutoff management continue. No quote catch-up trades after restart. No scheduled session means no price polling or entries.',
    'Absent a fresh quote at mandatory closure, close administratively with no exit fill and UNKNOWN P&L. Never manufacture a stale-price execution or profit.',
    'Spread is an assumed round-trip bps spread (half each side); adverse slippage bps on each side. Fees are flat per side plus configurable all-in turnover bps; not an exact broker tax calculation.',
]
DEFAULTS = {'capital': 300000.0, 'allocation_pct': 100/3, 'risk_pct': .5,
            'fee_flat': 20.0, 'fee_bps': 5.0, 'spread_bps': 4.0,
            'slippage_bps': 3.0, 'cutoff': '11:00', 'max_age_seconds': 15.0}


def clock():
    return datetime.now(IST)


def calendar_session(day):
    if not CALENDAR_FROM <= day <= CALENDAR_TO:
        return None
    if day.weekday() >= 5:
        return None
    return {'date': day.isoformat(), 'open': day.isoformat()+'T09:15:00+05:30',
            'close': day.isoformat()+'T15:15:00+05:30', 'kind': 'regular',
            'source': CALENDAR_SOURCE,
            'note': 'Regular dates verified 30 Oct–6 Nov 2026; conservative continuous-session boundary 15:15. No special-session hours inferred.'}


def next_session(now):
    for offset in range(9):
        session = calendar_session(now.date()+timedelta(days=offset))
        if session and datetime.fromisoformat(session['close']) > now:
            return session
    return None


def finite(value):
    if isinstance(value, bool):
        return None
    try:
        value = float(value)
        return value if math.isfinite(value) else None
    except (ValueError, TypeError):
        return None


class FyersPaperData:
    """Allowlisted GET quotes; reuse existing credentials and read-only history."""
    def quotes(self):
        provider = get_fyers_history_provider()
        app, token = provider._credentials()
        params = urllib.parse.urlencode({'symbols': ','.join('NSE:'+s+'-EQ' for s in SYMBOLS)})
        request = urllib.request.Request('https://api-t1.fyers.in/data/quotes?'+params,
            headers={'Authorization': app+':'+token, 'Accept': 'application/json', 'User-Agent': 'ZienQuant-Paper/1.0'})
        try:
            with urllib.request.urlopen(request, timeout=8) as response:
                payload = json.loads(response.read(1024*1024))
        except Exception:
            raise ValueError('FYERS quotes unavailable. Check local market-data credentials, permission or connectivity.') from None
        if payload.get('s') != 'ok':
            raise ValueError('FYERS did not authorize or return market quotes.')
        output = {}
        for row in payload.get('d', []):
            symbol = str(row.get('n', '')).removeprefix('NSE:').removesuffix('-EQ')
            if symbol not in SYMBOLS or row.get('s') != 'ok':
                continue
            value = row.get('v', {})
            output[symbol] = {'price': finite(value.get('lp')), 'timestamp': finite(value.get('tt')),
                'source': 'FYERS Quotes API', 'delay_status': 'Provider real-time feed; actual freshness checked using provider tt. End-to-end latency not guaranteed.'}
        return output

    def analysis(self, symbol, now):
        try:
            from .results_service import get_results_service
            from .earnings_research import analyze_event
        except ImportError:
            from results_service import get_results_service
            from earnings_research import analyze_event
        service = get_results_service()
        events = service.list_events(symbol=symbol, limit=1)['events']
        if not events:
            return {'setup': {'action': 'WAIT', 'reasons': ['No result filing in the local company ledger.']}}
        event = events[0]
        candles = service.get_candles(symbol)
        # Do not use the history adapter's ten-minute cache for a live trigger.
        provider = get_fyers_history_provider()
        today = now.date()
        live = provider._request_chunk('NSE:'+symbol+'-EQ', '5', today, today)
        # Earlier-session research may use its recorded historical sources.
        # Current-session indicators must never fall back to the public archive.
        historical = [c for c in candles
                   if not (c.get('timeframe', 5) == 5
                           and datetime.fromtimestamp(c['t'], IST).date() == today)]
        actions = service._meta('corporate_actions:'+symbol, {})
        opening = datetime.combine(today, time(9, 15), IST)
        expected = int((now-opening).total_seconds()//300)
        current = {}
        for c in live:
            stamp = finite(c.get('t'))
            if stamp is None or stamp < opening.timestamp() or stamp+300 > now.timestamp():
                continue
            if (stamp-opening.timestamp()) % 300:
                raise ValueError('FYERS candle grid is not aligned to the scheduled session.')
            values = tuple(finite(c.get(k)) for k in ('o','h','l','c','v'))
            if (any(v is None for v in values) or min(values[:4]) <= 0 or values[4] < 0
                    or values[1] < max(values[0],values[2],values[3])
                    or values[2] > min(values[0],values[1],values[3])):
                raise ValueError('FYERS candle OHLCV is invalid.')
            if stamp in current and tuple(current[stamp][k] for k in ('o','h','l','c','v')) != values:
                raise ValueError('Conflicting FYERS candle copies; signal blocked.')
            current[stamp] = {**c, 'timeframe': 5, 'closed': True,
                'source': 'FYERS Historical Data API', 'adjustments_verified': False,
                'corporate_action': today.isoformat() in actions}
        required = {opening.timestamp()+300*i for i in range(expected)}
        if not required or set(current) != required:
            raise ValueError('Incomplete current-session FYERS candles; public archive fallback is disabled.')
        result = analyze_event(event, service.history(symbol), {symbol: historical+list(current.values())}, now)
        result['event_id'] = event.get('id')
        result['signal_closed_at'] = max(current)+300
        result['market_data_evidence'] = {'source': 'FYERS Historical Data API', 'closed_candles': len(current),
            'latest_candle_end': result['signal_closed_at'], 'public_current_session_fallback': False}
        return result


class PaperSession:
    def __init__(self, path, data=None, now_fn=clock):
        self.path, self.data, self.now = Path(path), data or FyersPaperData(), now_fn
        self.lock = threading.RLock()
        self.stop_event = threading.Event()
        self.thread = None
        self.watchdog = None
        self._revision = 0
        self.s = {'mode': 'PAPER ONLY', 'version': VERSION, 'state': 'No strategy configured',
                  'reviewed': False, 'settings': dict(DEFAULTS), 'session': None,
                  'quotes': {}, 'rows': {}, 'trades': [], 'log': [], 'last_poll': None,
                  'error': None, 'reviewed_by': None}
        if self.path.exists():
            try:
                loaded = json.loads(self.path.read_text(), parse_constant=lambda value: (_ for _ in ()).throw(ValueError('Nonfinite paper state')))
                if not isinstance(loaded, dict) or not set(self.s).issubset(loaded):
                    raise ValueError('Incomplete paper state; original file preserved.')
                if (not isinstance(loaded['settings'], dict) or not set(DEFAULTS).issubset(loaded['settings'])
                        or not isinstance(loaded['trades'], list) or not isinstance(loaded['log'], list)
                        or not isinstance(loaded['rows'], dict) or not isinstance(loaded['quotes'], dict)
                        or type(loaded['reviewed']) is not bool):
                    raise ValueError('Invalid paper state schema; original file preserved.')
                if loaded['version'] != VERSION:
                    raise ValueError('Strategy changed; archive old paper state before reconfiguration.')
                self.s = loaded
            except Exception as exc:
                # Never silently reset capital or discard open positions on corruption.
                self.s['state'], self.s['error'] = 'Storage error', str(exc)
        self._seen = {}
        self._analysis_key = {}

    def save(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_suffix('.tmp')
        temporary.write_text(json.dumps(self.s, allow_nan=False, indent=2))
        temporary.replace(self.path)

    def log(self, kind, symbol='', reason='', **values):
        self.s['log'].append({'id': len(self.s['log'])+1, 'time': self.now().isoformat(),
                              'kind': kind, 'symbol': symbol, 'reason': reason, **values})

    def message(self, symbol, signal, reason, observed=None):
        row = self.s['rows'].setdefault(symbol, {})
        signature = (signal, reason, json.dumps(observed or {}, sort_keys=True))
        if self._seen.get(symbol) != signature:
            self.log('signal' if signal != 'NO TRADE' else 'ignored signal', symbol, reason,
                     signal=signal, observed=observed or {})
            self._seen[symbol] = signature
        row.update(signal=signal, reason=reason, observed=observed or {})

    def command(self, action, body, actor):
        with self.lock:
            now = self.now()
            if self.s['state'] == 'Storage error':
                raise ValueError('Paper state storage needs repair; no reset performed.')
            if action == 'review':
                if self.s['state'] in ('Armed', 'Running', 'Paused'):
                    raise ValueError('End the current session before changing its assumptions.')
                if body.get('version') != VERSION or body.get('acknowledged') is not True:
                    raise ValueError('Review all experimental rules and acknowledge their version first.')
                settings = dict(DEFAULTS)
                limits = {'capital': (1000, 1e8), 'allocation_pct': (1, 100/3), 'risk_pct': (.01, 5),
                          'fee_flat': (0, 1000), 'fee_bps': (0, 100), 'spread_bps': (0, 100),
                          'slippage_bps': (0, 100), 'max_age_seconds': (1, 30)}
                for key, (low, high) in limits.items():
                    value = finite(body.get('settings', {}).get(key, settings[key]))
                    if value is None or not low <= value <= high:
                        raise ValueError(f'{key} must be between {low} and {high}.')
                    settings[key] = value
                cutoff = body.get('settings', {}).get('cutoff', '11:00')
                try:
                    parsed = time.fromisoformat(cutoff)
                    if parsed.tzinfo or not time(10, 0) <= parsed <= time(15, 10):
                        raise ValueError()
                except (ValueError, TypeError):
                    raise ValueError('Cutoff must be 10:00–15:10 IST, HH:MM.') from None
                settings['cutoff'] = parsed.strftime('%H:%M')
                if self.s['trades']:
                    raise ValueError('Completed session capital/settings are immutable. Export/archive state to create a new experiment.')
                self.s.update(settings=settings, reviewed=True, reviewed_by=actor, state='Ready')
                self.log('rules reviewed', reason=VERSION, actor=actor, settings=settings)
            elif action == 'start':
                if not self.s['reviewed']:
                    self.log('ignored signal', reason='No strategy configured; rules have not been reviewed.')
                elif self.s['state'] == 'Paused':
                    self.s['state'] = 'Armed'
                    self.log('resumed', actor=actor)
                elif self.s['state'] in ('Ready',):
                    session = next_session(now)
                    if not session:
                        raise ValueError('No next session within the verified NSE calendar. Update the calendar before starting.')
                    cutoff = datetime.fromisoformat(session['date']+'T'+self.s['settings']['cutoff']+':00+05:30')
                    if now >= cutoff:
                        session = next_session(datetime.fromisoformat(session['close']))
                    if not session:
                        raise ValueError('No next verified session available.')
                    self.s.update(session=session, state='Armed', error=None)
                    self.log('session armed', reason='Polling only inside the scheduled session; no replay fills.', actor=actor, session=session)
                elif self.s['state'] not in ('Armed', 'Running'):
                    raise ValueError('This experiment has ended. Export it before starting a new experiment.')
            elif action == 'pause':
                if self.s['state'] not in ('Armed', 'Running'):
                    raise ValueError('No active paper session to pause.')
                self.s['state'] = 'Paused'
                self.log('paused', reason='Entries suspended. Existing-position risk exits and cutoff stay active.', actor=actor)
            elif action == 'end':
                for trade in self.s['trades']:
                    if trade['status'] == 'OPEN':
                        self.close(trade, now, 'End session requested; simulator closure')
                self.s['state'] = 'Ended'
                self.log('session ended', actor=actor)
            else:
                raise ValueError('Unknown paper-session command.')
            self._revision += 1
            self.save()
            return self.snapshot()

    def fresh(self, quote, now):
        price, stamp = finite(quote.get('price')), finite(quote.get('timestamp'))
        if price is None or stamp is None or price <= 0 or quote.get('source') != 'FYERS Quotes API':
            return False
        age = now.timestamp()-stamp
        start = self.s.get('session', {}).get('open') if self.s.get('session') else None
        return (start is not None and datetime.fromisoformat(start) <= now < datetime.fromisoformat(self.s['session']['close'])
                and stamp >= datetime.fromisoformat(start).timestamp()
                and 0 <= age <= self.s['settings']['max_age_seconds'])

    def fill(self, price, buy):
        s = self.s['settings']
        impact = (s['spread_bps']/2+s['slippage_bps'])/10000
        return price*(1+impact if buy else 1-impact)

    def fee(self, price, quantity):
        s = self.s['settings']
        return s['fee_flat']+price*quantity*s['fee_bps']/10000

    def close(self, trade, now, reason):
        quote = self.s['quotes'].get(trade['symbol'], {})
        # Administrative closure is explicitly NOT a price fill.
        fresh = self.fresh(quote, now) and not self.s['error']
        trade.update(exit_time=now.isoformat(), closed_by='simulator', exit_reason=reason)
        if fresh:
            price = self.fill(quote['price'], trade['direction'] < 0)
            gross = (price-trade['entry_price'])*trade['quantity']*trade['direction']
            fee = self.fee(price, trade['quantity'])+trade['entry_fee']
            trade.update(status='CLOSED', exit_price=price, exit_quote_timestamp=quote['timestamp'],
                         gross_pnl=gross, fees=fee, net_pnl=gross-fee,
                         return_pct=100*(gross-fee)/trade['allocation'])
        else:
            trade.update(status='CLOSED_UNPRICED', exit_price=None, gross_pnl=None, net_pnl=None,
                         return_pct=None, fees=None,
                         exit_reason=reason+'; no fresh quote: administrative closure, no simulated exit fill, P&L unknown')
        self.log('exit' if fresh else 'unpriced closure', trade['symbol'], trade['exit_reason'], trade=copy.deepcopy(trade))

    def _external(self, callback):
        """Release the state lock during market-data I/O; controls stay responsive."""
        revision = self._revision
        self.lock.release()
        try:
            try:
                value, error = callback(), None
            except Exception as exc:
                value, error = None, exc
        finally:
            self.lock.acquire()
        return value, error, revision != self._revision

    def deadline_check(self):
        """Independent watchdog: a slow provider cannot keep a virtual position open."""
        with self.lock:
            if self.s['state'] not in ('Armed', 'Running', 'Paused') or not self.s['session']:
                return False
            cutoff = datetime.fromisoformat(self.s['session']['date']+'T'+self.s['settings']['cutoff']+':00+05:30')
            now = self.now()
            if now < cutoff:
                return False
            for trade in self.s['trades']:
                if trade['status'] == 'OPEN':
                    self.close(trade, now, 'Configured session cutoff; closed by simulator')
            self.s['state'] = 'Ended'
            self._revision += 1
            self.log('session ended', reason='Configured session cutoff reached; no further entries.')
            self.save()
            return True

    def tick(self):
        with self.lock:
            now = self.now()
            if self.s['state'] not in ('Armed', 'Running', 'Paused'):
                return
            session = self.s['session']
            opening = datetime.fromisoformat(session['open'])
            cutoff = datetime.fromisoformat(session['date']+'T'+self.s['settings']['cutoff']+':00+05:30')
            if now < opening:
                return
            # Never ask the provider outside published continuous hours, even after restart.
            inside = now < datetime.fromisoformat(session['close'])
            if inside:
                quotes, error, changed = self._external(self.data.quotes)
                now = self.now()
                if changed:
                    return
                if error is None:
                    self.s['quotes'] = quotes
                    self.s['last_poll'], self.s['error'] = now.isoformat(), None
                else:
                    self.s['error'] = str(error)[:250]
                    for symbol in SYMBOLS:
                        self.message(symbol, 'NO TRADE', 'Market data error; entries blocked. '+self.s['error'])
            if self.deadline_check():
                return
            if self.s['state'] != 'Paused':
                self.s['state'] = 'Running'
            for trade in self.s['trades']:
                q = self.s['quotes'].get(trade['symbol'], {})
                if trade['status'] != 'OPEN' or not self.fresh(q, now) or self.s['error']:
                    continue
                p, d = q['price'], trade['direction']
                if (p-trade['stop'])*d <= 0 or (p-trade['target'])*d >= 0:
                    self.close(trade, now, 'Observed stop/target crossed; simulator quote fill')
            for symbol in SYMBOLS:
                quote = self.s['quotes'].get(symbol, {})
                if self.s['error'] or not self.fresh(quote, now):
                    self.message(symbol, 'NO TRADE', 'Missing, stale, future-dated or unauthorized-source quote; no fill.')
                    continue
                if self.s['state'] == 'Paused':
                    self.message(symbol, 'NO TRADE', 'Paused: entries disabled, risk management remains active.')
                    continue
                if any(t['symbol'] == symbol for t in self.s['trades']):
                    self.message(symbol, 'NO TRADE', 'One round trip per stock: position already opened or completed.')
                    continue
                if not time(9, 40) <= now.time().replace(tzinfo=None) < time(10, 0):
                    self.message(symbol, 'NO TRADE', 'Entry window is 09:40–10:00 IST; no current entry rule met.')
                    continue
                key = int(now.timestamp()//300)
                # Evaluate once per closed-candle bucket, including a logged failed assessment.
                if self._analysis_key.get(symbol) == key:
                    continue
                self._analysis_key[symbol] = key
                try:
                    analysis, error, changed = self._external(lambda: self.data.analysis(symbol, now))
                    now = self.now()
                    if changed or self.deadline_check():
                        return
                    if error is not None:
                        raise ValueError('Result/candle analysis unavailable.')
                    setup = analysis['setup']
                    if (now >= cutoff or not self.fresh(quote, now) or now.time().replace(tzinfo=None) >= time(10, 0)
                        or (analysis.get('signal_closed_at') is not None and quote['timestamp'] < analysis['signal_closed_at'])):
                        self.message(symbol, 'NO TRADE', 'Quote expired or entry window ended during analysis; no fill.')
                        continue
                    observed = {k: analysis.get(k) for k in ('sample_size', 'forecast', 'baseline', 'event_id', 'signal_closed_at', 'market_data_evidence')}
                    observed['levels'] = setup.get('levels', {})
                    action = setup.get('action')
                    if analysis.get('reaction_session', {}).get('date') != session['date']:
                        self.message(symbol, 'NO TRADE', 'No eligible result assigned to this scheduled reaction session.', observed)
                        continue
                    if action not in ('BUY_WATCH', 'SELL_WATCH'):
                        self.message(symbol, 'NO TRADE', '; '.join(setup.get('reasons', ['Exact research watch rules not met.'])), observed)
                        continue
                    direction = 1 if action == 'BUY_WATCH' else -1
                    self.message(symbol, 'LONG' if direction == 1 else 'SHORT', '; '.join(setup['reasons']), observed)
                    levels = setup['levels']
                    price = self.fill(quote['price'], direction == 1)
                    stop, target = finite(levels.get('stop')), finite(levels.get('target'))
                    if stop is None or target is None or (price-stop)*direction <= 0 or (target-price)*direction <= 0:
                        self.log('ignored signal', symbol, 'Observed entry already outside stop/target; no stale signal-price fill.')
                        continue
                    s = self.s['settings']
                    reserved = sum(t['allocation']+t['entry_fee'] for t in self.s['trades'] if t['status'] == 'OPEN')
                    realized = sum(t.get('net_pnl') or 0 for t in self.s['trades'] if t['status'] == 'CLOSED')
                    available = s['capital']+realized-reserved
                    budget = min(s['capital']*s['allocation_pct']/100, available)
                    quantity = max(0, min(int((budget-s['fee_flat'])/(price*(1+s['fee_bps']/10000))),
                                         int(s['capital']*s['risk_pct']/100/abs(price-stop))))
                    if not quantity or any(t['status'] == 'CLOSED_UNPRICED' for t in self.s['trades']):
                        self.log('ignored signal', symbol, 'Insufficient virtual capital/risk budget or unresolved P&L.')
                        continue
                    trade = {'id': len(self.s['trades'])+1, 'symbol': symbol, 'direction': direction,
                             'signal': 'LONG' if direction == 1 else 'SHORT', 'status': 'OPEN',
                             'allocation': quantity*price, 'quantity': quantity, 'entry_price': price,
                             'entry_fee': self.fee(price, quantity), 'entry_time': now.isoformat(),
                             'entry_quote_timestamp': quote['timestamp'], 'source': quote['source'],
                             'stop': stop, 'target': target, 'reason': self.s['rows'][symbol]['reason'],
                             'observed': observed, 'exit_price': None, 'exit_time': None,
                             'gross_pnl': None, 'net_pnl': None, 'return_pct': None}
                    self.s['trades'].append(trade)
                    self.log('entry', symbol, 'SIMULATED '+('BUY' if direction == 1 else 'SELL'), trade=copy.deepcopy(trade))
                except Exception:
                    if self.deadline_check():
                        return
                    self.message(symbol, 'NO TRADE', 'Result/candle analysis unavailable; no signal or simulated fill.')
            self.save()

    def snapshot(self):
        with self.lock:
            result = copy.deepcopy(self.s)
            now = self.now()
            result.update(rules=RULES, next_session=next_session(now), server_time=now.isoformat(),
                          calendar_valid_through=CALENDAR_TO.isoformat(), calendar_source=CALENDAR_SOURCE,
                          data_configured=get_fyers_history_provider().credentials_configured())
            capital = result['settings']['capital']
            total, gross_total, reserved = 0.0, 0.0, 0.0
            unknown = False
            for trade in result['trades']:
                if trade['status'] == 'OPEN':
                    reserved += trade['allocation']+trade['entry_fee']
                    q = result['quotes'].get(trade['symbol'], {})
                    if self.fresh(q, now) and not result['error']:
                        exit_mark = self.fill(q['price'], trade['direction'] < 0)
                        gross = (exit_mark-trade['entry_price'])*trade['quantity']*trade['direction']
                        trade.update(gross_pnl=gross, net_pnl=gross-trade['entry_fee']-self.fee(exit_mark, trade['quantity']),
                                     mark_price=q['price'], mark_timestamp=q['timestamp'])
                        trade['return_pct'] = 100*trade['net_pnl']/trade['allocation']
                    else:
                        unknown = True
                if trade['net_pnl'] is None:
                    unknown = True
                else:
                    total += trade['net_pnl']
                    gross_total += trade['gross_pnl']
            realized = sum(t['net_pnl'] for t in result['trades'] if t['status'] == 'CLOSED')
            for symbol, quote in result['quotes'].items():
                stamp = finite(quote.get('timestamp'))
                quote.update(fresh=self.fresh(quote, now) and not result['error'], age_seconds=now.timestamp()-stamp if stamp else None)
            result['portfolio'] = {'capital': capital, 'reserved': reserved,
                'available': None if any(t['status'] == 'CLOSED_UNPRICED' for t in result['trades']) else capital+realized-reserved,
                'gross_pnl': None if unknown else gross_total, 'net_pnl': None if unknown else total,
                'equity': None if unknown else capital+total, 'return_pct': None if unknown else total/capital*100,
                'trade_count': len(result['trades']), 'realized_net_pnl': realized}
            return result

    def export_csv(self):
        s = self.snapshot()
        output = io.StringIO()
        fields = ['record_type','time','symbol','signal','status','allocation','quantity','entry_price','exit_price',
                  'entry_time','exit_time','gross_pnl','net_pnl','return_pct','reason','source','data_timestamp','details']
        writer = csv.DictWriter(output, fieldnames=fields)
        writer.writeheader()
        def write(row):
            # Spreadsheet formula injection protection for exported free text.
            writer.writerow({k: ("'"+v if isinstance(v, str) and v.startswith(('=', '+', '-', '@')) else v) for k,v in row.items()})
        write({'record_type':'PAPER portfolio', 'time':s['server_time'], 'allocation':s['portfolio']['reserved'],
               'gross_pnl':s['portfolio']['gross_pnl'], 'net_pnl':s['portfolio']['net_pnl'], 'return_pct':s['portfolio']['return_pct'],
               'details':json.dumps({'portfolio':s['portfolio'], 'settings':s['settings'], 'session':s['session']})})
        for symbol in SYMBOLS:
            trade = next((t for t in s['trades'] if t['symbol'] == symbol), {})
            row = s['rows'].get(symbol, {})
            quote = s['quotes'].get(symbol, {})
            write({**{k:trade.get(k) for k in fields if k in trade}, 'record_type':'PAPER stock', 'symbol':symbol,
                   'signal':row.get('signal','NO TRADE'), 'reason':row.get('reason','No strategy configured' if not s['reviewed'] else 'Waiting for session'),
                   'source':quote.get('source'), 'data_timestamp':quote.get('timestamp'), 'details':json.dumps(row.get('observed', {}))})
        for row in s['log']:
            write({'record_type':'PAPER '+row['kind'], 'time':row['time'], 'symbol':row.get('symbol'),
                   'signal':row.get('signal'), 'reason':row.get('reason'), 'details':json.dumps(row)})
        return output.getvalue()

    def start_worker(self):
        if self.thread:
            return
        def run():
            while not self.stop_event.wait(5):
                try:
                    self.tick()
                except Exception:
                    with self.lock:
                        self.s['error'] = 'Paper worker/storage error; new entries paused until repaired.'
                        if self.s['state'] in ('Armed','Running'):
                            self.s['state'] = 'Paused'
        self.thread = threading.Thread(target=run, name='paper-only-monitor', daemon=True)
        self.thread.start()
        def watch_deadline():
            while not self.stop_event.wait(1):
                try:
                    self.deadline_check()
                except Exception:
                    with self.lock:
                        self.s['error'] = 'Cutoff/storage error; simulator requires attention.'
                        if self.s['state'] in ('Armed', 'Running'):
                            self.s['state'] = 'Paused'
        self.watchdog = threading.Thread(target=watch_deadline, name='paper-cutoff-watchdog', daemon=True)
        self.watchdog.start()

    def stop(self):
        self.stop_event.set()
        if self.thread:
            self.thread.join(timeout=10)
        if self.watchdog:
            self.watchdog.join(timeout=2)


_instance = None
_instance_lock = threading.Lock()
def get_paper_session():
    global _instance
    with _instance_lock:
        if _instance is None:
            _instance = PaperSession(Path(__file__).resolve().parent.parent/'work/runtime/paper_session.json')
        return _instance
