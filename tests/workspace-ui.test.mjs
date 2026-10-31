import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import { test } from 'node:test';
import vm from 'node:vm';
import { parseHTML } from 'linkedom';

const staticRoot = new URL('../marketscope/static/', import.meta.url);
const [html, controller, adapter, historicalChart, paperTrading, paperWatchlist, quantResearch, fixture] = await Promise.all([
  readFile(new URL('workspace.html', staticRoot), 'utf8'),
  readFile(new URL('workspace.js', staticRoot), 'utf8'),
  readFile(new URL('workspace-api.js', staticRoot), 'utf8'),
  readFile(new URL('historical-chart.js', staticRoot), 'utf8'),
  readFile(new URL('paper-trading.js', staticRoot), 'utf8'),
  readFile(new URL('paper-watchlist.js', staticRoot), 'utf8'),
  readFile(new URL('quant-research.js', staticRoot), 'utf8'),
  readFile(new URL('../marketscope/tests/fixtures/session_fixtures.json', import.meta.url), 'utf8').then(JSON.parse),
]);

function response(body, status = 200) {
  return { ok: status < 400, status, json: async () => structuredClone(body) };
}

async function workspace({ offline = false, candlesUnavailable = false, sessionState = 'PREVIOUS', trajectoryGap = false, dailyRanking = null, ownerExists=false, loginMfa=false, missingChange=false } = {}) {
  const { window, document } = parseHTML(html);
  const requests = [];
  const storage = new Map();
  let signedIn=false;
  window.HTMLElement.prototype.focus=()=>{};
  const buttons = document.querySelectorAll('button');
  for (const element of [document.getElementById('modal'), ...buttons]) {
    element.focus = () => {};
  }
  const dialog = document.getElementById('modal');
  dialog.showModal = () => { dialog.open = true; };
  dialog.close = () => { dialog.open = false; };
  window.HTMLElement.prototype.scrollIntoView = () => {};
  window.HTMLElement.prototype.setPointerCapture = () => {};
  window.HTMLElement.prototype.getBoundingClientRect = () => ({ x: 0, y: 0, left: 0, top: 0, right: 600, bottom: 300, width: 600, height: 300 });
  // LinkeDOM exposes a getter-only select.value; browsers expose both accessors.
  const selectValue = Object.getOwnPropertyDescriptor(window.HTMLSelectElement.prototype, 'value');
  Object.defineProperty(window.HTMLSelectElement.prototype, 'value', {
    configurable: true,
    get: selectValue.get,
    set(value) {
      for (const option of this.querySelectorAll('option')) {
        option.selected = option.value === String(value);
      }
    },
  });
  const symbols = ['RELIANCE', 'TCS'];
  const rawStocks = Object.fromEntries(symbols.map((symbol, index) => {
    const original = fixture.stocks[symbol];
    assert.ok(original?.candles?.length, `Missing fixture for ${symbol}`);
    const candles = original.candles;
    const last = candles.at(-1);
    const price = last.c;
    return [symbol, {
      symbol, name: original.name, sector: original.sector,
      status: 'ok', source: 'archive', price, session_open: candles[0].o,
      change_pct: index ? -1.2 : 1.4, volume: 12000, rvol: 1.6,
      updated_at: new Date(last.t * 1000).toISOString(),
      vwap: price, support: price * .98, resistance: price * 1.02,
      candles_preview: candles.slice(-20), order_blocks: [],
      metrics: { score: index ? 20 : 85, raw: { rsi: index ? 35 : 65 }, components: {} },
      confirmed: { score: index ? 23 : 82, raw: { rsi: index ? 37 : 63 }, components: {} },
      ai_decision: { action: index ? 'BREAKDOWN SHORT' : 'BREAKOUT LONG', signal_type: index ? 'SELL' : 'BUY', entry_low: price, entry_high: price, stop_loss: price * (index ? 1.01 : .99), target_1: price * (index ? .98 : 1.02), target_2: price * (index ? .97 : 1.03), risk_reward_ratio: 2, grade: 'A', rationale: 'TEST FIXTURE research plan.' },
    }];
  }));
  if(missingChange)rawStocks.RELIANCE.change_pct=null;
  const fetch = async (path, options = {}) => {
    requests.push({ path, options });
    const url = new URL(path, 'http://workspace.test');
    switch (url.pathname) {
      case '/api/bootstrap': return response({ config: { monitored_symbols: symbols, default_timeframe: 5, score_threshold: 65 }, catalogue: Object.fromEntries(symbols.map(s => [s, { name: rawStocks[s].name }])), session: { authenticated: signedIn, email:signedIn?'owner@example.test':null, role:signedIn?'owner':null, csrf_token:signedIn?'test-csrf-token':null }, has_owner: ownerExists });
      case '/api/login': {
        const body=JSON.parse(options.body);
        if(loginMfa&&!body.totp_code)return response({error:'mfa_required',detail:'Enter authenticator code',requires_mfa:true},400);
        if(loginMfa&&body.totp_code!=='123456')return response({error:'invalid_mfa',detail:'Invalid test code',requires_mfa:true},401);
        signedIn=true;return response({success:true,csrf_token:'test-csrf-token'});
      }
      case '/api/ai/chat': return response({reply:'Test-only model response',source:'local_llm',backend:'ollama',model:'fixture'});
      case '/api/logout': signedIn=false;return response({success:true});
      case '/api/market': return offline ? response({ error: 'feed_unavailable', detail: 'The received feed is offline.' }, 503) : response({ mode: 'live', data_mode: sessionState === 'LIVE' ? 'LIVE' : 'PREVIOUS_SESSION', session_state: sessionState, session_date: fixture.session_date, timeframe: Number(url.searchParams.get('timeframe')), stocks: rawStocks, daily_ranking: dailyRanking, trajectories: [{ symbol: 'RELIANCE', points: trajectoryGap ? [0, .3, null, 1.4] : [0, .3, .7, 1.4] }, { symbol: 'TCS', points: [0, -.2, -.7, -1.2] }] });
      case '/api/market/schedule': return response({ state: 'CLOSED', action: 'opens', target_timestamp: 1792986300, target_time_ist: '2026-10-26 09:15 IST' });
      case '/api/stocks/database': return response({ total_database: 2, total_matches: 2, stocks: symbols.map(s => ({ symbol: s, name: rawStocks[s].name, exchange: 'NSE' })) });
      case '/api/stock': return candlesUnavailable ? response({ error: 'candles_unavailable' }, 404) : response({ symbol: url.searchParams.get('symbol'), candles: fixture.stocks[url.searchParams.get('symbol')].candles });
      default: throw Error(`Unexpected request: ${path}`);
    }
  };
  const context = vm.createContext({
    document, window, console, URL, Intl, Date, Math, Map, Set, JSON, Number, AbortController,
    fetch, innerWidth: 1440, innerHeight: 1000,
    location: { hash: '', search: '', href: 'http://workspace.test/' },
    history: { replaceState: () => {} },
    localStorage: { getItem: key => storage.get(key) ?? null, setItem: (key, value) => storage.set(key, value) },
    matchMedia: () => ({ matches: true }),
    CSS: { escape: value => String(value).replace(/[&]/g, '\\&') },
    setTimeout: () => 1, clearTimeout: () => {}, setInterval: () => 1, clearInterval:()=>{},
    requestAnimationFrame: () => {},
    ResizeObserver: class { observe() {} unobserve() {} disconnect() {} },
  });
  const researchModule = new vm.SourceTextModule(quantResearch, { context, identifier: 'quant-research.js' });
  const chartModule = new vm.SourceTextModule(historicalChart, { context, identifier: 'historical-chart.js' });
  const watchModule = new vm.SourceTextModule(paperWatchlist, { context, identifier: 'paper-watchlist.js' });
  const paperModule = new vm.SourceTextModule(paperTrading, { context, identifier: 'paper-trading.js' });
  const apiModule = new vm.SourceTextModule(adapter, { context, identifier: 'workspace-api.js' });
  const uiModule = new vm.SourceTextModule(controller + '\nexport {state,refresh,navigate,setMode,setTimeframe,openDetail,hydrateStock,localBrief,sendAiMessage};', { context, identifier: 'workspace.js' });
  await uiModule.link(async specifier => {
    if (specifier === './historical-chart.js') return chartModule;
    if (specifier === './paper-trading.js') return paperModule;
    if (specifier === './quant-research.js') return researchModule;
    if (specifier === './paper-watchlist.js') return watchModule;
    assert.equal(specifier, './workspace-api.js');
    return apiModule;
  });
  await uiModule.evaluate();
  // Allow non-blocking schedule/catalogue/candle hydration to finish.
  for (let i = 0; i < 12; i++) await Promise.resolve();
  return { document, requests, storage, ui: uiModule.namespace };
}

