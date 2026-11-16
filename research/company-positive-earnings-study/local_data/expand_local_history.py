"""Read-only reconstruction of the local event ledger and financial fact pool.

Source-public and collection clocks are deliberately separate. Acquiring an old
filing today does not invalidate a public-information historical backtest. What
still needs verification is the original public vintage and earliest disclosure.
No market data is downloaded or fabricated by this script.
"""
from __future__ import annotations
import argparse, csv, hashlib, importlib.util, json, math, sqlite3
from collections import Counter, defaultdict
from datetime import date, datetime, time, timedelta, timezone
from pathlib import Path
from urllib.parse import quote

HERE=Path(__file__).resolve().parent
PROJECT=Path(__file__).resolve().parents[3]
CALENDAR=None
RUNTIME=PROJECT/'work/runtime'
IST=timezone(timedelta(hours=5,minutes=30))
START=date(2023,5,6)
END=date(2026,10,27)

def ro(path):
    for suffix in ('-wal','-journal'):
        sidecar=Path(str(path)+suffix)
        if sidecar.exists() and sidecar.stat().st_size:
            raise RuntimeError('Active SQLite journal; snapshot required: '+str(path))
    con=sqlite3.connect('file:'+quote(str(path),safe='/')+'?mode=ro&immutable=1',uri=True)
    con.row_factory=sqlite3.Row
    con.execute('PRAGMA query_only=ON')
    return con

def stamp(value):
    if not value:return None
    try:
        d=datetime.fromisoformat(str(value).replace('Z','+00:00'))
        return d.astimezone(IST) if d.tzinfo else d.replace(tzinfo=IST)
    except (ValueError,TypeError):return None

def canonical(data):return json.dumps(data,sort_keys=True,separators=(',',':'),allow_nan=False)
def finite(value):return isinstance(value,(int,float)) and not isinstance(value,bool) and math.isfinite(value)
def write_csv(name,rows):
    if not rows:return
    with (HERE/name).open('w',newline='') as stream:
        writer=csv.DictWriter(stream,fieldnames=list(rows[0]));writer.writeheader();writer.writerows(rows)

def valid_fact(fact):
    try:
        s,e=date.fromisoformat(fact['start']),date.fromisoformat(fact['period_end'])
        return (70 <= (e-s).days+1 <=110 and fact['basis'] in ('standalone','consolidated')
                and all(fact.get(k) for k in ('unit','revenue_metric','profit_metric'))
                and all(finite(fact.get(k)) for k in ('revenue','net_profit')))
    except (ValueError,TypeError,KeyError):return False

def compatible(current,previous):
    try:
        return (valid_fact(current) and valid_fact(previous)
            and all(current[k]==previous[k] for k in ('basis','unit','revenue_metric','profit_metric'))
            and date.fromisoformat(current['start'])==date.fromisoformat(previous['period_end'])+timedelta(days=1))
    except (ValueError,TypeError,KeyError):return False

def classify(current,previous):
    if not compatible(current,previous):return 'INCOMPARABLE',None,None
    r0,p0=previous['revenue'],previous['net_profit'];r1,p1=current['revenue'],current['net_profit']
    if r0<=0:return 'NONPOSITIVE_REVENUE_BASE',None,None
    gr=100*(r1/r0-1)
    if p0<=0:return ('TURNAROUND' if p0<0<p1 else 'ZERO_PROFIT_BASE' if p0==0 else 'LOSS_BASE'),gr,None
    gp=100*(p1/p0-1)
    return ('POSITIVE' if gr>0 and gp>0 else 'NEGATIVE' if gr<0 and gp<0 else 'MIXED_OR_ZERO'),gr,gp

