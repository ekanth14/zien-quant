"""Export model inputs from the expanded local positive-result archive.

This records candidate clocks, never certifies an original vintage. It will not
flip a quality flag merely because acquisition happened later or a URL exists.
Public reconstruction and operational replay remain separate model modes.
"""
from __future__ import annotations
import argparse
import hashlib
import json
import math
from datetime import datetime,timedelta
from pathlib import Path

from positive_path_model import IST,LANDMARKS,QUALITY_FLAGS,timestamp
from feature_snapshot import build_feature_snapshot


def fact_clock_candidates(fact_record:dict) -> tuple[datetime|None,list[dict]]:
    records=[]
    for record in fact_record.get('provenance',[]):
        if record.get('role')!='financials':continue
        claims=record.get('public_time_claims',{})
        selected={k:v for k,v in claims.items() if k in ('source_published_at','financials_available_at','financial_known_at','known_at') and v}
        values=[]
        for value in selected.values():
            try:values.append(timestamp(value))
            except (ValueError,TypeError,OverflowError):pass
        if values:
            records.append({'record':record,'max_clock_within_record':max(values).isoformat()})
    records.sort(key=lambda r:r['max_clock_within_record'])
    return (timestamp(records[0]['max_clock_within_record']) if records else None),records


def make_dataset(source:Path,lag_seconds:int=0,benchmark_path:Path|None=None)->dict:
    rows=[];excluded=[]
    benchmark=json.loads(benchmark_path.read_text()) if benchmark_path else None
    for line in source.read_text().splitlines():
        full=json.loads(line);event=full['event']
        if not event.get('complete_price_window'):
            excluded.append({'symbol':event['symbol'],'period_end':event['period_end'],'reason':'INCOMPLETE_PRICE_WINDOW'});continue
        bars=sorted(full['candles'],key=lambda b:b['ts'])
        current=full['current']['fact'];previous=full['previous']['fact']
        current_time,current_claims=fact_clock_candidates(full['current'])
        previous_time,previous_claims=fact_clock_candidates(full['previous'])
        actual_claims=[t for t in (current_time,previous_time) if t is not None]
        version_time=max(actual_claims) if len(actual_claims)==2 else timestamp(event['announced_at'])
        for landmark in LANDMARKS:
            decision=timestamp(f"{event['next_trading_date']}T{landmark}:00+05:30")
            future=[b for b in bars if datetime.fromtimestamp(b['ts'],IST)>=decision]
            market=benchmark.get('sessions',{}).get(event['next_trading_date'],{}).get('bars',[]) if benchmark is not None else None
            try:
                snapshot=build_feature_snapshot(current,previous,bars,decision,lag_seconds,market)
            except (ValueError,KeyError,TypeError,OverflowError) as exc:
                excluded.append({'symbol':event['symbol'],'landmark':landmark,'reason':str(exc)});continue
            if not future:
                excluded.append({'symbol':event['symbol'],'landmark':landmark,'reason':'NO_FUTURE_OUTCOME_BARS'});continue
            features=snapshot['features'];reference=snapshot['reference_price']
            benchmark_provenance=None
            if benchmark is not None:
                benchmark_provenance={'instrument':benchmark.get('instrument'),
                    'provider_verified':benchmark.get('provider_verified') is True,
                    'source_timestamps_verified':False,'price_adjustments_verified':False,'instrument_mapping_verified':False,
                    'source_labels':sorted({b['source'] for b in market}),
                    'last_feature_bar_end':snapshot['benchmark_last_feature_bar_end'],
                    'registered_bar_lag_seconds':lag_seconds,'bar_interval_minutes':5,
                    'benchmark_is_context_not_company_outcome':True,'index_volume_used':False}
            # Complete future targets use highs/lows strictly after decision;
            # never re-use the last completed feature candle's high or low.
            targets={'return_1100_bps':10000*math.log(bars[-1]['c']/reference),
                     'max_up_bps':10000*math.log(max([reference]+[b['h'] for b in future])/reference),
                     'max_down_bps':10000*math.log(min([reference]+[b['l'] for b in future])/reference)}
            for clock,key in (('10:00','return_1000_bps'),('10:30','return_1030_bps')):
                if clock>landmark:
                    cutoff=timestamp(f"{event['next_trading_date']}T{clock}:00+05:30")
                    checkpoint=[b for b in bars if datetime.fromtimestamp(b['ts'],IST)+timedelta(minutes=5)==cutoff]
                    if checkpoint:targets[key]=10000*math.log(checkpoint[0]['c']/reference)
            last_end=timestamp(snapshot['last_feature_bar_end'])
            acquired=datetime.now(IST).isoformat()
            known=max(version_time,last_end+timedelta(seconds=lag_seconds))
            row={
              'event_id':event['symbol']+'|'+event['period_end']+'|'+event['next_trading_date'],
              'symbol':event['symbol'],'company':event['company'],'sector':'UNKNOWN',
              'feature_variant':snapshot['feature_variant'],
              'session_date':event['next_trading_date'],'announced_at':event['announced_at'],
              'decision_at':decision.isoformat(),'features_known_at':known.isoformat(),
              'target_available_at':(timestamp(f"{event['next_trading_date']}T11:00:00+05:30")+timedelta(seconds=lag_seconds)).isoformat(),
              'reference_price':reference,'reference_kind':'last_completed_close_under_registered_public_bar_lag_assumption',
              'movement_band_bps':10.,'features':features,'targets':targets,
              'eligibility':{'positive':True,'leverage_above_3':event['leverage_snapshot']>3,'in_dashboard':True},
              'quality':dict.fromkeys(QUALITY_FLAGS,False),
              'provenance':{
                'original_publication_at':event['announced_at'],
                'financial_version_available_at':version_time.isoformat(),
                'financial_clock_is_unverified_candidate_claim':True,
                'current_fact_clock_candidates':current_claims,
                'previous_fact_clock_candidates':previous_claims,
                'last_feature_bar_end':last_end.isoformat(),'registered_bar_lag_seconds':lag_seconds,
                'acquired_at':acquired,'source_price_labels':sorted({b['source'] for b in bars}),
                'source_urls':event['source_urls'].split('|'),
                'selection_policy':full['selection_policy'],
                'later_acquisition_alone_does_not_invalidate_public_backtest':True,
                'original_source_vintage_needs_verification':True,
                'two_preannouncement_date_daily_controls':full.get('two_preannouncement_date_daily_controls',[])}}
            if benchmark_provenance is not None:row['provenance']['benchmark']=benchmark_provenance
            row['quality'].update(bars_complete=True,next_session_verified=True,
                continuous_0915_1100=event['continuous_target_window'])
            rows.append(row)
    result={'schema_version':'1.0','research_mode':'PUBLIC_INFORMATION_BACKTEST',
            'dataset_name':'expanded_local_positive_core_financial_opening_price_v1',
            'source_jsonl_sha256':hashlib.sha256(source.read_bytes()).hexdigest(),
            'scope_start_announcement_date':'2023-05-06','feature_variant':'core_financial_opening_price_v1',
            'movement_band_definition':'Fixed pre-registered10log-bps material-movement band, illustrative; no fee/fill/profit claim.',
            'public_bar_timing_assumption':{'registered_lag_seconds':lag_seconds,'real_arrival_verified':False},
            'acquisition_clock_warning':'Current acquisition is provenance, not historical information availability. Original-vintage verification remains required.',
            'excluded_events_or_landmarks':excluded,'rows':rows}
    if benchmark_path:
        result.update(feature_variant='core_financial_opening_price_with_market_context_v1',
            benchmark_sha256=hashlib.sha256(benchmark_path.read_bytes()).hexdigest(),
            benchmark_source_verified=benchmark.get('provider_verified') is True)
    return result


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source',type=Path,default=Path(__file__).resolve().parent.parent/'local_data'/'positive_events.jsonl')
    parser.add_argument('--output',type=Path,default=Path(__file__).with_name('local_model_dataset.json'))
    parser.add_argument('--bar-lag-seconds',type=int,default=0)
    parser.add_argument('--benchmark',type=Path,help='Optional separately-provenanced five-minute benchmark mornings JSON; creates a distinct feature variant')
    args=parser.parse_args()
    if not 0<=args.bar_lag_seconds<=3600:parser.error('bar lag must be from0to3600seconds')
    dataset=make_dataset(args.source,args.bar_lag_seconds,args.benchmark)
    args.output.write_text(json.dumps(dataset,indent=2,allow_nan=False)+'\n')
    print(json.dumps({'output':str(args.output),'rows':len(dataset['rows']),
       'companies':len({r['symbol'] for r in dataset['rows']}),'dates':sorted({r['session_date'] for r in dataset['rows']}),
       'original_financial_vintages_verified':sum(r['quality']['original_financial_vintage_verified'] for r in dataset['rows'])}))
