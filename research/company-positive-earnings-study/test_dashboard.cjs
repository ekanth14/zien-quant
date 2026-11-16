/* DOM/interaction verification, not real-browser pixel/layout certification. */
const fs=require('node:fs'),path=require('node:path'),vm=require('node:vm'),assert=require('node:assert/strict');
const {createRequire}=require('node:module');
const project=process.env.ZIEN_PROJECT||path.resolve(__dirname,'../..');
const {parseHTML}=createRequire(path.join(project,'package.json'))('linkedom');
const html=fs.readFileSync(path.join(__dirname,'COMPANY_REACTION_DASHBOARD.html'),'utf8');
const {window,document}=parseHTML(html);let width=900;
window.Element.prototype.getBoundingClientRect=function(){return {width,height:440,left:0,top:0,right:width,bottom:440};};
for(const select of document.querySelectorAll('select'))Object.defineProperty(select,'value',{value:'',writable:true});
const context=vm.createContext({document,console,Date,Intl,ResizeObserver:class{observe(){}disconnect(){}}});
const executable=[...html.matchAll(/<script([^>]*)>([\s\S]*?)<\/script>/g)].filter(m=>!m[1].includes('application/json'));
assert.equal(executable.length,1);new vm.Script(executable[0][2]);vm.runInContext(executable[0][2],context,{timeout:3000});
const data=JSON.parse(document.getElementById('study-data').textContent),$=id=>document.getElementById(id);
assert.equal(data.events.length,17);assert.equal(document.querySelectorAll('.company').length,17);
assert.equal(document.querySelectorAll('#coverage-body tr').length,4);
assert.equal(data.up_count,9);assert.equal(data.down_count,8);assert.equal(data.predictions,null);assert.equal(data.accuracy,null);
let combinations=0;
for(const e of data.events){
 $('company-select').onchange({target:{value:e.symbol}});
 assert.equal($('symbol').textContent,e.symbol);
 assert.equal($('company-name').textContent,e.company);
 for(const t of data.landmarks){
  [...$('landmarks').children].find(b=>b.textContent===t).onclick();
  const expected=Math.round(((Number(t.slice(0,2))*60+Number(t.slice(3)))-555)/5);
  assert.match($('chart-time').textContent,new RegExp(expected+' completed'));
  assert.ok($('chart-time').textContent.includes(new Date(e.date+'T12:00:00+05:30').toLocaleDateString('en-IN',{timeZone:'Asia/Kolkata',day:'2-digit',month:'short',year:'numeric'})));
  for(const mode of ['line','candle']){
   $(mode+'-mode').onclick();
   for(const reveal of [false,true]){
    $('outcome-toggle').checked=reveal;$('outcome-toggle').onchange();
    assert.equal($('outcome-panel').classList.contains('hidden'),!reveal);
    for(const market of [false,true]){
     $('market-toggle').checked=market;$('market-toggle').onchange();
     assert.equal($('benchmark-panel').classList.contains('hidden'),!market);
     assert.ok(!/NaN|Infinity/.test($('price-chart').innerHTML));
     assert.equal($('forecast-name').textContent,t==='11:00'?'Horizon ended':'Not fitted');
     combinations++;
    }
   }
  }
 }
}
$('search').value='CASTROL';$('search').oninput();assert.equal(document.querySelectorAll('.company').length,1);
$('search').value='no_such_company';$('search').oninput();assert.match($('companies').textContent,/No matching company/);
width=350;vm.runInContext('renderChart();renderBenchmark();',context);assert.equal($('price-chart').getAttribute('viewBox').split(' ')[2],'350');
$('price-chart').onkeydown({key:'ArrowLeft',preventDefault(){}});assert.ok(!$('tooltip').classList.contains('hidden'));assert.match($('tooltip').textContent,/Stored volume/);
const result={status:'passed',companies:17,independent_dates:3,render_combinations_checked:combinations,
  chart_modes:['line','candles'],landmarks:data.landmarks,company_search:true,own_company_identity:true,
  completed_bars_only:true,separate_observed_outcome:true,separate_market_context:true,small_svg_width:350,
  keyboard_ohlc_inspection:true,predictions:null,network_calls:0,
  limitation:'DOM/SVG behavior checked with linkedom; no real-browser visual screenshot or live website deployment.'};
fs.writeFileSync(path.join(__dirname,'dashboard_validation.json'),JSON.stringify(result,null,2)+'\n');
console.log(JSON.stringify(result,null,2));