test('reference layout boots against the current backend contract with risk-qualified cards', async () => {
  const { document, ui } = await workspace();
  assert.equal(document.getElementById('brand').textContent, 'Zien Quant');
  assert.equal(document.querySelectorAll('[data-trajectory]').length, 2);
  assert.equal(document.querySelectorAll('#buyTiles .signal-tile').length, 1);
  assert.equal(document.querySelectorAll('#sellTiles .signal-tile').length, 1);
  assert.match(document.getElementById('modeLabel').textContent, /Previous session/);
  assert.equal(document.getElementById('riverMode').getAttribute('href'), '/flow');
  assert.equal(ui.state.data.session_date, fixture.session_date);
  assert.ok(!/NaN|undefined/.test(document.getElementById('mainContent').innerHTML), 'Rendered overview must contain no missing values or invalid SVG coordinates');
});

test('workspace groups tasks, prioritizes the selected chart and keeps secondary context accessible',async()=>{
 const {document,ui}=await workspace();
 const groups=[...document.querySelectorAll('#navigation .nav-label')].map(e=>e.textContent);
 assert.deepEqual(groups,['MARKETS','SIMULATION & RESULTS','RESEARCH']);
 assert.equal(document.querySelector('#navigation [aria-current="page"]').dataset.view,'overview');
 const content=document.querySelector('#mainContent');
 assert.ok(content.innerHTML.indexOf('mainChartPanel')<content.innerHTML.indexOf('dailyFormulaPanel'));
 assert.ok(content.innerHTML.indexOf('dailyFormulaPanel')<content.innerHTML.indexOf('signalTerminal'));
 assert.equal(document.querySelector('#sessionContextDisclosure').hasAttribute('open'),false);
 assert.ok(document.querySelector('#sessionContextDisclosure [data-margin-legend]'));
 assert.equal(document.querySelector('[data-overlay="vwap"]').getAttribute('aria-pressed'),'true');
 ui.navigate('research');
 assert.equal(document.body.dataset.view,'research');
 assert.equal(document.querySelector('#navigation [aria-current="page"]').dataset.view,'research');
});

