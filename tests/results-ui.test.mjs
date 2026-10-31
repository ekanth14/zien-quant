import test from 'node:test';
import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import {parseHTML} from 'linkedom';
import {organizeResearchPanel} from '../marketscope/static/results.js';
import {growth,percentage,safeURL,dayKey,statusText,universeText,historyRange,coverageText,historyProgress,emptyCollection,sourceMessage,detailHTML,analysisHTML,filingHTML} from '../marketscope/static/results.js';

test('missing figures and invalid growth remain unavailable while a real zero is retained',()=>{
  assert.deepEqual(growth(null),{pct:null,note:'Comparable figure unavailable'});
  assert.equal(growth({pct:null,reason:'Loss to profit; percentage not comparable'}).pct,null);
  assert.equal(growth({pct:0}).pct,0);
  assert.equal(percentage(0),'+0.00%');
  assert.equal(percentage(null),'—');
  const html=detailHTML({id:'test',symbol:'TEST',financials:{revenue:null,net_profit:null},growth:{profit_qoq:{pct:null,reason:'Negative base'}}});
  assert.match(html,/Negative base/);
  assert.ok(!html.includes('+0.00%'));
  assert.ok(!html.includes('undefined'));
  assert.ok(!html.includes('NaN'));
});

test('unsafe source links and company markup cannot become executable UI',()=>{
  assert.equal(safeURL('javascript:alert(1)'), '');
  assert.equal(safeURL('https://user:password@example.com/path'), '');
  assert.equal(safeURL('https://www.nseindia.com/filing'), 'https://www.nseindia.com/filing');
  const html=detailHTML({id:'<bad>',symbol:'<img src=x onerror=alert(1)>',source_url:'javascript:alert(1)',news:[{title:'<script>bad()</script>',url:'javascript:bad()'}]});
  assert.ok(!html.includes('<script>'));
  assert.ok(!html.includes('<img'));
  assert.ok(!html.includes('href="javascript:'));
});

test('filings are grouped using India dates rather than the browser local timezone',()=>{
  assert.equal(dayKey('2026-10-25T20:00:00Z'),'2026-10-26');
  assert.equal(dayKey('nonsense'),'');
  const html=filingHTML({id:1,symbol:'TEST',announced_at:'2026-10-26T11:15:00Z',growth:{profit_qoq:12}},true);
  assert.match(html,/16:45/);
  assert.match(html,/aria-pressed="true"/);
  assert.match(html,/\+12.00%/);
});

test('stale or failed data is labelled and insufficient evidence cannot fabricate a buy',()=>{
  assert.equal(statusText({state:'stale',last_success_at:'2026-10-25T11:00:00Z'}).isStale,true);
  const html=analysisHTML({symbol:'TEST'});
  assert.match(html,/INSUFFICIENT EVIDENCE/);
  assert.ok(!html.includes('BUY WATCH'));
  assert.match(html,/No complete matching observations/);
});

test('actual conditional analysis shows its sample size, uncertainty and reason',()=>{
  const html=analysisHTML({symbol:'TEST',analysis:{status:'ready',sample_size:12,pattern:{probability_pct:75,wilson_low_pct:46.8,wilson_high_pct:91.1},baseline:{sample_size:50,pattern_probability_pct:40},forecast:{median_open_to_1000_pct:1.2},setup:{action:'BUY_WATCH',reasons:['Reclaim confirmed in closed candles'],levels:{entry:100,stop:98,target:104},cost_bps:20}}});
  assert.match(html,/BUY WATCH · CONDITIONAL/);
  assert.match(html,/95% interval 46.8–91.1%/);
  assert.match(html,/Reclaim confirmed/);
  assert.match(html,/50 prior observed events/);
  assert.match(html,/20 basis points/);
});

