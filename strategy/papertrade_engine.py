"""Deterministic candle-based MIS-style simulation. Never imports a trading client.

All timestamps are epoch seconds, candles are START-labelled, and decisions are
made at/after candle END. A signal cannot consume its own candle. OHLC touches
are hypothetical fills, reported at bar-end because exact intrabar times are unknown.
"""
from copy import deepcopy
from datetime import datetime,timedelta,timezone
import hashlib,json,math
from .papertrade_math import (atr_levels,position_size,slipped,order_cost,trade_pnl,
                             side_sign,portfolio_metrics,excursions)
IST=timezone(timedelta(hours=5,minutes=30))


def identity(*values):
    return hashlib.sha256(json.dumps(values,sort_keys=True,separators=(',',':'),allow_nan=False).encode()).hexdigest()[:28]


def at(day,hhmm):
    return datetime.fromisoformat(day+'T'+hhmm+':00+05:30').timestamp()


def validate_candle(c):
    """Require real finite positive OHLC, ordered extremes and explicit provenance."""
    if not c.get('source') or c.get('closed') is False:return False
    for k in ('t','o','h','l','c'):
        v=c.get(k)
        if isinstance(v,bool) or not isinstance(v,(int,float)) or not math.isfinite(v) or v<=0:return False
    return c['l']<=min(c['o'],c['c'])<=max(c['o'],c['c'])<=c['h']


