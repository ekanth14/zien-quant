#!/usr/bin/env python3
"""Resumable research-price collection. No trading or account-write operations.

CSV/CSV.GZ import is standard-library only. Parquet requires optional pyarrow.
All normalized epochs are candle START times. No prices are interpolated.
"""
from __future__ import annotations
import argparse, csv, gzip, hashlib, json, math, os, re, sqlite3, sys, time
import urllib.error, urllib.parse, urllib.request
from datetime import datetime, date, time as daytime, timedelta, timezone
from pathlib import Path

IST=timezone(timedelta(hours=5,minutes=30))
UTC=timezone.utc
SCHEMA='''
CREATE TABLE IF NOT EXISTS requests(symbol TEXT,day TEXT,start_ts INTEGER,end_ts INTEGER,
 PRIMARY KEY(symbol,day));
CREATE TABLE IF NOT EXISTS sources(id TEXT PRIMARY KEY,label TEXT,path TEXT,sha256 TEXT,
 acquired_at TEXT,interval_minutes INTEGER,timezone_convention TEXT,bar_stamp TEXT,
 provenance TEXT);
CREATE TABLE IF NOT EXISTS bars(symbol TEXT,ts INTEGER,minutes INTEGER,o REAL,h REAL,l REAL,
 c REAL,v REAL,source_id TEXT,PRIMARY KEY(symbol,ts,minutes,source_id));
CREATE TABLE IF NOT EXISTS conflicts(symbol TEXT,ts INTEGER,minutes INTEGER,source_a TEXT,
 source_b TEXT,kind TEXT,UNIQUE(symbol,ts,minutes,source_a,source_b,kind));
CREATE TABLE IF NOT EXISTS import_jobs(id TEXT PRIMARY KEY,status TEXT,rows_read INTEGER,
 accepted INTEGER,rejected INTEGER,error TEXT,updated_at TEXT);
CREATE TABLE IF NOT EXISTS api_attempts(ts REAL,endpoint TEXT);
CREATE TABLE IF NOT EXISTS api_jobs(symbol TEXT,day TEXT,status TEXT,attempts INTEGER,
 last_error TEXT,updated_at TEXT,PRIMARY KEY(symbol,day));
CREATE INDEX IF NOT EXISTS bars_lookup ON bars(symbol,ts,minutes);
'''

def now_iso(): return datetime.now(UTC).isoformat()
def sha256(path):
    digest=hashlib.sha256()
    with Path(path).open('rb') as stream:
        for block in iter(lambda:stream.read(1024*1024),b''): digest.update(block)
    return digest.hexdigest()
def open_db(path):
    path=Path(path);path.parent.mkdir(parents=True,exist_ok=True)
    con=sqlite3.connect(path);con.row_factory=sqlite3.Row;con.executescript(SCHEMA)
    return con
def symbol(value):
    value=str(value or '').strip().upper()
    if not re.fullmatch(r'[A-Z0-9][A-Z0-9&_.-]{0,29}',value): raise ValueError('Invalid symbol identity')
    return value
def numeric(value):
    if isinstance(value,bool): raise ValueError('Boolean OHLCV value')
    result=float(str(value).replace(',','').strip())
    if not math.isfinite(result): raise ValueError('Non-finite OHLCV value')
    return result
def parse_timestamp(value, convention=None):
    if isinstance(value,datetime): parsed=value
    else:
        value=str(value).strip()
        if re.fullmatch(r'\d{10}(?:\.\d+)?',value): return int(float(value))
        if re.fullmatch(r'\d{13}',value): return int(value)//1000
        parsed=datetime.fromisoformat(value.replace('Z','+00:00'))
    if parsed.tzinfo is None:
        if convention not in ('UTC','IST'): raise ValueError('Naive timestamp requires explicit UTC or IST convention')
        parsed=parsed.replace(tzinfo=UTC if convention=='UTC' else IST)
    return int(parsed.timestamp())
def validate_bar(sym,ts,minutes,o,h,l,c,v):
    if minutes not in (1,5) or ts%(60*minutes): raise ValueError('Unsupported interval or off-grid candle timestamp')
    o,h,l,c=tuple(numeric(x) for x in (o,h,l,c))
    v=None if v is None or str(v).strip()=='' else numeric(v)
    if min(o,h,l,c)<=0 or not l<=min(o,c)<=max(o,c)<=h or (v is not None and v<0): raise ValueError('Invalid OHLCV range')
    return (symbol(sym),int(ts),minutes,o,h,l,c,v)