test('daily coverage is shown separately and cannot silently populate an intraday probability',()=>{
  const html=analysisHTML({symbol:'TEST',analysis:{status:'insufficient_data',sample_size:0,daily_study:{sample_size:8,median_open_to_close_pct:1.4,open_to_close_up_probability_pct:75,median_close_return_pct:null,note:'Daily evidence does not prove the opening pattern.'}}});
  assert.match(html,/Next-session daily evidence/);
  assert.match(html,/\+1.40%/);
  assert.match(html,/Daily evidence does not prove the opening pattern/);
  assert.match(html,/Session open to session close/);
  assert.match(html,/INSUFFICIENT EVIDENCE/);
  assert.ok(!html.includes('BUY WATCH'));
});

test('results navigation and updates remain available without demo market data',()=>{
  const page=readFileSync(new URL('../marketscope/static/results.html',import.meta.url),'utf8');
  const dashboard=readFileSync(new URL('../marketscope/static/workspace.html',import.meta.url),'utf8');
  const source=readFileSync(new URL('../marketscope/static/results.js',import.meta.url),'utf8');
  assert.match(dashboard,/href="\/results\.html"/);
  assert.match(page,/id="companySearch"/);
  assert.match(page,/id="dateFrom"/);
  assert.match(source,/60_000/);
  assert.match(source,/next_offset/);
  assert.match(source,/\/api\/results\/detail\?id=/);
});

test('the results desk monitors only the website company universe and handles a missing catalogue',()=>{
  const page=readFileSync(new URL('../marketscope/static/results.html',import.meta.url),'utf8');
  assert.match(page,/Results from your company universe/);
  assert.match(page,/id="universeScope"/);
  assert.equal(universeText({universe_count:42}),'Monitoring 42 companies in your website · Published results only');
  assert.equal(statusText({state:'needs_catalogue'}).needsCatalogue,true);
  assert.equal(statusText({state:'ready',universe_count:0}).label,'Company catalogue needed');
  assert.match(universeText({state:'needs_catalogue'}),/Add companies to your website/);
  assert.ok(!page.includes('2613'));
  assert.ok(!page.includes('2,613'));
});


test('date-only publications, pending revisions and total income keep their meaning',()=>{
  const event={id:'test-only',symbol:'TEST',announced_at:'2026-10-23T00:00:00+05:30',announcement_time_known:false,financial_status:'revision_pending',financials:{revenue:120,revenue_metric:'total_income'},growth:{}};
  assert.match(filingHTML(event,false),/Time unavailable/);
  assert.match(detailHTML(event),/prior saved version/);
  assert.match(detailHTML(event),/Total income/);
});


test('the post-COVID start is the default and recent presets remain available',()=>{
  assert.deepEqual(historyRange(undefined,'2026-10-26'),{from:'2023-05-06',to:'2026-10-26'});
  assert.deepEqual(historyRange('today','2026-10-26'),{from:'2026-10-26',to:'2026-10-26'});
  assert.deepEqual(historyRange('30days','2026-10-26'),{from:'2026-09-27',to:'2026-10-26'});
  const source=readFileSync(new URL('../marketscope/static/results.js',import.meta.url),'utf8');
  assert.match(source,/initialRange = historyRange\('all', today\)/);
  assert.ok(!source.includes('start.getDate()-14'));
  const page=readFileSync(new URL('../marketscope/static/results.html',import.meta.url),'utf8');
  assert.match(page,/data-history-range="all" aria-pressed="true"/);
});

test('blocked collection does not imply that companies have not declared results',()=>{
  const empty=emptyCollection({state:'unavailable',event_count:0,errors:['HTTP Error 403: Forbidden'],universe_count:2613});
  assert.equal(empty.title,'Historical results have not been collected');
  assert.match(empty.body,/does not mean your companies have not declared results/);
  const filtered=emptyCollection({state:'partial',event_count:100,errors:['HTTP Error 403: Forbidden']});
  assert.equal(filtered.title,'No stored results match this view');
});

