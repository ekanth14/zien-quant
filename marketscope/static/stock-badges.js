/* Verified Zerodha equity MIS eligibility. Display only; never changes a trade. */
(function (root) {
  'use strict';
  const SOURCE = 'https://zerodha.com/margin-calculator/Equity/';
  const API = '/api/intraday-margins';
  const SNAPSHOT = '/static/data/zerodha-equity-margins.json';
  const MAX_AGE = 24 * 60 * 60 * 1000;
  const SVG = 'http://www.w3.org/2000/svg';
  const SELECTOR = '[data-margin-symbol],.stock-name,.stock-company,.tile-sym,.tt-sym,.tt-name,.m-sym,.m-name,.hero-sym,.hero-full-name,.sym-col,.search-item-sym,.search-item-name,.company-title>strong,.company-title>h2,.company-title>small,#hudSym,#hudName,#modalStockTitle,#drawerContextStock,.endpoint-text';
  let data = null, records = new Map(), aliases = new Map(), observer = null, scheduled = false, refreshing = false, expiryTimer = null;
  const escape = value => String(value ?? '').replace(/[&<>"']/g, character => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[character]));
  const number = value => value === null || value === '' || typeof value === 'boolean' ? null : Number.isFinite(Number(value)) ? Number(value) : null;
  const nameKey = value => String(value ?? '').trim().replace(/\s+/g, ' ').toLocaleLowerCase('en-IN');
  function symbolKey(value) {
    const symbol = String(value ?? '').trim().toUpperCase().replace(/^NSE:/, '').replace(/\.NS$/, '').replace(/-EQ$/, '');
    return /^[A-Z0-9&._-]{1,32}$/.test(symbol) ? symbol : '';
  }
  function fresh(now = Date.now()) {
    if (!data || data.stale === true || data.status === 'unavailable') return false;
    const checked = Date.parse(data.checked_at), expires = Date.parse(data.expires_at);
    return Number.isFinite(checked) && Number.isFinite(expires) && checked <= now + 300000 && expires > checked && now < Math.min(expires, checked + MAX_AGE);
  }
  function get(symbol, now = Date.now()) {
    return fresh(now) ? records.get(symbolKey(symbol)) || null : null;
  }
  function eligible(symbol, now = Date.now()) { return get(symbol, now)?.eligible === true; }
  function dateText(value) {
    const date = new Date(value);
    return Number.isFinite(date.valueOf()) ? date.toLocaleString('en-IN', {timeZone:'Asia/Kolkata',day:'2-digit',month:'short',year:'numeric',hour:'2-digit',minute:'2-digit',hour12:false}) + ' IST' : 'not supplied';
  }
  function leverageText(value) {
    const leverage = number(value);
    if (leverage === null) return '';
    return `${Number.isInteger(leverage) ? String(leverage) : leverage.toFixed(2).replace(/0+$/, '').replace(/\.$/, '')}×`;
  }
  function description(symbol) {
    const row = get(symbol);
    if (!row?.eligible) return '';
    const sourceDate = data.source_updated_at || data.source_date;
    return `Zerodha equity MIS leverage: ${leverageText(row.leverage)} (${row.margin_percent}% margin), strictly more than 3×. Checked ${dateText(data.checked_at)}${sourceDate ? `; broker list dated ${sourceDate}` : ''}. Availability can change; confirm in Zerodha before trading.`;
  }
  function html(symbol) {
    const title = description(symbol);
    return title ? `<span class="zerodha-margin-leverage" data-margin-badge="${escape(symbolKey(symbol))}" role="img" aria-label="${escape(title)}" title="${escape(title)}">${escape(leverageText(get(symbol).leverage))}</span>` : '';
  }
  function setData(payload, now = Date.now()) {
    records = new Map(); aliases = new Map();
    data = payload && typeof payload === 'object' ? {...payload} : null;
    if (data && data.provider && !/^zerodha$/i.test(data.provider)) data = null;
    if (data && data.product && !/^MIS(?:\/CO)?$/i.test(data.product)) data = null;
    if (data?.records && typeof data.records === 'object' && !Array.isArray(data.records)) {
      for (const [key, raw] of Object.entries(data.records)) {
        const symbol = symbolKey(key), margin = number(raw?.margin_percent), leverage = number(raw?.leverage);
        if (!symbol || !raw || !['NSE','NSE_EQ'].includes(String(raw.exchange || data.exchange || '').toUpperCase()) || margin === null || leverage === null || margin <= 0 || margin > 100 || leverage < 1 || leverage > 100 || Math.abs(100 / margin - leverage) > .025) continue;
        const record = Object.freeze({...raw, symbol, margin_percent:margin, leverage,
          eligible:raw.eligible !== false && leverage > 3 && 100 / margin > 3});
        records.set(symbol, record);
        const company = raw.company_name || raw.name;
        if (company) {
          const keyName = nameKey(company);
          aliases.set(keyName, aliases.has(keyName) && aliases.get(keyName) !== symbol ? null : symbol);
        }
      }
    }
    if (expiryTimer !== null && root.clearTimeout) root.clearTimeout(expiryTimer);
    expiryTimer = null;
    if (fresh(now) && root.setTimeout) {
      const expiry = Math.min(Date.parse(data.expires_at), Date.parse(data.checked_at) + MAX_AGE);
      expiryTimer = root.setTimeout(() => { decorate(); announce(); }, Math.max(1, expiry - Date.now() + 10));
    }
    decorate(); announce();
    return {count:records.size, eligible:[...records.values()].filter(record => record.eligible).length, stale:!fresh(now)};
  }
  function badgeNode(node) { return node?.nodeType === 1 && node.hasAttribute?.('data-margin-badge'); }
  function labelText(node) {
    return [...(node.childNodes || [])].filter(child => !badgeNode(child) && String(child.nodeName).toLowerCase() !== 'title').map(child => child.textContent || '').join('').trim();
  }
  function symbolFor(node) {
    const explicit = node.getAttribute('data-margin-symbol');
    if (explicit !== null) return symbolKey(explicit);
    const text = labelText(node), ticker = symbolKey(text);
    return records.has(ticker) ? ticker : aliases.get(nameKey(text)) || '';
  }
  function visibleLabel(node) {
    if (!node || node.closest?.('[data-margin-badge],script,style,option,select,input,textarea,[contenteditable="true"],[hidden]')) return false;
    if (root.getComputedStyle) {
      const style = root.getComputedStyle(node);
      if (style.display === 'none' || style.visibility === 'hidden') return false;
    }
    return true;
  }
  function updateLabel(node) {
    const symbol = symbolFor(node), row = get(symbol);
    const own = [...(node.children || [])].filter(badgeNode);
    if (!row?.eligible || !visibleLabel(node)) { own.forEach(badge => badge.remove()); return; }
    const title = description(symbol);
    let badge = own.shift(); own.forEach(extra => extra.remove());
    if (badge && badge.getAttribute('data-margin-badge') !== symbol) { badge.remove(); badge = null; }
    if (!badge) {
      const inSvg = node.namespaceURI === SVG;
      badge = inSvg ? node.ownerDocument.createElementNS(SVG, 'tspan') : node.ownerDocument.createElement('span');
      badge.setAttribute('class', 'zerodha-margin-leverage');
      badge.setAttribute('data-margin-badge', symbol);
      badge.setAttribute('role', 'img');
      if (inSvg) badge.setAttribute('dx', '4');
      node.appendChild(badge);
    }
    badge.textContent = leverageText(row.leverage);
    badge.setAttribute('title', title); badge.setAttribute('aria-label', title);
  }
  function legend(element) {
    const ok = fresh();
    element.classList.add('zerodha-margin-legend');
    const title = ok ? `Verified ${dateText(data.checked_at)}. The published leverage can change intraday.` : 'Margin data is missing or expired. Leverage labels are hidden until fresh data is verified.';
    const markup = ok ? `<a href="${SOURCE}" target="_blank" rel="noopener noreferrer"><span class="zerodha-legend-value" aria-hidden="true">3×+</span> Zerodha MIS leverage</a><small>Checked ${escape(dateText(data.checked_at))}</small>` : `<span>Margin data unavailable · leverage labels hidden</span>`;
    if (element.innerHTML !== markup) element.innerHTML = markup;
    element.title = title;
  }
  function decorate(scope = root.document) {
    if (!scope?.querySelectorAll) return;
    const targets = [...scope.querySelectorAll(SELECTOR)];
    if (scope.matches?.(SELECTOR)) targets.unshift(scope);
    for (const node of targets) updateLabel(node);
    // Also clean badges whose owning label was removed from the supported set.
    for (const badge of scope.querySelectorAll('[data-margin-badge]')) {
      if (!eligible(badge.getAttribute('data-margin-badge'))) badge.remove();
    }
    scope.querySelectorAll('[data-margin-legend]').forEach(legend);
  }
  function drawLeverage(ctx, symbol, x, y) {
    const row = get(symbol);
    if (!ctx || !row?.eligible || ![x,y].every(Number.isFinite)) return false;
    ctx.save(); ctx.fillStyle = '#f3c86a'; ctx.font = 'bold 9px Cascadia Mono, monospace'; ctx.textAlign = 'left';
    ctx.fillText(leverageText(row.leverage), x, y); ctx.restore();
    return true;
  }
  function announce() {
    if (root.document && root.CustomEvent && root.dispatchEvent) root.dispatchEvent(new root.CustomEvent('zien:margins-updated', {detail:{stale:!fresh(), checked_at:data?.checked_at || null}}));
  }
  async function read(url) {
    const controller = root.AbortController ? new root.AbortController() : null;
    const timer = controller && root.setTimeout ? root.setTimeout(() => controller.abort(), 12000) : null;
    try {
      const response = await root.fetch(url, {cache:'no-store',credentials:'same-origin',...(controller ? {signal:controller.signal} : {})});
      if (!response.ok) { const error = Error(`Margin source returned ${response.status}`); error.status = response.status; throw error; }
      return await response.json();
    } finally { if (timer !== null) root.clearTimeout(timer); }
  }
  async function refresh() {
    if (refreshing || !root.fetch) return;
    refreshing = true;
    try {
      let payload;
      try { payload = await read(API); }
      catch (error) {
        // A functioning API's stale/unavailable response is authoritative. Only
        // a missing/blocked API may use the independently verified snapshot.
        if (error.status && error.status !== 404) { setData(null); return; }
        payload = await read(SNAPSHOT);
      }
      setData(payload);
    } catch (_) { decorate(); announce(); }
    finally { refreshing = false; }
  }
  function schedule() {
    if (scheduled) return;
    scheduled = true;
    const run = () => { scheduled = false; decorate(); };
    if (root.requestAnimationFrame) root.requestAnimationFrame(run); else root.setTimeout(run, 30);
  }
  function boot() {
    if (!root.document) return;
    decorate(); refresh();
    if (root.MutationObserver && root.document.body) {
      observer = new root.MutationObserver(changes => {
        if (changes.some(change => change.type !== 'childList' || [...change.addedNodes,...change.removedNodes].some(node => !badgeNode(node) && !node.parentElement?.closest?.('[data-margin-badge],[data-margin-legend]')))) schedule();
      });
      observer.observe(root.document.body,{subtree:true,childList:true,characterData:true,attributes:true,attributeFilter:['data-margin-symbol','hidden','open']});
    }
    root.setInterval?.(() => { decorate(); if (!root.document.hidden) refresh(); }, 300000);
    root.document.addEventListener?.('visibilitychange', () => { if (!root.document.hidden) { decorate(); refresh(); } });
  }
  root.ZienMarginBadges = Object.freeze({html,get,eligible,decorate,drawLeverage,refresh,setData,description});
  if (root.document) {
    if (root.document.readyState === 'loading') root.document.addEventListener('DOMContentLoaded',boot,{once:true});
    else boot();
  }
})(typeof window !== 'undefined' ? window : globalThis);