test('mobile navigation has synchronized open state, backdrop and Escape dismissal',async()=>{
 const {document}=await workspace();const menu=document.querySelector('#mobileMenu');
 menu.onclick();assert.equal(menu.getAttribute('aria-expanded'),'true');
 assert.equal(document.querySelector('#navigationBackdrop').hidden,false);
 const tab=new document.defaultView.Event('keydown',{bubbles:true});Object.assign(tab,{key:'Tab'});document.dispatchEvent(tab);
 const event=new document.defaultView.Event('keydown',{bubbles:true});Object.assign(event,{key:'Escape'});document.dispatchEvent(event);
 assert.equal(menu.getAttribute('aria-expanded'),'false');
 assert.equal(document.querySelector('#navigationBackdrop').hidden,true);
 menu.onclick();document.querySelector('#navigationBackdrop').onclick();
 assert.equal(document.body.classList.contains('menu-open'),false);
});

test('scanner labels and result counts follow the existing filters',async()=>{
 const {document,ui}=await workspace();ui.navigate('scanner');
 assert.match(document.querySelector('#scannerResultCount').textContent,/2 of 2 stocks shown/);
 assert.match(document.querySelector('#stockFilter').closest('label').textContent,/Find a stock/);
 const input=document.querySelector('#stockFilter');input.value='TCS';input.oninput({target:input});
 assert.match(document.querySelector('#scannerResultCount').textContent,/1 of 2 stocks shown/);
 assert.ok(document.querySelector('#scannerCatalogue'));
});

test('scanner search, local watchlist, detail chart and historical explanations interact correctly', async () => {
  const { document, ui, storage, requests } = await workspace();
  ui.navigate('scanner');
  assert.equal(document.querySelectorAll('[data-card]').length, 2);
  document.getElementById('stockFilter').value = 'TCS';
  document.getElementById('stockFilter').oninput({ target: document.getElementById('stockFilter') });
  assert.equal(document.querySelectorAll('[data-card]').length, 1);
  document.querySelector('[data-watch="TCS"]').onclick({ stopPropagation() {} });
  assert.ok(JSON.parse(storage.get('zien-watchlist')).includes('TCS'));
  ui.openDetail('TCS');
  await ui.hydrateStock('TCS');
  assert.ok(document.querySelector('#detailChart svg'));
  assert.ok(!/NaN|undefined/.test(document.getElementById('detailChart').innerHTML), 'Detail chart coordinates must stay finite');
  await document.getElementById('analyzeStock').onclick();
  assert.match(document.getElementById('stockAiText').textContent, /RULE-BASED SESSION BRIEFING/);
  assert.ok(!requests.some(r => r.path.startsWith('/api/ai/analyze')));
});

