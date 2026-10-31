"""Pure deterministic paper-trade mathematics. No I/O, randomness or market fetching.

Prices in fill-based P&L already contain slippage. Therefore net=gross-fees;
slippage_cost is a separate benchmark disclosure, never a second deduction.
Rates are decimal fractions; risk_per_trade=.01 means one percent.
"""
import math
from statistics import mean, pstdev


def number(value, name='value', minimum=0):
    """Reject nonfinite/bool inputs instead of silently producing simulated money."""
    if isinstance(value, bool) or not isinstance(value, (int,float)) or not math.isfinite(value) or value < minimum:
        raise ValueError(f'{name} must be finite and ≥{minimum}')
    return float(value)


def side_sign(side):
    """LONG buys from cash; SHORT sells from zero holdings and buys to cover."""
    if side not in ('LONG','SHORT'):raise ValueError('side must be LONG or SHORT')
    return 1 if side=='LONG' else -1


def atr_levels(entry, atr, side, k_sl=1, k_tp=2, min_rr=1.5):
    """SL=entry−direction*k_sl*ATR; target=entry+direction*k_tp*ATR."""
    entry=number(entry,'entry',.000001);atr=number(atr,'ATR',.000001)
    k_sl=number(k_sl,'k_sl',.000001);k_tp=number(k_tp,'k_tp',.000001)
    if k_tp/k_sl < number(min_rr,'minimum R:R',.000001):raise ValueError('ATR R:R below gate')
    d=side_sign(side);stop=entry-d*k_sl*atr;target=entry+d*k_tp*atr
    if min(stop,target)<=0:raise ValueError('Nonpositive ATR price level')
    return {'entry':entry,'stop':stop,'target':target,'rr':k_tp/k_sl}


def slipped(price, buy, bps=5):
    """Adverse slippage on each simulated fill; bps/10000 is a fraction."""
    price=number(price,'price',.000001);bps=number(bps,'slippage bps')
    if bps>=10000:raise ValueError('slippage must be <10000 bps')
    return price*(1+(1 if buy else -1)*bps/10000)


def order_cost(price, qty, buy, rates):
    """One-order brokerage/taxes. GST includes brokerage, exchange and SEBI fees.

    STT is on the sell side, stamp on the buy side, also for SHORT entry/cover.
    Per-order fractional values are retained; contract-note rounding is not emulated.
    """
    turnover=number(price,'price')*number(qty,'quantity')
    if not qty:return {k:0. for k in ('brokerage','stt','exchange','sebi','gst','stamp','total')}
    rate=lambda k:number(rates.get(k,0),k)
    brokerage=min(rate('brokerage_cap'),turnover*rate('brokerage_rate'))
    exchange=turnover*rate('exchange_rate');sebi=turnover*rate('sebi_rate')
    items={'brokerage':brokerage,'stt':0 if buy else turnover*rate('stt_sell_rate'),
           'exchange':exchange,'sebi':sebi,'gst':(brokerage+exchange+sebi)*rate('gst_rate'),
           'stamp':turnover*rate('stamp_buy_rate') if buy else 0}
    return {**items,'total':sum(items.values())}


def gross_pnl(entry, exit, qty, side):
    """Price P&L using actual simulated fills, including short-from-zero sign."""
    return (number(exit,'exit')-number(entry,'entry'))*number(qty,'quantity')*side_sign(side)


def trade_pnl(entry, exit, qty, side, fees, deployed, risk):
    """Net=gross−fees; return on reserved margin; R on initial risk budget."""
    gross=gross_pnl(entry,exit,qty,side);fees=number(fees,'fees')
    return {'gross_pnl':gross,'fees':fees,'net_pnl':gross-fees,
            'return_pct':100*(gross-fees)/deployed if deployed>0 else None,
            'r_multiple':(gross-fees)/risk if risk>0 else None}


