"""Point-in-time earnings event studies from real archived exchange candles.

This module never fetches prices or invents observations. Prices supplied by the
caller are closed, timestamped archive rows. Results describe a historical cohort;
the watch setup is a separate, conditional research rule and never places orders.
"""
from __future__ import annotations

from datetime import date, datetime, time, timedelta
import math
from statistics import median
from typing import Any

try:
    from .market_schedule import IST, EXCHANGE_HOLIDAYS, get_next_trading_day, is_trading_day
except ImportError:
    from market_schedule import IST, EXCHANGE_HOLIDAYS, get_next_trading_day, is_trading_day

MIN_SAMPLES = 10
PATTERN_THRESHOLD_PCT = 0.10
ROUND_TRIP_COST_BPS = 20.0
MAX_CANDLE_AGE_SECONDS = 600
_CALENDAR_YEARS = {row[0] for row in EXCHANGE_HOLIDAYS}


def _number(value: Any) -> float | None:
    if isinstance(value, bool):
        return None
    try:
        result = float(value)
        return result if math.isfinite(result) else None
    except (ValueError, TypeError, OverflowError):
        return None


def _datetime(value: Any) -> datetime | None:
    if isinstance(value, datetime):
        dt = value
    elif isinstance(value, str):
        try:
            dt = datetime.fromisoformat(value.replace('Z', '+00:00'))
        except ValueError:
            return None
    else:
        return None
    # Guessing the timezone can assign an earnings filing to the wrong session.
    return dt.astimezone(IST) if dt.tzinfo is not None and dt.utcoffset() is not None else None


def reaction_session(announced_at: Any) -> dict[str, Any]:
    """Assign pre-open/after-hours filings, excluding intraday announcements.

    Calendar verification is deliberately limited to configured holiday years.
    Special exchange sessions must be added to the shared calendar explicitly.
    """
    dt = _datetime(announced_at)
    result = {'date': None, 'timing': 'unknown', 'eligible': False,
              'calendar_verified': False, 'reason': 'An exact timezone-aware filing timestamp is required.'}
    if dt is None:
        return result
    verified = dt.year in _CALENDAR_YEARS
    clock = dt.time().replace(tzinfo=None)
    if not is_trading_day(dt.date()):
        target = get_next_trading_day(dt.date())
        timing = 'non_trading_day'
    elif clock < time(9, 15):
        target, timing = dt.date(), 'pre_open'
    elif clock >= time(15, 30):
        target, timing = get_next_trading_day(dt.date() + timedelta(days=1)), 'after_close'
    else:
        return {**result, 'date': dt.date().isoformat(), 'timing': 'intraday',
                'calendar_verified': verified,
                'reason': 'Announced during trading: excluded from the overnight-results opening-pattern study.'}
    verified = verified and target.year in _CALENDAR_YEARS
    return {'date': target.isoformat(), 'timing': timing, 'eligible': verified,
            'calendar_verified': verified,
            'reason': ('Regular-session calendar mapping; special sessions are not inferred.' if verified else
                       'Holiday calendar is not verified for this year; excluded from historical inference.')}


def _event_session(event: dict) -> dict:
    result = reaction_session(event.get('announced_at'))
    if event.get('announcement_time_known') is False:
        return {**result, 'timing': 'unknown_time', 'eligible': False,
                'reason': 'Only the announcement date is known; no opening-session timing is inferred.'}
    return result


def _definitions_match(target: dict, candidate: dict) -> bool:
    current, other = target.get('financials') or {}, candidate.get('financials') or {}
    return all(current.get(field) == other.get(field) for field in
               ('unit', 'revenue_metric', 'profit_metric'))


def _known_at(event: dict) -> datetime | None:
    fields = ('financials_available_at', 'financial_known_at', 'financials_known_at', 'comparison_available_at',
              'known_at', 'revised_at')
    dates = []
    for field in fields:
        if event.get(field):
            dt = _datetime(event[field])
            if dt is None:
                return None
            dates.append(dt)
    return max(dates) if dates else None


def _growth(event: dict) -> tuple[float, float] | None:
    if event.get('financial_status') not in (None, 'available'):
        return None
    values = event.get('growth') or {}
    revenue = _number(values.get('revenue_qoq'))
    profit = _number(values.get('profit_qoq'))
    # Reject contradictory hand-imported growth too: conventional percentage
    # growth is undefined for a zero/negative comparison base.
    previous = event.get('previous') or {}
    for field in ('revenue', 'net_profit'):
        base = _number(previous.get(field))
        if base is not None and base <= 0:
            return None
    # The ingestion layer leaves non-comparable growth null.
    return (revenue, profit) if revenue is not None and profit is not None else None