def requested(con):
    return {(r['symbol'],r['day']):(r['start_ts'],r['end_ts']) for r in con.execute('SELECT * FROM requests')}
def init_requests(con,path,positive_only=False):
    count=0
    with Path(path).open(newline='') as stream:
        for row in csv.DictReader(stream):
            if positive_only and row.get('financial_class')!='POSITIVE': continue
            if str(row.get('continuous_target_window','')).lower()!='true': continue
            announced=parse_timestamp(row['announced_at']);announced_day=datetime.fromtimestamp(announced,IST).date()
            day=date.fromisoformat(row['next_trading_date'])
            if announced_day<date(2023,5,6) or day<=announced_day: continue
            begin=datetime.combine(day,daytime(9,15),IST);finish=datetime.combine(day,daytime(11),IST)
            if finish>datetime.now(IST): continue
            count+=con.execute('INSERT OR IGNORE INTO requests VALUES(?,?,?,?)',
                (symbol(row['symbol']),str(day),int(begin.timestamp()),int(finish.timestamp()))).rowcount
    con.commit();return count
def in_scope(ledger,sym,ts):
    key=(sym,datetime.fromtimestamp(ts,IST).date().isoformat())
    bounds=ledger.get(key)
    return bool(bounds and bounds[0]<=ts<bounds[1])
def normal_keys(row): return {re.sub(r'[ _-]','',str(k)).lower():v for k,v in row.items()}
def normalize_row(row,minutes,convention,stamp,fixed_symbol=None):
    r=normal_keys(row);sym=symbol(fixed_symbol or r.get('symbol') or r.get('ticker'))
    value=r.get('timestamp',r.get('datetime',r.get('date',r.get('time',r.get('ts')))))
    if 'date' in r and 'time' in r:
        day=str(r['date']);day=datetime.strptime(day,'%Y%m%d').date().isoformat() if re.fullmatch(r'\d{8}',day) else day
        value=day+'T'+str(r['time'])
    ts=parse_timestamp(value,convention)
    if stamp=='end': ts-=minutes*60
    if stamp not in ('start','end'): raise ValueError('Explicit candle start/end stamping is required')
    values=[r.get(long,r.get(short)) for long,short in [('open','o'),('high','h'),('low','l'),('close','c'),('volume','v')]]
    return validate_bar(sym,ts,minutes,*values)
def iter_rows(path,ledger=None):
    path=Path(path)
    if path.suffix.lower()=='.parquet':
        try:
            import pyarrow.dataset as ds
            import pyarrow as pa
        except ImportError: raise RuntimeError('Parquet import requires optional pyarrow; no data imported') from None
        dataset=ds.dataset(path,format='parquet');predicate=None
        if ledger and 'symbol' in dataset.schema.names and 'timestamp' in dataset.schema.names:
            bounds=list(ledger.values());low=min(b[0] for b in bounds);high=max(b[1] for b in bounds)
            predicate=ds.field('symbol').isin(sorted({key[0] for key in ledger}))
            field_type=dataset.schema.field('timestamp').type
            if pa.types.is_timestamp(field_type) and field_type.tz:
                predicate=predicate & (ds.field('timestamp')>=pa.scalar(datetime.fromtimestamp(low,UTC),type=field_type)) & (ds.field('timestamp')<pa.scalar(datetime.fromtimestamp(high,UTC),type=field_type))
        for batch in dataset.scanner(filter=predicate,batch_size=32768).to_batches(): yield from batch.to_pylist()
    else:
        opener=gzip.open if path.suffix.lower()=='.gz' else open
        with opener(path,'rt',newline='',encoding='utf-8-sig') as stream: yield from csv.DictReader(stream)
def store_bar(con,bar,source_id):
    sym,ts,minutes,*values=bar
    existing=con.execute('SELECT * FROM bars WHERE symbol=? AND ts=? AND minutes=?',(sym,ts,minutes)).fetchall()
    for other in existing:
        previous=tuple(other[k] for k in ('o','h','l','c','v'))
        if previous!=tuple(values):
            con.execute('INSERT OR IGNORE INTO conflicts VALUES(?,?,?,?,?,?)',
                (sym,ts,minutes,other['source_id'],source_id,'WITHIN_SOURCE' if other['source_id']==source_id else 'CROSS_SOURCE'))
            if other['source_id']==source_id: return False
    return bool(con.execute('INSERT OR IGNORE INTO bars VALUES(?,?,?,?,?,?,?,?,?)',(*bar,source_id)).rowcount)