test('stored announcement coverage and financial enrichment are explicitly different',()=>{
  const text=coverageText({event_count:100,financial_count:21,earliest_announcement:'2024-01-12T16:00:00+05:30',latest_announcement:'2026-10-23T16:00:00+05:30'});
  assert.match(text,/100 published filings stored/);
  assert.match(text,/21 with financial figures/);
  assert.match(text,/79 awaiting figures/);
  assert.match(text,/2024/);
  assert.match(historyProgress({backfill_chunks_remaining:63}),/63 batches remaining/);
  assert.match(historyProgress({backfill_complete:true}),/actually stored/);
});

test('metadata-only announcements remain visible while comparison numbers are pending',()=>{
  const event={id:'metadata-only',symbol:'TEST',company:'Test Fixture',announced_at:'2024-01-12T16:00:00+05:30',period_end:'2023-12-31',financials:{},financial_status:'pending'};
  const html=filingHTML(event,false);
  assert.match(html,/TEST/);
  assert.match(html,/Figures pending · filing available/);
  assert.match(html,/Profit QoQ —/);
  assert.ok(!html.includes('+0.00%'));
});


test('the user-selected post-COVID boundary is visible and cannot select 2021',()=>{
  const page=readFileSync(new URL('../marketscope/static/results.html',import.meta.url),'utf8');
  assert.deepEqual(historyRange('all','2026-10-26'),{from:'2023-05-06',to:'2026-10-26'});
  assert.match(page,/Results from 6 May 2023 onward/);
  assert.match(page,/min="2023-05-06"/);
  const detail=detailHTML({symbol:'TEST',previous_source_url:'https://nsearchives.nseindia.com/corporate/test.xml',financials:{},growth:{}});
  assert.match(detail,/Prior quarter filing/);
});


test('a pending source document does not imply the results ledger is empty',()=>{
 assert.match(sourceMessage('TMB: document: HTTP Error 404: Not Found'),/announcements remain visible/);
 assert.match(sourceMessage('NSE results: HTTP Error 403: Forbidden'),/saved results are shown/);
});

test('results detail keeps financial data visible and groups secondary evidence without losing links',()=>{
 const {document}=parseHTML('<aside id="research"></aside>');
 const panel=document.querySelector('#research');
 panel.innerHTML=detailHTML({symbol:'TEST',financials:{revenue:120,net_profit:20},previous:{revenue:100},growth:{},analysis:{daily_study:{sample_size:4}},news:[{title:'Verified source',url:'https://www.nseindia.com/filing'}]});
 organizeResearchPanel(panel);
 assert.equal(panel.querySelectorAll('.comparison-scroll table').length,1);
 assert.equal(panel.querySelector('.comparison-scroll').getAttribute('aria-label'),'Quarterly financial comparison');
 assert.match(panel.querySelector('.comparison-table').textContent,/120/);
 const groups=panel.querySelectorAll('.research-disclosure');assert.equal(groups.length,2);
 assert.ok([...groups].every(group=>!group.hasAttribute('open')));
 assert.ok(panel.querySelector('a[href="https://www.nseindia.com/filing"]'));
 assert.ok(!panel.querySelector('.comparison-table').closest('details'));
});

test('results route keeps essential navigation available through an accessible mobile menu',()=>{
 const page=readFileSync(new URL('../marketscope/static/results.html',import.meta.url),'utf8');
 const {document}=parseHTML(page);
 assert.equal(document.querySelector('#resultsMenu').getAttribute('aria-controls'),'resultsNavigation');
 assert.equal(document.querySelector('#resultsMenu').getAttribute('aria-expanded'),'false');
 for(const route of ['/#paperwatch','/#paper-report','/#research','/#admin'])assert.ok(document.querySelector(`a[href="${route}"]`));
 assert.ok(document.querySelector('.history-coverage summary'));
 assert.equal(document.querySelector('#filings').getAttribute('tabindex'),'0');
 assert.ok(document.querySelector('.history-coverage [data-margin-legend]'));
 assert.equal(document.querySelectorAll('[data-margin-legend]').length,1);
 const css=readFileSync(new URL('../marketscope/static/results.css',import.meta.url),'utf8');
 assert.match(css,/max-height:min\(60dvh,640px\)/);
});