def _candle(raw: Any, now: datetime) -> dict | None:
    if not isinstance(raw, dict) or raw.get('closed') is False or raw.get('stale'):
        return None
    ts = _number(raw.get('t', raw.get('ts', raw.get('time'))))
    tf = _number(raw.get('timeframe', 5))
    if ts is None or tf not in (5, 1440) or ts <= 0:
        return None
    try:
        dt = datetime.fromtimestamp(ts, IST)
    except (ValueError, OverflowError, OSError):
        return None
    # Daily archive timestamps may be midnight or session open; a daily bar is
    # usable only after the actual session close, never after midnight + 24h.
    end = datetime.combine(dt.date(), time(15, 30), IST) if tf == 1440 else dt + timedelta(minutes=5)
    if end > now:
        return None
    values = {key: _number(raw.get(key, raw.get(long))) for key, long in
              (('o', 'open'), ('h', 'high'), ('l', 'low'), ('c', 'close'), ('v', 'volume'))}
    if any(values[key] is None or values[key] <= 0 for key in ('o', 'h', 'l', 'c')):
        return None
    if values['h'] < max(values['o'], values['c'], values['l']) or values['l'] > min(values['o'], values['c']):
        return None
    if values['v'] is None or values['v'] < 0:
        return None
    return {**values, 't': int(ts), 'dt': dt, 'timeframe': int(tf), 'end': end, 'source': str(raw.get('source') or ''),
            'adjustments_verified': raw.get('adjustments_verified') is True,
            'corporate_action': raw.get('corporate_action') is True}


def _index_candles(rows: Any, now: datetime) -> dict:
    result: dict = {'intraday': {}, 'daily': {}}
    if isinstance(rows, dict):
        # Accept service adapters which split daily and intraday lists.
        rows = list(rows.get('intraday', [])) + list(rows.get('daily', []))
    for raw in rows if isinstance(rows, (list, tuple)) else []:
        row = _candle(raw, now)
        if row is None:
            continue
        d = row['dt'].date().isoformat()
        if row['timeframe'] == 1440:
            result['daily'][d] = row
        elif row['dt'].second == 0 and row['dt'].microsecond == 0:
            result['intraday'].setdefault(d, {})[row['dt'].strftime('%H:%M')] = row
    return result


def _pct(last: float, first: float) -> float:
    return (last / first - 1.0) * 100.0


def _previous_day(day: date) -> date:
    previous = day - timedelta(days=1)
    while not is_trading_day(previous):
        previous -= timedelta(days=1)
    return previous


def _observation(event: dict, index: dict, cutoff: datetime) -> tuple[dict | None, str | None]:
    session = _event_session(event)
    if not session['eligible']:
        return None, 'intraday_or_unverified_calendar'
    d = date.fromisoformat(session['date'])
    if datetime.combine(d, time(15, 30), IST) > cutoff:
        return None, 'outcome_not_complete_before_target'
    day = index['intraday'].get(session['date'], {})
    labels = [f'{(9 * 60 + 15 + offset) // 60:02}:{(9 * 60 + 15 + offset) % 60:02}'
              for offset in range(0, 45, 5)]
    if any(label not in day for label in labels):
        return None, 'missing_intraday_windows'
    rows = [day[label] for label in labels]
    if any(row['corporate_action'] for row in rows) or (index['daily'].get(session['date']) or {}).get('corporate_action'):
        return None, 'corporate_action_confound'
    if any(rows[i + 1]['t'] - rows[i]['t'] != 300 for i in range(8)):
        return None, 'missing_intraday_windows'
    previous = _previous_day(d).isoformat()
    sources = {row['source'] for row in rows}
    if len(sources) != 1:
        return None, 'mixed_price_sources'
    candidates = (index['intraday'].get(previous, {}).get('15:25'), index['daily'].get(previous))
    prior = next((row for row in candidates if row and row['source'] == rows[0]['source']), None)
    if not prior:
        return None, 'missing_prior_close'
    initial = _pct(rows[2]['c'], rows[0]['o'])
    pullback = _pct(rows[4]['c'], rows[2]['c'])
    recovery = _pct(rows[8]['c'], rows[4]['c'])
    close = next((row for row in (day.get('15:25'), index['daily'].get(session['date']))
                  if row and row['source'] == rows[0]['source']), None)
    return {'id': event.get('id'), 'symbol': event.get('symbol'),
            'announced_at': event.get('announced_at'), 'session_date': session['date'],
            'gap_pct': _pct(rows[0]['o'], prior['c']) if rows[0]['adjustments_verified'] and prior['adjustments_verified'] else None,
            'initial_rise_pct': initial, 'pullback_pct': pullback, 'recovery_pct': recovery,
            'open_to_1000_pct': _pct(rows[8]['c'], rows[0]['o']),
            'close_return_pct': _pct(close['c'], prior['c']) if close and close['adjustments_verified'] and prior['adjustments_verified'] else None,
            'pattern_observed': (initial >= PATTERN_THRESHOLD_PCT and
                                 pullback <= -PATTERN_THRESHOLD_PCT and
                                 recovery >= PATTERN_THRESHOLD_PCT)}, None


