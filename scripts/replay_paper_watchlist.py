#!/usr/bin/env python3
"""Replay stored candles into an isolated research report; never mutates live ledger."""
import argparse,json,tempfile,sys
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
from marketscope.paper_watchlist import PaperWatchlist
from strategy.papertrade_replay import replay_archive
parser=argparse.ArgumentParser(description=__doc__)
parser.add_argument('--day',required=True);parser.add_argument('--symbols',required=True,nargs='+')
parser.add_argument('--candles',default=str(ROOT/'work/runtime/candles.db'))
parser.add_argument('--config',default=str(ROOT/'config/papertrade.yaml'));parser.add_argument('--output',required=True)
args=parser.parse_args()
with tempfile.TemporaryDirectory() as scratch:
    reader=PaperWatchlist(Path(scratch)/'scratch.db',args.candles,args.config)
    report=replay_archive(reader,args.day,args.symbols)
    target=Path(args.output);target.parent.mkdir(parents=True,exist_ok=True);target.write_text(json.dumps(report,indent=2,allow_nan=False))
    print(json.dumps({'output':str(target),'metrics':report['metrics'],'coverage':report['replay_audit']},indent=2))
