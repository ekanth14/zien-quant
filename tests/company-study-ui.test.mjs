import {test} from 'node:test';
import assert from 'node:assert/strict';
import {readFile} from 'node:fs/promises';
import vm from 'node:vm';
import {parseHTML} from 'linkedom';
const base=new URL('../marketscope/static/',import.meta.url);
const html=await readFile(new URL('company-study.html',base),'utf8');
const code=await readFile(new URL('company-study.js',base),'utf8');
const fixture={as_of:'2026-10-27T10:00:00+05:30',counts:{company_periods:14796,positive_events:62,complete_windows:17,missing_windows:45,dates:3,above_open:9,below_open:8},events:[{id:'event1',symbol:'<TEST>',company:'Fixture <script>bad()</script>',next_trading_date:'2026-10-26',complete_price_window:true}],models:{'09:40':{status:'INSUFFICIENT_VERIFIED_DATA'}},years:{2026:{events:1,comparable:1,positive:1,positive_complete_mornings:1}},limitations:['No verified vintage'],landmarks:['09:30','09:40'],detail:{event:{symbol:'TEST',company:'Test company',next_trading_date:'2026-10-26',period_end:'2026-09-30',announced_at:'2026-10-25',revenue_qoq_pct:4,pat_qoq_pct:5,return_0915_1100_pct:null},current:{revenue:104,net_profit:21},previous:{revenue:100,net_profit:20},candles:[],landmark:'09:40',source_urls:['javascript:alert(1)','https://example.com/source'],quality:{vintage_verified:false},observed_features:null,forecast:{status:'MODEL_NOT_VALIDATED',predictions:null,blockers:['UNVERIFIED_VINTAGE']}}};
async function render(){
 const {window,document}=parseHTML(html),requests=[];
 const form=document.getElementById('filters');
 Object.defineProperty(form,'elements',{value:{search:{value:''},coverage:{value:'all'}}});
 const context=vm.createContext({document,URL,URLSearchParams,console,fetch:async url=>{requests.push(url);return {ok:true,json:async()=>structuredClone(fixture)}}});
 vm.runInContext(code,context);await new Promise(resolve=>setImmediate(resolve));
 return {document,requests,form,window};
}
test('research page displays supplied counts and model abstention',async()=>{
 const {document}=await render();
 assert.match(document.getElementById('status').textContent,/17 complete company mornings across 3 dates/);
 assert.match(document.getElementById('detail').textContent,/Forecast unavailable/);
 assert.match(document.getElementById('detail').textContent,/Company prices are missing/);
 assert.equal(document.querySelector('.abstain h3').textContent,'Forecast unavailable');
});
test('source text is escaped and active URL schemes are removed',async()=>{
 const {document}=await render();
 assert.equal(document.querySelector('#events script'),null);
 assert.match(document.getElementById('events').textContent,/<script>bad/);
 assert.equal(document.querySelectorAll('.sources a').length,1);
 assert.equal(document.querySelector('.sources a').getAttribute('href'),'https://example.com/source');
});
test('search and checkpoint changes only call read-only research API',async()=>{
 const {document,requests,form}=await render();
 form.elements.search.value='APOLLO';form.elements.coverage.value='missing';form.onsubmit({preventDefault(){}});
 await new Promise(resolve=>setImmediate(resolve));
 assert.match(requests.at(-1),/search=APOLLO&coverage=missing/);
 document.getElementById('landmark').onchange({target:{value:'09:30'}});
 await new Promise(resolve=>setImmediate(resolve));
 assert.match(requests.at(-1),/landmark=09%3A30/);
 assert.ok(requests.every(url=>url.startsWith('/api/company-study?')));
});