def aggregate_five(rows):
    rows=sorted(rows,key=lambda x:x[1])
    if len(rows)!=5 or len({r[0] for r in rows})!=1 or any(r[2]!=1 for r in rows): return None
    if [r[1] for r in rows]!=[rows[0][1]+60*i for i in range(5)]: return None
    # NSE target five-minute blocks are aligned to 09:15, which is epoch%300=0.
    if rows[0][1]%300: return None
    result=(rows[0][0],rows[0][1],5,rows[0][3],max(r[4] for r in rows),min(r[5] for r in rows),rows[-1][6],None if any(r[7] is None for r in rows) else sum(r[7] for r in rows))
    return validate_bar(*result)
def resample_source(con,source_id):
    total=0
    for req in con.execute('SELECT * FROM requests').fetchall():
        rows=con.execute('SELECT symbol,ts,minutes,o,h,l,c,v FROM bars WHERE symbol=? AND source_id=? AND minutes=1 AND ts>=? AND ts<? ORDER BY ts',
            (req['symbol'],source_id,req['start_ts'],req['end_ts'])).fetchall()
        by_start={r['ts']:tuple(r) for r in rows}
        for ts in range(req['start_ts'],req['end_ts'],300):
            minute_rows=[by_start[t] for t in range(ts,ts+300,60) if t in by_start]
            try: bar=aggregate_five(minute_rows)
            except (ValueError,OverflowError): bar=None
            if bar:
                conflict=con.execute('SELECT 1 FROM conflicts WHERE symbol=? AND minutes=1 AND ts>=? AND ts<? AND (source_a=? OR source_b=?) AND kind=\'WITHIN_SOURCE\' LIMIT 1',
                    (req['symbol'],ts,ts+300,source_id,source_id)).fetchone()
                if not conflict: total+=store_bar(con,bar,source_id)
    con.commit();return total
def import_file(con,path,label,minutes,convention,stamp,fixed_symbol=None,provenance='',max_rows=None):
    path=Path(path).resolve();stat=path.stat();digest=sha256(path)
    if not label.strip(): raise ValueError('Source label required')
    identity=json.dumps([digest,label,minutes,convention,stamp,fixed_symbol],sort_keys=True)
    source_id=hashlib.sha256(identity.encode()).hexdigest();ledger=requested(con)
    old=con.execute('SELECT * FROM import_jobs WHERE id=?',(source_id,)).fetchone()
    if old and old['status']=='COMPLETE': return {'status':'ALREADY_IMPORTED','source_id':source_id}
    con.execute('INSERT OR IGNORE INTO sources VALUES(?,?,?,?,?,?,?,?,?)',
        (source_id,label,str(path),digest,now_iso(),minutes,convention or 'AWARE_OR_EPOCH_ONLY',stamp,provenance))
    counts={'rows_read':old['rows_read'] if old else 0,'accepted':old['accepted'] if old else 0,'rejected':old['rejected'] if old else 0,'skipped':0};status='COMPLETE';error=None
    offset=counts['rows_read'];run_rows=0;symbols={key[0] for key in ledger}
    try:
        for index,raw in enumerate(iter_rows(path,ledger)):
            if index<offset: continue
            if max_rows is not None and run_rows>=max_rows:
                status='PAUSED_ROW_BUDGET';break
            counts['rows_read']+=1;run_rows+=1
            try:
                r=normal_keys(raw);sym=symbol(fixed_symbol or r.get('symbol') or r.get('ticker'))
                if sym not in symbols: counts['skipped']+=1;continue
                bar=normalize_row(raw,minutes,convention,stamp,fixed_symbol)
                if not in_scope(ledger,bar[0],bar[1]): counts['skipped']+=1;continue
                counts['accepted']+=store_bar(con,bar,source_id)
            except (ValueError,TypeError,OverflowError):
                counts['rejected']+=1
            if counts['rows_read']%5000==0: con.commit()
        current=path.stat()
        if (current.st_size,current.st_mtime_ns)!=(stat.st_size,stat.st_mtime_ns): raise ValueError('Input file changed during import')
        if counts['rejected']: status='PARTIAL_INVALID_ROWS'
    except Exception as exc:
        status='FAILED';error=type(exc).__name__+': import failed; inspect schema or optional dependency'
        raise
    finally:
        con.execute('INSERT OR REPLACE INTO import_jobs VALUES(?,?,?,?,?,?,?)',
            (source_id,status,counts['rows_read'],counts['accepted'],counts['rejected'],error,now_iso()));con.commit()
    derived=resample_source(con,source_id) if minutes==1 else 0
    return {'status':status,'source_id':source_id,'sha256':digest,'derived_5m':derived,**counts}
