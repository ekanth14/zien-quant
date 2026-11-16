#!/usr/bin/env python3
"""Bounded official NSE document collection, immutable raw files, exact parser.

No invented publication clock. Unknown/PDF formats stay saved and unparsed.
DNS, HTTP authorization/rate blocking and suspicious block pages stop the batch.
"""
import argparse,csv,hashlib,importlib.util,json,sqlite3,sys,time
import urllib.error,urllib.parse,urllib.request
from datetime import datetime,timezone
from pathlib import Path

ALLOWED_HOSTS={'nsearchives.nseindia.com','archives.nseindia.com','www.nseindia.com'}
MAX_BYTES=12_000_000
def stamp():return datetime.now(timezone.utc).isoformat()
def validated_url(value):
    parsed=urllib.parse.urlsplit(value)
    if parsed.scheme!='https' or parsed.hostname not in ALLOWED_HOSTS or parsed.username or parsed.password or parsed.port not in (None,443):
        raise ValueError('Only exact official NSE HTTPS document hosts are allowed')
    return value
class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self,*args,**kwargs):return None
def load_parser(project):
    if not project:return None
    path=Path(project).resolve()/'marketscope/results_service.py'
    if not path.is_file():raise ValueError('Existing project financial parser not found')
    spec=importlib.util.spec_from_file_location('research_exact_financial_parser',path)
    module=importlib.util.module_from_spec(spec);sys.modules[spec.name]=module;spec.loader.exec_module(module)
    return module.parse_financial_document
def document_kind(content):
    prefix=content.lstrip()[:4096].lower()
    if prefix.startswith(b'%pdf-'):return 'PDF_UNPARSED'
    if any(marker in prefix for marker in (b'access denied',b'captcha',b'cloudflare challenge')):return 'BLOCK_PAGE'
    if prefix.startswith((b'<?xml',b'<xbrl',b'<xbrli:xbrl')) or b'contextref=' in content[:500000].lower():return 'XML_OR_IXBRL'
    return 'UNSUPPORTED_FORMAT'
