"""Build an offline review of each company's own post-positive-earnings chart.

The HTML is a review artifact, not deployment or a fitted prediction service.
Reads portable files inside this package and never calls a market-data API.
"""
from pathlib import Path
import hashlib,json
from datetime import datetime,timezone,timedelta

HERE=Path(__file__).resolve().parent
IST=timezone(timedelta(hours=5,minutes=30))
LANDMARKS=['09:30','09:40','10:00','10:30','11:00']

def build():
    positive_path=HERE/'local_data/positive_events.jsonl'
    records=[json.loads(line) for line in positive_path.read_text().splitlines()]
    summary=json.loads((HERE/'local_data/local_expansion_summary.json').read_text())
    model_path=HERE/'model/local_model_dataset_with_market.json'
    model=json.loads(model_path.read_text())
    model_rows={(r['symbol'],r['session_date'],r['decision_at'][11:16]):r for r in model['rows']}
    benchmark=json.loads((HERE/'public_data/benchmark_mornings.json').read_text())
    events=[]
    for pair in records:
        e=pair['event']
        if not e['complete_price_window']:continue
        key=(e['symbol'],e['next_trading_date'])
        rows={t:model_rows.get((*key,t)) for t in LANDMARKS if t!='11:00'}
        states={}
        for t,row in rows.items():
            if row is None:raise ValueError('Missing expected model diagnostics: '+str((*key,t)))
            p=row['provenance']
            states[t]={
                'decision_at':row['decision_at'],'reference_price':row['reference_price'],
                'features':row['features'],'quality':row['quality'],
                'features_known_at_claim':row['features_known_at'],
                'financial_version_available_at_claim':p.get('financial_version_available_at'),
                'public_disclosure_at_claim':p.get('original_publication_at'),
                'acquired_at':p.get('acquired_at'),
                'source_price_labels':p.get('source_price_labels'),
                'source_urls':p.get('source_urls'),
                'predictions':None,'accuracy':None,
                'forecast_blockers':['Original public financial vintage unverified','Price source/adjustment/instrument mapping unverified','Only 3 distinct observed dates; chronological validation unavailable'],
            }
        public_claims=[];collection_claims=[]
        for f in (pair['current'],pair['previous']):
            for p in f['provenance']:
                public_claims.extend(v for v in p['public_time_claims'].values() if v)
                collection_claims.extend(v for v in p['collection_time_claims'].values() if v)
        bars=[]
        for b in pair['candles']:
            bars.append({**b,'time':datetime.fromtimestamp(b['ts'],IST).strftime('%H:%M')})
        market=benchmark['sessions'].get(e['next_trading_date'],{})
        events.append({'symbol':e['symbol'],'company':e['company'],'date':e['next_trading_date'],
            'announced_at':e['announced_at'],'period_end':e['period_end'],
            'leverage':e['leverage_snapshot'],'revenue_qoq_pct':e['revenue_qoq_pct'],'pat_qoq_pct':e['pat_qoq_pct'],
            'current':pair['current']['fact'],'previous':pair['previous']['fact'],
            'financial_class':'POSITIVE','scope_alternatives':e['all_scope_financial_class_variants'],
            'bars':bars,'states':states,'source_urls':e['source_urls'].split('|'),
            'earliest_collection_claim':min(collection_claims) if collection_claims else None,
            'latest_collection_claim':max(collection_claims) if collection_claims else None,
            'original_public_vintage_verified':False,
            'benchmark':{'instrument':'NIFTY50_INDEX','provider_verified':False,'bars':market.get('bars',[])},
            'final_return_pct':e['return_0915_1100_pct']})
    events.sort(key=lambda e:(e['date'],e['symbol']))
    assert len(events)==17 and len({(e['symbol'],e['date']) for e in events})==17
    payload={'title':'Company reaction after positive earnings','landmarks':LANDMARKS,'events':events,
        'coverage':summary['years'],'universe_count':summary['website_companies'],
        'eligible_count':summary['eligible_companies'],'classified_positive_pairs':summary['positive_events'],
        'announcement_from':'2023-05-06','distinct_dates':len({e['date'] for e in events}),
        'up_count':sum(e['final_return_pct']>0 for e in events),'down_count':sum(e['final_return_pct']<0 for e in events),
        'source_hashes':{'positive_events.jsonl':hashlib.sha256(positive_path.read_bytes()).hexdigest(),
                         'local_model_dataset_with_market.json':hashlib.sha256(model_path.read_bytes()).hexdigest()},
        'predictions':None,'accuracy':None,'orders_enabled':False}
    encoded=json.dumps(payload,ensure_ascii=False,separators=(',',':'),allow_nan=False).replace('<','\\u003c')
    template=(HERE/'dashboard_template.html').read_text()
    assert template.count('__PAYLOAD__')==1
    destination=HERE/'COMPANY_REACTION_DASHBOARD.html'
    destination.write_text(template.replace('__PAYLOAD__',encoded))
    assert destination.stat().st_size<1_000_000
    print(json.dumps({'path':str(destination),'bytes':destination.stat().st_size,'companies':len(events),
                      'dates':payload['distinct_dates'],'up':payload['up_count'],'down':payload['down_count'],
                      'future_predictions':None,'network_calls':0},indent=2))

if __name__=='__main__':build()