test('confirmed mode withholds provisional plans and timeframe requests retain the selected interval', async () => {
  const { document, ui, requests } = await workspace();
  document.querySelector('[data-signal="confirmed"]').onclick();
  assert.equal(document.querySelectorAll('.signal-tile').length, 0);
  await ui.setTimeframe(15);
  for (let i = 0; i < 12; i++) await Promise.resolve();
  assert.equal(ui.state.timeframe, 15);
  assert.ok(requests.some(r => r.path.includes('/api/market?') && r.path.includes('timeframe=15')));
  assert.ok(!/NaN|undefined/.test(document.getElementById('mainContent').innerHTML), 'Confirmed and alternate timeframe views must stay valid');
});

test('source failure shows an explicit empty state while catalogue and methodology remain usable', async () => {
  const { document, ui } = await workspace({ offline: true });
  assert.equal(document.querySelectorAll('[data-trajectory]').length, 0);
  assert.match(document.getElementById('mainContent').textContent, /Market data is unavailable/);
  assert.equal(document.getElementById('modeLabel').textContent, 'FEED UNAVAILABLE');
  document.getElementById('searchButton').onclick();
  for (let i = 0; i < 12; i++) await Promise.resolve();
  assert.equal(document.querySelectorAll('[data-result]').length, 2);
  ui.navigate('methodology');
  assert.match(document.getElementById('mainContent').textContent, /Inspect the signal/);
});

test('missing trajectory observations remain visual gaps rather than an interpolated segment', async () => {
  const { document } = await workspace({ trajectoryGap: true });
  const path = document.querySelector('[data-trajectory="RELIANCE"] .stock-trace').getAttribute('d');
  assert.equal((path.match(/M/g) || []).length, 2);
  assert.ok(!/NaN|undefined/.test(path));
});

test('automatic live-session charts refresh their bounded cache after the observation interval', async () => {
  const { ui, requests } = await workspace({ sessionState: 'LIVE' });
  await ui.hydrateStock('RELIANCE');
  const count = () => requests.filter(r => r.path.startsWith('/api/stock?') && r.path.includes('RELIANCE')).length;
  const initial = count();
  await ui.hydrateStock('RELIANCE');
  assert.equal(count(), initial, 'Fresh selected history must not generate duplicate requests');
  const originalNow = Date.now;
  try {
    Date.now = () => originalNow() + 31000;
    await ui.hydrateStock('RELIANCE');
  } finally {
    Date.now = originalNow;
  }
  assert.equal(count(), initial + 1, 'Selected live history must not stay permanently cached');
});

test('daily formula panel shows authoritative leaders and Laya answers with the same ranking', async () => {
  const dailyRanking = { session_date: fixture.session_date, formula_version: 'pdf-quant-1', eligible_count: 2, excluded_count: 0,
    best: {symbol:'RELIANCE',score:76.25,as_of:fixture.session_date+'T10:00:00Z',components:{ema:{contribution:14}}},
    worst: {symbol:'TCS',score:23.75,as_of:fixture.session_date+'T10:00:00Z',components:{ema:{contribution:-14}}}, weights:{ema:20} };
  const {document,requests}=await workspace({dailyRanking});
  const panel=document.getElementById('dailyFormulaPanel');
  assert.equal(panel.querySelectorAll('[data-daily-stock]').length,2);
  assert.match(panel.textContent,/76.25/);
  document.getElementById('dailyMethod').onclick();
  assert.match(document.getElementById('modalBody').textContent,/Auditable daily ranking/);
  document.getElementById('aiMessage').value='Which is the best and worst stock today?';
  await document.getElementById('aiChatForm').onsubmit({preventDefault(){}});
  assert.match(document.getElementById('aiConversation').textContent,/strongest observed RELIANCE/);
  assert.match(document.getElementById('aiConversation').textContent,/weakest observed TCS/);
  assert.ok(!requests.some(r=>r.path.startsWith('/api/ai/chat')));
});

test('missing or mismatched daily rankings cannot display forced winners', async () => {
  for(const dailyRanking of [null,{session_date:'1999-01-01',best:{symbol:'RELIANCE',score:80},worst:{symbol:'TCS',score:20}}]){
    const {document}=await workspace({dailyRanking});
    const panel=document.getElementById('dailyFormulaPanel');
    assert.equal(panel.querySelectorAll('[data-daily-stock]').length,0);
    assert.match(panel.textContent,/Ranking unavailable/);
  }
});

