/**
 * Connects the reference workspace to the existing MarketScope HTTP service.
 * Numeric market fields are retained only when supplied by the service.
 */

const finite = value => {
  if (value === null || value === undefined || value === '' || typeof value === 'boolean') return null;
  const number = Number(value);
  return Number.isFinite(number) ? number : null;
};

export function timestampSeconds(value) {
  if (value === null || value === undefined || value === '') return null;
  const numeric = finite(value);
  if (numeric !== null) return numeric > 1e12 ? numeric / 1000 : numeric;
  if (typeof value !== 'string') return null;
  const iso = value.trim().replace(/^(\d{4}-\d{2}-\d{2}) /, '$1T').replace(/ IST$/, '+05:30');
  const parsed = Date.parse(iso);
  return Number.isFinite(parsed) ? parsed / 1000 : null;
}

export function normalizeCandles(candles) {
  if (!Array.isArray(candles)) return [];
  const byTime = new Map();
  for (const original of candles) {
    if (!original || typeof original !== 'object') continue;
    const candle = {
      t: timestampSeconds(original.t ?? original.ts ?? original.timestamp ?? original.time),
      o: finite(original.o ?? original.open),
      h: finite(original.h ?? original.high),
      l: finite(original.l ?? original.low),
      c: finite(original.c ?? original.close),
      v: finite(original.v ?? original.volume),
      closed: original.closed === true,
    };
    if (candle.t === null || candle.t <= 0 || [candle.o, candle.h, candle.l, candle.c].some(v => v === null || v <= 0)) continue;
    if (candle.h < Math.max(candle.o, candle.l, candle.c) || candle.l > Math.min(candle.o, candle.h, candle.c)) continue;
    if (candle.v !== null && candle.v < 0) candle.v = null;
    byTime.set(candle.t, candle);
  }
  return [...byTime.values()].sort((a, b) => a.t - b.t);
}

function normalizeZones(zones) {
  if (!Array.isArray(zones)) return [];
  return zones.map(zone => ({
    ...zone,
    low: finite(zone.low),
    high: finite(zone.high),
    t: timestampSeconds(zone.t ?? zone.time),
    direction: zone.direction ?? zone.type,
  })).filter(zone => zone.low !== null && zone.high !== null && zone.low > 0 && zone.high >= zone.low && ['bullish', 'bearish'].includes(zone.direction));
}

function normalizeMetrics(metrics, stock) {
  if (!metrics || finite(metrics.score) === null) return null;
  const raw = metrics.raw || {};
  return {
    ...metrics,
    score: finite(metrics.score),
    rsi: finite(metrics.rsi ?? raw.rsi),
    vwap: finite(metrics.vwap ?? stock.vwap),
    rvol: finite(metrics.rvol ?? stock.rvol),
    ema_fast: finite(metrics.ema_fast ?? raw.fast_ema),
    ema_slow: finite(metrics.ema_slow ?? raw.slow_ema),
    momentum: finite(metrics.momentum ?? raw.momentum_pct),
    trend: finite(metrics.trend ?? raw.trend_pct),
    vwap_distance: finite(metrics.vwap_distance ?? raw.vwap_dist_pct),
    atr: finite(metrics.atr ?? raw.atr),
    support: finite(metrics.support ?? stock.support),
    resistance: finite(metrics.resistance ?? stock.resistance),
    zones: normalizeZones(metrics.zones ?? stock.order_blocks),
    components: { ...(metrics.components || {}) },
    raw: { ...raw },
  };
}

function normalizeSetup(decision) {
  if (!decision || typeof decision !== 'object') return null;
  const action = String(decision.action ?? decision.type ?? 'unavailable');
  return {
    ...decision,
    type: action.toLowerCase().replace(/[^a-z0-9]+/g, '_').replace(/^_|_$/g, ''),
    action,
    direction: decision.direction ?? (decision.signal_type === 'BUY' ? 'long' : decision.signal_type === 'SELL' ? 'short' : 'neutral'),
    entry: finite(decision.entry ?? decision.entry_price ?? decision.entry_low),
    entry_low: finite(decision.entry_low),
    entry_high: finite(decision.entry_high),
    stop_loss: finite(decision.stop_loss ?? decision.stop),
    target1: finite(decision.target1 ?? decision.target_1),
    target2: finite(decision.target2 ?? decision.target_2),
    risk_reward: finite(decision.risk_reward ?? decision.risk_reward_ratio),
    conviction: finite(decision.conviction ?? decision.conviction_pct),
    reason: decision.reason ?? decision.rationale ?? null,
    // Public research data has no execution-eligibility attestation.
    eligible: false,
    factors: Array.isArray(decision.factors) ? [...decision.factors] : [],
  };
}