def calendar_functions(calendar):
    closed={d for days in calendar['weekdays_closed_regular_sessions'].values() for d in days}
    special={row['date']:row for row in calendar['special_live_sessions']}
    def live(d):
        if str(d.year) not in calendar['weekdays_closed_regular_sessions']:return None
        return d.isoformat() in special or d.weekday()<5 and d.isoformat() not in closed
    def after(d):
        for n in range(1,31):
            target=d+timedelta(days=n)
            if live(target):
                row=special.get(target.isoformat())
                return target,not row or row.get('morning_0915_1100_complete_session') is True
        return None,False
    def before(d):
        out=[]
        for n in range(1,31):
            target=d-timedelta(days=n)
            if live(target):out.append(target)
            if len(out)==2:return out
        return out
    return after,before

def main():
    HERE.mkdir(parents=True,exist_ok=True)
    with ro(RUNTIME/'stock_master.db') as c:
        catalogue={row['symbol']:row['name'] for row in c.execute('select symbol,name from stocks')}
    margins=json.loads((RUNTIME/'zerodha-equity-margins.json').read_text())
    eligible={symbol:{**r,'name':catalogue[symbol]} for symbol,r in margins['records'].items()
              if symbol in catalogue and finite(r.get('leverage')) and r['leverage']>3 and r.get('eligible') is True}
    calendar=json.loads(CALENDAR.read_text(encoding='utf-8'))
    after,before=calendar_functions(calendar)
    firsts={};events=[];pool={};source_counts=Counter();source_url_events=defaultdict(list)
    def add_fact(symbol,fact,event,role,origin):
        if not isinstance(fact,dict) or not valid_fact(fact):return
        key=(symbol,canonical(fact))
        source_url=(event.get('previous_source_url') if role=='previous' else event.get('year_ago_source_url') if role=='year_ago' else None) or event.get('financial_source_url') or event.get('document_url') or event.get('source_url')
        public={k:event.get(k) for k in ('source_published_at','financials_available_at','financial_known_at','comparison_available_at','revised_at','known_at') if event.get(k)}
        acquired={k:event.get(k) for k in ('first_seen_at','metadata_imported_at') if event.get(k)}
        provenance={'event_id':event.get('id'),'origin':origin,'role':role,'source_url':source_url,
                    'public_time_claims':public,'collection_time_claims':acquired,
                    'original_announcement_unknown':bool(event.get('original_announcement_unknown')),
                    'original_public_vintage_verified':False}
        record=pool.setdefault(key,{'symbol':symbol,'fact':fact,'provenance':[]})
        if provenance not in record['provenance']:record['provenance'].append(provenance)
    with ro(RUNTIME/'results.db') as c:
        for table in ('result_events','result_scope_archive','result_versions'):
            for row in c.execute('select payload from '+table):
                e=json.loads(row['payload']);symbol=e.get('symbol')
                if symbol not in eligible:continue
                source_counts[table]+=1
                announced=stamp(e.get('announced_at'))
                if not announced:continue
                key=(symbol,e.get('period_end'))
                if key not in firsts or announced<firsts[key]:firsts[key]=announced
                if table!='result_versions':
                    events.append(e)
                    source_url_events[e.get('document_url') or e.get('source_url')].append(e)
                for role in ('financials','previous','year_ago'):
                    fact=e.get(role) or {}
                    if isinstance(fact,dict) and isinstance(fact.get('financials'),dict):fact=fact['financials']
                    add_fact(symbol,fact,e,role,table)
                for fact in e.get('reported_comparatives') or []:add_fact(symbol,fact,e,'reported_comparative',table)
    # Reparse all locally cached source XMLs with the existing exact-context parser.
    spec=importlib.util.spec_from_file_location('local_results_parser',PROJECT/'marketscope/results_service.py')
    parser=importlib.util.module_from_spec(spec);spec.loader.exec_module(parser)
    cache_summary=[]
    for file in sorted((RUNTIME/'results-rss-cache').glob('*.json')):
        record=json.loads(file.read_text());url=record.get('url');linked=source_url_events.get(url,[])
        xml=record.get('xml')
        if not isinstance(xml,str):continue
        sha=hashlib.sha256(xml.encode()).hexdigest();rows_added=0
        for event in linked:
            enriched={**event,'source_published_at':record.get('published_at') or event.get('source_published_at'),
                      'first_seen_at':record.get('stored_at') or event.get('first_seen_at')}
            try:facts=parser.parse_financial_document(xml.encode(),enriched)
            except ValueError:facts=[]
            for fact in facts:
                add_fact(event['symbol'],fact,enriched,'cached_xml','results-rss-cache')
                rows_added+=1
        cache_summary.append({'path':str(file),'source_url':url,'sha256_xml':sha,'linked_eligible_events':len(linked),'parsed_compatible_fact_attempts':rows_added})
    facts=list(pool.values());by_symbol=defaultdict(list)
    for row in facts:by_symbol[row['symbol']].append(row)
    # Each issuer/reporting period is one result event. Prefer a comparable
    # consolidated pair; latest revised values remain explicit retrospective facts.
    grouped=defaultdict(list)
    for e in events:
        key=(e['symbol'],e['period_end'])
        a=firsts[key]
        if START<=a.date()<=END:grouped[key].append(e)
    ledger=[];pair_rows=[];positive_events=[];document_requests={};candles_export=[];daily_export=[]
    with ro(RUNTIME/'candles.db') as c:
        candle_inventory=[dict(r) for r in c.execute("select timeframe,count(*) as rows,count(distinct symbol) as symbols,min(ts) as first_ts,max(ts) as last_ts from candles group by timeframe")]
        for (symbol,period),candidates in sorted(grouped.items()):
            announced=firsts[(symbol,period)];target,continuous=after(announced.date())
            current_choices=[row for row in by_symbol[symbol] if row['fact']['period_end']==period]
            options=[]
            for current in current_choices:
                previous_choices=[r for r in by_symbol[symbol] if compatible(current['fact'],r['fact'])]
                for previous in previous_choices:
                    cls,gr,gp=classify(current['fact'],previous['fact'])
                    # Prefer directly co-reported comparisons, then provenance-bearing current facts.
                    current_urls={p['source_url'] for p in current['provenance'] if p.get('source_url')}
                    previous_urls={p['source_url'] for p in previous['provenance'] if p.get('source_url')}
                    options.append((current['fact']['basis']=='consolidated',bool(current_urls&previous_urls),current,previous,cls,gr,gp))
            options.sort(key=lambda o:(o[0],o[1],canonical(o[2]['fact']),canonical(o[3]['fact'])),reverse=True)
            selected=options[0] if options else None
            current,previous,cls,gr,gp=(selected[2:] if selected else (None,None,'INCOMPARABLE',None,None))
            selected_scope_options=[o for o in options if all(o[2]['fact'][k]==current['fact'][k] for k in ('basis','unit','revenue_metric','profit_metric'))] if selected else []
            numeric_variants={(o[2]['fact']['revenue'],o[2]['fact']['net_profit'],o[3]['fact']['revenue'],o[3]['fact']['net_profit']) for o in selected_scope_options}
            class_variants=sorted({o[4] for o in selected_scope_options})
            start=datetime.combine(target,time(9,15),IST) if target else None
            bars=[dict(r) for r in c.execute('select ts,o,h,l,c,v,source from candles where symbol=? and timeframe=5 and ts>=? and ts<? order by ts',(symbol,int(start.timestamp()),int((start+timedelta(minutes=105)).timestamp())))] if start and continuous else []
            expected=[int((start+timedelta(minutes=5*i)).timestamp()) for i in range(21)] if start else []
            valid=(len(bars)==21 and [b['ts'] for b in bars]==expected and len({b['source'] for b in bars})==1
                   and all(all(finite(b[k]) for k in ('o','h','l','c','v')) and min(b['o'],b['h'],b['l'],b['c'])>0 and b['l']<=min(b['o'],b['c'])<=max(b['o'],b['c'])<=b['h'] and b['v']>=0 for b in bars))
            row={'symbol':symbol,'company':eligible[symbol]['name'],'period_end':period,'announced_at':announced.isoformat(),
                 'next_trading_date':target.isoformat() if target else None,'continuous_target_window':continuous,
                 'leverage_snapshot':eligible[symbol]['leverage'],'financial_class':cls,'revenue_qoq_pct':gr,'pat_qoq_pct':gp,
                 'current_fact_available':bool(current_choices),'comparable_pair_available':selected is not None,
                 'multiple_financial_pair_variants':len(options),'selected_scope':current['fact']['basis'] if current else None,
                 'selected_scope_pair_variants':len(selected_scope_options),'financial_variant_conflict':len(numeric_variants)>1,
                 'financial_class_conflict':len(class_variants)>1,'financial_class_variants':'|'.join(class_variants),
                 'all_scope_financial_class_variants':'|'.join(sorted({o[2]['fact']['basis']+':'+o[4] for o in options})),
                 'original_public_vintage_verified':False,'public_backtest_ready':False,
                 'candle_count':len(bars),'complete_price_window':valid,'return_0915_1100_pct':100*(bars[-1]['c']/bars[0]['o']-1) if valid else None,
                 'source_urls':'|'.join(sorted({e.get('document_url') or e.get('source_url') or '' for e in candidates}))}
            ledger.append(row)
            if selected:
                pair={'event':row,'current':current,'previous':previous,
                      'financial_pair_alternatives':[{'current_fact':o[2]['fact'],'previous_fact':o[3]['fact'],'class':o[4],'revenue_qoq_pct':o[5],'pat_qoq_pct':o[6],'same_source_document':o[1]} for o in options],
                      'selection_policy':'Consolidated preferred; same-source-document comparison preferred; deterministic variant selected for coverage only. Scope differences are not revision conflicts; conflicting values within selected scope need original-vintage verification.',
                      'clock_policy':'Source-public claims and local acquisition clocks remain separate. Later acquisition is not automatically a historical-public-data exclusion.'}
                pair_rows.append(pair)
                if cls=='POSITIVE':
                    pair['candles']=bars
                    prevdays=before(announced.date());controls=[]
                    for day in prevdays:
                        ds=datetime.combine(day,time.min,IST)
                        dr=[dict(r) for r in c.execute('select ts,o,h,l,c,v,source from candles where symbol=? and timeframe=1440 and ts>=? and ts<? order by ts',(symbol,int(ds.timestamp()),int((ds+timedelta(days=1)).timestamp())))]
                        controls.append({'date':day.isoformat(),'rows':dr,'verified_adjustment':False,'arrival_metadata_available':False})
                        daily_export.extend({'symbol':symbol,'result_period':period,'control_date':day.isoformat(),**r} for r in dr)
                    pair['two_preannouncement_date_daily_controls']=controls
                    positive_events.append(pair)
                    candles_export.extend({'symbol':symbol,'result_period':period,'observation_date':target.isoformat(),**b} for b in bars)
            # Download original files for every eligible event with absent pairs;
            # revised/latest document contents must not be treated as original.
            if not selected or row['financial_variant_conflict']:
                for e in candidates:
                    url=e.get('document_url') or e.get('source_url')
                    if not url or not url.startswith('https://nsearchives.nseindia.com/'):continue
                    request=document_requests.setdefault(url,{'url':url,'symbol':symbol,'company':row['company'],'period_end':period,'announcement_at':announced.isoformat(),
                        'basis':e.get('basis'),'reason':'MISSING_COMPARABLE_FINANCIAL_PAIR' if not selected else 'CONFLICTING_FINANCIAL_VARIANTS','original_announcement_unknown':bool(e.get('original_announcement_unknown'))})
    write_csv('eligible_event_coverage.csv',ledger)
    write_csv('financial_document_requests.csv',list(document_requests.values()))
    write_csv('positive_candles_available.csv',candles_export)
    write_csv('positive_preannouncement_daily_controls.csv',daily_export)
    for name,rows in [('financial_fact_variants.jsonl',facts),('comparable_pairs.jsonl',pair_rows),('positive_events.jsonl',positive_events)]:
        with (HERE/name).open('w') as stream:
            for row in rows:stream.write(json.dumps(row,ensure_ascii=False,allow_nan=False)+'\n')
    (HERE/'cached_xml_inventory.json').write_text(json.dumps(cache_summary,indent=2)+'\n')
    summaries={str(y):{'events':sum(r['announced_at'].startswith(str(y)) for r in ledger),
                       'comparable':sum(r['announced_at'].startswith(str(y)) and r['comparable_pair_available'] for r in ledger),
                       'positive':sum(r['announced_at'].startswith(str(y)) and r['financial_class']=='POSITIVE' for r in ledger),
                       'positive_complete_mornings':sum(r['announced_at'].startswith(str(y)) and r['financial_class']=='POSITIVE' and r['complete_price_window'] for r in ledger)} for y in range(2023,2027)}
    summary={'created_at':datetime.now(IST).isoformat(),'website_companies':len(catalogue),'eligible_companies':len(eligible),
             'source_rows':dict(source_counts),'financial_fact_variants':len(facts),'eligible_company_periods':len(ledger),
             'financial_classes':dict(Counter(r['financial_class'] for r in ledger)),'years':summaries,
             'positive_events':len(positive_events),'positive_complete_mornings':sum(p['event']['complete_price_window'] for p in positive_events),
             'positive_complete_morning_dates':sorted({p['event']['next_trading_date'] for p in positive_events if p['event']['complete_price_window']}),
             'positive_with_both_preannouncement_daily_controls':sum(all(len(d['rows'])==1 for d in p['two_preannouncement_date_daily_controls']) for p in positive_events),
             'financial_document_requests':len(document_requests),'cached_xml_files':len(cache_summary),'candle_inventory':candle_inventory,
             'financial_variants_conflicted_events':sum(r['financial_variant_conflict'] for r in ledger),
             'financial_classes_conflicted_events':sum(r['financial_class_conflict'] for r in ledger),
             'source_files':{'results_db':str(RUNTIME/'results.db'),'candles_db':str(RUNTIME/'candles.db'),'margin_snapshot':str(RUNTIME/'zerodha-equity-margins.json')},
             'limitations':['Current margin universe is not historical eligibility proof.','No original public financial vintages or earliest venue announcements independently certified.','First stored disclosure is not certified earliest disclosure.','Conflicting financial variants are exported and cannot be used for learned forecasts until resolved.','Source-public and acquisition clocks are separate; later local acquisition does not automatically invalidate hypothetical public-information research.','All prices retain source labels; original response, adjustments and arrival metadata unverified.','Complete older intraday prices remain unavailable locally.']}
    (HERE/'local_expansion_summary.json').write_text(json.dumps(summary,indent=2,allow_nan=False)+'\n')
    print(json.dumps(summary,indent=2,allow_nan=False))

if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--project',type=Path,default=PROJECT)
    parser.add_argument('--calendar',type=Path,required=True,help='Verified calendar JSON; not bundled in the source archive')
    parser.add_argument('--output-dir',type=Path,required=True,help='New working directory; preserve the supplied immutable snapshot')
    parser.add_argument('--until',type=date.fromisoformat,default=END)
    args=parser.parse_args()
    if not args.calendar.is_file():parser.error('Verified calendar file does not exist')
    if args.output_dir.resolve()==Path(__file__).resolve().parent:parser.error('Choose a separate output directory')
    PROJECT=args.project.resolve();RUNTIME=PROJECT/'work/runtime';CALENDAR=args.calendar;HERE=args.output_dir;END=args.until
    main()