test('password-only sign-in never demands an authenticator and logout clears the session', async()=>{
  const {document,ui,requests}=await workspace({ownerExists:true});
  document.getElementById('account').onclick();
  const form=document.getElementById('loginForm');
  form.querySelector('[name=email]').value='owner@example.test';
  form.querySelector('[name=password]').value='test-only-password';
  assert.equal(form.querySelector('[name=totp_code]').disabled,true);
  await form.onsubmit({preventDefault(){}});
  assert.equal(ui.state.user.role,'owner');
  assert.equal(ui.state.csrf,'test-csrf-token');
  assert.equal(JSON.parse(requests.find(r=>r.path==='/api/login').options.body).totp_code,'');
  document.getElementById('account').onclick();
  await document.getElementById('signout').onclick();
  assert.equal(ui.state.user,null);
  assert.equal(ui.state.csrf,null);
});

test('MFA is requested only after credentials, recovery is visible and fresh-code retry works', async()=>{
  const {document,ui}=await workspace({ownerExists:true,loginMfa:true});
  document.getElementById('account').onclick();
  const form=document.getElementById('loginForm'),code=form.querySelector('[name=totp_code]');
  form.querySelector('[name=email]').value='owner@example.test';
  form.querySelector('[name=password]').value='test-only-password';
  await form.onsubmit({preventDefault(){}});
  assert.equal(ui.state.user,null);
  assert.equal(document.getElementById('mfaStep').hidden,false);
  assert.equal(code.required,true);
  document.getElementById('accountRecovery').onclick();
  assert.equal(document.getElementById('recoveryHelp').hidden,false);
  assert.match(document.getElementById('recoveryHelp').textContent,/python manage.py reset-mfa/);
  code.value='000000';await form.onsubmit({preventDefault(){}});
  assert.equal(ui.state.user,null);
  assert.equal(code.value,'');
  assert.match(document.getElementById('loginError').textContent,/Invalid test code/);
  code.value='123456';await form.onsubmit({preventDefault(){}});
  assert.equal(ui.state.user.role,'owner');
  assert.equal(form.querySelector('[name=password]').value,'');
});

test('scanner sorts unknown percentage changes last and sector averages keep them unavailable',async()=>{
 const {document,ui}=await workspace({missingChange:true});
 const energy=document.querySelector('[data-sector="Energy"]');
 assert.match(energy.textContent,/0 of 1 with prior-close change/);
 assert.ok(!energy.textContent.includes('0.00%'));
 ui.navigate('scanner');
 document.getElementById('sortFilter').onchange({target:{value:'change'}});
 assert.equal(document.querySelector('[data-card]').dataset.card,'TCS');
 assert.match(document.querySelector('[data-card="RELIANCE"]').textContent,/—/);
});


test('paper lab remains reachable inside dashboard when market feed is offline', async () => {
  const {document,ui}=await workspace({offline:true});
  ui.navigate('paper');
  for(let i=0;i<12;i++)await Promise.resolve();
  assert.match(document.querySelector('#mainContent').textContent,/VIRTUAL DESK/);
  assert.match(document.querySelector('#mainContent').textContent,/Connection error/);
  assert.equal(document.querySelector('#paperStart').disabled,true);
  ui.navigate('methodology');
  assert.equal(document.querySelector('#paperState'),null);
});


test('signed-in stored-session chat reaches model with context; visitors remain rules-only',async()=>{
 const {document,ui,requests}=await workspace({ownerExists:true});
 await ui.sendAiMessage('Explain VWAP');
 assert.equal(requests.filter(r=>r.path==='/api/ai/chat').length,0);
 document.getElementById('account').onclick();
 const form=document.getElementById('loginForm');
 form.querySelector('[name=email]').value='owner@example.test';
 form.querySelector('[name=password]').value='test-only-password';
 await form.onsubmit({preventDefault(){}});
 await ui.sendAiMessage('Explain VWAP after hours');
 const req=requests.find(r=>r.path==='/api/ai/chat');assert.ok(req);
 const body=JSON.parse(req.options.body);
 assert.equal(body.timeframe,5);assert.equal(body.mode,ui.state.mode);
 assert.deepEqual(body.watchlist,Array.from(ui.state.watch));
 assert.ok(Array.isArray(body.history));
 assert.equal(ui.state.aiMessages.at(-1).backend,'ollama',ui.state.aiMessages.at(-1).content);
});
