"""Resumable official FYERS candle collection, with immutable source snapshots.

No orders. Existing website catalogue only. Empty responses are not coverage.
"""
import argparse
import hashlib
import json
import sqlite3
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from marketscope.env_loader import load_dotenv
load_dotenv()
from marketscope.fyers_history import get_fyers_history_provider
from marketscope.stock_database import get_stock_database
from marketscope.results_history import valid_candle
from marketscope.candle_archive import get_candle_archive


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--symbols',default='TCS,ASIANPAINT,TRENT,COALINDIA')
    parser.add_argument('--all-companies',action='store_true')
    parser.add_argument('--from-date',default='2023-05-06')
    parser.add_argument('--to-date',default=date.today().isoformat())
    parser.add_argument('--interval',choices=['1','3','5','15','D'],default='5')
    parser.add_argument('--max-requests',type=int,default=20)
    args=parser.parse_args()
    start,end=date.fromisoformat(args.from_date),date.fromisoformat(args.to_date)
    if start<date(2023,5,6) or start>end or end>date.today() or not 1<=args.max_requests<=10000:
        parser.error('Invalid dates/request budget')
    catalogue=set(get_stock_database().get_all_symbols())
    symbols=sorted(catalogue if args.all_companies else set(args.symbols.upper().split(',')))
    if not set(symbols)<=catalogue: parser.error('Symbols must be in the website catalogue')
    provider=get_fyers_history_provider(); app,token=provider._credentials()
    root=Path('work/fyers-datasets');root.mkdir(parents=True,exist_ok=True)
    archive=get_candle_archive(); requests=0
    with sqlite3.connect(root/'collection.sqlite') as ledger:
        ledger.execute('CREATE TABLE IF NOT EXISTS jobs(id TEXT PRIMARY KEY,status TEXT,payload TEXT)')
        for symbol in symbols:
            matches=provider.search_instruments(symbol)['instruments']
            instrument=next((r['symbol'] for r in matches if r['short_symbol']==symbol and r['kind']=='equity'),None)
            if not instrument:
                print(symbol,'no verified FYERS equity mapping',flush=True);continue
            cursor=start
            while cursor<=end and requests<args.max_requests:
                last=min(end,cursor+timedelta(days=29)); key=f'{instrument}|{args.interval}|{cursor}|{last}'
                old=ledger.execute('SELECT status FROM jobs WHERE id=?',(key,)).fetchone()
                if old and old[0]=='received': cursor=last+timedelta(days=1);continue
                stamp=datetime.now(timezone.utc).isoformat();requests+=1
                params=urllib.parse.urlencode(dict(symbol=instrument,resolution=args.interval,date_format='1',range_from=cursor.isoformat(),range_to=last.isoformat(),cont_flag='1'))
                req=urllib.request.Request('https://api-t1.fyers.in/data/history?'+params,headers={'Authorization':f'{app}:{token}','Accept':'application/json'})
                audit=dict(instrument=instrument,interval=args.interval,start=cursor.isoformat(),end=last.isoformat(),retrieved_at=stamp,
                           source='FYERS history',availability='Retrospective retrieval; not historical arrival evidence')
                try:
                    with urllib.request.urlopen(req,timeout=20) as response: raw=response.read(8000001)
                    if len(raw)>8000000:raise ValueError('size')
                    payload=json.loads(raw)
                    if payload.get('s')!='ok' or not isinstance(payload.get('candles'),list):raise ValueError('provider')
                    tf=1440 if args.interval=='D' else int(args.interval)
                    rows=[]
                    for values in payload['candles']:
                        if not isinstance(values,list) or len(values)<6:raise ValueError('schema')
                        row=valid_candle(dict(zip(('t','o','h','l','c','v'),values[:6])),tf)
                        if row is None:raise ValueError('invalid candle')
                        if cursor<=datetime.fromtimestamp(row['t'],timezone(timedelta(hours=5,minutes=30))).date()<=last:rows.append(row)
                    digest=hashlib.sha256(raw).hexdigest();path=root/(digest+'.json')
                    if not path.exists():
                        with path.open('xb') as stream:stream.write(raw)
                    audit.update(count=len(rows),sha256=digest,snapshot=str(path),status='received' if rows else 'empty_unverified')
                    archive.archive_closed_candles(symbol,tf,rows,source='FYERS')
                except urllib.error.HTTPError as exc:
                    audit.update(status='blocked',error=f'FYERS HTTP {exc.code}')
                except Exception as exc:
                    audit.update(status='blocked',error='Network or data validation failure: '+type(exc).__name__)
                ledger.execute('INSERT OR REPLACE INTO jobs VALUES(?,?,?)',(key,audit['status'],json.dumps(audit)));ledger.commit()
                print(symbol,cursor,last,audit['status'],audit.get('count',0),audit.get('error',''),flush=True)
                if audit['status']=='blocked':return
                cursor=last+timedelta(days=1);time.sleep(.4)
            if requests>=args.max_requests:break
    print('Request budget completed; rerun identical arguments to resume. Received does not certify session completeness.')


if __name__=='__main__':main()
