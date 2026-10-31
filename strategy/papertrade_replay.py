"""Offline causal replay of existing candles.db, never a broker execution path."""
from datetime import datetime
from .papertrade_engine import PaperEngine,at,build_report,IST,validate_candle


def replay_archive(reader,day,symbols,engine_config=None):
    """At each timestamp process known candles, then emit closed-prefix decisions.

    Simultaneous signals share capital in lexicographic symbol order. Historical
    archive provenance is not certified point-in-time availability or profitability.
    Warm-up rows precede session opening; current day's future rows never enter a
    decision prefix. Caller owns a scratch ledger, not the website's live ledger.
    """
    from marketscope.paper_watchlist import evaluate_closed_prefix
    tf=reader.cfg['timeframe_minutes'];end=at(day,reader.cfg['square_off'])
    prefixes={};today={};timeline=[]
    for symbol in sorted(set(symbols)):
        prefixes[symbol]=reader.archive(symbol,tf,before=at(day,'09:15'),limit=250)
        today[symbol]=[c for c in reader.archive(symbol,tf,day=day,before=end) if at(day,'09:15')<=c['t'] and validate_candle(c)]
        timeline.extend((c['t']+tf*60,symbol,c) for c in today[symbol])
    engine=PaperEngine(reader.cfg)
    for stamp,symbol,c in sorted(timeline,key=lambda x:(x[0],x[1])):
        engine.candle(symbol,c,tf,stamp)
        prefixes[symbol].append(c)
        decision,atr=evaluate_closed_prefix(symbol,prefixes[symbol],engine_config)
        if decision is not None and stamp<end:
            decision['observed_candle_end']=stamp
            engine.signal(symbol,stamp,decision,atr,tf,c['source'],c['c'])
    engine.finish(day,end)
    report=build_report(engine,day,{(s,tf):bars for s,bars in today.items()},at(day,reader.cfg['report_time']))
    expected=int((end-at(day,'09:15'))/(tf*60))
    report['replay_audit']={'mode':'OFFLINE HISTORICAL SIMULATION, not live paper fills','source_file':reader.candles_path,
                          'rows':{s:len(rows) for s,rows in today.items()},'expected_per_symbol':expected,
                          'complete':{s:len(rows)==expected and all(b['t']-a['t']==tf*60 for a,b in zip(rows,rows[1:])) for s,rows in today.items()},
                          'simultaneous_allocation_order':'lexicographic symbols; shared capital',
                          'lookahead':'No candle with end after decision time is used. No data-vintage certification.'}
    return report