def _daily_observation(event: dict, index: dict, cutoff: datetime) -> dict | None:
    session = _event_session(event)
    if not session['eligible']:
        return None
    d = date.fromisoformat(session['date'])
    if datetime.combine(d, time(15, 30), IST) > cutoff:
        return None
    day = index['intraday'].get(session['date'], {})
    previous = _previous_day(d).isoformat()
    prior_candidates = (index['daily'].get(previous), index['intraday'].get(previous, {}).get('15:25'))
    daily = index['daily'].get(session['date'])
    first = daily or day.get('09:15')
    last = daily or day.get('15:25')
    if not first or not last or first['source'] != last['source'] or first['corporate_action'] or last['corporate_action']:
        return None
    prior = next((row for row in prior_candidates if row and row['source'] == first['source']), None)
    if not prior:
        return None
    return {'id': event.get('id'), 'symbol': event.get('symbol'),
            'session_date': session['date'],
            'gap_pct': _pct(first['o'], prior['c']) if first['adjustments_verified'] and prior['adjustments_verified'] else None,
            'close_return_pct': _pct(last['c'], prior['c']) if last['adjustments_verified'] and prior['adjustments_verified'] else None,
            'open_to_close_pct': _pct(last['c'], first['o'])}


def _daily_summary(observations: list[dict]) -> dict:
    n = len(observations)
    returns = [row['close_return_pct'] for row in observations if row['close_return_pct'] is not None]
    successes = sum(value > 0 for value in returns)
    low, high = wilson_interval(successes, len(returns))
    within = [row['open_to_close_pct'] for row in observations]
    within_low, within_high = wilson_interval(sum(value > 0 for value in within), n)
    return {'sample_size': n, 'close_return_sample_size': len(returns),
            'up_probability_pct': successes * 100 / len(returns) if returns else None,
            'up_wilson_low_pct': low, 'up_wilson_high_pct': high,
            'median_close_return_pct': median(returns) if returns else None,
            'median_open_to_close_pct': median(within) if n else None,
            'open_to_close_up_probability_pct': 100 * sum(value > 0 for value in within) / n if n else None,
            'open_to_close_wilson_low_pct': within_low, 'open_to_close_wilson_high_pct': within_high}


def wilson_interval(successes: int, count: int) -> tuple[float | None, float | None]:
    """95% Wilson score interval; percentage points, not predictive certainty."""
    if count <= 0:
        return None, None
    z = 1.959963984540054
    p = successes / count
    scale = 1 + z * z / count
    center = (p + z * z / (2 * count)) / scale
    half = z * math.sqrt(p * (1 - p) / count + z * z / (4 * count * count)) / scale
    return 100 * max(0.0, center - half), 100 * min(1.0, center + half)


def _summary(observations: list[dict]) -> dict:
    n = len(observations)
    returns = [row['open_to_1000_pct'] for row in observations]
    successes = sum(value > 0 for value in returns)
    patterns = sum(row['pattern_observed'] for row in observations)
    low, high = wilson_interval(successes, n)
    return {'sample_size': n, 'up_probability_pct': 100 * successes / n if n else None,
            'up_wilson_low_pct': low, 'up_wilson_high_pct': high,
            'pattern_probability_pct': 100 * patterns / n if n else None,
            'median_open_to_1000_pct': median(returns) if n else None}


