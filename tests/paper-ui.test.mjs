import {test} from 'node:test';
import assert from 'node:assert/strict';
import {parseHTML} from 'linkedom';
import {mountPaperTrading} from '../marketscope/static/paper-trading.js';
function fixture(){return {state:'No strategy configured',version:'test-v1',reviewed:false,settings:{capital:300000,allocation_pct:100/3,risk_pct:.5,fee_flat:20,fee_bps:5,spread_bps:4,slippage_bps:3,max_age_seconds:15,cutoff:'11:00'},portfolio:{capital:300000,reserved:0,available:300000,net_pnl:0,return_pct:0,trade_count:0},session:null,next_session:null,server_time:'2026-10-30T08:00:00+05:30',calendar_source:'https://www.nseindia.com/',calendar_valid_through:'2026-11-06',data_configured:false,quotes:{},trades:[],rows:{},log:[],rules:['TEST experimental rules'],last_poll:null};}
const settle=async()=>{for(let i=0;i<10;i++)await Promise.resolve();};
test('paper view displays empty virtual capital and review gate without invented trades',async()=>{
 const {document}=parseHTML('<main id="root"></main>');const root=document.querySelector('main');const calls=[];
 const cleanup=mountPaperTrading(root,async(path,opts)=>{calls.push([path,opts]);return fixture();});
 try{await settle();assert.match(root.textContent,/PAPER/);assert.match(root.textContent,/3,00,000/);assert.match(root.textContent,/No strategy configured/);assert.match(root.textContent,/No valid trades/);assert.equal(root.querySelectorAll('#paperRows tr').length,3);assert.match(root.textContent,/Asian Paints/);assert.match(root.textContent,/Coal India/);assert.match(root.textContent,/Trent/);assert.equal(root.querySelector('#paperPause').disabled,true);assert.equal(calls.length,1);assert.equal(root.querySelector('#paperFields input').value,'300000');}finally{cleanup();}
});
test('paper view suppresses quotes and P&L on connection error',async()=>{
 const {document}=parseHTML('<main></main>');const root=document.querySelector('main');
 const cleanup=mountPaperTrading(root,async()=>{throw new Error('offline fixture');});
 try{await settle();assert.match(root.textContent,/Connection error/);assert.match(root.textContent,/No current profit is asserted/);assert.equal(root.querySelector('#paperStart').disabled,true);}finally{cleanup();}
});
test('unpriced closure is explicit and unsafe text is escaped',async()=>{
 const {document}=parseHTML('<main></main>');const root=document.querySelector('main');const s=fixture();s.reviewed=true;s.state='Ended';s.portfolio.net_pnl=null;s.portfolio.return_pct=null;s.trades=[{symbol:'TRENT',direction:1,signal:'LONG',status:'CLOSED_UNPRICED',quantity:1,allocation:100,entry_price:100,exit_price:null,net_pnl:null,gross_pnl:null,exit_reason:'<script>unsafe</script>'}];
 const cleanup=mountPaperTrading(root,async()=>s);
 try{await settle();assert.match(root.textContent,/P&L unknown/);assert.equal(root.querySelectorAll('script').length,0);assert.equal(root.querySelector('#paperStart').disabled,true);}finally{cleanup();}
});
test('experiment review remains discoverable and ledger is collapsed without removing controls',async()=>{
 const {document}=parseHTML('<main></main>');const root=document.querySelector('main');const calls=[];
 const cleanup=mountPaperTrading(root,async(path,options)=>{calls.push([path,options]);return fixture();});
 try{await settle();assert.equal(root.querySelector('#paperReviewDetails').open,true);assert.equal(root.querySelector('#paperLogDetails').open,false);assert.ok(root.querySelector('#paperReviewDetails #paperSettings'));assert.ok(root.querySelector('#paperLogDetails #paperLogCount'));assert.equal(root.querySelector('.paper-table-scroll').getAttribute('role'),'region');assert.ok(root.querySelector('th[scope="col"]'));assert.equal(calls.length,1);}finally{cleanup();}
});
