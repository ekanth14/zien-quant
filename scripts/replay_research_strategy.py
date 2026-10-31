"""Offline research CLI. Input JSON {symbol: [sourced OHLCV candles]} only."""
import argparse,json,sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from strategy.research_lab import replay,cost_stress,EXECUTABLE
from marketscope.paper_watchlist import load_config

def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--input',type=Path,required=True)
    p.add_argument('--day',required=True)
    p.add_argument('--strategy',choices=EXECUTABLE,required=True)
    p.add_argument('--output',type=Path,required=True)
    p.add_argument('--retrospective',action='store_true',help='Explicitly assume archived bars were available at close; not point-in-time validation')
    p.add_argument('--cost-stress',action='store_true',help='Separate 1x, 1.5x and 2x fee/slippage replays')
    args=p.parse_args()
    runner=cost_stress if args.cost_stress else replay
    result=runner(args.strategy,args.day,json.loads(args.input.read_text()),load_config(),retrospective=args.retrospective)
    # Exclusive creation protects prior experiments and the original data file.
    with args.output.open('x') as stream: json.dump(result,stream,indent=2,allow_nan=False)
    print(f'Research-only replay saved: {args.output}')
if __name__=='__main__':main()