export function normalizeStock(stock = {}, context = {}) {
  const sessionState = context.session_state ?? stock.session_state ?? null;
  const historical = ['PREVIOUS', 'CLOSED_TODAY'].includes(sessionState) || ['PREVIOUS_SESSION', 'CLOSED_SESSION', 'INCOMPLETE_SESSION'].includes(context.data_mode ?? stock.data_mode);
  const candles = normalizeCandles(stock.candles ?? stock.candles_preview);
  const suppliedPoints = context.trajectory?.points ?? stock.trajectory ?? [];
  return {
    ...stock,
    symbol: String(stock.symbol ?? context.symbol ?? ''),
    name: stock.name ?? stock.company_name ?? stock.symbol ?? context.symbol ?? '',
    sector: stock.sector ?? 'Unclassified',
    status: stock.status ?? 'unavailable',
    price: finite(stock.price),
    change: finite(stock.change ?? stock.change_pct),
    open_change: finite(stock.open_change ?? stock.open_change_pct),
    volume: finite(stock.volume),
    previous_close: finite(stock.previous_close),
    session_open: finite(stock.session_open),
    updated_at: timestampSeconds(stock.quote_timestamp ?? stock.updated_at),
    candles,
    trajectory: Array.isArray(suppliedPoints) ? suppliedPoints.map(finite) : [],
    metrics: normalizeMetrics(stock.metrics, stock),
    confirmed: normalizeMetrics(stock.confirmed, stock),
    setup: normalizeSetup(stock.setup ?? stock.ai_decision),
    confirmed_setup: normalizeSetup(stock.confirmed_setup),
    confirmation_independent: false,
    source: historical ? 'archive' : stock.source ?? 'public',
    source_label: context.data_source ?? stock.source ?? (historical ? 'Real session candle archive' : 'Yahoo Finance exchange candles'),
    delayed: true,
    historical,
    stale: stock.status === 'stale' || stock.stale === true,
    gap: stock.gap === true,
    signal_eligible: false,
    session_state: sessionState,
    session_date: context.session_date ?? stock.session_date ?? null,
    comparison: { reason: stock.source === 'FYERS' ? 'Authenticated FYERS REST polling; quote age is checked server-side.' : 'Provider candles and stored session archive; independent source comparison unavailable.' },
  };
}

export function normalizeConfig(config = {}, catalogue = {}) {
  return {
    ...config,
    brand: config.brand_name ?? config.brand ?? 'MarketScope',
    tagline: config.tagline ?? 'NSE Flow Terminal & Directional Intelligence',
    accent: '#4ed5b2',
    announcement: '',
    rank_threshold: finite(config.score_threshold ?? config.rank_threshold) ?? 65,
    ema_fast: finite(config.fast_ema_period ?? config.ema_fast) ?? 9,
    ema_slow: finite(config.slow_ema_period ?? config.ema_slow) ?? 21,
    rsi_period: finite(config.rsi_period) ?? 14,
    timeframe: finite(config.default_timeframe ?? config.timeframe) ?? 5,
    refresh_ms: Math.max(3000, finite(config.refresh_interval_ms ?? config.refresh_ms) ?? 3000),
    universe: Array.isArray(config.monitored_symbols) ? [...config.monitored_symbols] : Object.keys(catalogue),
    features: { flow: true, sectors: true },
    navigation: ['overview', 'scanner', 'watchlist', 'paperwatch', 'paper-report', 'paper', 'research', 'methodology', 'admin'],
    formula_version: 'legacy',
    score_weights: {
      trend: finite(config.weight_trend) ?? 35,
      momentum: finite(config.weight_momentum) ?? 30,
      vwap: finite(config.weight_vwap) ?? 20,
      rsi: finite(config.weight_rsi) ?? 15,
    },
    holidays: Array.isArray(config.holidays) ? [...config.holidays] : [],
    plans: [],
    capabilities: { source_switching: false, formula_v2: false, user_management: false, appearance_settings: false, independent_confirmation: false },
  };
}

