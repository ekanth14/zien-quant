"""Join newly collected public index candles to each company's own result event.

These are benchmark controls, never substitutes for the company's own OHLCV.
Public publisher prices are not independently certified by NSE or a broker.
"""
from __future__ import annotations
import csv, hashlib, json, math
from collections import defaultdict, Counter
from datetime import datetime, timedelta, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
IST = timezone(timedelta(hours=5, minutes=30))
LANDMARKS = [570,580,600,630,660]


def build():
    root = HERE / 'public_data'
    provenance = json.loads((root/'download_provenance.json').read_text())
    by_day = defaultdict(dict)
    minute_count = 0
    for info in provenance['files']:
        path = root/info['file']
        raw = path.read_bytes()
        assert hashlib.sha256(raw).hexdigest() == info['local_sha256']
        assert hashlib.sha1(b'blob '+str(len(raw)).encode()+b'\0'+raw).hexdigest() == info['github_blob_sha']
        for row in csv.DictReader(raw.decode().splitlines()):
            ts = int(row['Epoch'])
            stamp = datetime.fromtimestamp(ts, IST)
            text_stamp = datetime.fromisoformat(row['Timestamp']).replace(tzinfo=IST)
            if stamp != text_stamp or stamp.second:
                raise ValueError('Publisher epoch and stated IST timestamp disagree')
            o,h,l,c = [float(row[k]) for k in ['Open','High','Low','Close']]
            if not all(math.isfinite(v) and v>0 for v in [o,h,l,c]) or not l<=min(o,c)<=max(o,c)<=h:
                raise ValueError('Invalid index OHLC')
            key = stamp.date().isoformat()
            minute = stamp.hour*60+stamp.minute
            candle = {'ts':ts,'time':stamp.strftime('%H:%M'),'o':o,'h':h,'l':l,'c':c}
            if minute in by_day[key] and by_day[key][minute] != candle:
                raise ValueError('Conflicting minute in benchmark source')
            by_day[key][minute] = candle
            minute_count += 1
    sessions = {}
    for day, source in sorted(by_day.items()):
        bars=[]
        for minute in range(555,660,5):
            group=[source.get(m) for m in range(minute,minute+5)]
            if any(x is None for x in group):
                continue
            bars.append({'ts':group[0]['ts'],'time':f'{minute//60:02}:{minute%60:02}',
                         'o':group[0]['o'],'h':max(x['h'] for x in group),
                         'l':min(x['l'] for x in group),'c':group[-1]['c'],
                         'source':'technovusin/nifty50-historical-data community benchmark',
                         'volume':None})
        if len(bars)==21:
            sessions[day]={'bars':bars,'volume_usage':'NOT_USED_INDEX_VOLUME_UNVERIFIED',
                'independently_verified':False,
                'source_timestamp_convention':'Epoch UTC; matching text IST; start-stamped 1-minute bars',
                'returns_bps':{f'{m//60:02}:{m%60:02}':10000*math.log(bars[(m-555)//5-1]['c']/bars[0]['o']) for m in LANDMARKS}}
    (root/'benchmark_mornings.json').write_text(json.dumps({'schema_version':'1.0','instrument':'NIFTY50_INDEX',
       'purpose':'Market context; not company return evidence','provider_verified':False,
       'sessions':sessions},indent=2,allow_nan=False)+'\n')
    events=[json.loads(x) for x in (HERE/'local_data'/'positive_events.jsonl').read_text().splitlines()]
    context=[]
    for row in events:
        event=row['event']
        if not event['complete_price_window']:
            continue
        day=event['next_trading_date']
        if day not in sessions:
            continue
        bars=row['candles']
        for minute in LANDMARKS:
            candle=bars[(minute-555)//5-1]
            value=10000*math.log(candle['c']/bars[0]['o'])
            clock=f'{minute//60:02}:{minute%60:02}'
            market=sessions[day]['returns_bps'][clock]
            context.append({'symbol':event['symbol'],'company':event['company'],'session_date':day,
                'decision_time_ist':clock,'financial_class':event['financial_class'],
                'company_open_to_decision_bps':value,'nifty_open_to_decision_bps':market,
                'company_minus_nifty_bps':value-market,
                'measure':'Raw contemporaneous difference; not estimated beta-adjusted alpha',
                'source_quality':'Company archive and community benchmark unverified'})
    with (root/'benchmark_context.csv').open('w',newline='') as f:
        writer=csv.DictWriter(f,fieldnames=list(context[0]) if context else ['symbol'])
        writer.writeheader();writer.writerows(context)
    summary={'downloaded_minute_rows':minute_count,'complete_benchmark_mornings':len(sessions),
        'first_date':min(sessions) if sessions else None,'last_date':max(sessions) if sessions else None,
        'company_events_with_benchmark':len(context)//len(LANDMARKS),'context_rows':len(context),
        'company_directions_at_1100':dict(Counter('UP' if r['event']['return_0915_1100_pct']>0 else 'DOWN' if r['event']['return_0915_1100_pct']<0 else 'FLAT' for r in events if r['event']['complete_price_window'])),
        'scope_definition':'Company has positive financial result; its own next-day chart can rise or fall.',
        'prices_downloaded_online':True,'new_company_price_history_downloaded':False,
        'all_hashes_and_timezones_checked':True,'trained_forecasts':False}
    (root/'benchmark_validation.json').write_text(json.dumps(summary,indent=2)+'\n')
    print(json.dumps(summary,indent=2))


if __name__=='__main__':
    build()
