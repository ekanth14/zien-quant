"""Separate persisted ATR paper watchlist, reusing decision/journal/archive modules.

No Kite or broker trading client. HTTP snapshots and the server-owned monitor
both feed the same serialized ledger. Empty feeds never create simulated positions.
"""
from __future__ import annotations
import csv,io,json,math,sqlite3,threading,time,hashlib
from pathlib import Path
from datetime import datetime
from contextlib import closing
try:
    from .market_schedule import IST
    from .market import (calculate_atr,calculate_ema,calculate_rsi,calculate_vwap,
                         calculate_rvol,calculate_support_resistance,calculate_order_blocks,calculate_composite_score)
    from .decision_engine import evaluate_ai_decision
except ImportError:
    from market_schedule import IST
    from market import (calculate_atr,calculate_ema,calculate_rsi,calculate_vwap,
                        calculate_rvol,calculate_support_resistance,calculate_order_blocks,calculate_composite_score)
    from decision_engine import evaluate_ai_decision
from strategy.papertrade_engine import PaperEngine,build_report,at,validate_candle
from strategy.papertrade_math import order_cost,slipped,gross_pnl

ROOT=Path(__file__).resolve().parent.parent

def load_config(path=None):
    # JSON syntax is a strict YAML subset; avoids a new runtime dependency.
    cfg=json.loads(Path(path or ROOT/'config/papertrade.yaml').read_text())
    for key in ('capital','risk_per_trade','leverage','max_capital_per_trade','k_sl','k_tp','min_rr','entry_valid_minutes','time_stop_minutes','trailing_atr','reversal_confidence','timeframe_minutes','max_candle_lag_seconds'):
        value=cfg[key]
        if isinstance(value,bool) or not isinstance(value,(int,float)) or not math.isfinite(value) or value<=0:raise ValueError('Invalid paper config: '+key)
    if not isinstance(cfg['slippage_bps'],(int,float)) or isinstance(cfg['slippage_bps'],bool) or not 0<=cfg['slippage_bps']<10000:raise ValueError('Invalid slippage')
    for key in ('enabled','trailing_enabled','reversal_enabled','flip_enabled'):
        if type(cfg[key]) is not bool:raise ValueError('Invalid boolean '+key)
    if cfg['risk_per_trade']>1 or cfg['leverage']<1 or cfg['k_tp']/cfg['k_sl']<cfg['min_rr']:raise ValueError('Invalid risk/leverage/R:R config')
    if cfg['entry_policy']!='zone_midpoint' or cfg['atr_period']!=14:raise ValueError('Only explicit midpoint and ATR(14) policy supported')
    if cfg['timeframe_minutes'] not in (1,5,15,60):raise ValueError('Unsupported configured timeframe')
    if cfg['flip_enabled'] and not cfg['reversal_enabled']:raise ValueError('Flip requires reversal enabled')
    if not ('09:15'<cfg['square_off']<='15:15'<cfg['report_time']<='23:59'):raise ValueError('Invalid session/report clocks')
    for key,value in cfg['fees'].items():
        if isinstance(value,bool) or not isinstance(value,(int,float)) or not math.isfinite(value) or value<0:raise ValueError('Invalid fee '+key)
    return cfg


def watchlist_session(day,cfg):
    """Use the installed published regular calendar; never infer special hours."""
    cal=cfg['calendar']
    if day.year!=cal['year'] or day.weekday()>=5 or day.isoformat() in cal['holidays'] or day.isoformat() in cal['unconfigured_special_sessions']:
        return None
    return {'date':day.isoformat(),'open':cal['regular_open'],'end':cal['conservative_end'],'source':cal['source']}