def _setup(event: dict, index: dict, session: dict, now: datetime, forecast: dict,
           baseline: dict, n: int, eligible: bool) -> dict:
    result = {'action': 'INSUFFICIENT_DATA' if not eligible or n < MIN_SAMPLES else 'WAIT',
              'reasons': [], 'levels': {'entry': None, 'stop': None, 'target': None,
                                        'vwap': None, 'opening_high': None, 'opening_low': None},
              'cost_bps': ROUND_TRIP_COST_BPS, 'orders_placed': False}
    if result['action'] == 'INSUFFICIENT_DATA':
        result['reasons'] = [f'At least {MIN_SAMPLES} comparable, point-in-time observations with complete intraday candles are required.']
        return result
    if now.date().isoformat() != session['date'] or not time(9, 40) <= now.time().replace(tzinfo=None) < time(10, 0):
        result['reasons'] = ['Watch triggers are evaluated only on the reaction session from 09:40 to 10:00 IST.']
        return result
    day = index['intraday'].get(session['date'], {})
    opening = [day.get(label) for label in ('09:15', '09:20', '09:25')]
    if any(row is None for row in opening):
        result['reasons'] = ['The complete 09:15–09:30 opening range is unavailable.']
        return result
    rows = sorted([row for row in day.values() if time(9, 15) <= row['dt'].time().replace(tzinfo=None) < time(10, 0)], key=lambda row: row['t'])
    if any(row['corporate_action'] for row in rows):
        result['reasons'] = ['A split or dividend on this session confounds the earnings setup; wait for a separately reviewed event.']
        return result
    latest = rows[-1] if rows else None
    if not latest or latest['dt'].time().replace(tzinfo=None) < time(9, 40) or (now - latest['end']).total_seconds() > MAX_CANDLE_AGE_SECONDS:
        result['reasons'] = ['A fresh closed 5-minute candle beginning at or after 09:40 is required.']
        return result
    expected_count = int((latest['t'] - opening[0]['t']) / 300) + 1
    if len(rows) != expected_count or any(b['t'] - a['t'] != 300 for a, b in zip(rows, rows[1:])):
        result['reasons'] = ['Intraday gaps prevent a verified VWAP and trigger.']
        return result
    volume = sum(row['v'] for row in rows)
    if volume <= 0:
        result['reasons'] = ['Verified volume is required for VWAP confirmation.']
        return result
    vwap = sum((row['h'] + row['l'] + row['c']) / 3 * row['v'] for row in rows) / volume
    high = max(row['h'] for row in opening)
    low = min(row['l'] for row in opening)
    result['levels'].update({'vwap': vwap, 'opening_high': high, 'opening_low': low})
    probability = forecast['up_probability_pct']
    base_probability = baseline['up_probability_pct']
    med = forecast['median_open_to_1000_pct']
    cost_pct = ROUND_TRIP_COST_BPS / 100
    bull = (forecast['up_wilson_low_pct'] > 50 and probability - base_probability >= 10
            and med > cost_pct)
    bear = (forecast['up_wilson_high_pct'] < 50 and base_probability - probability >= 10
            and med < -cost_pct)
    if bull and latest['c'] > max(vwap, high):
        entry, stop = latest['c'], min(row['l'] for row in rows)
        action, direction = 'BUY_WATCH', 1
    elif bear and latest['c'] < min(vwap, low):
        entry, stop = latest['c'], max(row['h'] for row in rows)
        action, direction = 'SELL_WATCH', -1
    else:
        result['reasons'] = ['Historical direction must have a 95% Wilson bound beyond 50%, a ≥10-point edge over baseline, and a fresh VWAP/opening-range confirmation.']
        return result
    risk = abs(entry - stop)
    if risk <= 0 or risk / entry > .05:
        result['reasons'] = ['The observed stop distance is invalid or greater than 5%; wait for a tighter structure.']
        return result
    result['action'] = action
    result['levels'].update({'entry': entry, 'stop': stop, 'target': entry + direction * 1.5 * risk})
    result['reasons'] = ['Historical cohort supports this direction relative to its baseline.',
                         'Fresh closed candle confirms VWAP and the opening range.',
                         'Entry is the last closed price; stop is the observed session extreme; target is an illustrative 1.5R rule, not a price forecast.',
                         'No live execution, fill probability or strategy win rate has been measured.']
    return result


