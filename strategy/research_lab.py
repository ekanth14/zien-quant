"""Experimental closed-bar research adapters. No network, database or order client.

These are proposed NSE baselines, not reproductions of any author's backtest.
They deliberately do not connect to the automatic paper watchlist.
"""
from copy import deepcopy
from datetime import datetime
from math import isfinite
from pathlib import Path
import hashlib
import json
from .papertrade_engine import IST, PaperEngine, at, validate_candle
from .papertrade_math import portfolio_metrics

ROOT = Path(__file__).resolve().parents[1]
REGISTRY = ROOT / 'marketscope/static/data/quant-research.json'
EXECUTABLE = ('cash_baseline', 'orb15_atr', 'ema9_vwap', 'orb15_rvol_liquid', 'donchian20_atr')


def opening_liquidity(bars, day, interval):
    """Use 10 most recent complete prior matched-time windows; no future volume.

    Thresholds are preregistered research assumptions, not fitted NSE optima.
    """
    groups = {}
    for b in bars:
        date = datetime.fromtimestamp(b['t'], IST).date().isoformat()
        groups.setdefault(date, []).append(b)
    start = at(day, '09:15')
    opening = [b for b in groups.get(day, []) if start <= b['t'] < start+900]
    expected = set(range(int(start), int(start+900), interval*60))
    if {b['t'] for b in opening} != expected:
        return None, 'Incomplete opening volume window'
    reference = []
    offset = bars[-1]['t']-start
    for date in sorted(groups, reverse=True):
        if date >= day: continue
        origin = at(date, '09:15')
        window = [b for b in groups[date] if origin <= b['t'] < origin+900]
        matched = [b for b in groups[date] if b['t'] == origin+offset]
        if {b['t'] for b in window} == {origin+i*interval*60 for i in range(15//interval)} and len(matched)==1:
            reference.append((sum(b['v'] for b in window), matched[0]['v']))
        if len(reference)==10: break
    if len(reference)<10:
        return None, 'Need 10 prior complete opening and matched-bar volume windows'
    mean_volume=sum(v[0] for v in reference)/10
    if mean_volume<=0: return None, 'Zero reference opening volume'
    values={'reference_sessions':10,'opening_rvol':sum(b['v'] for b in opening)/mean_volume,
            'opening_turnover_inr':sum((b['h']+b['l']+b['c'])/3*b['v'] for b in opening),
            'max_quantity':int(min(v[1] for v in reference)*.01),
            'min_rvol':1.5,'min_opening_turnover_inr':10000000,'min_price_inr':50,
            'participation_limit':.01}
    if values['opening_rvol']<1.5: return values, 'Opening relative volume below 1.5'
    if values['opening_turnover_inr']<10000000: return values, 'Opening turnover below INR 1 crore'
    if bars[-1]['c']<50: return values, 'Price below INR 50'
    if values['max_quantity']<1: return values, 'Historical volume participation permits no shares'
    return values, None


def catalogue():
    """Read-only catalog; no promotion/activation endpoint exists."""
    data=json.loads(REGISTRY.read_text())
    candidate=deepcopy(next(s for s in data['strategies'] if s['id']=='orb15_atr'))
    candidate.update(id='orb15_rvol_liquid',name='ORB15 + matched-time RVOL and liquidity',version='orb-rvol-v1',
        entry_rules='Original ORB/VWAP cross plus opening RVOL ≥1.5 against 10 prior complete 15-minute windows; opening turnover ≥INR 1 crore; price ≥INR 50. Missing references block.',
        formula='RVOL = current 09:15–09:30 volume / mean prior 10 matched opening volumes. Quantity ≤1% of minimum prior matched decision-bar volume.',
        limitations='Experimental thresholds. Turnover is a liquidity proxy, not observed spread/depth. Independent 1x/1.5x/2x cost runs available via CLI. No NSE profitability validation.')
    data['strategies'].append(candidate)
    trend=deepcopy(candidate)
    trend.update(id='donchian20_atr',name='Donchian 20-bar breakout + VWAP',version='donchian20-v1',
        formula='Upper = max(previous 20 highs); Lower = min(previous 20 lows). Current bar excluded.',
        entry_rules='LONG: previous close ≤ upper < close and close > session VWAP. SHORT: previous close ≥ lower > close and close < session VWAP. 20 prior contiguous same-session bars required.',
        exit_rules='Existing isolated replay: 1 ATR stop, 2 ATR target, 90 minute timeout, 15:15 cutoff.',
        data_requirements='Authorized contiguous closed OHLCV bars, provider identity and availability timestamps; 20 prior same-session bars and ATR(14) warm-up.',
        validation='Unit-tested implementation only; no held-out NSE profitability evidence.',
        source_ids=[],limitations='Original implementation of a standard channel concept. Experimental 20-bar parameter; not calibrated or profitability validated for NSE.')
    data['strategies'].append(trend)
    return data


def ema(values, period):
    if len(values) < period:
        return None
    value = sum(values[:period]) / period
    for item in values[period:]:
        value += 2 / (period + 1) * (item - value)
    return value


def atr14(bars):
    if len(bars) < 15:
        return None
    return sum(max(b['h']-b['l'], abs(b['h']-a['c']), abs(b['l']-a['c']))
               for a, b in zip(bars[-15:-1], bars[-14:])) / 14


def vwap(bars):
    volume = sum(b['v'] for b in bars)
    return sum((b['h']+b['l']+b['c'])/3*b['v'] for b in bars)/volume if volume > 0 else None


def evaluate(strategy_id, bars, as_of, interval=5):
    """Return a research-only candidate from an observed closed prefix.

    Caller certifies a scheduled regular session and data usage rights. Reject
    unavailable, nonfinite, missing, duplicated or out-of-order observations.
    No interpolation; no positive confidence score is manufactured.
    """
    if strategy_id not in EXECUTABLE:
        raise ValueError('Strategy has no executable research baseline')
    if isinstance(interval, bool) or interval not in (1, 3, 5, 15):
        raise ValueError('Supported research intervals: 1, 3, 5, 15 minutes')
    if isinstance(as_of, bool) or not isinstance(as_of, (int, float)) or not isfinite(as_of):
        raise ValueError('Invalid observation time')
    result = dict(strategy_id=strategy_id, version='research-v1', signal='NO TRADE',
                  mode='EXPERIMENTAL_RESEARCH_ONLY', approved=False, probability=None,
                  observed_at=as_of, values={})
    def blocked(reason):
        return {**result, 'reason': reason}
    if strategy_id == 'cash_baseline':
        return blocked('Cash benchmark: no entries; zero trading exposure')
    if not bars:
        return blocked('No candles supplied')
    if not isinstance(bars, (list, tuple)):
        return blocked('Candle sequence required')
    for b in bars:
        if not isinstance(b, dict) or not validate_candle(b) or b.get('closed') is not True:
            return blocked('Invalid or unclosed candle')
        if not isinstance(b.get('source'),str) or not b['source'].strip():
            return blocked('Missing provider identity')
        volume = b.get('v')
        arrival = b.get('available_at')
        if (isinstance(volume, bool) or not isinstance(volume, (int, float)) or not isfinite(volume) or volume < 0
                or isinstance(arrival, bool) or not isinstance(arrival, (int, float)) or not isfinite(arrival)
                or arrival < b['t'] + interval*60 or arrival > as_of or b['t'] + interval*60 > as_of):
            return blocked('Missing volume, future candle or unavailable observation')
    if any(a['t'] >= b['t'] for a, b in zip(bars, bars[1:])):
        return blocked('Duplicate or unordered candles')
    day = datetime.fromtimestamp(as_of, IST).date().isoformat()
    start, end = at(day, '09:15'), at(day, '15:15')
    if not start <= as_of < end:
        return blocked('Outside research entry window 09:15–15:15 IST')
    session = [b for b in bars if start <= b['t'] < end]
    if not session or session[0]['t'] != start:
        return blocked('Missing session opening candle')
    if any(b['t']-a['t'] != interval*60 for a,b in zip(session, session[1:])):
        return blocked('Missing session candles; no interpolation')
    if as_of-(session[-1]['t']+interval*60) > interval*60:
        return blocked('Stale closed candle')
    if len({b['source'] for b in bars}) != 1:
        return blocked('Mixed providers require reconciliation before evaluation')
    atr = atr14(bars)
    if atr is None or atr <= 0:
        return blocked('Insufficient ATR(14) warm-up or zero volatility')
    close = session[-1]['c']; current_vwap = vwap(session)
    result['values'] = dict(close=close, atr14_sma=atr, vwap=current_vwap)
    if current_vwap is None or len(session) < 2:
        return blocked('No volume-weighted session or previous completed bar')
    side = None
    if strategy_id in ('orb15_atr', 'orb15_rvol_liquid'):
        opening = [b for b in session if b['t']+interval*60 <= start+15*60]
        if len(opening) != 15//interval or session[-1]['t'] < start+15*60:
            return blocked('Opening 15-minute range not complete')
        high=max(b['h'] for b in opening); low=min(b['l'] for b in opening)
        previous=session[-2]['c']
        result['values'].update(opening_high=high, opening_low=low, previous_close=previous)
        if strategy_id == 'orb15_rvol_liquid':
            liquidity, reason = opening_liquidity(bars, day, interval)
            result['values']['liquidity'] = liquidity
            result['version'] = 'orb-rvol-v1'
            if reason: return blocked(reason)
        if previous <= high < close and close > current_vwap: side='LONG'
        elif previous >= low > close and close < current_vwap: side='SHORT'
    elif strategy_id == 'donchian20_atr':
        if len(session)<21: return blocked('Need 20 prior contiguous same-session channel bars')
        history=session[-21:-1]
        upper=max(b['h'] for b in history);lower=min(b['l'] for b in history)
        previous=session[-2]['c']
        result['version']='donchian20-v1'
        result['values'].update(channel_upper=upper,channel_lower=lower,channel_lookback=20,previous_close=previous)
        if previous <= upper < close and close > current_vwap: side='LONG'
        elif previous >= lower > close and close < current_vwap: side='SHORT'
    else:
        previous_vwap=vwap(session[:-1]); closes=[b['c'] for b in bars]
        current_ema=ema(closes,9); previous_ema=ema(closes[:-1],9)
        result['values'].update(ema9=current_ema, previous_ema9=previous_ema, previous_vwap=previous_vwap)
        if previous_vwap is None or previous_ema is None:
            return blocked('Insufficient previous EMA/VWAP')
        if previous_ema <= previous_vwap and current_ema > current_vwap: side='LONG'
        elif previous_ema >= previous_vwap and current_ema < current_vwap: side='SHORT'
    if not side:
        return blocked('Exact closed-bar crossing rule not met')
    sign=1 if side=='LONG' else -1
    if min(close-sign*atr,close+sign*2*atr) <= 0:
        return blocked('Nonpositive ATR bracket')
    return {**result,'signal':side,'reason':'Experimental closed-bar rule met; not an approved live signal',
            'plan':{'entry':close,'stop':close-sign*atr,'target':close+sign*2*atr,
                    'entry_policy':'Next-bar limit at signal close; expires after 15 minutes',
                    'exit_policy':'1 ATR stop, 2 ATR target, 90 minute timeout, 15:15 cutoff'}}


def replay(strategy_id, day, symbols, config, *, retrospective=False):
    """Isolated in-memory replay using the existing conservative fill engine.

    symbols maps symbol to start-labelled sourced candles. Retrospective mode
    explicitly assumes availability at bar close; it is NOT point-in-time evidence.
    All non-cash strategies require a scheduled session from the configured year.
    """
    from marketscope.paper_watchlist import watchlist_session
    if strategy_id not in EXECUTABLE: raise ValueError('Unsupported research strategy')
    if not isinstance(symbols, dict):
        raise ValueError('Input must map symbols to candle lists')
    cfg=deepcopy(config)
    if not watchlist_session(datetime.fromisoformat(day).date(),cfg):
        raise ValueError('No verified regular session in the configured calendar')
    cfg.update(k_sl=1,k_tp=2,min_rr=1.5,time_stop_minutes=90,entry_valid_minutes=15,
               leverage=1,trailing_enabled=False,reversal_enabled=False,flip_enabled=False)
    tf=cfg['timeframe_minutes']
    if isinstance(tf, bool) or tf not in (1, 3, 5, 15):
        raise ValueError('Supported research intervals: 1, 3, 5, 15 minutes')
    end=at(day,'15:15');cfg['square_off']='15:15'
    engine=PaperEngine(cfg); prefixes={s:[] for s in symbols};timeline=[]
    for symbol,rows in symbols.items():
        if not isinstance(symbol,str) or not symbol.strip() or not isinstance(rows,list):
            raise ValueError('Each symbol must be nonempty and contain a candle list')
        if any(not isinstance(b,dict) for b in rows):
            raise ValueError('Candle rows must be objects')
        if any(not validate_candle(b) for b in rows):
            raise ValueError('Invalid archive candle')
        if any(a['t']>=b['t'] for a,b in zip(rows,rows[1:])): raise ValueError('Duplicate/unordered archive')
        if any(not isinstance(b.get('source'),str) or not b['source'].strip() for b in rows):
            raise ValueError('Missing provider identity')
        if len({b['source'] for b in rows})>1:
            raise ValueError('Mixed providers require reconciliation before replay')
        for original in rows:
            b=deepcopy(original)
            if not validate_candle(b) or b.get('closed') is not True: raise ValueError('Invalid/unclosed archive candle')
            volume=b.get('v')
            if isinstance(volume,bool) or not isinstance(volume,(int,float)) or not isfinite(volume) or volume<0:
                raise ValueError('Invalid or missing archive volume')
            if retrospective: b['available_at']=b['t']+tf*60
            arrival=b.get('available_at')
            if isinstance(arrival,bool) or not isinstance(arrival,(int,float)) or not isfinite(arrival) or arrival<b['t']+tf*60:
                raise ValueError('Missing or invalid availability; use explicitly retrospective mode for unversioned archives')
            if b['t']+tf*60<=end: timeline.append((arrival,symbol,b))
    for stamp,symbol,b in sorted(timeline,key=lambda item:(item[0],item[1],item[2]['t'])):
        if stamp>end: continue
        if b['t']>=at(day,'09:15'): engine.candle(symbol,b,tf,stamp)
        prefixes[symbol].append(b)
        if stamp>=end or b['t']<at(day,'09:15'): continue
        r=evaluate(strategy_id,prefixes[symbol],stamp,tf)
        engine.event(day,stamp,'RESEARCH_SIGNAL',symbol,r['reason'],research=r)
        if r['signal']=='NO TRADE': continue
        plan=r['plan']
        # Grade B is only a legacy engine compatibility field, not a research rating.
        decision=dict(signal_type='BUY' if r['signal']=='LONG' else 'SELL',gated=False,
                      grade='B',action='EXPERIMENTAL',risk_reward_ratio=2,entry_low=plan['entry'],
                      entry_high=plan['entry'],observed_candle_end=b['t']+tf*60,
                      research_strategy=strategy_id,research_version='research-v1')
        original_cap=engine.cfg['max_capital_per_trade']
        liquidity=r['values'].get('liquidity')
        if liquidity:
            engine.cfg['max_capital_per_trade']=min(original_cap,liquidity['max_quantity']*plan['entry'])
        engine.signal(symbol,stamp,decision,r['values']['atr14_sma'],tf,b['source'],b['c'])
        engine.cfg['max_capital_per_trade']=original_cap
    engine.finish(day,end)
    expected=set(range(int(at(day,'09:15')),int(end),tf*60))
    coverage={symbol: {'observed_bars':len(stamps), 'expected_bars':len(expected),
                       'complete':stamps==expected}
              for symbol,rows in symbols.items()
              for stamps in [{int(b['t']) for b in rows if b.get('available_at',b['t']+tf*60 if retrospective else float('inf'))<=end and at(day,'09:15')<=b['t']<end}]}
    data_status=('COMPLETE' if coverage and all(v['complete'] for v in coverage.values())
                 else 'INCOMPLETE' if any(v['observed_bars'] for v in coverage.values()) else 'NO_DATA')
    payload=json.dumps(symbols,sort_keys=True,allow_nan=False).encode()
    return dict(mode='OFFLINE RESEARCH SIMULATION',strategy_id=strategy_id,version='research-v1',
                approved=False,day=day,data_status=data_status,coverage=coverage,retrospective_availability_assumed=retrospective,
                data_sha256=hashlib.sha256(payload).hexdigest(),config=cfg,
                metrics=portfolio_metrics(engine.trades,cfg['capital']),trades=engine.trades,events=engine.events,
                limitations=['No validation of profitability or source licensing.',
                             'Separate experiment capital; never sum independent runs as a portfolio.',
                             'Lexicographic allocation; OHLC cannot resolve intrabar path.',
                             'Coverage and provider vintages must be audited before comparing performance.'])


def cost_stress(strategy_id, day, symbols, config, *, retrospective=False):
    """Independent replays: re-size and re-fill at each cost level; no best-case selection."""
    runs=[]
    for multiplier in (1.,1.5,2.):
        cfg=deepcopy(config)
        cfg['slippage_bps']*=multiplier
        for key in cfg['fees']:
            if key!='gst_rate': cfg['fees'][key]*=multiplier
        result=replay(strategy_id,day,symbols,cfg,retrospective=retrospective)
        runs.append({'cost_multiplier':multiplier,'report':result})
    return {'mode':'COST SENSITIVITY / SEPARATE PAPER RUNS','runs':runs,
            'assumptions':'All fee bases and brokerage cap scaled; GST rate unchanged. Slippage scaled. No measured spread/queue model. Not historical tax schedules.'}