class PaperEngine:
    def __init__(self,config,trades=None,events=None):
        self.cfg=deepcopy(config);self.trades=deepcopy(trades or []);self.events=deepcopy(events or [])
    def event(self,day,stamp,kind,symbol,reason,**details):
        row={'id':identity(day,stamp,kind,symbol,reason,details),'day':day,'at':stamp,
             'kind':kind,'symbol':symbol,'reason':reason,**details}
        if not any(e['id']==row['id'] for e in self.events):self.events.append(row)
    def account(self):
        unknown=any(t['status']=='CLOSED' and t.get('net_pnl') is None for t in self.trades)
        equity=self.cfg['capital']+sum(t.get('net_pnl') or 0 for t in self.trades if t['status']=='CLOSED')
        reserved=sum(t['reserved'] for t in self.trades if t['status'] in ('PENDING','OPEN'))
        return {'equity':None if unknown else equity,'available':None if unknown else max(0.,equity-reserved),'reserved':reserved}
    def signal(self,symbol,stamp,decision,atr,timeframe,source,price,journal_ids=None):
        """Reuse the engine gate/direction, freeze independent ATR plans and costs."""
        cfg=self.cfg;day=datetime.fromtimestamp(stamp,IST).date().isoformat()
        side='LONG' if decision.get('signal_type') in ('BUY','BULL') else 'SHORT' if decision.get('signal_type') in ('SELL','BEAR') else None
        if side is None and decision.get('gated'):
            # Original engine erases direction on gate failure. Infer ONLY from
            # valid preserved shadow stop/target geometry, otherwise leave unknown.
            sl,tp=decision.get('shadow_stop_loss'),decision.get('shadow_target_1')
            if isinstance(sl,(int,float)) and isinstance(tp,(int,float)):
                side='LONG' if sl<price<tp else 'SHORT' if tp<price<sl else None
        event_key=identity(day,symbol,decision.get('observed_candle_end',stamp),decision,atr,timeframe)
        if any(e.get('signal_key')==event_key for e in self.events):return None
        record={'decision':deepcopy(decision),'atr':atr,'timeframe':timeframe,'source':source,'price':price,
                'journal_ids':journal_ids or [],'signal_key':event_key}
        if stamp<at(day,'09:15') or stamp>=at(day,cfg['square_off']):
            self.event(day,stamp,'IGNORED',symbol,'Outside the paper entry session',**record);return None
        if (not side or decision.get('gated') or decision.get('risk_reward_ratio',0)<cfg['min_rr']
                or 'AVOID' in decision.get('action','') or decision.get('grade') not in ('A+','A','B')):
            self.event(day,stamp,'GATE_BLOCKED',symbol,'Original decision engine gate did not pass',side=side,**record);return None
        opened=next((t for t in self.trades if t['day']==day and t['symbol']==symbol and t['status'] in ('PENDING','OPEN')),None)
        if opened:
            opposite=opened['side']!=side
            if (opened['status']=='OPEN' and opposite and cfg['reversal_enabled']
                    and decision.get('conviction_pct',0)>=cfg['reversal_confidence']):
                self.close(opened,stamp,price,'SIGNAL_REVERSAL',source)
                if not cfg['flip_enabled']:
                    self.event(day,stamp,'IGNORED',symbol,'Reversal closed position; flip disabled',**record);return None
            else:
                self.event(day,stamp,'IGNORED',symbol,'Active-symbol dedupe; reversal disabled or confidence insufficient',**record);return None
        try:
            low=decision.get('entry_low');high=decision.get('entry_high')
            if not all(isinstance(v,(int,float)) and math.isfinite(v) and v>0 for v in (low,high)) or low>high:raise ValueError('Missing valid entry zone')
            entry=(low+high)/2
            levels=atr_levels(entry,atr,side,cfg['k_sl'],cfg['k_tp'],max(cfg['min_rr'],decision.get('minimum_rr',cfg['min_rr'])))
            account=self.account()
            if account['available'] is None:raise ValueError('Unpriced prior closure blocks capital allocation')
            size=position_size(max(0.,account['equity']),cfg['risk_per_trade'],entry,levels['stop'],side,
                               cfg['leverage'],cfg['max_capital_per_trade'],account['available'],cfg['fees'],cfg['slippage_bps'])
            if size['qty']<=0:raise ValueError('Insufficient virtual margin or risk budget')
        except (ValueError,TypeError) as exc:
            self.event(day,stamp,'IGNORED',symbol,str(exc),**record);return None
        trade={'id':identity('trade',event_key),'day':day,'symbol':symbol,'side':side,'status':'PENDING',
               'decision_time':stamp,'first_eligible_bar':at(day,'09:15')+math.ceil((stamp-at(day,'09:15'))/(timeframe*60))*timeframe*60,'expires_at':min(stamp+cfg['entry_valid_minutes']*60,at(day,cfg['square_off'])),
               'planned_entry':entry,'stop':levels['stop'],'initial_stop':levels['stop'],'target':levels['target'],
               'atr':atr,'qty':size['qty'],'reserved':size['margin'],'risk_budget':size['risk_budget'],
               'planned_risk':size['planned_risk'],'capital_deployed':size['margin'],
               'timeframe':timeframe,'last_bar':None,'entry_time':None,'exit_time':None,'entry_price':None,
               'exit_price':None,'gross_pnl':None,'net_pnl':None,'return_pct':None,'r_multiple':None,
               'cfg':deepcopy(cfg),'source':source,'journal_ids':journal_ids or [],'signal':record,
               'coverage_complete':True,'bars':[],'trailing_active':False}
        self.trades.append(trade)
        self.event(day,stamp,'PENDING',symbol,'Engine gate passed; independent ATR simulation plan',trade_id=trade['id'],**record)
        return trade
    def close(self,t,stamp,raw_price,reason,source):
        if t['status']!='OPEN':return
        cfg=t['cfg'];d=side_sign(t['side'])
        t.update(status='CLOSED',exit_time=stamp,close_reason=reason,exit_source=source,closed_by='paper simulator')
        if raw_price is None:
            t.update(exit_price=None,raw_exit=None,fees=None,slippage_cost=None,
                     gross_pnl=None,net_pnl=None,return_pct=None,r_multiple=None)
            t['coverage_complete']=False
        else:
            exit=slipped(raw_price,d==-1,cfg['slippage_bps'])
            fee=order_cost(exit,t['qty'],d==-1,cfg['fees'])
            t.update(exit_price=exit,raw_exit=raw_price,exit_fee_breakdown=fee)
            t.update(trade_pnl(t['entry_price'],exit,t['qty'],t['side'],t['entry_fee']+fee['total'],t['capital_deployed'],t['risk_budget']))
            t['slippage_cost']=(abs(t['entry_price']-t['raw_entry'])+abs(exit-raw_price))*t['qty']
        self.event(t['day'],stamp,'CLOSED',t['symbol'],reason,trade_id=t['id'],exit_price=t['exit_price'],net_pnl=t['net_pnl'],source=source)
    def candle(self,symbol,c,timeframe,available_at):
        """Advance using only a completed sourced candle; duplicate bars are idempotent."""
        if not validate_candle(c):return
        end=c['t']+timeframe*60
        if end>available_at:return
        for t in self.trades:
            if t['symbol']!=symbol or t['timeframe']!=timeframe or t['status'] not in ('PENDING','OPEN'):continue
            if datetime.fromtimestamp(c['t'],IST).date().isoformat()!=t['day']:continue
            if c['t']<t['decision_time'] or (t['last_bar'] is not None and c['t']<=t['last_bar']):continue
            cutoff=at(t['day'],t['cfg']['square_off'])
            if end>cutoff:
                if t['status']=='OPEN':self.close(t,cutoff,None,'AUTO_SQUARE_OFF','Missing boundary candle; no exit fill')
                else:t.update(status='EXPIRED',close_reason='UNFILLED_AT_SQUARE_OFF',exit_time=cutoff)
                continue
            if t['last_bar'] is not None and c['t']!=t['last_bar']+timeframe*60:t['coverage_complete']=False
            t['last_bar']=c['t'];t['last_source']=c['source'];t['last_price']=c['c'];t['last_data_timestamp']=end
            d=side_sign(t['side']);cfg=t['cfg'];entry_bar=False
            if t['status']=='PENDING':
                if end>t['expires_at']:
                    t.update(status='EXPIRED',close_reason='ENTRY_EXPIRED',exit_time=t['expires_at'])
                    self.event(t['day'],t['expires_at'],'EXPIRED',symbol,'Entry validity elapsed',trade_id=t['id']);continue
                touched=c['l']<=t['planned_entry'] if d==1 else c['h']>=t['planned_entry']
                if touched:
                    raw=min(c['o'],t['planned_entry']) if d==1 else max(c['o'],t['planned_entry'])
                    if not (raw-t['initial_stop'])*d>0 or not (t['target']-raw)*d>0:
                        t.update(status='EXPIRED',close_reason='INVALID_ENTRY_GAP',exit_time=end)
                        self.event(t['day'],end,'IGNORED',symbol,'Entry gap crossed the plan bracket; no invented valid fill',trade_id=t['id']);continue
                    account=self.account()
                    available=(account['available']+t['reserved']) if account['available'] is not None else 0
                    sized=position_size(max(0.,account['equity'] or 0),cfg['risk_per_trade'],raw,t['initial_stop'],t['side'],cfg['leverage'],cfg['max_capital_per_trade'],available,cfg['fees'],cfg['slippage_bps'])
                    t['qty']=min(t['qty'],sized['qty'])
                    if t['qty']<=0:
                        t.update(status='EXPIRED',close_reason='INSUFFICIENT_MARGIN_AT_FILL',exit_time=end);continue
                    t['planned_risk']=sized['planned_risk']
                    fill=slipped(raw,d==1,cfg['slippage_bps'])
                    if (t['target']-fill)*d<=0:
                        t.update(status='EXPIRED',close_reason='SLIPPAGE_INVALIDATES_ENTRY',exit_time=end)
                        self.event(t['day'],end,'IGNORED',symbol,'Adverse entry fill is beyond target',trade_id=t['id']);continue
                    fee=order_cost(fill,t['qty'],d==1,cfg['fees'])
                    t.update(status='OPEN',raw_entry=raw,entry_price=fill,entry_time=end,entry_bar_start=c['t'],
                             entry_fee=fee['total'],entry_fee_breakdown=fee,entry_source=c['source'],
                             capital_deployed=fill*t['qty']/cfg['leverage']+fee['total'],
                             reserved=fill*t['qty']/cfg['leverage']+fee['total'])
                    entry_bar=True
                    self.event(t['day'],end,'OPEN',symbol,'SIMULATED '+('BUY' if d==1 else 'SELL')+' on candle touch; time is bar-end',trade_id=t['id'],entry_price=fill,source=c['source'])
                elif end>=t['expires_at']:
                    t.update(status='EXPIRED',close_reason='ENTRY_EXPIRED',exit_time=end)
                    self.event(t['day'],end,'EXPIRED',symbol,'Entry validity elapsed unfilled',trade_id=t['id'])
                    continue
                else:continue
            if (d==1 and c['l']<=t['stop']) or (d==-1 and c['h']>=t['stop']):
                raw=min(c['o'],t['stop']) if d==1 else max(c['o'],t['stop'])
                self.close(t,end,raw,'TRAILING_STOP' if t['trailing_active'] else 'STOPLOSS_HIT',c['source']);continue
            if not entry_bar and ((d==1 and c['h']>=t['target']) or (d==-1 and c['l']<=t['target'])):
                self.close(t,end,t['target'],'TARGET_HIT',c['source']);continue
            if end==cutoff:
                self.close(t,end,c['c'],'AUTO_SQUARE_OFF',c['source']);continue
            if end>=t['entry_time']+cfg['time_stop_minutes']*60:
                self.close(t,end,c['c'],'TIME_STOP',c['source']);continue
            if cfg['trailing_enabled']:
                candidate=c['h']-cfg['trailing_atr']*t['atr'] if d==1 else c['l']+cfg['trailing_atr']*t['atr']
                if (candidate-t['stop'])*d>0:
                    t['stop']=candidate;t['trailing_active']=True
    def finish(self,day,now):
        """No boundary candle means an unpriced administrative close, never a fake fill."""
        cutoff=at(day,self.cfg['square_off'])
        if now<cutoff:return
        for t in self.trades:
            if t['day']!=day:continue
            if t['status']=='OPEN':self.close(t,cutoff,None,'AUTO_SQUARE_OFF','Missing 15:15 boundary candle; no exit fill')
            elif t['status']=='PENDING':
                t.update(status='EXPIRED',exit_time=min(t['expires_at'],cutoff),close_reason='ENTRY_EXPIRED')
                self.event(day,t['exit_time'],'EXPIRED',t['symbol'],'No valid entry before cutoff',trade_id=t['id'])