def analyze_event(event: dict, history: list[dict], candles_by_symbol: dict,
                  as_of: datetime | str | None = None) -> dict[str, Any]:
    """Analyze same-company/same-basis results with a point-in-time cutoff.

    All percentages are percentage points. A cohort's historical frequencies are
    descriptive; no LLM, optimized thresholds or guaranteed forecasts are used.
    """
    now = _datetime(as_of) if as_of is not None else datetime.now(IST)
    now = now or datetime.now(IST)
    event = event if isinstance(event, dict) else {}
    announcement = _datetime(event.get('announced_at'))
    session = _event_session(event)
    growth = _growth(event)
    excluded: dict[str, int] = {}
    def exclude(reason: str) -> None:
        excluded[reason] = excluded.get(reason, 0) + 1
    symbol = str(event.get('symbol') or '').upper()
    basis = event.get('basis')
    known = _known_at(event)
    target_ready = (announcement is not None and announcement <= now and growth is not None
                    and basis in ('consolidated', 'standalone') and known is not None and known <= now
                    and session['eligible'])
    # A comparison fetched after the reaction session began cannot establish what
    # investors knew before the opening. Suppress historical predictions for it.
    decision_cutoff = announcement
    if session['date']:
        start = datetime.combine(date.fromisoformat(session['date']), time(9, 15), IST)
        if known is not None and known > start:
            target_ready = False
    index = _index_candles((candles_by_symbol or {}).get(symbol, []), now)
    eligible: list[dict] = []
    unique: dict[tuple, dict] = {}
    considered = 0
    for candidate in history if isinstance(history, (list, tuple)) else []:
        if not isinstance(candidate, dict) or str(candidate.get('symbol') or '').upper() != symbol:
            continue
        considered += 1
        if candidate.get('basis') != basis:
            exclude('different_reporting_basis'); continue
        if (candidate.get('id') == event.get('id') and event.get('id') is not None) or candidate.get('period_end') == event.get('period_end'):
            exclude('target_period'); continue
        if not candidate.get('period_end') or not event.get('period_end') or str(candidate['period_end']) >= str(event['period_end']):
            exclude('not_an_earlier_quarter'); continue
        announced = _datetime(candidate.get('announced_at'))
        available = _known_at(candidate)
        if decision_cutoff is None or announced is None or available is None or announced >= decision_cutoff or available > decision_cutoff:
            exclude('not_known_before_target'); continue
        candidate_session = _event_session(candidate)
        if candidate_session['date']:
            candidate_open = datetime.combine(date.fromisoformat(candidate_session['date']), time(9, 15), IST)
            if available > candidate_open:
                exclude('financials_not_known_before_historical_open'); continue
        if not _definitions_match(event, candidate):
            exclude('different_financial_definition'); continue
        if _growth(candidate) is None:
            exclude('incomparable_financials'); continue
        key = (symbol, basis, candidate['period_end'])
        previous = unique.get(key)
        if previous is None or _known_at(previous) < available:
            unique[key] = candidate
    baseline_observations: list[dict] = []
    matched: list[dict] = []
    daily_baseline: list[dict] = []
    daily_matched: list[dict] = []
    for candidate in unique.values():
        candidate_growth = _growth(candidate)
        similar = growth is not None and all(abs(value - reference) <= max(5.0, abs(reference) * .25)
                                            for value, reference in zip(candidate_growth, growth))
        daily = _daily_observation(candidate, index, decision_cutoff or now)
        if daily is not None:
            daily_baseline.append(daily)
            if similar:
                daily_matched.append(daily)
        observation, reason = _observation(candidate, index, decision_cutoff or now)
        if observation is None:
            exclude(reason or 'missing_candles'); continue
        baseline_observations.append(observation)
        eligible.append(candidate)
        if similar:
            matched.append(observation)
    matched.sort(key=lambda row: row['session_date'])
    stats = _summary(matched)
    baseline = _summary(baseline_observations)
    pattern_count = sum(row['pattern_observed'] for row in matched)
    p_low, p_high = wilson_interval(pattern_count, len(matched))
    returns = sorted(row['open_to_1000_pct'] for row in matched)
    def quantile(p: float) -> float | None:
        if not returns:
            return None
        at = (len(returns) - 1) * p
        lo, hi = math.floor(at), math.ceil(at)
        return returns[lo] + (returns[hi] - returns[lo]) * (at - lo)
    sufficient = target_ready and len(matched) >= MIN_SAMPLES
    forecast = {**stats, 'direction': ('insufficient_data' if not sufficient else
                'upward_tendency' if stats['median_open_to_1000_pct'] > 0 else
                'downward_tendency' if stats['median_open_to_1000_pct'] < 0 else 'mixed'),
                'range_low_pct': quantile(.25), 'range_high_pct': quantile(.75),
                'confidence_label': ('Limited historical evidence' if not sufficient else 'Historical cohort; not a validated predictive model'),
                'validated_out_of_sample': False}
    windows = []
    for label, start_time, end_time, key in (
        ('Initial move', '09:15', '09:30', 'initial_rise_pct'),
        ('Pullback window', '09:30', '09:40', 'pullback_pct'),
        ('Recovery window', '09:40', '10:00', 'recovery_pct')):
        windows.append({'label': label, 'start': start_time, 'end': end_time,
                        'median_return_pct': median(row[key] for row in matched) if matched else None})
    notes = []
    if not target_ready:
        notes.append('A comparable quarterly filing known before its reaction-session open is required; late-fetched or revised figures cannot establish a historical point-in-time signal.')
    if len(matched) < MIN_SAMPLES:
        notes.append(f'Only {len(matched)} comparable observations; no buy/sell inference before {MIN_SAMPLES}.')
    if session['timing'] == 'unknown_time':
        notes.append('The filing has a date without a verified announcement time; intraday or overnight reaction timing is unavailable.')
    if session['timing'] == 'intraday':
        notes.append('Intraday results require a separate event-window study; they are not assigned an overnight opening pattern.')
    return {'status': 'ready' if sufficient else 'not_applicable' if not session['eligible'] else 'insufficient_data',
            'as_of': now.isoformat(), 'reaction_session': session, 'sample_size': len(matched),
            'minimum_samples': MIN_SAMPLES, 'matched_samples': matched,
            'baseline': baseline,
            'daily_study': {**_daily_summary(daily_matched), 'matched_samples': sorted(daily_matched, key=lambda row: row['session_date']),
                            'baseline': _daily_summary(daily_baseline),
                            'note': 'Separate daily open-to-close evidence. Prior-close returns are shown only when adjustment basis is verified; splits/dividends are excluded. Daily bars cannot test intraday timing or authorize a watch trigger.'},
            'pattern': {'label': 'Rise → pullback → recovery hypothesis', 'observed_count': pattern_count,
                        'probability_pct': 100 * pattern_count / len(matched) if matched else None,
                        'wilson_low_pct': p_low, 'wilson_high_pct': p_high, 'windows': windows,
                        'minimum_move_pct': PATTERN_THRESHOLD_PCT},
            'forecast': forecast,
            'setup': _setup(event, index, session, now, forecast, baseline, len(matched), sufficient),
            'coverage': {'events_considered': considered, 'eligible_events': len(unique),
                         'events_with_complete_candles': len(baseline_observations), 'excluded': excluded,
                         'first_session': matched[0]['session_date'] if matched else None,
                         'last_session': matched[-1]['session_date'] if matched else None},
            'methodology': {'cohort': 'Same company, reporting basis and comparable quarterly revenue/profit QoQ growth.',
                            'similarity': 'Both growth figures within max(5 percentage points, 25% of the target absolute growth).',
                            'cutoff': 'Each historical financial snapshot must be known before its own reaction-session open; completed outcomes must predate the target announcement. Late revisions are excluded.',
                            'windows': 'Closed 5-minute bars: [09:15,09:30), [09:30,09:40), [09:40,10:00) IST. No interpolation.',
                            'outcome': 'Open-to-10:00 return; cross-session gap/close returns require verified adjustment basis and the exact preceding regular-session close. Known split/dividend reaction sessions are excluded.',
                            'interval': '95% Wilson score interval; observations are not assumed to prove future causation.',
                            'costs': 'Gross cohort returns; watch-rule sensitivity assumes 20 bps round-trip costs. Actual charges/slippage vary.',
                            'limitations': 'No out-of-sample validation, execution backtest, market/sector adjustment, consensus-surprise data or special-session inference. Correlated market regimes, survivorship and corporate actions may distort raw returns.'},
            'notes': notes}