def collect(requests_path,db_path,output_dir,project=None,limit=5,delay=1.0,retry_errors=False,reparse=False,opener=None,sleep=time.sleep):
    if not 1<=limit<=100 or delay<1:raise ValueError('Use a batch limit of1–100 and delay of at least1second')
    parser=load_parser(project);output=Path(output_dir).resolve();output.mkdir(parents=True,exist_ok=True)
    db=Path(db_path);db.parent.mkdir(parents=True,exist_ok=True)
    con=sqlite3.connect(db);con.row_factory=sqlite3.Row
    con.execute('CREATE TABLE IF NOT EXISTS documents(url TEXT PRIMARY KEY,path TEXT,sha256 TEXT,acquired_at TEXT,status TEXT,error TEXT,parsed_count INTEGER,request_metadata TEXT,source_metadata TEXT)')
    opener=opener or urllib.request.build_opener(NoRedirect());attempts=downloaded=parsed=unsupported=0;stop_reason=None;seen=set()
    try:
        with Path(requests_path).open(newline='') as stream:
            for request in csv.DictReader(stream):
                url=validated_url(request['url'])
                if url in seen:continue
                seen.add(url);old=con.execute('SELECT * FROM documents WHERE url=?',(url,)).fetchone()
                cached=bool(old and not old['status'].startswith('ERROR_') and old['path'] and Path(old['path']).is_file())
                terminal=('PARSED','PDF_UNPARSED','UNSUPPORTED_FORMAT','UNSUPPORTED_XML_TAXONOMY','UNSUPPORTED_XML_OR_NUMERIC_FORMAT')
                if old and old['status'] in terminal and not reparse:continue
                if old and old['status'].startswith('ERROR_') and not retry_errors:continue
                if attempts>=limit:break
                attempts+=1;content=None;source_metadata={};acquired=None;raw_path=None;digest=None;status='UNKNOWN';error=None;count=0
                if cached:
                    raw_path=Path(old['path']);content=raw_path.read_bytes();digest=hashlib.sha256(content).hexdigest()
                    if digest!=old['sha256']:raise ValueError('Cached document hash changed')
                    acquired=old['acquired_at'];source_metadata=json.loads(old['source_metadata'] or '{}')
                else:
                    if downloaded:sleep(delay)
                    try:
                        req=urllib.request.Request(url,headers={'User-Agent':'ZienQuantResearch/1.0','Accept':'application/xml,text/xml,application/pdf'})
                        with opener.open(req,timeout=12) as response:
                            content=response.read(MAX_BYTES+1)
                            source_metadata={'content_type':response.headers.get('Content-Type'),'http_last_modified_claim':response.headers.get('Last-Modified'),'etag':response.headers.get('ETag')}
                        if len(content)>MAX_BYTES:raise ValueError('Document exceeds byte budget')
                        acquired=stamp();digest=hashlib.sha256(content).hexdigest();kind=document_kind(content)
                        raw_path=output/(digest+('.pdf' if kind=='PDF_UNPARSED' else '.xml' if kind=='XML_OR_IXBRL' else '.bin'))
                        if not raw_path.exists():raw_path.write_bytes(content)
                        downloaded+=1
                    except urllib.error.HTTPError as exc:
                        status=f'ERROR_HTTP_{exc.code}';error='Official host HTTP response';stop_reason=status
                    except (urllib.error.URLError,TimeoutError,OSError):
                        status='ERROR_NETWORK';error='Network/DNS unavailable; no repeated batch retries';stop_reason=status
                    except ValueError:
                        status='ERROR_SIZE';error='Document exceeded size/schema budget';stop_reason=status
                if content is not None and stop_reason is None:
                    kind=document_kind(content)
                    facts=[]
                    if kind=='BLOCK_PAGE':status='ERROR_BLOCK_PAGE';stop_reason=status
                    elif kind!='XML_OR_IXBRL':status=kind;unsupported+=1
                    elif parser is None:status='PARSER_NOT_CONFIGURED';unsupported+=1
                    else:
                        try:
                            event={'symbol':request['symbol'],'period_end':request['period_end'],'basis':request['basis'],'source_url':url,'document_url':url}
                            facts=parser(content,event)
                            json.dumps(facts,allow_nan=False)
                            status='PARSED' if facts else 'UNSUPPORTED_XML_TAXONOMY'
                            count=len(facts);parsed+=bool(facts);unsupported+=not bool(facts)
                        except (ValueError,TypeError,OverflowError):status='UNSUPPORTED_XML_OR_NUMERIC_FORMAT';facts=[];count=0;unsupported+=1
                    if facts:
                        payload={'symbol':request['symbol'],'source_url':url,'raw_sha256':digest,'acquired_at':acquired,'facts':facts,
                            'announcement_time_claim_from_request':request.get('announcement_at'),'original_public_vintage_verified':False,
                            'publication_time_inferred_from_download':False,'source_metadata':source_metadata}
                        name=hashlib.sha256(url.encode()).hexdigest()[:16]+'-'+digest+'.facts.json'
                        (output/name).write_text(json.dumps(payload,indent=2,allow_nan=False)+'\n')
                con.execute('INSERT OR REPLACE INTO documents VALUES(?,?,?,?,?,?,?,?,?)',
                    (url,str(raw_path) if raw_path else None,digest,acquired,status,error,count,json.dumps(request),json.dumps(source_metadata)));con.commit()
                if stop_reason:break
    finally:con.close()
    return {'attempted_unique_documents':attempts,'new_documents_downloaded':downloaded,'documents_with_parsed_facts':parsed,
        'unsupported_or_unparsed':unsupported,'circuit_break_reason':stop_reason,'original_public_vintage_verified':False,'orders_enabled':False}
def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--requests',required=True);p.add_argument('--db',required=True);p.add_argument('--output-dir',required=True);p.add_argument('--project');p.add_argument('--limit',type=int,default=5);p.add_argument('--delay',type=float,default=1);p.add_argument('--retry-errors',action='store_true');p.add_argument('--reparse',action='store_true')
    args=p.parse_args()
    try:
        result=collect(args.requests,args.db,args.output_dir,args.project,args.limit,args.delay,args.retry_errors,args.reparse)
        print(json.dumps(result,indent=2));return 0 if result['circuit_break_reason'] is None else 2
    except (ValueError,OSError,sqlite3.Error):
        print(json.dumps({'status':'STOPPED','detail':'Check official URL, writable output, request schema and exact parser path.'}),file=sys.stderr);return 1
if __name__=='__main__':raise SystemExit(main())