def replay_signal(signal,candles,config):
    """Causal replay: decisions fixed at t, only subsequent complete bars can fill."""
    engine=PaperEngine(config)
    engine.signal(signal['symbol'],signal['stamp'],signal['decision'],signal['atr'],signal['timeframe'],signal['source'],signal['price'])
    for c in sorted(candles,key=lambda c:c['t']):engine.candle(signal['symbol'],c,signal['timeframe'],c['t']+signal['timeframe']*60)
    if engine.trades:engine.finish(engine.trades[0]['day'],at(engine.trades[0]['day'],config['square_off']))
    return engine.trades[0] if engine.trades else None


def build_report(engine,day,archive_rows,generated_at,daily_history=None):
    """Deterministic EOD facts and explicitly hindsight-only same-signal replays.

    Archive rows are keyed (symbol,timeframe); no new signals are mined from future
    charts. Missing/gapped windows suppress MFE and what-if claims.
    """
    cfg=engine.cfg;rows=deepcopy([t for t in engine.trades if t['day']==day]);events=[e for e in engine.events if e['day']==day]
    suggestions=[];replay_summary={v['name']:{'priced':0,'net_pnl':0.,'turned_winner':0,'eligible':0} for v in cfg['what_if']}
    for t in rows:
        allbars=archive_rows.get((t['symbol'],t['timeframe']),[])
        window=[c for c in allbars if t.get('entry_bar_start') is not None and c['t']>=t['entry_bar_start'] and c['t']+t['timeframe']*60<=(t.get('exit_time') or 0)]
        expected=(t.get('exit_time',0) or 0)-(t.get('entry_bar_start',0) or 0)
        complete=(t['status']=='CLOSED' and t.get('exit_price') is not None and t['coverage_complete']
                  and bool(window) and len(window)*t['timeframe']*60==expected
                  and all(validate_candle(c) for c in window)
                  and all(b['t']-a['t']==t['timeframe']*60 for a,b in zip(window,window[1:])))
        # OHLC cannot isolate pre-entry or post-exit ticks inside boundary bars.
        # Use ONLY fully held interior bars for trustworthy conservative MFE/MAE.
        interior=[c for c in window if c['t']>=t.get('entry_time',float('inf')) and c['t']+t['timeframe']*60<(t.get('exit_time') or 0)]
        t['excursion_coverage']='Complete fully held interior bars only; entry/exit-bar intrabar order unknown' if complete else 'Unavailable: incomplete or unpriced trade window'
        t['hindsight']={'label':'HINDSIGHT / HYPOTHETICAL — not future profit',**(excursions(t['entry_price'],t['side'],interior) if complete and interior else excursions(1,'LONG',[])),
                        'best_case_return_pct':None,'capture_efficiency':None,'what_if':[]}
        if t['hindsight']['mfe_price'] is not None:
            raw=t['hindsight']['mfe_price'];exit=slipped(raw,t['side']=='SHORT',t['cfg']['slippage_bps'])
            fees=t['entry_fee']+order_cost(exit,t['qty'],t['side']=='SHORT',t['cfg']['fees'])['total']
            best=trade_pnl(t['entry_price'],exit,t['qty'],t['side'],fees,t['capital_deployed'],t['risk_budget'])
            t['hindsight'].update(best_case_return_pct=best['return_pct'],best_case_net_pnl=best['net_pnl'],capture_efficiency=t['net_pnl']/best['net_pnl'] if best['net_pnl']>0 else None)
        future=[c for c in allbars if c['t']>=t['decision_time'] and c['t']+t['timeframe']*60<=at(day,cfg['square_off'])]
        full=(bool(future) and future[0]['t']==t['first_eligible_bar'] and future[-1]['t']+t['timeframe']*60==at(day,cfg['square_off']) and all(b['t']-a['t']==t['timeframe']*60 for a,b in zip(future,future[1:])))
        for variant in cfg['what_if']:
            altered={**t['cfg'],**{k:v for k,v in variant.items() if k!='name'}}
            signal={'symbol':t['symbol'],'stamp':t['decision_time'],'decision':t['signal']['decision'],'atr':t['atr'],'timeframe':t['timeframe'],'source':t['source'],'price':t['signal']['price']}
            alt=replay_signal(signal,future,altered) if full else None
            item={'name':variant['name'],'net_pnl':alt.get('net_pnl') if alt else None,'return_pct':alt.get('return_pct') if alt else None,
                  'status':alt['status'] if alt else 'INSUFFICIENT_DATA_OR_GATE','basis':'Independent same-signal sizing; not an executable combined portfolio'}
            t['hindsight']['what_if'].append(item)
            summary=replay_summary[variant['name']]
            if alt:summary['eligible']+=1
            if item['net_pnl'] is not None:
                summary['priced']+=1;summary['net_pnl']+=item['net_pnl']
                summary['turned_winner']+=int((t.get('net_pnl') or 0)<0 and item['net_pnl']>0)
        # Raw candle arrays stay in the candle archive, not the HTTP report.
        t.pop('bars',None)
    prior=[t for t in engine.trades if t['day']<day and t['status']=='CLOSED']
    starting=cfg['capital']+sum(t.get('net_pnl') or 0 for t in prior)
    metrics=portfolio_metrics(rows,starting) if starting>0 else {'net_pnl':None,'return_pct':None,'equity_curve':[]}
    if any(t.get('net_pnl') is None for t in prior):
        metrics.update(net_pnl=None,return_pct=None,prior_unpriced=True,max_drawdown_pct=None,equity_curve=[])
        starting=None
    daily_by_day={r['day']:r['return_pct']/100 for r in (daily_history or []) if r.get('return_pct') is not None and r['day']<=day}
    observed_days={t['day'] for t in engine.trades}|{e['day'] for e in engine.events}
    for d in sorted(d for d in observed_days if d<=day):
        subset=[t for t in engine.trades if t['day']==d and t['status']=='CLOSED']
        opening=cfg['capital']+sum(t.get('net_pnl') or 0 for t in engine.trades if t['day']<d and t['status']=='CLOSED')
        unresolved=any(t['day']<=d and ((t['status']=='CLOSED' and t.get('net_pnl') is None) or t['status'] in ('PENDING','OPEN')) for t in engine.trades)
        if not unresolved and opening>0:daily_by_day[d]=sum(t['net_pnl'] for t in subset)/opening
    daily=[daily_by_day[d] for d in sorted(daily_by_day)]
    metrics['sharpe_like']=portfolio_metrics([],cfg['capital'],daily)['sharpe_like']
    metrics['sharpe_days']=len(daily)
    for name,summary in replay_summary.items():
        suggestions.append(f'HINDSIGHT {name}: {summary["priced"]} priced independent replays; {summary["turned_winner"]} observed losing trades became winners. No recommendation of future profitability.')
    shadows=[]
    for e in events:
        if e['kind']!='GATE_BLOCKED':continue
        result=None
        if e.get('side') and e.get('atr'):
            d=deepcopy(e['decision']);d.update(gated=0,signal_type='BUY' if e['side']=='LONG' else 'SELL',grade='B',action='HYPOTHETICAL GATE BYPASS',risk_reward_ratio=cfg['min_rr'],entry_low=e['price'],entry_high=e['price'])
            bars=archive_rows.get((e['symbol'],e['timeframe']),[])
            future=[c for c in bars if e['at']<=c['t'] and c['t']+e['timeframe']*60<=at(day,cfg['square_off'])]
            complete=bool(future) and future[0]['t']==at(day,'09:15')+math.ceil((e['at']-at(day,'09:15'))/(e['timeframe']*60))*e['timeframe']*60 and future[-1]['t']+e['timeframe']*60==at(day,cfg['square_off']) and all(b['t']-a['t']==e['timeframe']*60 for a,b in zip(future,future[1:]))
            if complete:result=replay_signal({'symbol':e['symbol'],'stamp':e['at'],'decision':d,'atr':e['atr'],'timeframe':e['timeframe'],'source':e['source'],'price':e['price']},future,cfg)
        shadows.append({'symbol':e['symbol'],'time':e['at'],'gate_reason':e['decision'].get('gate_reason'),'hypothetical_net_pnl':result.get('net_pnl') if result else None,'label':'HINDSIGHT gate bypass; independent ATR plan, not original engine levels'})
    return {'day':day,'generated_at':generated_at,'paper_only':True,'starting_capital':starting,'metrics':metrics,'trades':rows,'events':events,
            'blocked_signals':shadows,'replay_summary':replay_summary,'suggestions':suggestions,
            'disclaimer':'All achieved results are SIMULATED, not actual brokerage profit. Hindsight/MFE/replays are hypothetical, incomplete-data-sensitive and never promises of future returns.',
            'assumptions':cfg['assumptions'],'config_version':cfg['version']}