export function normalizeBootstrap(payload = {}) {
  const session = payload.session || {};
  return {
    ...payload,
    config: normalizeConfig(payload.config, payload.catalogue),
    user: session.authenticated ? { email: session.email, role: session.role, plan: session.plan ?? null } : null,
    csrf: session.csrf_token ?? null,
    owner_exists: payload.has_owner === true,
    schedule: payload.market_schedule ?? null,
    catalogue: { stocks: Object.entries(payload.catalogue || {}).map(([symbol, info]) => ({ symbol, ...info })), count: Object.keys(payload.catalogue || {}).length },
  };
}

export function normalizeMarket(payload = {}, { mode = 'live', timeframe = 5 } = {}) {
  const trajectories = new Map((payload.trajectories || []).map(item => [item.symbol, item]));
  const entries = Array.isArray(payload.stocks) ? payload.stocks.map(stock => [stock.symbol, stock]) : Object.entries(payload.stocks || {});
  const allStocks = entries.map(([symbol, stock]) => normalizeStock(stock, { ...payload, symbol, trajectory: trajectories.get(symbol) }));
  const available = allStocks.filter(stock => stock.status === 'ok' && stock.price !== null && stock.price > 0);
  const historical = ['PREVIOUS', 'CLOSED_TODAY'].includes(payload.session_state) || payload.data_mode !== 'LIVE';
  const status = !available.length ? 'unavailable' : historical ? 'historical' : payload.feed?.failed_count || payload.feed?.stale_count ? 'degraded' : 'connected';
  const message = !available.length ? 'No real market candles are available for this session.' : historical ? `${payload.status_label ?? 'Stored session'} · ${payload.session_date ?? ''}${payload.complete === 0 ? ' · Incomplete history' : ''}` : status === 'degraded' ? 'Public candle feed · some symbols unavailable' : 'Public exchange candles · as timestamped';
  return {
    ...payload,
    mode,
    timeframe: finite(payload.timeframe) ?? timeframe,
    status,
    historical,
    message,
    stocks: available,
    unavailable_stocks: allStocks.filter(stock => !available.includes(stock)),
    received_at: timestampSeconds(payload.timestamp),
    as_of: payload.as_of ?? payload.timestamp ?? null,
    schedule: payload.market_schedule ?? null,
    exchange_ms: null,
    sources: { public: { status, provider: payload.provider ?? payload.data_source ?? 'Exchange candles', message, timestamp: timestampSeconds(payload.as_of ?? payload.timestamp) } },
  };
}

export function normalizeAI(payload = {}) {
  return {
    ...payload,
    backend: payload.source === 'local_llm' ? 'ollama' : 'rules',
    summary: payload.institutional_briefing ?? payload.summary ?? payload.reply ?? null,
    text: payload.institutional_briefing ?? payload.summary ?? payload.reply ?? null,
  };
}

export function toBackendConfig(patch = {}) {
  const aliases = { brand: 'brand_name', rank_threshold: 'score_threshold', ema_fast: 'fast_ema_period', ema_slow: 'slow_ema_period', refresh_ms: 'refresh_interval_ms', timeframe: 'default_timeframe', universe: 'monitored_symbols' };
  const supported = new Set(['brand_name', 'tagline', 'score_threshold', 'fast_ema_period', 'slow_ema_period', 'rsi_period', 'refresh_interval_ms', 'default_timeframe', 'formula_approved', 'monitored_symbols', 'weight_trend', 'weight_momentum', 'weight_vwap', 'weight_rsi', 'min_rr_hurdle', 'flow_movers_count', 'flow_rvol_min', 'flow_score_threshold', 'flow_rr_min']);
  const mapped = {};
  for (const [key, value] of Object.entries(patch)) {
    if (key === 'formula_version' && value === 'legacy') continue;
    const target = aliases[key] ?? key;
    if (!supported.has(target)) throw new Error(`The current server does not support changing ${key.replaceAll('_', ' ')} from this workspace.`);
    mapped[target] = value;
  }
  return mapped;
}

const SUPPORTED_ROUTES = new Set(['/api/research/strategies', '/api/paper-watchlist', '/api/paper-watchlist/report', '/api/paper/status', '/api/paper/control', '/api/bootstrap', '/api/market', '/api/stock', '/api/historical/symbols', '/api/historical/candles', '/api/health', '/api/admin', '/api/admin/audit-logs', '/api/config/history', '/api/config/preview', '/api/session/preview', '/api/config', '/api/backup/status', '/api/backup', '/api/ai/status', '/api/ai/pulse', '/api/ai/analyze', '/api/ai/chat', '/api/stocks/database', '/api/stocks/quote', '/api/stocks/track', '/api/stocks/untrack', '/api/market/schedule', '/api/signals/stats', '/api/session/summary', '/api/flow', '/api/login', '/api/logout', '/api/metrics']);

