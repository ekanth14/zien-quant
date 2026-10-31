const $ = (selector, root = document) => root.querySelector(selector);
const $$ = (selector, root = document) => [...root.querySelectorAll(selector)];
const safe = value => String(value ?? '').replace(/[&<>"']/g, char => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[char]));
const IST = 'Asia/Kolkata';
const MIN_DATE = '2023-05-06';

const money = value => Number.isFinite(Number(value))
  ? `₹${Number(value).toLocaleString('en-IN', { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`
  : '—';
const formatDate = timestamp => new Date(timestamp * 1000).toLocaleDateString('en-IN', {
  timeZone: IST, day: '2-digit', month: 'short', year: 'numeric',
});
const isoDate = timestamp => new Date(timestamp * 1000).toLocaleDateString('en-CA', { timeZone: IST });
const formatTime = timestamp => new Date(timestamp * 1000).toLocaleTimeString('en-IN', {
  timeZone: IST, hour: '2-digit', minute: '2-digit', hour12: false,
});
const todayIST = () => new Intl.DateTimeFormat('en-CA', { timeZone: IST }).format(new Date());
const dateOffset = (dateText, days) => {
  const date = new Date(`${dateText}T00:00:00Z`);
  date.setUTCDate(date.getUTCDate() + days);
  return new Intl.DateTimeFormat('en-CA', { timeZone: IST }).format(date);
};

export function createHistoricalChart({ api }) {
  const panel = $('#historyPanel');
  const backdrop = $('#historyBackdrop');
  const state = {
    open: false, expanded: false, instrument: null, results: [], searchTimer: null, searchRequestId: 0,
    range: '1M', resolution: '5', dailyCandles: [], lineCandles: [], intradayCandles: [],
    selectedDate: null, selectedPoint: null, selectedCandle: null, loading: false, intradayMessage: null, intradayMessageKind: '',
    error: null, requestId: 0, intradayRequestId: 0, latestAvailableDate: null,
  };
  let resizeObserver = null;
  let returnFocus = null;

  function setStatus(message, kind = '') {
    const status = $('#historyStatus');
    status.textContent = message;
    status.className = `history-status${kind ? ` ${kind}` : ''}`;
  }

  function showChartMessage(container, message, kind = '') {
    container.innerHTML = `<div class="history-chart-message${kind ? ` ${kind}` : ''}">${safe(message)}</div>`;
  }

  function setPanelOpen(open) {
    if (open && !state.open) returnFocus = document.activeElement;
    state.open = open;
    panel.hidden = !open;
    backdrop.hidden = !open;
    if (open) panel.setAttribute('aria-modal', 'true');
    else panel.removeAttribute('aria-modal');
    document.body.classList.toggle('history-panel-open', open);
    if (open) {
      requestAnimationFrame(() => {
        if (!state.open) return;
        $('#historySymbolSearch').focus({ preventScroll: true });
        drawCharts();
      });
    } else if (returnFocus?.isConnected) {
      returnFocus.focus({ preventScroll: true });
      returnFocus = null;
    }
  }

  function setExpanded(expanded) {
    state.expanded = expanded;
    panel.classList.toggle('expanded', expanded);
    $('#historyExpand').setAttribute('aria-label', expanded ? 'Collapse historical chart' : 'Expand historical chart');
    $('#historyExpand').title = expanded ? 'Collapse chart' : 'Expand chart';
    if (expanded && state.instrument && state.selectedDate && state.range === '1D') {
      state.intradayCandles = state.lineCandles;
      state.selectedCandle = state.intradayCandles.length ? state.intradayCandles.length - 1 : null;
      if (state.selectedCandle !== null) updateCandleReadout(state.selectedCandle);
    } else if (expanded && state.instrument && state.selectedDate && !state.intradayCandles.length) {
      loadIntraday(state.selectedDate);
    }
    requestAnimationFrame(drawCharts);
  }

  function renderSearchResults() {
    const results = $('#historySymbolResults');
    if (!state.results.length) {
      const query = $('#historySymbolSearch').value.trim();
      results.hidden = !query;
      results.innerHTML = query ? '<div class="history-no-results">No matching NSE instrument was found.</div>' : '';
      return;
    }
    results.hidden = false;
    results.innerHTML = state.results.map((item, index) => `<button type="button" class="history-symbol-result" data-history-result="${index}">
      <span><b>${safe(item.short_symbol)}</b><small>${safe(item.name)}</small></span><span class="badge">${item.kind === 'index' ? 'INDEX' : 'NSE'}</span>
    </button>`).join('');
    $$('[data-history-result]', results).forEach(button => {
      button.onclick = () => chooseInstrument(state.results[Number(button.dataset.historyResult)]);
    });
  }

  async function searchSymbols(query, { autoSelect = false } = {}) {
    const clean = String(query || '').trim();
    if (!clean) {
      state.searchRequestId += 1;
      state.results = [];
      renderSearchResults();
      return;
    }
    const requestId = ++state.searchRequestId;
    try {
      const data = await api(`/api/historical/symbols?q=${encodeURIComponent(clean)}&limit=15`);
      if (requestId !== state.searchRequestId) return;
      state.results = Array.isArray(data.instruments) ? data.instruments : [];
      renderSearchResults();
      if (autoSelect) {
        const exact = state.results.find(item => item.short_symbol.toUpperCase() === clean.toUpperCase())
          || state.results.find(item => item.symbol.toUpperCase() === clean.toUpperCase())
          || state.results[0];
        if (exact) await chooseInstrument(exact);
        else setStatus('No matching NSE instrument was found in FYERS’s symbol master.', 'empty');
      }
    } catch (error) {
      if (requestId === state.searchRequestId) setStatus(error.message || 'FYERS symbol search is unavailable.', 'error');
    }
  }

  async function chooseInstrument(instrument) {
    state.instrument = instrument;
    state.dailyCandles = [];
    state.lineCandles = [];
    state.intradayCandles = [];
    state.selectedDate = null;
    state.selectedPoint = null;
    state.selectedCandle = null;
    $('#historySymbolSearch').value = `${instrument.short_symbol} · ${instrument.name}`;
    $('#historySymbolResults').hidden = true;
    $('#historySymbolResults').innerHTML = '';
    $('#historyDataSource').textContent = 'FYERS Historical API · not streaming; delay depends on your data permission';
    $('#historyCandleTitle').textContent = 'Selected day';
    setStatus(`Loading recorded prices for ${instrument.short_symbol}…`);
    await loadRange('1M');
  }

  function rangeDates(range) {
    const today = todayIST();
    if (range === '5D') return { from: dateOffset(today, -21), to: today, label: '5 TRADING DAYS' };
    if (range === '1M') return { from: dateOffset(today, -31), to: today, label: '1 MONTH' };
    if (range === '6M') return { from: dateOffset(today, -183), to: today, label: '6 MONTHS' };
    if (range === 'YTD') return { from: `${today.slice(0, 4)}-01-01`, to: today, label: 'YEAR TO DATE' };
    return { from: $('#historyDateFrom').value, to: $('#historyDateTo').value, label: 'CUSTOM RANGE' };
  }

  function updateDateControls() {
    const minDate = MIN_DATE;
    const maxDate = state.latestAvailableDate || todayIST();
    for (const input of [$('#historyDay'), $('#historyDateFrom'), $('#historyDateTo')]) {
      input.min = minDate;
      input.max = maxDate;
    }
    if (state.selectedDate) $('#historyDay').value = state.selectedDate;
    $('#historyDateTo').value = $('#historyDateTo').value || maxDate;
    $('#historyDateFrom').value = $('#historyDateFrom').value || dateOffset(maxDate, -31);
  }

  async function requestCandles(from, to, resolution) {
    if (!state.instrument) return [];
    const params = new URLSearchParams({
      symbol: state.instrument.symbol,
      from,
      to,
      resolution,
    });
    const payload = await api(`/api/historical/candles?${params.toString()}`);
    return Array.isArray(payload.candles) ? payload.candles : [];
  }

  async function loadRange(range = state.range) {
    if (!state.instrument) return;
    state.range = range;
    state.error = null;
    state.intradayMessage = null;
    state.intradayMessageKind = '';
    state.loading = true;
    state.requestId += 1;
    const requestId = state.requestId;
    state.lineCandles = [];
    state.dailyCandles = [];
    setStatus('Loading historical data from FYERS…');
    drawCharts();
    $$('.history-range-row [data-history-range]').forEach(button => button.classList.toggle('active', button.dataset.historyRange === range));
    $('#historyCustomRange').hidden = range !== 'CUSTOM';
    $('#historyRangeStamp').textContent = range === '1D' ? 'INTRADAY' : rangeDates(range).label;
    $('#historyChartTitle').textContent = range === '1D' ? 'Intraday price' : 'Daily close';

    try {
      if (range === '1D') {
        const date = state.selectedDate || state.latestAvailableDate || todayIST();
        state.selectedDate = date;
        state.lineCandles = await requestCandles(date, date, state.resolution);
        if (requestId !== state.requestId) return;
        state.dailyCandles = [];
        const latestIntradayDate = state.lineCandles.length ? isoDate(state.lineCandles.at(-1).t) : null;
        if (latestIntradayDate && (!state.latestAvailableDate || latestIntradayDate > state.latestAvailableDate)) state.latestAvailableDate = latestIntradayDate;
      } else {
        const { from, to } = rangeDates(range);
        if (!from || !to || from < MIN_DATE || from > to || (state.latestAvailableDate && to > state.latestAvailableDate)) throw new Error('Choose dates from 6 May 2023 through the latest available date.');
        state.dailyCandles = await requestCandles(from, to, 'D');
        if (requestId !== state.requestId) return;
        if (range === '5D') state.dailyCandles = state.dailyCandles.slice(-5);
        state.lineCandles = state.dailyCandles;
        if (state.dailyCandles.length) {
          const returnedDate = isoDate(state.dailyCandles.at(-1).t);
          if (!state.latestAvailableDate || returnedDate > state.latestAvailableDate) state.latestAvailableDate = returnedDate;
          const selectedInRange = state.selectedDate && state.dailyCandles.some(candle => isoDate(candle.t) === state.selectedDate);
          if (!selectedInRange) state.selectedDate = returnedDate;
        }
      }
      if (requestId !== state.requestId) return;
      state.loading = false;
      updateDateControls();
      if (state.lineCandles.length) {
        setStatus(`${state.lineCandles.length} recorded ${range === '1D' ? 'intraday points' : 'daily candles'} · FYERS historical data · not streaming.`);
        updateSummary(state.lineCandles.length - 1);
        if (state.expanded) await loadIntraday(state.selectedDate, requestId);
        else {
          state.intradayCandles = [];
          $('#historyCandleTitle').textContent = formatDay(state.selectedDate);
          $('#historyCandleReadout').textContent = 'Expand the panel to load this day’s intraday candles.';
        }
      } else {
        state.intradayCandles = [];
        state.intradayMessage = state.selectedDate
          ? `FYERS returned no intraday candles for ${formatDay(state.selectedDate)}. It may be a weekend, NSE holiday, listing-history gap, or plan limit.`
          : 'No intraday candles are available for this range.';
        state.intradayMessageKind = 'empty';
        $('#historyCandleTitle').textContent = state.selectedDate ? formatDay(state.selectedDate) : 'Selected day';
        $('#historyCandleReadout').textContent = state.intradayMessage;
        setStatus('FYERS returned no candles for this range. Weekends, holidays, listing dates, and plan coverage can leave gaps.', 'empty');
        renderSummary(null);
      }
    } catch (error) {
      if (requestId !== state.requestId) return;
      state.loading = false;
      state.error = error.message || 'Historical prices are unavailable.';
      setStatus(state.error, 'error');
      state.intradayCandles = [];
      state.intradayMessage = state.error;
      state.intradayMessageKind = 'error';
      $('#historyCandleTitle').textContent = state.selectedDate ? formatDay(state.selectedDate) : 'Selected day';
      $('#historyCandleReadout').textContent = state.error;
      renderSummary(null);
    }
    drawCharts();
  }

  async function loadIntraday(day, parentRequestId = state.requestId) {
    if (!state.instrument || !day) return;
    state.selectedDate = day;
    updateDateControls();
    $('#historyCandleTitle').textContent = formatDay(day);
    state.intradayRequestId += 1;
    const requestId = state.intradayRequestId;
    state.intradayMessage = `Loading ${state.resolution}-minute candles for ${formatDay(day)}…`;
    state.intradayMessageKind = '';
    $('#historyCandleReadout').textContent = `Loading ${state.resolution}-minute candles for ${formatDay(day)}…`;
    showChartMessage($('#historyCandleChart'), 'Loading the selected trading day…');
    if (state.range === '1D' && state.lineCandles.length && isoDate(state.lineCandles[0].t) === day) {
      state.intradayCandles = state.lineCandles;
      state.selectedCandle = state.intradayCandles.length - 1;
      drawIntradayChart();
      updateCandleReadout(state.selectedCandle);
      return;
    }
    try {
      const candles = await requestCandles(day, day, state.resolution);
      if (requestId !== state.intradayRequestId || parentRequestId !== state.requestId) return;
      state.intradayCandles = candles;
      state.selectedCandle = candles.length ? candles.length - 1 : null;
      if (!candles.length) {
        const oneYearAgo = dateOffset(todayIST(), -365);
        const message = day < oneYearAgo
          ? 'No intraday candles were returned for this date. FYERS plan coverage may limit older intraday history; the daily chart remains available.'
          : 'No intraday candles were returned for this date. It may be a weekend, NSE holiday, or unavailable symbol history.';
        state.intradayMessage = message;
        state.intradayMessageKind = 'empty';
        $('#historyCandleReadout').textContent = message;
        showChartMessage($('#historyCandleChart'), message, 'empty');
      } else {
        state.intradayMessage = null;
        state.intradayMessageKind = '';
        updateCandleReadout(state.selectedCandle);
        drawIntradayChart();
      }
    } catch (error) {
      if (requestId !== state.intradayRequestId) return;
      state.intradayCandles = [];
      state.intradayMessage = error.message || 'Intraday candles are unavailable.';
      state.intradayMessageKind = 'error';
      $('#historyCandleReadout').textContent = error.message || 'Intraday candles are unavailable.';
      showChartMessage($('#historyCandleChart'), error.message || 'Intraday candles are unavailable.', 'error');
    }
  }

  function formatDay(day) {
    const parsed = new Date(`${day}T12:00:00+05:30`);
    return parsed.toLocaleDateString('en-IN', { timeZone: IST, weekday: 'short', day: '2-digit', month: 'short', year: 'numeric' });
  }

  function renderSummary(candle, index = null) {
    const price = $('#historyPrice');
    const change = $('#historyPriceChange');
    const time = $('#historyPriceTime');
    if (!candle) {
      price.textContent = '—';
      change.textContent = state.instrument ? 'No price recorded for this range' : 'Choose a symbol';
      change.className = '';
      time.textContent = 'FYERS Historical API · not streaming; delay depends on your data permission';
      return;
    }
    const base = state.lineCandles[0]?.c;
    const percent = Number.isFinite(base) && base !== 0 ? (candle.c / base - 1) * 100 : null;
    price.textContent = money(candle.c);
    change.textContent = percent === null ? formatDate(candle.t) : `${percent >= 0 ? '+' : ''}${percent.toFixed(2)}% over selected range`;
    change.className = percent === null ? '' : percent >= 0 ? 'positive' : 'negative';
    const day = formatDate(candle.t);
    const timestamp = state.range === '1D' ? `${day}, ${formatTime(candle.t)} IST` : `${day} · daily close`;
    const latest = state.lineCandles.at(-1);
    const latestText = latest ? `Latest available ${formatDate(latest.t)}${state.range === '1D' ? ` ${formatTime(latest.t)} IST` : ''}` : 'Latest date unavailable';
    time.textContent = `${timestamp} · ${latestText} · FYERS Historical API · historical, not streaming; delay depends on your data permission`;
    if (index !== null) state.selectedPoint = index;
  }

  function updateSummary(index) {
    const candle = state.lineCandles[index];
    if (!candle) return;
    renderSummary(candle, index);
  }

  function drawLineChart() {
    const area = $('#historyLineChart');
    if (!state.instrument) return showChartMessage(area, 'Search for an NSE stock or index to begin.');
    if (state.loading && !state.lineCandles.length) return showChartMessage(area, 'Loading actual FYERS candles…');
    const candles = state.lineCandles;
    if (!candles.length) return showChartMessage(area, state.error || 'No recorded prices are available for this range.', state.error ? 'error' : 'empty');
    const rect = area.getBoundingClientRect();
    const width = Math.max(300, Math.floor(rect.width || 400));
    const height = Math.max(190, Math.floor(rect.height || 250));
    const pad = { left: 68, right: 16, top: 14, bottom: 32 };
    const values = candles.map(item => Number(item.c)).filter(Number.isFinite);
    let low = Math.min(...values), high = Math.max(...values);
    const spread = high - low || Math.max(1, Math.abs(high) * 0.01);
    low -= spread * 0.12; high += spread * 0.12;
    const plotWidth = width - pad.left - pad.right;
    const plotHeight = height - pad.top - pad.bottom;
    const x = index => pad.left + (candles.length <= 1 ? plotWidth / 2 : index / (candles.length - 1) * plotWidth);
    const y = value => pad.top + (high - value) / (high - low) * plotHeight;
    const path = candles.map((item, index) => `${index ? 'L' : 'M'}${x(index).toFixed(1)},${y(item.c).toFixed(1)}`).join(' ');
    const lineUp = candles.at(-1).c >= candles[0].c;
    const color = lineUp ? 'var(--green)' : 'var(--red)';
    const areaPath = `${path} L${x(candles.length - 1).toFixed(1)},${(height - pad.bottom).toFixed(1)} L${x(0).toFixed(1)},${(height - pad.bottom).toFixed(1)} Z`;
    let svg = `<svg viewBox="0 0 ${width} ${height}" role="img" aria-label="${safe(state.instrument.short_symbol)} historical price chart with ${candles.length} actual FYERS observations."><defs><linearGradient id="historyFill" x1="0" x2="0" y1="0" y2="1"><stop offset="0%" stop-color="${color}" stop-opacity=".24"/><stop offset="100%" stop-color="${color}" stop-opacity=".015"/></linearGradient></defs>`;
    for (let i = 0; i <= 3; i += 1) {
      const value = high - (high - low) * i / 3;
      const yy = y(value);
      svg += `<line x1="${pad.left}" x2="${width - pad.right}" y1="${yy}" y2="${yy}" class="history-grid-line"/><text x="${pad.left - 8}" y="${yy + 3}" class="history-axis-label" text-anchor="end">${money(value)}</text>`;
    }
    svg += `<path d="${areaPath}" fill="url(#historyFill)"/><path d="${path}" fill="none" stroke="${color}" stroke-width="2.5" stroke-linejoin="round" stroke-linecap="round"/>`;
    const tickIndices = [...new Set([0, Math.floor((candles.length - 1) / 2), candles.length - 1])];
    tickIndices.forEach(index => {
      const label = state.range === '1D' ? formatTime(candles[index].t) : new Date(candles[index].t * 1000).toLocaleDateString('en-IN', { timeZone: IST, day: '2-digit', month: 'short' });
      svg += `<text x="${x(index)}" y="${height - 8}" class="history-axis-label" text-anchor="${index === 0 ? 'start' : index === candles.length - 1 ? 'end' : 'middle'}">${safe(label)}</text>`;
    });
    const selectedDate = state.selectedDate;
    candles.forEach((candle, index) => {
      const selected = selectedDate === isoDate(candle.t);
      const radius = selected ? 4.5 : candles.length < 80 ? 2.7 : 2;
      svg += `<circle data-history-point="${index}" cx="${x(index)}" cy="${y(candle.c)}" r="9" fill="transparent" pointer-events="all" tabindex="0" role="button" aria-label="Select ${safe(formatDate(candle.t))}, close ${safe(money(candle.c))}"/><circle cx="${x(index)}" cy="${y(candle.c)}" r="${radius}" fill="${selected ? color : 'var(--panel)'}" stroke="${color}" stroke-width="1.8" pointer-events="none"/>`;
    });
    svg += '</svg>';
    area.innerHTML = svg;
    $$('[data-history-point]', area).forEach(point => {
      const index = Number(point.dataset.historyPoint);
      point.addEventListener('focus', () => updateSummary(index));
      point.addEventListener('keydown', event => {
        if (event.key === 'Enter' || event.key === ' ') {
          event.preventDefault();
          selectTradingDay(isoDate(candles[index].t));
        }
      });
    });
    const pointAt = event => {
      const rect = area.getBoundingClientRect();
      const chartX = (event.clientX - rect.left) / Math.max(1, rect.width) * width;
      if (chartX < pad.left || chartX > width - pad.right) return null;
      return Math.max(0, Math.min(candles.length - 1, Math.round((chartX - pad.left) / Math.max(1, plotWidth) * (candles.length - 1))));
    };
    area.onpointermove = event => {
      if (event.pointerType === 'touch') return;
      const index = pointAt(event);
      if (index !== null) updateSummary(index);
    };
    area.onclick = event => {
      const index = pointAt(event);
      if (index !== null) selectTradingDay(isoDate(candles[index].t));
    };
    if (state.selectedPoint === null) updateSummary(candles.length - 1);
  }

  async function selectTradingDay(day) {
    if (!day || day < MIN_DATE || day > (state.latestAvailableDate || todayIST())) return;
    state.selectedDate = day;
    if (state.range !== '1D') state.requestId += 1;
    const visibleIndex = state.lineCandles.findIndex(candle => isoDate(candle.t) === day);
    if (visibleIndex >= 0) updateSummary(visibleIndex);
    else {
      renderSummary(null);
      $('#historyPriceChange').textContent = 'Selected day is outside the visible price range';
      $('#historyPriceTime').textContent = `${formatDay(day)} · FYERS historical data · no price is shown for an off-chart date`;
    }
    updateDateControls();
    if (state.range === '1D') await loadRange('1D');
    else if (state.expanded) await loadIntraday(day);
    else {
      state.intradayCandles = [];
      $('#historyCandleTitle').textContent = formatDay(day);
      $('#historyCandleReadout').textContent = 'Expand the panel to load this day’s intraday candles.';
    }
    drawCharts();
  }

  function updateCandleReadout(index) {
    const candle = state.intradayCandles[index];
    if (!candle) return;
    state.selectedCandle = index;
    const volume = Number.isFinite(Number(candle.v)) ? ` · V ${Number(candle.v).toLocaleString('en-IN')}` : '';
    $('#historyCandleReadout').textContent = `${formatTime(candle.t)} IST · O ${money(candle.o)} · H ${money(candle.h)} · L ${money(candle.l)} · C ${money(candle.c)}${volume}`;
  }

  function drawIntradayChart() {
    const area = $('#historyCandleChart');
    const candles = state.intradayCandles;
    if (!state.instrument) return showChartMessage(area, 'Choose an instrument first.');
    if (!candles.length) return showChartMessage(area, state.intradayMessage || (state.selectedDate ? `No intraday candles loaded for ${formatDay(state.selectedDate)}.` : 'Choose a trading day to load intraday candles.'), state.intradayMessageKind || 'empty');
    const rect = area.getBoundingClientRect();
    const width = Math.max(300, Math.floor(rect.width || 400));
    const height = Math.max(210, Math.floor(rect.height || 270));
    const hasVolume = candles.some(item => Number(item.v) > 0);
    const pad = { left: 60, right: 12, top: 15, bottom: hasVolume ? 50 : 32 };
    const volumeHeight = hasVolume ? 34 : 0;
    const prices = candles.flatMap(item => [Number(item.h), Number(item.l)]).filter(Number.isFinite);
    let low = Math.min(...prices), high = Math.max(...prices);
    const spread = high - low || Math.max(1, Math.abs(high) * 0.01);
    low -= spread * 0.08; high += spread * 0.08;
    const plotWidth = width - pad.left - pad.right;
    const plotHeight = height - pad.top - pad.bottom;
    const step = plotWidth / candles.length;
    const x = index => pad.left + (index + 0.5) * step;
    const y = value => pad.top + (high - value) / (high - low) * plotHeight;
    const maxVolume = Math.max(1, ...candles.map(item => Number(item.v) || 0));
    let svg = `<svg viewBox="0 0 ${width} ${height}" role="img" aria-label="${safe(state.instrument.short_symbol)} intraday candles for ${safe(formatDay(state.selectedDate))}, ${safe(state.resolution)} minute interval."><defs><clipPath id="historyCandleClip"><rect x="${pad.left}" y="${pad.top}" width="${plotWidth}" height="${plotHeight}"/></clipPath></defs>`;
    for (let i = 0; i <= 3; i += 1) {
      const value = high - (high - low) * i / 3;
      const yy = y(value);
      svg += `<line x1="${pad.left}" x2="${width - pad.right}" y1="${yy}" y2="${yy}" class="history-grid-line"/><text x="${pad.left - 8}" y="${yy + 3}" class="history-axis-label" text-anchor="end">${money(value)}</text>`;
    }
    svg += '<g clip-path="url(#historyCandleClip)">';
    candles.forEach((candle, index) => {
      const color = candle.c >= candle.o ? 'var(--green)' : 'var(--red)';
      const bodyWidth = Math.max(2, Math.min(12, step * 0.56));
      const bodyTop = Math.min(y(candle.o), y(candle.c));
      const bodyHeight = Math.max(1.5, Math.abs(y(candle.o) - y(candle.c)));
      svg += `<g data-history-candle="${index}" tabindex="0" role="button" aria-label="${safe(formatTime(candle.t))} IST open ${safe(money(candle.o))}, high ${safe(money(candle.h))}, low ${safe(money(candle.l))}, close ${safe(money(candle.c))}${Number(candle.v) > 0 ? `, volume ${safe(Number(candle.v).toLocaleString('en-IN'))}` : ''}"><line x1="${x(index)}" x2="${x(index)}" y1="${y(candle.h)}" y2="${y(candle.l)}" stroke="${color}" stroke-width="1.2"/><rect x="${x(index) - bodyWidth / 2}" y="${bodyTop}" width="${bodyWidth}" height="${bodyHeight}" rx=".6" fill="${color}" opacity=".9"/></g>`;
    });
    svg += '</g>';
    if (hasVolume) {
      candles.forEach((candle, index) => {
        const value = Number(candle.v) || 0;
        const barHeight = value / maxVolume * volumeHeight;
        const color = candle.c >= candle.o ? 'var(--green)' : 'var(--red)';
        svg += `<rect x="${x(index) - Math.max(1, step * .25)}" y="${height - 24 - barHeight}" width="${Math.max(1, step * .5)}" height="${barHeight}" fill="${color}" opacity=".32"/>`;
      });
    }
    const tickIndices = [...new Set([0, Math.floor((candles.length - 1) / 2), candles.length - 1])];
    tickIndices.forEach(index => {
      svg += `<text x="${x(index)}" y="${height - 8}" class="history-axis-label" text-anchor="${index === 0 ? 'start' : index === candles.length - 1 ? 'end' : 'middle'}">${formatTime(candles[index].t)}</text>`;
    });
    svg += '</svg>';
    area.innerHTML = svg;
    $$('[data-history-candle]', area).forEach(candleNode => {
      const index = Number(candleNode.dataset.historyCandle);
      candleNode.addEventListener('pointerenter', () => updateCandleReadout(index));
      candleNode.addEventListener('focus', () => updateCandleReadout(index));
      candleNode.addEventListener('click', () => updateCandleReadout(index));
      candleNode.addEventListener('keydown', event => {
        if (event.key === 'Enter' || event.key === ' ') {
          event.preventDefault();
          updateCandleReadout(index);
        }
      });
    });
    $('#historyCandleFoot').textContent = `${candles.length} actual candles · ${state.resolution} min · FYERS historical${hasVolume ? ' · volume included' : ' · volume unavailable'}.`;
  }

  function drawCharts() {
    if (!state.open) return;
    drawLineChart();
    if (state.expanded) drawIntradayChart();
  }

  function bind() {
    panel.setAttribute('role', 'dialog');
    panel.setAttribute('tabindex', '-1');
    panel.addEventListener('keydown', event => {
      if (!state.open) return;
      if (event.key === 'Escape') {
        event.preventDefault();
        event.stopPropagation();
        setPanelOpen(false);
        return;
      }
      if (event.key !== 'Tab') return;
      const controls = $$('a[href], button, input, select, textarea, [tabindex]', panel)
        .filter(element => !element.disabled && element.type !== 'hidden'
          && element.getAttribute('tabindex') !== '-1'
          && !element.closest('[hidden], [aria-hidden="true"]')
          && (typeof element.getClientRects !== 'function' || element.getClientRects().length > 0));
      const first = controls[0];
      const last = controls[controls.length - 1];
      if (!first) {
        event.preventDefault();
        panel.focus({ preventScroll: true });
      } else if (event.shiftKey && (document.activeElement === first || document.activeElement === panel)) {
        event.preventDefault();
        last.focus({ preventScroll: true });
      } else if (!event.shiftKey && (document.activeElement === last || document.activeElement === panel)) {
        event.preventDefault();
        first.focus({ preventScroll: true });
      }
    });
    $('#historyClose').onclick = () => setPanelOpen(false);
    backdrop.onclick = () => setPanelOpen(false);
    $('#historyExpand').onclick = () => setExpanded(!state.expanded);
    $('#historySymbolSearch').addEventListener('input', event => {
      clearTimeout(state.searchTimer);
      state.searchTimer = setTimeout(() => searchSymbols(event.target.value), 220);
    });
    $('#historySymbolSearch').addEventListener('keydown', event => {
      if (event.key === 'Enter' && state.results[0]) {
        event.preventDefault();
        chooseInstrument(state.results[0]);
      }
    });
    $$('.history-range-row [data-history-range]').forEach(button => {
      button.onclick = () => {
        if (button.dataset.historyRange === 'CUSTOM') {
          state.range = 'CUSTOM';
          $('#historyCustomRange').hidden = false;
          $$('.history-range-row [data-history-range]').forEach(item => item.classList.toggle('active', item === button));
          return;
        }
        loadRange(button.dataset.historyRange);
      };
    });
    $('#historyApplyRange').onclick = () => loadRange('CUSTOM');
    $('#historyDay').addEventListener('change', event => selectTradingDay(event.target.value));
    $('#historyResolution').addEventListener('change', event => {
      state.resolution = event.target.value;
      if (!state.selectedDate) return;
      if (state.range === '1D') loadRange('1D');
      else if (state.expanded) loadIntraday(state.selectedDate);
      else $('#historyCandleReadout').textContent = `Expand the panel to load ${state.resolution}-minute candles.`;
    });
    resizeObserver = new ResizeObserver(() => requestAnimationFrame(drawCharts));
    resizeObserver.observe(panel);
  }

  async function open(symbol, name = '') {
    setPanelOpen(true);
    setExpanded(false);
    state.instrument = null;
    state.selectedDate = null;
    state.latestAvailableDate = null;
    state.range = '1M';
    state.resolution = $('#historyResolution').value || '5';
    state.dailyCandles = [];
    state.lineCandles = [];
    state.intradayCandles = [];
    state.selectedPoint = null;
    state.error = null;
    state.intradayMessage = null;
    state.intradayMessageKind = '';
    renderSummary(null);
    $('#historyDataSource').textContent = 'FYERS Historical API · not streaming; delay depends on your data permission';
    $('#historyResolution').value = state.resolution;
    $('#historySymbolSearch').value = name ? `${symbol} · ${name}` : symbol;
    $('#historyCustomRange').hidden = true;
    $$('.history-range-row [data-history-range]').forEach(button => button.classList.toggle('active', button.dataset.historyRange === '1M'));
    setStatus('Resolving the exact instrument in FYERS’s NSE symbol master…');
    showChartMessage($('#historyLineChart'), 'Resolving FYERS instrument…');
    showChartMessage($('#historyCandleChart'), 'Expand the panel to view intraday candles.');
    await searchSymbols(symbol, { autoSelect: true });
  }

  return { bind, open, close: () => setPanelOpen(false), resize: drawCharts };
}
