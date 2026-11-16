#!/usr/bin/env python3
"""Run the public Parquet backfill in a network-capable local runtime.

Default is a plan: no downloads. --execute downloads/imports an explicit bounded
number of public shards. No credentials, trades or automatic quality attestations.
"""
from __future__ import annotations
import argparse,importlib.util,json,sys,urllib.parse
from pathlib import Path
from collect_history import open_db,init_requests,public_download,import_file,write_coverage,export_complete,now_iso

HERE=Path(__file__).resolve().parent


def run(args):
    manifest=json.loads(args.manifest.read_text())
    source=next(s for s in manifest['sources'] if s.get('publisher')=='xxparthparekhxx')
    files=source['files'][:args.max_shards]
    plan={'status':'PLAN_ONLY','source':source['repository_url'],'files':files,
      'large_download_warning':'Publisher advertises about 10.4 GB across all eight shards; inspect available disk first.',
      'actual_company_coverage':'Not established until per-symbol/session import and financial verification.',
      'source_latest_timestamp_claim':source.get('viewer_latest_timestamp_claim'),
      'will_fetch_credentials':False,'orders_enabled':False}
    if not args.execute:return plan
    if importlib.util.find_spec('pyarrow') is None:
        return {**plan,'status':'DEPENDENCY_MISSING','detail':'Install optional pyarrow in a private research environment before downloading Parquet shards.'}
    args.raw_dir.mkdir(parents=True,exist_ok=True)
    con=open_db(args.db)
    started=now_iso();results=[];status='IMPORT_BATCH_COMPLETED';error=None
    try:
        init_requests(con,args.events,positive_only=args.positive_only)
        for item in files:
            print(json.dumps({'status':'STARTING_SHARD','source_url':item['url']}),flush=True)
            meta=public_download(item['url'],args.raw_dir,item.get('sha256'),args.max_bytes_per_shard)
            filename=Path(urllib.parse.unquote(urllib.parse.urlsplit(item['url']).path)).name
            imported=import_file(con,args.raw_dir/filename,
              'Public xxparthparekhxx minute archive; rawfileSHA/version recorded',1,'UTC','start',
              provenance='Public publisher archive; original price adjustments and financial vintages unverified; no source rights or accuracy certification inferred',
              max_rows=args.max_rows_per_shard)
            results.append({'file':filename,'download_sha256':meta['sha256'],'import':imported})
            print(json.dumps({'status':'SHARD_PROCESSED','file':filename,'import_status':imported['status']}),flush=True)
        coverage=write_coverage(con,args.db.parent/'missing_requests.csv')
        export_complete(con,args.db.parent/'collected_complete_candles.csv')
    except (OSError,ValueError,RuntimeError) as exc:
        status='STOPPED_RESUMABLE';error=type(exc).__name__
        coverage=write_coverage(con,args.db.parent/'missing_requests.csv')
    finally:con.close()
    result={'status':status,'started_at':started,'ended_at':now_iso(),'results':results,
      'error_type':error,'coverage':coverage,'full_requested_study_complete':False,
      'financial_verification_still_required':True,'prices_verified_by_download_alone':False,
      'orders_enabled':False}
    (args.db.parent/'public_collection_run.json').write_text(json.dumps(result,indent=2,allow_nan=False)+'\n')
    return result


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--manifest',type=Path,default=HERE/'public_download_manifest.json')
    p.add_argument('--events',type=Path,default=HERE.parent/'local_data'/'eligible_event_coverage.csv')
    p.add_argument('--db',type=Path,default=HERE/'public_backfill.sqlite')
    p.add_argument('--raw-dir',type=Path,default=HERE/'raw_public')
    p.add_argument('--max-shards',type=int,default=1)
    p.add_argument('--max-bytes-per-shard',type=int,default=2_000_000_000)
    p.add_argument('--max-rows-per-shard',type=int)
    p.add_argument('--positive-only',action='store_true',help='Collect only currently classified positive events; omitting also preserves unclassified-event coverage for later financial parsing')
    p.add_argument('--execute',action='store_true')
    args=p.parse_args()
    if not 1<=args.max_shards<=8:p.error('max-shards must be 1–8')
    if args.max_bytes_per_shard<=0:p.error('Positive byte budget required')
    if args.max_rows_per_shard is not None and args.max_rows_per_shard<=0:p.error('Positive row budget required')
    response=run(args)
    print(json.dumps(response,indent=2,allow_nan=False))
    raise SystemExit(0 if response['status'] in ('PLAN_ONLY','IMPORT_BATCH_COMPLETED') else 2)