def evaluate_closed_prefix(symbol,candles,cfg=None):
    """Existing indicator/decision functions on this prefix ONLY; no Laya calls.

    Full ATR(14) requires 15 closes. No engine artificial ATR fallback is accepted.
    A signal at candle end cannot use any later row or daily final OHLC.
    """
    cfg=cfg or {};candles=[c for c in candles if validate_candle(c)]
    if len(candles)<max(15,int(cfg.get('slow_ema_period',21))):return None,None
    closes=[c['c'] for c in candles];price=closes[-1]
    fast=calculate_ema(closes,int(cfg.get('fast_ema_period',9)))[-1]
    slow=calculate_ema(closes,int(cfg.get('slow_ema_period',21)))[-1]
    momentum=(price/closes[-6]-1)*100
    day=datetime.fromtimestamp(candles[-1]['t'],IST).date()
    session=[c for c in candles if datetime.fromtimestamp(c['t'],IST).date()==day]
    vwap=calculate_vwap(session);rsi=calculate_rsi(closes,int(cfg.get('rsi_period',14)))
    rvol=calculate_rvol([c.get('v',0) for c in candles]);support,resistance=calculate_support_resistance(candles)
    metrics=calculate_composite_score(fast,slow,momentum,price,vwap,rsi,config=cfg)
    atr=calculate_atr(candles,14)
    decision=evaluate_ai_decision(symbol,price,metrics,vwap,support,resistance,rvol,calculate_order_blocks(candles),config=cfg,closed_confirming=True,atr=atr)
    decision['minimum_rr']=float(cfg.get('min_rr_hurdle',1.5))
    return decision,atr


