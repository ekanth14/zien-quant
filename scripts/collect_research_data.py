"""Resume existing earnings/financial/FYERS collection and export coverage.

No orders; no invented timestamps. Run on the laptop with network access.
"""
import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from marketscope.env_loader import load_dotenv
load_dotenv()
from marketscope.results_service import get_results_service


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--batches', type=int, default=120)
    parser.add_argument('--enrichment-cycles', type=int, default=1)
    parser.add_argument('--audit-only', action='store_true')
    args = parser.parse_args()
    if not 1 <= args.batches <= 120 or not 0 <= args.enrichment_cycles <= 100:
        parser.error('Use 1–120 batches and 0–100 enrichment cycles')
    service = get_results_service()
    if not args.audit_only:
        result = service.backfill(max_batches=args.batches, date_from='2023-05-06',
                                  date_to=datetime.now().date().isoformat(), enrich=False)
        print('Earnings backfill:', result.get('backfill_job_state', 'unknown'), flush=True)
        for index in range(args.enrichment_cycles):
            status = service.refresh()
            print('Enrichment cycle', index+1, 'state:', status.get('state'), flush=True)
    events = service.history(include_versions=False)
    summary = {'generated_at':datetime.now(timezone.utc).isoformat(),
               'scope_start':'2023-05-06','event_count':len(events),
               'status':service.status(), 'events':[],
               'limitations':['Collection time is not historical public availability.',
                              'An exact announcement timestamp alone does not certify a financial figure was available then.',
                              'Calendar-unverified reaction sessions remain ineligible.',
                              'Complete earnings history does not imply complete numeric comparisons or intraday coverage.']}
    from marketscope.earnings_research import reaction_session
    for event in events:
        summary['events'].append({k:event.get(k) for k in ('id','symbol','announced_at',
            'announcement_time_known','known_at','period_end','basis','financial_status',
            'financials_available_at','original_announcement_unknown')})
        summary['events'][-1]['reaction_session']=reaction_session(event.get('announced_at'))
    target=Path('work/research-coverage');target.mkdir(parents=True,exist_ok=True)
    path=target/('earnings-'+datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')+'.json')
    with path.open('x') as stream: json.dump(summary,stream,indent=2)
    print('Coverage evidence:',path)


if __name__ == '__main__': main()