def coverage(con):
    result=[]
    for req in con.execute('SELECT * FROM requests ORDER BY day,symbol'):
        source_rows=con.execute('SELECT source_id,ts,v FROM bars WHERE symbol=? AND minutes=5 AND ts>=? AND ts<? ORDER BY ts',
            (req['symbol'],req['start_ts'],req['end_ts'])).fetchall()
        grouped={}
        for r in source_rows: grouped.setdefault(r['source_id'],set()).add(r['ts'])
        wanted=set(range(req['start_ts'],req['end_ts'],300))
        clean=[]
        for source_id,available in grouped.items():
            bad=con.execute('SELECT 1 FROM conflicts WHERE symbol=? AND ts>=? AND ts<? AND (source_a=? OR source_b=?) LIMIT 1',
                (req['symbol'],req['start_ts'],req['end_ts'],source_id,source_id)).fetchone()
            if wanted<=available and not bad: clean.append(source_id)
        best=max(grouped.values(),key=len,default=set());missing=sorted(wanted-best)
        result.append({'symbol':req['symbol'],'day':req['day'],'status':'COMPLETE_PRICE_WINDOW' if clean else 'MISSING_OR_CONFLICTED',
            'complete_source_ids':'|'.join(clean),'best_source_bar_count':len(best),'missing_bar_starts':'|'.join(datetime.fromtimestamp(t,IST).strftime('%H:%M') for t in missing),
            'null_volume_bar_count':sum(r['v'] is None for r in source_rows),
            'original_provider_arrival_verified':False,'financial_vintage_verified':False})
    return result
class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self,*args,**kwargs): return None
def public_download(url,directory,expected_sha=None,max_bytes=2_000_000_000,timeout=20):
    """Bounded public HTTPS GET, resumable only with matching URL+ETag metadata.

    Redirects are followed by urllib only for this unauthenticated public GET.
    No cookies, bearer credentials, app secrets or account headers are accepted.
    """
    parsed=urllib.parse.urlsplit(url)
    if max_bytes<=0: raise ValueError('A positive download byte budget is required')
    if parsed.scheme!='https' or not parsed.hostname or parsed.username or parsed.password: raise ValueError('Public HTTPS URL required')
    if expected_sha and not re.fullmatch(r'[a-fA-F0-9]{64}',expected_sha): raise ValueError('Invalid SHA256')
    name=Path(urllib.parse.unquote(parsed.path)).name
    if not name or name in ('.','..'): raise ValueError('URL must identify a file')
    directory=Path(directory).resolve();directory.mkdir(parents=True,exist_ok=True)
    dest=directory/name;part=directory/(name+'.part');meta_path=directory/(name+'.download.json')
    meta=json.loads(meta_path.read_text()) if meta_path.exists() else {}
    if dest.exists():
        digest=sha256(dest)
        if (expected_sha and digest==expected_sha.lower()) or (meta.get('complete') and meta.get('sha256')==digest and meta.get('url')==url): return meta
        raise ValueError('Existing destination does not match verified download metadata')
    offset=part.stat().st_size if part.exists() else 0
    if offset and (meta.get('url')!=url or not meta.get('etag') or meta['etag'].startswith('W/')): raise ValueError('Cannot safely resume without original URL and strong ETag')
    headers={'User-Agent':'ZienQuantResearch/1.0','Accept-Encoding':'identity'}
    if offset: headers.update(Range=f'bytes={offset}-',**{'If-Range':meta['etag']})
    req=urllib.request.Request(url,headers=headers)
    with urllib.request.urlopen(req,timeout=timeout) as response:
        status=getattr(response,'status',200)
        if offset and status==206:
            if not response.headers.get('Content-Range','').startswith(f'bytes {offset}-'): raise ValueError('Resume range mismatch')
            if response.headers.get('ETag')!=meta['etag']: raise ValueError('Resume ETag mismatch')
        elif status==200: offset=0
        else: raise ValueError('Unexpected public download response')
        declared=response.headers.get('Content-Length')
        if declared and offset+int(declared)>max_bytes: raise ValueError('Download exceeds byte budget')
        meta={'url':url,'etag':response.headers.get('ETag'),'acquired_at':now_iso(),'complete':False,'expected_sha256':expected_sha}
        meta_path.write_text(json.dumps(meta,indent=2)+'\n')
        with part.open('ab' if offset else 'wb') as stream:
            total=offset
            while True:
                block=response.read(min(1024*1024,max_bytes-total+1))
                if not block: break
                total+=len(block)
                if total>max_bytes: raise ValueError('Download exceeded byte budget')
                stream.write(block)
        if declared and part.stat().st_size!=offset+int(declared): raise ValueError('Truncated download; partial file retained')
    digest=sha256(part)
    if expected_sha and digest!=expected_sha.lower(): raise ValueError('SHA256 mismatch; partial retained for inspection')
    os.replace(part,dest);meta.update(complete=True,sha256=digest,bytes=dest.stat().st_size)
    meta_path.write_text(json.dumps(meta,indent=2)+'\n');return meta
