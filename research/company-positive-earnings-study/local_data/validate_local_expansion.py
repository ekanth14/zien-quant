"""Contract and arithmetic checks for the expanded local archive outputs."""
import csv,json,math
from datetime import date,datetime,timedelta
from pathlib import Path
from expand_local_history import compatible,classify

ROOT=Path(__file__).resolve().parent
def main():
    ledger=list(csv.DictReader((ROOT/'eligible_event_coverage.csv').open()))
    pairs=[json.loads(l) for l in (ROOT/'comparable_pairs.jsonl').read_text().splitlines()]
    positives=[json.loads(l) for l in (ROOT/'positive_events.jsonl').read_text().splitlines()]
    assert len({(r['symbol'],r['period_end']) for r in ledger})==len(ledger)
    for e in ledger:
        assert date.fromisoformat(e['announced_at'][:10])>=date(2023,5,6)
        assert float(e['leverage_snapshot'])>3
        assert date.fromisoformat(e['next_trading_date'])>date.fromisoformat(e['announced_at'][:10])
    for pair in pairs:
        a,b=pair['current']['fact'],pair['previous']['fact'];e=pair['event']
        assert compatible(a,b)
        cls,gr,gp=classify(a,b)
        assert e['financial_class']==cls
        assert math.isclose(e['revenue_qoq_pct'],gr,abs_tol=1e-10)
        if gp is not None:assert math.isclose(e['pat_qoq_pct'],gp,abs_tol=1e-10)
        for fact in (pair['current'],pair['previous']):
            assert fact['provenance']
            for source in fact['provenance']:
                assert source['original_public_vintage_verified'] is False
                assert 'public_time_claims' in source and 'collection_time_claims' in source
    complete=[]
    for pair in positives:
        e=pair['event'];assert e['financial_class']=='POSITIVE'
        assert pair['current']['fact']['revenue']>pair['previous']['fact']['revenue']>0
        assert pair['current']['fact']['net_profit']>pair['previous']['fact']['net_profit']>0
        assert e['public_backtest_ready'] is False
        for control in pair['two_preannouncement_date_daily_controls']:
            assert control['date']<e['announced_at'][:10]
        if e['complete_price_window']:
            complete.append(pair)
            bars=pair['candles'];assert len(bars)==21
            expected=datetime.fromisoformat(e['next_trading_date']+'T09:15:00+05:30')
            assert [b['ts'] for b in bars]==[int((expected+timedelta(minutes=5*i)).timestamp()) for i in range(21)]
            assert len({b['source'] for b in bars})==1
            assert math.isclose(e['return_0915_1100_pct'],100*(bars[-1]['c']/bars[0]['o']-1),abs_tol=1e-10)
    assert len({(p['event']['symbol'],p['event']['next_trading_date']) for p in complete})==len(complete)
    result={'status':'passed','ledger_rows_checked':len(ledger),'comparable_pairs_arithmetic_checked':len(pairs),
            'positive_pairs_checked':len(positives),'complete_positive_mornings_checked':len(complete),
            'unique_complete_stock_dates':len(complete),'complete_positive_dates':sorted({p['event']['next_trading_date'] for p in complete}),
            'checks':['uniqueissuerperiod','announcementonlycutoff','strictleverage','literalnextdate','matchingquarterdefinitions','growthatithmetic','separatepublicandacquiredclocks','nocertifiedvintageassumption','preannouncementdailycontrols','21exactbars','nomultipledatecloning','returnarithmetic']}
    (ROOT/'local_validation_results.json').write_text(json.dumps(result,indent=2)+'\n')
    print(json.dumps(result,indent=2))
if __name__=='__main__':main()