class PaperWatchlist:
    def __init__(self,db_path=None,candles_path=None,config_path=None,now_fn=None):
        self.db_path=str(db_path or ROOT/'work/runtime/marketscope.db')
        self.candles_path=str(candles_path or ROOT/'work/runtime/candles.db')
        self.cfg=load_config(config_path);self.now=now_fn or (lambda:datetime.now(IST))
        self.lock=threading.RLock();self.stop_event=threading.Event();self.thread=None;self.error=None
        Path(self.db_path).parent.mkdir(parents=True,exist_ok=True)
        with self.connection() as con:
            con.executescript((ROOT/'marketscope/migrations/006_paper_watchlist.sql').read_text())
            self._report_days={r[0] for r in con.execute('SELECT day FROM paper_watch_reports')}
            trades=[json.loads(r[0]) for r in con.execute('SELECT payload FROM paper_watch_trades ORDER BY day,id')]
            events=[json.loads(r[0]) for r in con.execute('SELECT payload FROM paper_watch_events ORDER BY day,at,id')]
        self.engine=PaperEngine(self.cfg,trades,events)
    def connection(self):
        con=sqlite3.connect(self.db_path,timeout=10);con.execute('PRAGMA journal_mode=WAL');return closing_connection(con)
    def save(self):
        with self.connection() as con:
            for t in self.engine.trades:
                con.execute('INSERT INTO paper_watch_trades VALUES(?,?,?,?,?) ON CONFLICT(id) DO UPDATE SET status=excluded.status,payload=excluded.payload',
                            (t['id'],t['day'],t['symbol'],t['status'],json.dumps(t,allow_nan=False)))
            for e in self.engine.events:
                con.execute('INSERT OR IGNORE INTO paper_watch_events VALUES(?,?,?,?,?,?)',
                            (e['id'],e['day'],str(e['at']),e['kind'],e['symbol'],json.dumps(e,allow_nan=False)))
    def archive(self,symbol,timeframe,before=None,day=None,limit=None):
        if not Path(self.candles_path).exists():return []
        params=[symbol,timeframe];where='symbol=? AND timeframe=?'
        if before is not None:where+=' AND ts+?<=?';params.extend([timeframe*60,before])
        if day:where+=' AND ts>=? AND ts<?';params.extend([at(day,'00:00'),at(day,'23:59')+60])
        query='SELECT ts,o,h,l,c,v,source FROM candles WHERE '+where+' ORDER BY ts'+(' DESC LIMIT ?' if limit else '')
        if limit:params.append(limit)
        with closing(sqlite3.connect('file:'+self.candles_path+'?mode=ro',uri=True)) as con:
            values=con.execute(query,params).fetchall()
        rows=[dict(zip(('t','o','h','l','c','v','source'),r),closed=True) for r in values]
        return list(reversed(rows)) if limit else rows
    def observe_snapshot(self,snapshot,timeframe=5,engine_config=None,journal_ids=None):
        now=self.now();day=now.date().isoformat();session=watchlist_session(now.date(),self.cfg)
        if not self.cfg['enabled'] or not session or not at(day,'09:15')<=now.timestamp()<at(day,self.cfg['square_off']):return
        if snapshot.get('data_mode')!='LIVE' or snapshot.get('session_state')!='LIVE':return
        with self.lock:
            self.advance(now.timestamp())
            for symbol,stock in snapshot.get('stocks',{}).items():
                if stock.get('status')!='ok':continue
                bars=self.archive(symbol,timeframe,before=now.timestamp(),limit=250)
                if not bars:continue
                latest=bars[-1];stamp=latest['t']+timeframe*60
                if (datetime.fromtimestamp(stamp,IST).date()!=now.date()
                        or now.timestamp()-stamp>self.cfg['max_candle_lag_seconds']):
                    self.engine.event(day,now.timestamp(),'IGNORED',symbol,'Missing/stale closed source candle; no pending trade');continue
                decision,atr=evaluate_closed_prefix(symbol,bars,engine_config)
                if decision is None:continue
                # Snapshot's provisional direction never overrides closed-candle checks.
                terminal=stock.get('ai_decision') or {}
                if terminal.get('gated'):
                    decision=dict(terminal)
                elif terminal.get('signal_type') not in ('BUY','SELL'):
                    continue
                elif terminal.get('risk_reward_ratio',0)<max(self.cfg['min_rr'],float((engine_config or {}).get('min_rr_hurdle',1.5))):
                    decision={**terminal,'gated':1,'gate_reason':'Original engine R:R failed configured gate'}
                elif decision.get('signal_type')!=terminal['signal_type']:
                    self.engine.event(day,stamp,'IGNORED',symbol,'Terminal provisional setup lacks matching closed-candle confirmation');continue
                decision['observed_candle_end']=stamp
                linked=[]
                if journal_ids:
                    with self.connection() as con:
                        linked=[r[0] for r in con.execute('SELECT id FROM signals WHERE symbol=? AND id IN ('+','.join('?' for _ in journal_ids)+')',[symbol,*journal_ids])]
                self.engine.signal(symbol,now.timestamp(),decision,atr,timeframe,latest['source'],latest['c'],linked)
            self.save()
    def advance(self,now_stamp):
        before=[(t['id'],t['status'],t['last_bar'],t.get('stop'),t.get('net_pnl')) for t in self.engine.trades]
        # Replay only rows whose complete candle ended by now. Closed positions
        # and duplicate timestamps cannot execute twice after a server restart.
        active={(t['symbol'],t['timeframe'],t['day']) for t in self.engine.trades if t['status'] in ('PENDING','OPEN')}
        for symbol,timeframe,day in sorted(active):
            for c in self.archive(symbol,timeframe,before=now_stamp,day=day):self.engine.candle(symbol,c,timeframe,now_stamp)
        for t in self.engine.trades:
            if t['status']=='PENDING' and now_stamp>=t['expires_at']:
                t.update(status='EXPIRED',exit_time=t['expires_at'],close_reason='ENTRY_EXPIRED')
                self.engine.event(t['day'],t['expires_at'],'EXPIRED',t['symbol'],'No observed touch within validity',trade_id=t['id'])
        for day in {t['day'] for t in self.engine.trades}:self.engine.finish(day,now_stamp)
        after=[(t['id'],t['status'],t['last_bar'],t.get('stop'),t.get('net_pnl')) for t in self.engine.trades]
        return before!=after
    def report(self,day=None,generate=False):
        with self.lock:
            if day:
                datetime.strptime(day,'%Y-%m-%d')
            with self.connection() as con:
                saved=con.execute('SELECT payload FROM paper_watch_reports'+(' WHERE day=?' if day else ' ORDER BY day DESC LIMIT 1'),(day,) if day else ()).fetchone()
            if saved:return json.loads(saved[0])
            day=day or self.now().date().isoformat()
            if not generate:return {'day':day,'status':'EMPTY','trades':[],'disclaimer':'No saved completed-session report. No simulated data.'}
            now=self.now().timestamp()
            if now<at(day,self.cfg['report_time']):raise ValueError('Final report available at configured 15:35 IST or later')
            self.advance(now)
            keys={(t['symbol'],t['timeframe']) for t in self.engine.trades if t['day']==day}
            keys.update((e['symbol'],e['timeframe']) for e in self.engine.events if e['day']==day and e['kind']=='GATE_BLOCKED')
            archived={key:self.archive(*key,before=now,day=day) for key in keys}
            with self.connection() as con:
                history=[json.loads(r[0]) for r in con.execute('SELECT payload FROM paper_watch_reports WHERE day<? ORDER BY day',(day,))]
            daily_history=[{'day':r['day'],'return_pct':r['metrics'].get('return_pct')} for r in history]
            report=build_report(self.engine,day,archived,now,daily_history)
            with self.connection() as con:con.execute('INSERT OR IGNORE INTO paper_watch_reports VALUES(?,?,?)',(day,str(now),json.dumps(report,allow_nan=False)))
            self._report_days.add(day)
            self.save();return report
    def status(self):
        with self.lock:
            now=self.now();day=now.date().isoformat();session=watchlist_session(now.date(),self.cfg)
            rows=[]
            for t in self.engine.trades:
                if t['day']!=day:continue
                row={k:v for k,v in t.items() if k not in ('bars','cfg')};row['unrealized_pnl']=None
                if t['status']=='OPEN' and t.get('last_data_timestamp') and 0<=now.timestamp()-t['last_data_timestamp']<=self.cfg['max_candle_lag_seconds']:
                    exit=slipped(t['last_price'],t['side']=='SHORT',t['cfg']['slippage_bps'])
                    fee=order_cost(exit,t['qty'],t['side']=='SHORT',t['cfg']['fees'])['total']+t['entry_fee']
                    row['unrealized_pnl']=gross_pnl(t['entry_price'],exit,t['qty'],t['side'])-fee
                row['stale']=not t.get('last_data_timestamp') or now.timestamp()-t['last_data_timestamp']>self.cfg['max_candle_lag_seconds']
                row['data_status']='Stale/no current mark' if row['stale'] else 'Closed-candle observation; not an executable tick quote'
                rows.append(row)
            with self.connection() as con:
                days=[r[0] for r in con.execute('SELECT day FROM paper_watch_reports ORDER BY day DESC LIMIT 90')]
            market_open=bool(session) and at(day,'09:15')<=now.timestamp()<at(day,self.cfg['square_off'])
            return {'market_open':market_open,'report_days':days,'paper_only':True,'experimental':True,'enabled':self.cfg['enabled'],'error':self.error,
                    'now':now.timestamp(),'calendar_verified':now.year==self.cfg['calendar']['year'],'session_verified':bool(session),'day':day,'square_off':at(day,self.cfg['square_off']),
                    'report_at':at(day,self.cfg['report_time']),'account':self.engine.account(),'trades':rows,
                    'events':[e for e in self.engine.events if e['day']==day][-100:],'config':self.cfg,
                    'last_report':self.report() if not market_open else None}
    def export(self,day=None,fmt='json'):
        report=self.report(day)
        if fmt=='json':return json.dumps(report,indent=2,allow_nan=False)
        out=io.StringIO();fields=['record_type','id','symbol','side','status','entry_time','exit_time','entry_price','exit_price','qty','initial_stop','target','close_reason','gross_pnl','fees','net_pnl','return_pct','r_multiple','details']
        writer=csv.DictWriter(out,fieldnames=fields);writer.writeheader()
        def safe(v):return "'"+v if isinstance(v,str) and v.startswith(('=','+','-','@')) else v
        writer.writerow({'record_type':'PAPER SUMMARY','details':safe(json.dumps({k:v for k,v in report.items() if k not in ('trades','events')}))})
        for row in report.get('trades',[]):
            writer.writerow({**{k:safe(row.get(k)) for k in fields if k in row},'record_type':'SIMULATED TRADE','details':safe(json.dumps(row.get('hindsight',{})))})
        for row in report.get('events',[]):writer.writerow({'record_type':'EVENT '+row['kind'],'symbol':safe(row['symbol']),'details':safe(json.dumps(row))})
        return out.getvalue()
    def start(self,db_manager=None):
        if self.thread:return
        def loop():
            last_fetch=0
            while not self.stop_event.wait(2):
                try:
                    now=self.now();day=now.date().isoformat();session=watchlist_session(now.date(),self.cfg)
                    with self.lock:
                        if self.advance(now.timestamp()):self.save()
                    if session and self.cfg['enabled'] and at(day,'09:15')<=now.timestamp()<at(day,self.cfg['square_off']) and time.monotonic()-last_fetch>=30:
                        last_fetch=time.monotonic()
                        try:
                            from .scraper import get_live_scraper_feed
                            from .signal_journal import SignalJournal
                        except ImportError:
                            from scraper import get_live_scraper_feed
                            from signal_journal import SignalJournal
                        config=db_manager.get_latest_config() if db_manager else None
                        snapshot=get_live_scraper_feed().get_snapshot(timeframe=self.cfg['timeframe_minutes'],config=config)
                        snapshot.update(data_mode='LIVE',session_state='LIVE')
                        ids=SignalJournal(db_manager).log_snapshot_signals(snapshot,timeframe=self.cfg['timeframe_minutes']) if db_manager else []
                        self.observe_snapshot(snapshot,self.cfg['timeframe_minutes'],config,ids)
                    if session and now.timestamp()>=at(day,self.cfg['report_time']):self.report(day,generate=True)
                    self.error=None
                except Exception as exc:self.error='Paper watchlist update failed: '+type(exc).__name__
        self.thread=threading.Thread(target=loop,name='paper-watchlist-data',daemon=True);self.thread.start()
        # A slow scanner must not delay administrative square-off or expiry.
        def deadline():
            while not self.stop_event.wait(1):
                with self.lock:
                    try:
                        now=self.now()
                        if self.advance(now.timestamp()):self.save()
                        days={t['day'] for t in self.engine.trades}|{e['day'] for e in self.engine.events}
                        if watchlist_session(now.date(),self.cfg):days.add(now.date().isoformat())
                        for day in sorted(days-self._report_days):
                            if now.timestamp()>=at(day,self.cfg['report_time']):self.report(day,generate=True)
                    except Exception:self.error='Paper watchlist cutoff/report/storage failure'
        self.watchdog=threading.Thread(target=deadline,name='paper-watchlist-cutoff',daemon=True);self.watchdog.start()
    def stop(self):
        self.stop_event.set()
        if self.thread:self.thread.join(timeout=3)
        if hasattr(self,'watchdog'):self.watchdog.join(timeout=2)


class closing_connection:
    def __init__(self,conn):self.conn=conn
    def __enter__(self):return self.conn
    def __exit__(self,kind,value,tb):
        try:
            if kind is None:self.conn.commit()
            else:self.conn.rollback()
        finally:self.conn.close()

_instances={}
_lock=threading.Lock()
def get_paper_watchlist(db_path=None):
    import os
    key=str(Path(db_path or os.environ.get('MS_DB_PATH') or ROOT/'work/runtime/marketscope.db').resolve())
    with _lock:
        if key not in _instances:
            _instances[key]=PaperWatchlist(db_path=key,candles_path=os.environ.get('MS_CANDLES_DB_PATH'))
        return _instances[key]