class RateLimiter:
    """Persistent data-API budgets, with a conservative sliding 24-hour cap."""
    def __init__(self,con,per_second=5,per_minute=50,per_day=5000):
        self.con=con;self.limits=((1,per_second),(60,per_minute),(86400,per_day))
    def delay(self,now):
        waits=[]
        for window,limit in self.limits:
            rows=[r[0] for r in self.con.execute('SELECT ts FROM api_attempts WHERE ts>? ORDER BY ts',(now-window,))]
            if len(rows)>=limit: waits.append(rows[-limit]+window-now+0.01)
        return max(waits,default=0)
    def acquire(self,endpoint,clock=time.time,sleep=time.sleep):
        wait=self.delay(clock())
        if wait>61: raise RuntimeError('Daily API budget reached; resume later')
        if wait>0:
            sleep(min(wait,60))
            if wait>60:sleep(wait-60)
        self.con.execute('INSERT INTO api_attempts VALUES(?,?)',(clock(),endpoint));self.con.commit()
def fyers_collect(con,master_path,max_jobs=5,plan='standard',env_file=None,retry_incomplete=False):
    """Optional private-header read-only collector. Never logs credentials.

    A maximum job batch is explicit. A symbol-master identity match is required;
    no guessed ticker suffixes. Authentication/rate-limit errors stop the batch.
    """
    private=dict(os.environ)
    if env_file:
        for line in Path(env_file).read_text().splitlines():
            match=re.fullmatch(r'\s*(?:export\s+)?([A-Z_][A-Z0-9_]*)\s*=\s*(.*?)\s*',line)
            if match and match[1] not in private: private[match[1]]=match[2].strip('\"\'')
    app=(private.get('FYERS_APP_ID') or private.get('FYERS_CLIENT_ID') or '').strip();token=private.get('FYERS_ACCESS_TOKEN','').strip()
    if not app or not token: raise ValueError('Private FYERS app ID and access token are required in environment; do not paste them into chat')
    mapping={}
    with Path(master_path).open(newline='') as stream:
        for r in csv.reader(stream):
            for i,value in enumerate(r):
                if value.startswith('NSE:') and len(r)>i+4 and len(r)>2 and r[2]=='0': mapping[r[i+4].strip().upper()]=value
    if not mapping: raise ValueError('Verified NSE symbol master contains no usable equity identity mappings')
    limits=(10,500,500000) if plan=='prime' else (5,50,5000)
    limiter=RateLimiter(con,*limits);done=0
    opener=urllib.request.build_opener(NoRedirect())
    db_path=Path(con.execute('PRAGMA database_list').fetchone()[2]);raw_dir=db_path.parent/'raw_fyers';raw_dir.mkdir(parents=True,exist_ok=True)
    missing=[r for r in coverage(con) if r['status']!='COMPLETE_PRICE_WINDOW']
    for request in missing:
        if done>=max_jobs: break
        if request['symbol'] not in mapping: continue
        prior=con.execute('SELECT status FROM api_jobs WHERE symbol=? AND day=?',(request['symbol'],request['day'])).fetchone()
        if prior and prior[0] in ('PROVIDER_NO_DATA','PROVIDER_PARTIAL_WINDOW') and not retry_incomplete: continue
        params=urllib.parse.urlencode({'symbol':mapping[request['symbol']],'resolution':'5','date_format':'1','range_from':request['day'],'range_to':request['day'],'cont_flag':'1'})
        endpoint='https://api-t1.fyers.in/data/history';limiter.acquire(endpoint)
        con.execute('INSERT INTO api_jobs VALUES(?,?,?,1,NULL,?) ON CONFLICT(symbol,day) DO UPDATE SET status=excluded.status,attempts=attempts+1,last_error=NULL,updated_at=excluded.updated_at',
            (request['symbol'],request['day'],'REQUESTING',now_iso()));con.commit()
        req=urllib.request.Request(endpoint+'?'+params,headers={'Authorization':app+':'+token,'Accept':'application/json'})
        try:
            with opener.open(req,timeout=20) as response: raw_body=response.read(4*1024*1024+1)
            if len(raw_body)>4*1024*1024: raise ValueError('FYERS response exceeded byte budget')
            payload=json.loads(raw_body)
        except urllib.error.HTTPError as exc:
            con.execute('UPDATE api_jobs SET status=?,last_error=?,updated_at=? WHERE symbol=? AND day=?',
                ('STOPPED',f'HTTP_{exc.code}',now_iso(),request['symbol'],request['day']));con.commit()
            if exc.code in (401,403): raise RuntimeError('FYERS authorization rejected; reconnect privately') from None
            if exc.code==429: raise RuntimeError('FYERS rate limit reached; stop and resume after provider window resets') from None
            raise RuntimeError('FYERS data request failed; no credentials logged') from None
        if payload.get('s')!='ok': raise RuntimeError('FYERS did not return successful history; inspect entitlement privately')
        sid=hashlib.sha256(raw_body).hexdigest();timestamp=now_iso();raw_path=raw_dir/(sid+'.json')
        if not raw_path.exists(): raw_path.write_bytes(raw_body)
        con.execute('INSERT OR IGNORE INTO sources VALUES(?,?,?,?,?,?,?,?,?)',
            (sid,'FYERS authenticated History API',str(raw_path),sid,timestamp,5,'EPOCH_UTC','start','Provider access via private Authorization header; adjustment and instrument history still require verification; master sha256='+sha256(master_path)))
        ledger=requested(con)
        for raw in payload.get('candles',[]):
            if not isinstance(raw,list) or len(raw)!=6: raise ValueError('Unexpected FYERS OHLCV row schema')
            bar=validate_bar(request['symbol'],int(raw[0]),5,*raw[1:])
            if in_scope(ledger,bar[0],bar[1]): store_bar(con,bar,sid)
        target_start=datetime.combine(date.fromisoformat(request['day']),daytime(9,15),IST).timestamp()
        count=con.execute('SELECT COUNT(*) FROM bars WHERE symbol=? AND source_id=? AND minutes=5 AND ts>=? AND ts<?',
            (request['symbol'],sid,int(target_start),int(target_start)+6300)).fetchone()[0]
        status='PROVIDER_NO_DATA' if not payload.get('candles') else 'RECEIVED_DATA' if count==21 else 'PROVIDER_PARTIAL_WINDOW'
        con.execute('UPDATE api_jobs SET status=?,updated_at=? WHERE symbol=? AND day=?',
            (status,now_iso(),request['symbol'],request['day']))
        con.commit();done+=1
    return {'requests_performed':done,'price_only':True,'orders_enabled':False}