export function createWorkspaceAPI(getState = () => ({}), { fetchImpl = globalThis.fetch, onUnauthorized = () => {} } = {}) {
  return async function request(path, { method = 'GET', body, signal } = {}) {
    const state = getState() || {};
    const url = new URL(path, 'http://workspace.local');
    if (!String(path).startsWith('/') || String(path).startsWith('//') || !SUPPORTED_ROUTES.has(url.pathname)) {
      throw new Error('This capability is not available in the current MarketScope server.');
    }
    const requestedMode = url.searchParams.get('mode') ?? state.mode ?? 'live';
    const requestedSource = url.searchParams.get('source') ?? 'public';
    if (!['live', 'auto', 'previous'].includes(requestedMode)) throw new Error('Choose the current feed or a stored previous session.');
    if (!['public', 'auto'].includes(requestedSource)) throw new Error('This server provides public exchange candles and stored sessions. Broker routes and source comparison are unavailable.');
    if (requestedMode === 'previous' && url.pathname === '/api/ai/pulse') {
      throw new Error('Historical AI requests cannot select a session date on this server. Return to the automatic session to request a briefing.');
    }
    if (url.pathname !== '/api/ai/analyze') url.searchParams.delete('mode');
    url.searchParams.delete('source');
    if (url.pathname === '/api/market' && requestedMode === 'previous') url.searchParams.set('session', 'previous');
    let outgoing = body;
    if (url.pathname === '/api/login' && body) outgoing = { email: body.email, password: body.password, totp_code: body.totp_code ?? body.code ?? '' };
    if (url.pathname === '/api/config' || url.pathname === '/api/config/preview' || url.pathname === '/api/session/preview') outgoing = toBackendConfig(body);
    if (url.pathname === '/api/ai/chat' && body) outgoing = {
      message: body.message,
      symbol: body.symbol ?? null,
      timeframe: body.timeframe,
      mode: body.mode ?? requestedMode,
      history: body.history,
      watchlist: body.watchlist,
    };
    const headers = { Accept: 'application/json' };
    if (outgoing !== undefined) headers['Content-Type'] = 'application/json';
    if (state.csrf || state.csrfToken) headers['X-CSRF-Token'] = state.csrf ?? state.csrfToken;
    const response = await fetchImpl(`${url.pathname}${url.search}`, { method, credentials: 'same-origin', cache: 'no-store', headers, ...(outgoing !== undefined ? { body: JSON.stringify(outgoing) } : {}), ...(signal ? { signal } : {}) });
    let data;
    try { data = await response.json(); } catch { throw new Error('The server returned an unreadable response.'); }
    if (!response.ok) {
      if (response.status === 401 && url.pathname !== '/api/login') onUnauthorized();
      const error = new Error(data.detail ?? data.error ?? `Request failed (${response.status})`);
      error.status = response.status;
      error.code = data.error;
      error.retry_after = data.retry_after ?? null;
      error.requires_mfa = data.requires_mfa === true;
      throw error;
    }
    switch (url.pathname) {
      case '/api/bootstrap': return normalizeBootstrap(data);
      case '/api/market': return normalizeMarket(data, { mode: requestedMode, timeframe: finite(url.searchParams.get('timeframe')) ?? state.timeframe ?? 5 });
      case '/api/stock': return { ...data, candles: normalizeCandles(data.candles) };
      case '/api/ai/analyze':
      case '/api/ai/pulse':
      case '/api/ai/chat': return normalizeAI(data);
      case '/api/stocks/database': return { ...data, total: data.total_database, count: data.total_database, matches: data.total_matches, source: 'NSE & BSE stock master' };
      case '/api/config': return { ...data, config: normalizeConfig(data.config) };
      case '/api/config/history': return { ...data, versions: (data.history || []).map(item => ({ ...item, config: (() => { try { return JSON.parse(item.config_json); } catch { return null; } })() })) };
      case '/api/admin': return { ...data, sessions: [], sessions_available: false, health: { requests: null, errors: null, latency: { p50_ms: null, p95_ms: null } } };
      case '/api/backup/status': return { ...data, latest: data.backups?.[0] ? { ...data.backups[0], created_at: timestampSeconds(data.backups[0].modified) } : null };
      default: return data;
    }
  };
}
