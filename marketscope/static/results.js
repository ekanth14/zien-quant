const RESULTS_START_DATE = '2023-05-06';
const safe = value => String(value ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const finite = value => typeof value === 'number' && Number.isFinite(value) ? value : null;
const percentage = value => finite(value) === null ? '—' : `${value >= 0 ? '+' : ''}${value.toFixed(2)}%`;
const count = value => finite(value) === null ? '—' : value.toLocaleString('en-IN');
const amount = value => finite(value) === null ? '—' : value.toLocaleString('en-IN', {maximumFractionDigits: 2});
const nonempty = value => Array.isArray(value) ? value : typeof value === 'string' && value ? [value] : [];
function growth(value) {
  if (finite(value) !== null) return {pct: value, note: ''};
  if (!value || typeof value !== 'object') return {pct: null, note: 'Comparable figure unavailable'};
  return {pct: finite(value.pct) ?? finite(value.percentage) ?? finite(value.value), note: value.reason || value.note || value.status || ''};
}
function safeURL(value) {
  try { const url = new URL(String(value)); return url.protocol === 'https:' && !url.username && !url.password ? url.href : ''; } catch { return ''; }
}
function time(value, options = {}) {
  if (!value) return 'Time unavailable';
  const date = new Date(value);
  if (!Number.isFinite(date.valueOf())) return 'Time unavailable';
  return date.toLocaleString('en-IN', {timeZone: 'Asia/Kolkata', day: '2-digit', month: 'short', year: 'numeric', hour: '2-digit', minute: '2-digit', hour12: false, ...options});
}
function dayKey(value) {
  if (!value) return '';
  const date = new Date(value);
  if (!Number.isFinite(date.valueOf())) return '';
  return new Date(date.valueOf() + 330 * 60_000).toISOString().slice(0, 10);
}
function dateLabel(value) {
  if (!/^\d{4}-\d{2}-\d{2}$/.test(value || '')) return value || 'Date unavailable';
  return new Date(`${value}T12:00:00+05:30`).toLocaleDateString('en-IN', {timeZone: 'Asia/Kolkata', day: '2-digit', month: 'long', year: 'numeric'});
}
function metricHTML(value) {
  const item = growth(value), cls = item.pct === null ? '' : item.pct < 0 ? 'red' : 'blue';
  return `<span class="${cls}">${percentage(item.pct)}</span>${item.note ? `<small>${safe(item.note)}</small>` : ''}`;
}
function notesHTML(notes) {
  return nonempty(notes).filter(Boolean).map(note => `<div class="insight">${safe(typeof note === 'object' ? note.text || note.reason || note.message : note)}</div>`).join('');
}
function statusText(status = {}) {
  const state = String(status.state || status.status || 'pending').toLowerCase();
  const needsCatalogue = state === 'needs_catalogue' || status.universe_count === 0;
  const isStale = !!status.stale || ['stale','error','failed','unavailable','offline'].includes(state);
  const checked = status.last_checked_at || status.last_checked || status.last_attempt_at;
  const success = status.last_success_at || status.last_success;
  const errors = nonempty(status.errors).slice(0,3).map(value => typeof value === 'string' ? value : value.message || '').filter(Boolean);
  return {state, isStale, needsCatalogue, checked, success, error: status.error || status.last_error || errors.join(' · ') || null, label: needsCatalogue ? 'Company catalogue needed' : isStale ? 'Source needs attention' : ['refreshing','syncing','running'].includes(state) ? 'Checking your companies’ disclosures' : state === 'partial' ? 'Partially updated disclosures' : success ? 'Published disclosures received' : 'Waiting for your companies’ disclosures'};
}
function universeText(status = {}) {
  if (statusText(status).needsCatalogue) return 'No company catalogue is available. Add companies to your website before results can be monitored.';
  const size = finite(status.universe_count);
  return size === null ? 'Results from your company universe. Reading the website catalogue…' : `Monitoring ${count(size)} companies in your website · Published results only`;
}
function historyRange(preset = 'all', today = dayKey(new Date().toISOString())) {
  if (preset === 'today') return {from: today, to: today};
  if (preset === '30days') {
    const start = new Date(`${today}T12:00:00+05:30`);
    start.setUTCDate(start.getUTCDate() - 29);
    return {from: [RESULTS_START_DATE, dayKey(start.toISOString())].sort().at(-1), to: today};
  }
  // This is a view over every stored filing, not a promise of source coverage.
  return {from: RESULTS_START_DATE, to: today};
}
function coverageText(status = {}) {
  const records = finite(status.event_count), figures = finite(status.financial_count);
  if (records === null) return 'Stored history coverage has not been received yet.';
  const pending = figures === null ? null : Math.max(0, records - figures);
  const parts = [`${count(records)} published filings stored`];
  if (figures !== null) parts.push(`${count(figures)} with financial figures`, `${count(pending)} awaiting figures`);
  if (status.earliest_announcement && status.latest_announcement) parts.push(`${dateLabel(dayKey(status.earliest_announcement))} – ${dateLabel(dayKey(status.latest_announcement))}`);
  return parts.join(' · ');
}
function historyProgress(status = {}) {
  const imported = status.history_official_exports_loaded;
  if (imported && imported.range_from === RESULTS_START_DATE && imported.source_exports > 0) return 'Official historical exports are loaded for the selected post-COVID period. Financial figures and comparisons continue to update separately; unmatched company names are excluded.';
  if (status.backfill_complete === true) return 'Configured historical collection completed. Coverage above describes the records actually stored.';
  const remaining = finite(status.backfill_chunks_remaining);
  if (remaining !== null) return `Historical collection is incomplete · ${count(remaining)} batches remaining. Financial documents are enriched separately; published filings stay visible while figures are pending.`;
  return 'Historical collection has not been confirmed. The page shows stored disclosures only; an empty database does not mean no company announced results.';
}
function emptyCollection(status = {}) {
  const source = statusText(status);
  if (source.needsCatalogue) return {title:'Your company catalogue is needed', body:'Add companies to the website catalogue to monitor their published results. The results desk will not fall back to companies outside your website.'};
  const stored = finite(status.event_count);
  if (stored === 0 && (source.error || source.isStale)) return {title:'Historical results have not been collected', body:'The source is unavailable or rejected the request. This does not mean your companies have not declared results. Source status above explains the collection failure.'};
  if (stored === 0 && status.backfill_complete !== true) return {title:'Historical collection is incomplete', body:'There are no stored filings yet. Past announcements will appear as collection succeeds; missing financial comparisons will not hide a published filing.'};
  return {title:'No stored results match this view', body:'Try Post-COVID history or clear the company and growth filters. This describes the stored dataset only, not whether companies announced results.'};
}
function sourceMessage(error) {
  const text = typeof error === 'object' && error ? error.message || error.detail || '' : String(error || '');
  if (/document.*(?:404|not found)|filing identity unavailable/i.test(text)) return 'Some newly published result documents are not ready yet. Their announcements remain visible, and collection retries automatically.';
  if (/403|forbidden|urlopen|nodename|network error|timeout/i.test(text)) return 'A source could not be reached. Available saved results are shown, and automatic checks continue.';
  return text || 'Displaying the last successfully stored disclosures.';
}
function filingHTML(event, selected) {
  const profit = growth(event.growth?.profit_qoq), badgeClass = profit.pct === null ? 'unknown' : profit.pct < 0 ? 'negative' : '';
  const stamp = new Date(event.announced_at), valid = Number.isFinite(stamp.valueOf());
  const clock = valid && event.announcement_time_known !== false ? stamp.toLocaleTimeString('en-IN', {timeZone:'Asia/Kolkata',hour:'2-digit',minute:'2-digit',hour12:false}) : 'Time unavailable';
  const basis = event.basis || 'Scope unconfirmed';
  const hasFigures = event.financial_status === 'available' || Object.values(event.financials || {}).some(value => finite(value) !== null);
  const figureLabel = hasFigures ? '' : '<span class="pending-figures">Figures pending · filing available</span>';
  return `<button class="filing-card${selected ? ' selected' : ''}" data-event="${safe(event.id)}" aria-pressed="${selected}"><span class="filing-top"><span class="company-icon">${safe(String(event.symbol || '?').slice(0,2))}</span><span class="company-title"><strong data-margin-symbol="${safe(event.symbol || '')}">${safe(event.symbol || 'Symbol unavailable')}</strong><small data-margin-symbol="${safe(event.symbol || '')}">${safe(event.company || event.symbol || 'Company unavailable')}</small></span><span class="filing-time">${safe(clock)}<small>${event.revision && Number(event.revision) > 1 ? `Revision ${safe(event.revision)}` : 'Published'}</small></span></span><span class="filing-bottom"><span class="filing-meta">${safe(event.period_end || 'Period unavailable')} · ${safe(basis)}${figureLabel}</span><span class="growth-pill ${badgeClass}">Profit QoQ ${percentage(profit.pct)}</span></span></button>`;
}
function chartHTML(points) {
  const usable = nonempty(points).map(p => ({time: p?.time || p?.t || p?.label, value: finite(p?.return_pct) ?? finite(p?.value)})).filter(p => p.time && p.value !== null);
  if (usable.length < 2) return '';
  const lo = Math.min(0, ...usable.map(p => p.value)), hi = Math.max(0, ...usable.map(p => p.value));
  const y = value => 100 - ((value-lo)/(hi-lo || 1))*80;
  const d = usable.map((p,i) => `${i ? 'L' : 'M'}${12+i/(usable.length-1)*456},${y(p.value).toFixed(2)}`).join(' ');
  return `<div class="chart-wrap"><svg viewBox="0 0 480 120" role="img" aria-label="Observed historical reaction returns"><path d="M12 ${y(0)}H468" stroke="#2e4560" stroke-dasharray="3 4"/><path d="${d}" stroke="#75b5ff" stroke-width="2" fill="none"/></svg><div class="chart-caption"><span>${safe(usable[0].time)}</span><span>Observed returns · %</span><span>${safe(usable.at(-1).time)}</span></div></div>`;
}
function analysisHTML(event) {
  const analysis = event.analysis || {}, pattern = analysis.pattern || {}, forecast = analysis.forecast || {}, baseline = analysis.baseline || {}, coverage = analysis.coverage || {}, reaction = analysis.reaction_session || event.reaction_session || {};
  const n = finite(analysis.sample_size) ?? finite(analysis.sample_count);
  const probability = finite(pattern.probability_pct), low = finite(pattern.wilson_low_pct), high = finite(pattern.wilson_high_pct);
  const med = finite(forecast.median_open_to_1000_pct) ?? finite(analysis.median_return);
  const ready = analysis.status === 'ready';
  const windows = nonempty(pattern.windows);
  const definitions = [['09:15–09:30','Opening move','Did the price rise after opening?'],['09:30–09:40','Pullback','Did it retreat after the opening move?'],['09:40–10:00','Recovery','Did it recover after 09:40?']];
  const sampleRows = nonempty(analysis.matched_samples || analysis.samples).slice(0,12);
  const daily = analysis.daily_study || {}, dailyN = finite(daily.sample_size), dailyMedian = finite(daily.median_open_to_close_pct), dailyProbability = finite(daily.open_to_close_up_probability_pct), dailyLow = finite(daily.open_to_close_wilson_low_pct), dailyHigh = finite(daily.open_to_close_wilson_high_pct);
  const dailyHTML = dailyN !== null ? `<section class="section-pad"><div class="subhead"><h3>Next-session daily evidence</h3><small>Separate from the opening pattern</small></div><div class="research-metrics"><div><span>COMPARABLE DAILY SESSIONS</span><strong>${count(dailyN)}</strong><small>Complete observed daily candles</small></div><div><span>MEDIAN NEXT-SESSION RETURN</span><strong class="${dailyMedian === null ? '' : dailyMedian < 0 ? 'red' : 'blue'}">${percentage(dailyMedian)}</strong><small>Session open to session close</small></div><div><span>POSITIVE OPEN-TO-CLOSE FREQUENCY</span><strong>${dailyProbability === null ? '—' : `${dailyProbability.toFixed(1)}%`}</strong><small>${dailyLow !== null && dailyHigh !== null ? `95% interval ${dailyLow.toFixed(1)}–${dailyHigh.toFixed(1)}%` : 'Probability unavailable'}</small></div><div><span>ALL-RESULTS DAILY BASELINE</span><strong>${finite(daily.baseline?.up_probability_pct) === null ? '—' : `${daily.baseline.up_probability_pct.toFixed(1)}%`}</strong><small>${finite(daily.baseline?.sample_size) === null ? 'Baseline unavailable' : `${count(daily.baseline.sample_size)} observed daily sessions`}</small></div></div><p class="callout">${safe(daily.note || 'Daily candles cannot establish what happened at 09:30 or 09:40 and do not authorize an intraday setup.')}</p></section>` : '';
  const setup = analysis.setup || {}, action = ['BUY_WATCH','SELL_WATCH','WAIT','INSUFFICIENT_DATA'].includes(setup.action) ? setup.action : 'INSUFFICIENT_DATA';
  const label = {BUY_WATCH:'BUY WATCH · CONDITIONAL',SELL_WATCH:'SELL WATCH · CONDITIONAL',WAIT:'WAIT FOR CONFIRMATION',INSUFFICIENT_DATA:'INSUFFICIENT EVIDENCE'}[action];
  const level = setup.levels || {};
  const reasons = nonempty(setup.reasons || setup.reason);
  const excluded = coverage.excluded && typeof coverage.excluded === 'object' ? Object.entries(coverage.excluded).filter(([,value]) => finite(value) !== null && value > 0).map(([key,value]) => `${key.replaceAll('_',' ')}: ${count(value)}`) : [];
  return `${dailyHTML}<section class="section-pad"><div class="subhead"><h3>What happened after similar results?</h3><small>${safe(reaction.date || 'Session not yet established')}</small></div><div class="reaction-banner"><span>⌁</span><p>${ready ? 'A historical comparison is available. The sequence below remains a conditional pattern, not a forecast of a guaranteed move.' : safe(reaction.reason || analysis.summary || 'Not enough complete historical evidence to establish the opening-rise, pullback and recovery pattern.')}</p></div><div class="window-grid">${definitions.map(([range,title,question],i) => { const window = windows[i] || {}; return `<article><small>${safe(window.start && window.end ? `${window.start}–${window.end}` : range)}</small><strong>${title}</strong><p>${finite(window.median_return_pct) !== null ? `<span class="${window.median_return_pct < 0 ? 'red' : 'blue'}">${percentage(window.median_return_pct)}</span> median return` : question}</p></article>`; }).join('')}</div>${chartHTML(analysis.reaction_path || pattern.path)}<div class="research-metrics"><div><span>SIMILAR HISTORICAL EVENTS</span><strong>${count(n)}</strong><small>${safe(analysis.methodology?.cohort || 'Comparable prior announcements')}</small></div><div><span>RISE → PULLBACK → RECOVERY</span><strong>${probability === null ? '—' : `${probability.toFixed(1)}%`}</strong><small>${low !== null && high !== null ? `95% interval ${low.toFixed(1)}–${high.toFixed(1)}%` : 'Probability unavailable until evidence exists'}</small></div><div><span>MEDIAN OPEN → 10:00 RETURN</span><strong class="${med === null ? '' : med < 0 ? 'red' : 'blue'}">${percentage(med)}</strong><small>${safe(forecast.confidence_label || 'Historical observation, not assured return')}</small></div><div><span>ALL-RESULTS PATTERN BASELINE</span><strong>${finite(baseline.pattern_probability_pct) === null ? '—' : `${baseline.pattern_probability_pct.toFixed(1)}%`}</strong><small>${finite(baseline.sample_size) === null ? 'Baseline unavailable' : `${count(baseline.sample_size)} prior observed events`}</small></div></div>${notesHTML(analysis.notes)}<details><summary>View observations & data coverage</summary><p>${finite(coverage.events_considered) !== null ? `${count(coverage.events_considered)} prior events considered. ` : ''}${finite(coverage.events_with_complete_candles) !== null ? `${count(coverage.events_with_complete_candles)} have complete required candles. ` : ''}${coverage.first_session && coverage.last_session ? `Coverage: ${safe(coverage.first_session)} to ${safe(coverage.last_session)}. ` : ''}${analysis.as_of ? `Analysis as of ${safe(time(analysis.as_of))} IST.` : ''}</p>${excluded.length ? `<p>Excluded: ${safe(excluded.join(' · '))}</p>` : ''}${sampleRows.length ? `<ul class="sample-list">${sampleRows.map(row => `<li><span><b data-margin-symbol="${safe(row.symbol || event.symbol || '')}">${safe(row.symbol || event.symbol)}</b> · ${safe(row.session_date || '')}</span><span>${percentage(finite(row.open_to_1000_pct))} to 10:00</span></li>`).join('')}</ul>` : '<p>No complete matching observations available yet. No outcome has been assumed.</p>'}<p>Only information available before the target announcement belongs in its historical comparison. Missing candles and incomplete sessions are excluded. Similar growth alone does not account for expectations, valuation, guidance or market regime. The configured holiday calendar currently covers 2025–2027; older sessions are excluded. Ten comparable quarterly reactions are required, so a directional forecast may remain unavailable.</p></details></section><section class="section-pad"><div class="setup-head"><h3>Conditional trading context</h3><span class="setup-label ${action === 'BUY_WATCH' ? 'buy' : action === 'SELL_WATCH' ? 'sell' : ''}">${label}</span></div><p class="setup-copy">${safe(reasons[0] || (action === 'INSUFFICIENT_DATA' ? 'Wait for enough comparable announcements and verified intraday observations. Good quarter-on-quarter growth alone does not create a trade.' : 'Wait for the stated current-session confirmation before considering this research setup.'))}</p>${reasons.length > 1 ? notesHTML(reasons.slice(1)) : ''}${[level.entry,level.stop,level.target].some(v => finite(v) !== null) ? `<div class="levels-grid"><div><span>Observed trigger · ₹</span><strong>${amount(level.entry)}</strong></div><div><span>Invalidation · ₹</span><strong>${amount(level.stop)}</strong></div><div><span>Rule-based target · ₹</span><strong>${amount(level.target)}</strong></div></div>` : ''}<p class="callout">${finite(setup.cost_bps) === null ? 'Execution costs and slippage are not guaranteed by historical observations.' : `Research cost assumption: ${setup.cost_bps} basis points. Actual execution costs and slippage can differ.`} Watch labels are conditional research. No orders are submitted.</p></section>`;
}
function detailHTML(event) {
  const f = event.financials || {}, previous = event.previous || {}, g = event.growth || {}, news = nonempty(event.news), source = safeURL(event.source_url);
  const previousFinancials = previous.financials || previous;
  const unit = f.unit || event.unit || 'Unit unavailable';
  const insights = nonempty(g.notes).concat(nonempty(event.notes));
  if (event.financial_status === 'revision_pending') insights.unshift('A revised filing is awaiting verification. These figures are the prior saved version; directional research is withheld.');
  return `<div class="company-head"><div class="company-head-top"><span class="company-icon">${safe(String(event.symbol || '?').slice(0,2))}</span><div class="company-title"><h2 data-margin-symbol="${safe(event.symbol || '')}">${safe(event.symbol || 'Company result')}</h2><small data-margin-symbol="${safe(event.symbol || '')}">${safe(event.company || 'Company name unavailable')}</small></div>${source ? `<a class="source-link" href="${safe(source)}" target="_blank" rel="noopener noreferrer">Source filing ↗</a>` : ''}</div><div class="company-badges"><span>Period ended ${safe(event.period_end || 'unavailable')}</span><span>${safe(event.basis || 'Reporting scope unavailable')}</span><span>${safe(event.announcement_time_known === false ? dateLabel(dayKey(event.announced_at)) + ' · Time unavailable' : time(event.announced_at) + ' IST')}</span>${event.revision ? `<span>Revision ${safe(event.revision)}</span>` : ''}</div></div><section class="section-pad"><div class="subhead"><h3>The quarter, compared</h3><small>${safe(unit)}</small></div><table class="comparison-table"><thead><tr><th scope="col">Metric</th><th scope="col">Reported</th><th scope="col">Prior quarter</th><th scope="col">QoQ</th><th scope="col">YoY</th></tr></thead><tbody>${[[f.revenue_metric === 'total_income' ? 'Total income' : 'Revenue','revenue','revenue_qoq','revenue_yoy'],['Net profit','net_profit','profit_qoq','profit_yoy'],['EPS · ₹','eps','eps_qoq','eps_yoy']].map(([name,key,qoq,yoy]) => `<tr><td>${name}</td><td>${amount(f[key])}</td><td>${amount(previousFinancials[key])}</td><td>${metricHTML(g[qoq])}</td><td>${metricHTML(g[yoy])}</td></tr>`).join('')}</tbody></table><p class="comparison-caption">${previous.period_end ? `Prior period ended ${safe(previous.period_end)}. ` : ''}QoQ: previous quarter. YoY: same quarter last year. “—” means unavailable or not comparable; negative and zero-base changes need context.</p>${event.previous_source_url && safeURL(event.previous_source_url) ? `<p class="comparison-caption"><a class="source-link" href="${safe(safeURL(event.previous_source_url))}" target="_blank" rel="noopener noreferrer">Prior quarter filing ↗</a>${event.year_ago_source_url && safeURL(event.year_ago_source_url) ? ` · <a class="source-link" href="${safe(safeURL(event.year_ago_source_url))}" target="_blank" rel="noopener noreferrer">Year-ago filing ↗</a>` : ""}</p>` : ""}${notesHTML(insights)}</section>${analysisHTML(event)}<section class="section-pad"><div class="subhead"><h3>Company disclosures & context</h3><small>Source-linked facts</small></div>${news.length ? `<div class="news-list">${news.slice(0,12).map(item => { const url = safeURL(item.url || item.source_url || item.link); const title = item.title || item.subject || item.headline || 'Company disclosure'; const body = `<span>${safe(time(item.published_at || item.announced_at || item.date))}<em>${safe(item.tone || item.sentiment || item.category || 'DISCLOSURE')}</em></span><b>${safe(title)} ${url ? '↗' : ''}</b>${item.summary || item.note ? `<p>${safe(item.summary || item.note)}</p>` : ''}`; return url ? `<a class="news-item" href="${safe(url)}" target="_blank" rel="noopener noreferrer">${body}</a>` : `<div class="news-item">${body}</div>`; }).join('')}</div>` : '<p class="small-empty">No additional verified company disclosures are currently stored for this result. An empty feed does not establish an absence of company risks.</p>'}<p class="callout">Positive developments are shown alongside other published disclosures. A headline does not establish its financial impact.</p></section>`;
}

function organizeResearchPanel(panel) {
  // Preserve source content and calculations; collapse only secondary reading.
  panel.querySelectorAll('.comparison-table').forEach(table => {
    const scroll = panel.ownerDocument.createElement('div');
    scroll.className = 'comparison-scroll';
    scroll.setAttribute('role','region'); scroll.setAttribute('aria-label','Quarterly financial comparison'); scroll.tabIndex = 0;
    table.replaceWith(scroll); scroll.append(table);
  });
  panel.querySelectorAll(':scope > .section-pad').forEach(section => {
    const heading = section.querySelector('h3')?.textContent;
    if (!['Next-session daily evidence','Company disclosures & context'].includes(heading)) return;
    const details = panel.ownerDocument.createElement('details'); details.className = 'research-disclosure';
    const summary = panel.ownerDocument.createElement('summary'); summary.textContent = heading;
    section.replaceWith(details); details.append(summary, section);
    section.querySelector('.subhead h3')?.remove();
  });
}

async function boot() {
  const $ = selector => document.querySelector(selector);
  const menu = $('#resultsMenu'), navigation = $('#resultsNavigation');
  const closeNavigation = () => { navigation?.classList.remove('results-nav-open'); menu?.setAttribute('aria-expanded','false'); menu?.setAttribute('aria-label','Open workspace navigation'); };
  menu?.addEventListener('click', () => {
    const open = menu.getAttribute('aria-expanded') !== 'true';
    navigation.classList.toggle('results-nav-open', open);
    menu.setAttribute('aria-expanded', String(open));
    menu.setAttribute('aria-label', `${open ? 'Close' : 'Open'} workspace navigation`);
    if (open) navigation.querySelector('a')?.focus();
  });
  document.addEventListener('keydown', event => { if (event.key === 'Escape' && menu?.getAttribute('aria-expanded') === 'true') { closeNavigation(); menu.focus(); } });
  navigation?.querySelectorAll('a').forEach(link => link.addEventListener('click', closeNavigation));
  const renderDetail = event => {
    const panel = $('#research'); panel.innerHTML = detailHTML(event);
    organizeResearchPanel(panel);
  };
  let events = [], selectedID = null, detailCache = new Map(), busy = false, revision = 0, detailRevision = 0, poll, nextOffset = null, total = null, sourceStatus = {};
  const today = dayKey(new Date().toISOString());
  const initialRange = historyRange('all', today);
  $('#dateTo').value = initialRange.to; $('#dateFrom').value = initialRange.from;
  async function fetchJSON(path) {
    const controller = new AbortController(), timer = setTimeout(() => controller.abort(), 20_000);
    try {
      const response = await fetch(path, {credentials:'same-origin',cache:'no-store',signal:controller.signal});
      const body = await response.json();
      if (response.status === 404 && !path.includes('/detail')) throw Error('Restart your local website to enable Results & Reactions, then refresh this page.');
      if (!response.ok) throw Error(body.detail || body.error || `Request failed (${response.status})`);
      return body;
    } catch (error) { if (error.name === 'AbortError') throw Error('The results service took too long to respond. Your last received data is kept.'); throw error; }
    finally { clearTimeout(timer); }
  }
  function renderStatus(status = {}) {
    sourceStatus = status;
    const s = statusText(status);
    $('#universeScope').textContent = universeText(status);
    $('#universeScope').title = status.universe_source ? `Company universe source: ${String(status.universe_source).replaceAll('_',' ')}` : 'Your website company catalogue';
    $('#sourceState').textContent = s.label;
    $('#sourceDot').className = `status-dot${s.needsCatalogue ? ' pending' : s.isStale ? ' down' : !s.success ? ' pending' : ''}`;
    $('#storedCoverage').textContent = coverageText(status);
    $('#historyProgress').textContent = historyProgress(status);
    const coverage = status.coverage || {};
    $('#sourceDescription').textContent = status.description || (finite(status.event_count) !== null ? `${count(status.event_count)} filings stored · ${count(status.financial_count)} with figures · ${count(status.symbols_with_candles)} companies with candles` : finite(coverage.symbols_enriched) !== null ? `${count(coverage.symbols_enriched)} companies enriched · polling every ${status.poll_seconds || status.refresh_interval_seconds || 300}s` : 'NSE published filings · server checks every 5 minutes');
    $('#lastCheck').textContent = s.checked ? `Checked ${time(s.checked)} IST` : 'No completed source check yet';
    $('#nextCheck').textContent = status.next_refresh_at || status.next_poll_at ? `Next check ${time(status.next_refresh_at || status.next_poll_at, {day:undefined,month:undefined,year:undefined})} IST` : finite(status.backfill_chunks_remaining) !== null && status.backfill_chunks_remaining > 0 ? `History backfill: ${count(status.backfill_chunks_remaining)} batches remaining · Checks every ${Math.round((status.poll_seconds || 300)/60)} min` : 'Automatic checks while server is running';
    $('#errorBanner').hidden = !s.error && !s.isStale && !s.needsCatalogue;
    $('#errorBanner').title = typeof s.error === 'string' ? s.error : '';
    if (!$('#errorBanner').hidden) $('#errorBanner').textContent = s.needsCatalogue ? universeText(status) : `${s.isStale ? 'Source data may be stale. ' : ''}${sourceMessage(s.error)}${s.success ? ` Last successful check: ${time(s.success)} IST.` : ' No successful source refresh has been confirmed.'}`;
  }
  function renderSummary() {
    $('#totalCount').textContent = count(total ?? events.length);
    $('#positiveCount').textContent = count(events.filter(e => (growth(e.growth?.profit_qoq).pct ?? -Infinity) > 0).length);
    $('#negativeCount').textContent = count(events.filter(e => (growth(e.growth?.profit_qoq).pct ?? Infinity) < 0).length);
    const analyses = events.filter(e => e.analysis);
    $('#evidenceCount').textContent = analyses.length ? count(analyses.filter(e => (finite(e.analysis.sample_size) ?? 0) > 0).length) : '—';
  }
  function renderList() {
    $('#filingList').setAttribute('aria-busy','false');
    if (!events.length) {
      const empty = emptyCollection(sourceStatus);
      $('#filingList').innerHTML = `<div class="empty-state"><span class="empty-symbol">◷</span><h3>${safe(empty.title)}</h3><p>${safe(empty.body)}</p></div>`;
      $('#listFooter').textContent = '';
      return;
    }
    const grouped = new Map();
    [...events].sort((a,b) => (Date.parse(b.announced_at) || 0)-(Date.parse(a.announced_at) || 0)).forEach(event => { const key = dayKey(event.announced_at) || 'Date unavailable'; if (!grouped.has(key)) grouped.set(key,[]); grouped.get(key).push(event); });
    $('#filingList').innerHTML = [...grouped].map(([date,items]) => `<div class="date-heading">${safe(dateLabel(date))}<span>${items.length} ${items.length === 1 ? 'filing' : 'filings'}</span></div>${items.map(event => filingHTML(event,String(event.id) === selectedID)).join('')}`).join('');
    $('#listFooter').innerHTML = `<span>${events.length.toLocaleString('en-IN')}${total !== null ? ` of ${count(total)}` : ''} received disclosures · Reporting scopes are shown separately.</span>${nextOffset !== null ? '<button type="button" id="loadMore" class="button">Load more results ↓</button>' : ''}`;
    $('#loadMore')?.addEventListener('click',() => refresh(true));
    $('#filingList').querySelectorAll('[data-event]').forEach(button => button.addEventListener('click',() => select(button.dataset.event,true)));
  }
  async function select(id, scroll = false) {
    selectedID = String(id); const current = events.find(e => String(e.id) === selectedID); if (!current) return;
    renderList(); renderDetail(detailCache.get(selectedID) || current);
    if (scroll && matchMedia('(max-width: 1020px)').matches) $('#research').scrollIntoView({behavior:matchMedia('(prefers-reduced-motion: reduce)').matches ? 'auto' : 'smooth',block:'start'});
    const token = ++detailRevision;
    $('#research').classList.add('research-loading');
    try {
      const response = await fetchJSON(`/api/results/detail?id=${encodeURIComponent(id)}`), event = response.event || response.result || response;
      if (token !== detailRevision || selectedID !== String(id)) return;
      if (!event || typeof event !== 'object' || !event.symbol) throw Error('The selected result could not be read.');
      const merged = {...current,...event,analysis:response.analysis || event.analysis || current.analysis}; detailCache.set(selectedID,merged); renderDetail(merged);
    } catch (error) {
      if (token !== detailRevision) return;
      renderDetail(current);
      const p = document.createElement('p'); p.className = 'notice warning'; p.textContent = `Detailed analysis unavailable: ${error.message}`; $('#research').append(p);
    } finally { if (token === detailRevision) $('#research').classList.remove('research-loading'); }
  }
  async function refresh(append = false) {
    append = append === true;
    if (busy) return;
    const from = $('#dateFrom').value, to = $('#dateTo').value;
    if (!from || !to || from > to || from < RESULTS_START_DATE) { $('#errorBanner').hidden = false; $('#errorBanner').textContent = 'Choose a date range from 6 May 2023 onward. The start date must be on or before the end date.'; return; }
    busy = true; $('#refresh').disabled = true; if ($('#loadMore')) $('#loadMore').disabled = true; const current = ++revision;
    const params = new URLSearchParams({from,to,search:$('#companySearch').value.trim(),tone:$('#tone').value,limit:'100',offset:String(append ? nextOffset ?? 0 : 0)});
    try {
      const payload = await fetchJSON(`/api/results?${params}`); if (current !== revision) return;
      if (!Array.isArray(payload.events)) throw Error('The results service returned an unreadable filing list.');
      const received = payload.events.filter(e => e && e.id != null && dayKey(e.announced_at) >= RESULTS_START_DATE && String(e.period_end || '') >= RESULTS_START_DATE);
      if (append) { const seen = new Set(events.map(e => String(e.id))); events = events.concat(received.filter(e => !seen.has(String(e.id)))); }
      else events = received;
      total = finite(payload.total); nextOffset = finite(payload.next_offset); detailCache.clear(); renderStatus(payload.status || {}); renderSummary();
      if (!events.some(e => String(e.id) === selectedID)) selectedID = events[0] ? String(events[0].id) : null;
      renderList();
      if (selectedID) await select(selectedID);
      else { ++detailRevision; $('#research').innerHTML = '<div class="empty-state research-empty"><span class="empty-symbol">◎</span><h2>No result selected</h2><p>Published disclosures will appear in the ledger when available. Historical probabilities stay unavailable until enough verified observations exist.</p></div>'; }
    } catch (error) {
      $('#errorBanner').hidden = false; $('#errorBanner').textContent = `Results service unavailable: ${error.message}${events.length ? ' The last received list is kept; it may be stale.' : ' Check that the updated website server is running.'}`;
      $('#sourceState').textContent = 'Results service unavailable'; $('#sourceDot').className = 'status-dot down';
      if (!events.length) { $('#filingList').setAttribute('aria-busy','false'); $('#filingList').innerHTML = '<div class="empty-state"><span class="empty-symbol">↯</span><h3>Could not reach the results service</h3><p>Use Check now to retry. No unavailable company result or reaction has been filled with a guessed value.</p></div>'; }
    } finally { busy = false; $('#refresh').disabled = false; if ($('#loadMore')) $('#loadMore').disabled = false; }
  }
  $('#filters').addEventListener('submit',event => {event.preventDefault();document.querySelectorAll('[data-history-range]').forEach(button => button.setAttribute('aria-pressed','false'));refresh();});
  document.querySelectorAll('[data-history-range]').forEach(button => button.addEventListener('click',() => {
    const range = historyRange(button.dataset.historyRange, dayKey(new Date().toISOString()));
    $('#dateFrom').value = range.from; $('#dateTo').value = range.to;
    document.querySelectorAll('[data-history-range]').forEach(item => item.setAttribute('aria-pressed',String(item === button)));
    refresh();
  }));
  $('#refresh').addEventListener('click',refresh);
  document.addEventListener('visibilitychange',() => {if (!document.hidden) refresh();});
  poll = setInterval(() => {if (!document.hidden) refresh();},60_000);
  window.addEventListener('pagehide',() => clearInterval(poll),{once:true});
  await refresh();
}
if (typeof document !== 'undefined' && document.querySelector('#filingList')) boot();
export {safe, finite, percentage, growth, safeURL, dayKey, statusText, universeText, historyRange, coverageText, historyProgress, emptyCollection, sourceMessage, filingHTML, detailHTML, analysisHTML, organizeResearchPanel, boot};