def write_coverage(con,path):
    rows=coverage(con);path=Path(path);path.parent.mkdir(parents=True,exist_ok=True)
    if rows:
        with path.open('w',newline='') as stream:
            out=csv.DictWriter(stream,fieldnames=list(rows[0]));out.writeheader();out.writerows(rows)
    return {'requested_windows':len(rows),'complete_windows':sum(r['status']=='COMPLETE_PRICE_WINDOW' for r in rows),'report':str(path)}
def export_complete(con,path):
    """Export complete, nonconflicted source windows. No source is silently mixed."""
    path=Path(path);path.parent.mkdir(parents=True,exist_ok=True);count=0
    names=['symbol','observation_date','ts','o','h','l','c','v','source','source_id','acquired_at','source_raw_sha256','provider_arrival_verified','financial_vintage_verified']
    with path.open('w',newline='') as stream:
        out=csv.DictWriter(stream,fieldnames=names);out.writeheader()
        for req in coverage(con):
            if req['status']!='COMPLETE_PRICE_WINDOW': continue
            for source_id in req['complete_source_ids'].split('|'):
                begin=int(datetime.combine(date.fromisoformat(req['day']),daytime(9,15),IST).timestamp())
                rows=con.execute('SELECT b.*,s.label,s.acquired_at,s.sha256 FROM bars b JOIN sources s ON s.id=b.source_id WHERE b.symbol=? AND b.source_id=? AND b.minutes=5 AND b.ts>=? AND b.ts<? ORDER BY b.ts',
                    (req['symbol'],source_id,begin,begin+6300))
                for r in rows:
                    record={k:r[k] for k in ['symbol','ts','o','h','l','c','v','source_id','acquired_at']}
                    record.update(observation_date=req['day'],source=r['label'],source_raw_sha256=r['sha256'],provider_arrival_verified=False,financial_vintage_verified=False)
                    out.writerow(record);count+=1
    return {'exported_5m_bars':count,'path':str(path),'prices_certify_original_vintage':False}