def position_size(capital, risk_per_trade, entry, stop, side, leverage, max_capital, available, fees, slippage_bps):
    """Whole shares capped by margin and planned stop loss INCLUDING costs/slippage.

    Binary-search quantity accounts for the brokerage cap and keeps planned risk
    ≤capital*risk_per_trade. Actual gaps can exceed planned risk.
    """
    d=side_sign(side);capital=number(capital,'capital');risk_fraction=number(risk_per_trade,'risk fraction')
    if risk_fraction>1:raise ValueError('risk fraction must be ≤1')
    leverage=number(leverage,'leverage',1);available=number(available,'available')
    max_capital=number(max_capital,'max capital');entry=number(entry,'entry',.000001)
    stop=number(stop,'stop',.000001)
    if (entry-stop)*d<=0:return {'qty':0,'margin':0.,'risk_budget':capital*risk_fraction,'planned_risk':0.}
    fill=slipped(entry,d==1,slippage_bps);stop_fill=slipped(stop,d==-1,slippage_bps)
    risk_budget=capital*risk_fraction;budget=min(max_capital,available)
    def values(q):
        entry_fee=order_cost(fill,q,d==1,fees)['total']
        exit_fee=order_cost(stop_fill,q,d==-1,fees)['total']
        return fill*q/leverage+entry_fee, max(0.,-gross_pnl(fill,stop_fill,q,side))+entry_fee+exit_fee
    lo,hi=0,max(0,int(budget*leverage/fill))
    while lo<hi:
        mid=(lo+hi+1)//2;margin,risk=values(mid)
        if margin<=budget and risk<=risk_budget:lo=mid
        else:hi=mid-1
    margin,risk=values(lo)
    return {'qty':lo,'margin':margin,'risk_budget':risk_budget,'planned_risk':risk}


def excursions(entry, side, candles):
    """Raw-price MFE/MAE, requiring the caller to certify complete entry/exit bars."""
    d=side_sign(side)
    if not candles:return {'mfe_price':None,'mae_price':None,'mfe_pct':None,'mae_pct':None}
    high=max(c['h'] for c in candles);low=min(c['l'] for c in candles)
    favorable=max(entry,high) if d==1 else min(entry,low)
    adverse=min(entry,low) if d==1 else max(entry,high)
    return {'mfe_price':favorable,'mae_price':adverse,'mfe_pct':100*d*(favorable-entry)/entry,
            'mae_pct':100*d*(adverse-entry)/entry}


def portfolio_metrics(trades, capital, daily_returns=None):
    """Realized virtual results, chronological closed-equity drawdown and daily Sharpe.

    Unknown P&L makes combined returns unknown. Sharpe needs two distinct daily
    returns with nonzero dispersion; one trading day never gets a Sharpe score.
    """
    closed=[t for t in trades if t['status']=='CLOSED'];priced=[t for t in closed if t.get('net_pnl') is not None]
    values=[t['net_pnl'] for t in priced];wins=[v for v in values if v>0];losses=[v for v in values if v<0]
    unknown=len(priced)!=len(closed) or any(t['status']=='OPEN' for t in trades)
    equity=peak=capital;drawdown=0.;curve=[]
    for t in sorted(priced,key=lambda x:(x.get('exit_time') or '',x['id'])):
        equity+=t['net_pnl'];peak=max(peak,equity)
        drawdown=max(drawdown,(peak-equity)/peak*100 if peak else 0)
        curve.append({'time':t['exit_time'],'equity':equity})
    rs=[t['r_multiple'] for t in priced if t.get('r_multiple') is not None]
    daily=daily_returns or [];std=pstdev(daily) if len(daily)>=2 else 0
    return {'net_pnl':None if unknown else sum(values),'return_pct':None if unknown else sum(values)/capital*100,
            'priced_trades':len(values),'unpriced_trades':len(closed)-len(priced),
            'win_rate':100*len(wins)/len(values) if values else None,
            'average_win':mean(wins) if wins else None,'average_loss':mean(losses) if losses else None,
            'profit_factor':sum(wins)/abs(sum(losses)) if losses else None,
            'expectancy_r':mean(rs) if rs else None,'max_drawdown_pct':None if unknown else drawdown,
            'sharpe_like':mean(daily)/std*math.sqrt(252) if std>0 else None,'equity_curve':curve,
            'metric_basis':'Priced closed simulated trades only; drawdown on realized equity, excluding intratrade marks.',
            'best_trade':max(priced,key=lambda t:t['net_pnl'])['id'] if priced else None,
            'worst_trade':min(priced,key=lambda t:t['net_pnl'])['id'] if priced else None,
            'expired':sum(t['status']=='EXPIRED' for t in trades)}
