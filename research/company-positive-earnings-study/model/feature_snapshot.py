"""Causal feature builder shared by offline training and a read-only website.

Standard library only. It consumes no future labels. Source-vintage and
instrument attestations are separate gates in positive_path_model.row_reasons.
"""
from __future__ import annotations
import math
from datetime import date,datetime,time,timedelta,timezone

IST=timezone(timedelta(hours=5,minutes=30))


def _clock(value):
    result=value if isinstance(value,datetime) else datetime.fromisoformat(str(value).replace('Z','+00:00'))
    if result.tzinfo is None or result.utcoffset() is None:raise ValueError('Timezone-aware decision/availability required')
    return result.astimezone(IST)


def _finite(value):
    if isinstance(value,bool) or not isinstance(value,(int,float)) or not math.isfinite(value):raise ValueError('Finite numeric price/financial value required')
    return float(value)


def _prefix(bars,decision,lag_seconds):
    start=datetime.combine(decision.date(),time(9,15),IST)
    finish=datetime.combine(decision.date(),time(11),IST)
    expected=max(0,min(21,int((decision-timedelta(seconds=lag_seconds)-start).total_seconds()//300)))
    selected=[]
    for original in bars:
        begin=datetime.fromtimestamp(_finite(original['ts']),IST)
        if begin<start or begin>=finish:continue
        end=begin+timedelta(minutes=5)
        if end>decision:continue
        available=end+timedelta(seconds=lag_seconds)
        if original.get('available_at'):
            arrived=_clock(original['available_at'])
            if arrived<end:raise ValueError('Final OHLC bar arrival precedes its close')
            available=max(available,arrived)
        if available>decision:continue
        if begin.second or begin.microsecond or begin.minute%5:raise ValueError('Feature timestamp is not an exact five-minute start')
        o,h,l,c=(_finite(original[k]) for k in ('o','h','l','c'))
        if min(o,h,l,c)<=0 or not l<=min(o,c)<=max(o,c)<=h:raise ValueError('Invalid OHLC feature bar')
        selected.append({**original,'ts':int(begin.timestamp()),'o':o,'h':h,'l':l,'c':c})
    selected.sort(key=lambda b:b['ts'])
    expected_starts=[int((start+timedelta(minutes=5*i)).timestamp()) for i in range(expected)]
    if [b['ts'] for b in selected]!=expected_starts:raise ValueError('Missing, duplicate, nonconsecutive or stale feature prefix')
    if len(selected)<3:raise ValueError('Opening15-minute range not available under registered lag')
    return selected


def build_feature_snapshot(current,previous,bars,decision_at,lag_seconds=0,benchmark_bars=None):
    """Build the same known-prefix features for training and current inference.

    `bars` use ts/o/h/l/c and optional available_at; timestamps denote five-
    minute starts. Extra future bars are ignored before reading their OHLC.
    No completed future11:00 window, volume, target or NumPy is required.
    """
    decision=_clock(decision_at)
    if not isinstance(lag_seconds,(int,float)) or isinstance(lag_seconds,bool) or not math.isfinite(lag_seconds) or not 0<=lag_seconds<=3600:raise ValueError('Invalid registered lag')
    if decision.strftime('%H:%M') not in ('09:30','09:40','10:00','10:30') or decision.second:raise ValueError('Unregistered model landmark')
    for key in ('unit','basis','revenue_metric','profit_metric'):
        if not current.get(key) or current[key]!=previous.get(key):raise ValueError('Incompatible financial definitions')
    ends=[]
    for row in (previous,current):
        first,last=date.fromisoformat(row['start']),date.fromisoformat(row['period_end'])
        if not 70<=(last-first).days+1<=110:raise ValueError('Nonquarter financial interval')
        ends.append(last)
    if date.fromisoformat(current['start'])!=ends[0]+timedelta(days=1) or not 70<=(ends[1]-ends[0]).days<=110:raise ValueError('Financial quarters not adjacent')
    if ends[1]>=decision.date():raise ValueError('Financial reporting quarter not complete before decision')
    r1,r0=(_finite(r['revenue']) for r in (current,previous))
    p1,p0=(_finite(r['net_profit']) for r in (current,previous))
    if not r1>r0>0 or not p1>p0>0:raise ValueError('Not an ordinary positive financial result')
    closed=_prefix(bars,decision,lag_seconds)
    reference=closed[-1]['c'];open_price=closed[0]['o'];opening=closed[:3]
    orh=max(b['h'] for b in opening);orl=min(b['l'] for b in opening)
    features={
        'log_revenue_qoq':math.log(r1/r0),'log_pat_qoq':math.log(p1/p0),
        'pat_margin_change_bps':10000*(p1/r1-p0/r0),
        'return_from_open_log_bps':10000*math.log(reference/open_price),
        'first_15m_return_log_bps':10000*math.log(opening[-1]['c']/open_price),
        'observed_range_log_bps':10000*math.log(max(b['h'] for b in closed)/min(b['l'] for b in closed)),
        'opening_range_position':(reference-orl)/(orh-orl) if orh>orl else 0.,
        'drawdown_from_observed_high_log_bps':10000*math.log(reference/max(b['h'] for b in closed)),
        'latest_close_momentum_log_bps':10000*math.log(reference/closed[-2]['c'])}
    result={'features':features,'reference_price':reference,'prefix_count':len(closed),
        'last_feature_bar_end':(datetime.fromtimestamp(closed[-1]['ts'],IST)+timedelta(minutes=5)).isoformat(),
        'registered_bar_lag_seconds':lag_seconds,'volume_used':False,
        'reference_kind':'last_completed_close_under_registered_bar_lag',
        'feature_variant':'core_financial_opening_price_v1','benchmark_last_feature_bar_end':None}
    if benchmark_bars is not None:
        market=_prefix(benchmark_bars,decision,lag_seconds)
        if [b['ts'] for b in market]!=[b['ts'] for b in closed]:raise ValueError('Benchmark prefix is not aligned with company prefix')
        market_return=10000*math.log(market[-1]['c']/market[0]['o'])
        features.update(market_return_from_open_log_bps=market_return,
            stock_minus_market_return_from_open_log_bps=features['return_from_open_log_bps']-market_return,
            market_first_15m_return_log_bps=10000*math.log(market[2]['c']/market[0]['o']),
            market_observed_range_log_bps=10000*math.log(max(b['h'] for b in market)/min(b['l'] for b in market)))
        result.update(feature_variant='core_financial_opening_price_with_market_context_v1',
            benchmark_last_feature_bar_end=(datetime.fromtimestamp(market[-1]['ts'],IST)+timedelta(minutes=5)).isoformat())
    if not all(math.isfinite(v) for v in features.values()):raise ValueError('Unsupported numerical feature range')
    return result