def main():
    parser=argparse.ArgumentParser(description=__doc__);sub=parser.add_subparsers(dest='command',required=True)
    p=sub.add_parser('init');p.add_argument('--events',required=True);p.add_argument('--db',required=True);p.add_argument('--positive-only',action='store_true')
    p=sub.add_parser('import');p.add_argument('path');p.add_argument('--db',required=True);p.add_argument('--source',required=True);p.add_argument('--interval',type=int,choices=(1,5),required=True);p.add_argument('--timezone',choices=('UTC','IST'));p.add_argument('--bar-stamp',choices=('start','end'),required=True);p.add_argument('--symbol');p.add_argument('--provenance',default='Public archive; source vintages/adjustments not independently verified');p.add_argument('--max-rows',type=int)
    p=sub.add_parser('coverage');p.add_argument('--db',required=True);p.add_argument('--output',required=True)
    p=sub.add_parser('export');p.add_argument('--db',required=True);p.add_argument('--output',required=True)
    p=sub.add_parser('download');p.add_argument('url');p.add_argument('--output-dir',required=True);p.add_argument('--sha256');p.add_argument('--max-bytes',type=int,default=2_000_000_000)
    p=sub.add_parser('fyers');p.add_argument('--db',required=True);p.add_argument('--master',required=True);p.add_argument('--max-jobs',type=int,default=5);p.add_argument('--plan',choices=('standard','prime'),default='standard');p.add_argument('--env-file');p.add_argument('--retry-incomplete',action='store_true')
    args=parser.parse_args()
    try:
        if args.command=='download': out=public_download(args.url,args.output_dir,args.sha256,args.max_bytes)
        else:
            with open_db(args.db) as con:
                if args.command=='init': out={'new_requested_windows':init_requests(con,args.events,args.positive_only)}
                elif args.command=='coverage': out=write_coverage(con,args.output)
                elif args.command=='export': out=export_complete(con,args.output)
                elif args.command=='fyers': out=fyers_collect(con,args.master,args.max_jobs,args.plan,args.env_file,args.retry_incomplete)
                else: out=import_file(con,args.path,args.source,args.interval,args.timezone,args.bar_stamp,args.symbol,args.provenance,args.max_rows)
        print(json.dumps(out,indent=2,allow_nan=False));return 0
    except (ValueError,RuntimeError,OSError,sqlite3.Error) as exc:
        # User-presentable custom messages never contain Authorization values.
        print(json.dumps({'status':'STOPPED','error':type(exc).__name__,'detail':str(exc) if not args.command=='fyers' else 'FYERS collection stopped; check private authorization, entitlement, network and rate budget.'}),file=sys.stderr);return 1
if __name__=='__main__': raise SystemExit(main())
