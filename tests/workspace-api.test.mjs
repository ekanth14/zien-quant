import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import test from 'node:test';

const source = await readFile(new URL('../marketscope/static/workspace-api.js', import.meta.url), 'utf8');
const { normalizeCandles, normalizeBootstrap, normalizeMarket, normalizeStock, normalizeAI, timestampSeconds, toBackendConfig, createWorkspaceAPI } = await import(`data:text/javascript;base64,${Buffer.from(source).toString('base64')}`);

const stock = {
  symbol: 'TCS', name: 'Tata Consultancy Services', sector: 'Technology', status: 'ok',
  price: 4100, change_pct: 1.25, open_change_pct: 0.5, volume: 8000, updated_at: '2026-10-23T10:00:00Z',
  vwap: 4080, rvol: 1.5, support: 4050, resistance: 4150,
  metrics: { score: 75, signal: 'BUY', components: { trend: 25 }, raw: { rsi: 61.2, momentum_pct: 0.5 } },
  confirmed: { score: 72, raw: { rsi: 60 } },
  order_blocks: [{ type: 'bullish', low: 4060, high: 4070, time: 1792746900 }],
  ai_decision: { action: 'BREAKOUT LONG', grade: 'A', entry_low: 4090, entry_high: 4100, stop_loss: 4050, target_1: 4200, target_2: 4250, risk_reward_ratio: 2, conviction_pct: 80, rationale: 'Provided structure' },
};

test('bootstrap maps the actual session and configuration contract', () => {
  const b = normalizeBootstrap({ has_owner: true, config: { brand_name: 'Zien', default_timeframe: 15, score_threshold: 70, monitored_symbols: ['TCS'] }, session: { authenticated: true, email: 'owner@example.com', role: 'owner', csrf_token: 'token' }, catalogue: { TCS: { name: 'TCS' } } });
  assert.equal(b.config.brand, 'Zien');
  assert.equal(b.config.timeframe, 15);
  assert.equal(b.config.rank_threshold, 70);
  assert.deepEqual(b.config.universe, ['TCS']);
  assert.equal(b.user.role, 'owner');
  assert.equal(b.csrf, 'token');
  assert.equal(b.owner_exists, true);
});

test('missing price, metrics and OHLC fields remain absent rather than becoming zeroes', () => {
  const s = normalizeStock({ symbol: 'TCS', status: 'failed', price: null, metrics: { score: null } });
  assert.equal(s.price, null);
  assert.equal(s.metrics, null);
  assert.equal(s.updated_at, null);
  assert.equal(s.volume, null);
  assert.deepEqual(s.candles, []);
  assert.deepEqual(normalizeCandles([{ t: 100, c: 12 }]), []);
});

test('market maps supplied trajectories and excludes failed or stale symbols from rankings', () => {
  const result = normalizeMarket({ data_mode: 'LIVE', session_state: 'LIVE', feed: { failed_count: 1 }, stocks: { TCS: stock, BAD: { symbol: 'BAD', status: 'failed', price: null }, OLD: { ...stock, symbol: 'OLD', status: 'stale' } }, trajectories: [{ symbol: 'TCS', points: [0, 0.25, null, 0.5] }] });
  assert.equal(result.stocks.length, 1);
  assert.equal(result.unavailable_stocks.length, 2);
  assert.equal(result.status, 'degraded');
  assert.deepEqual(result.stocks[0].trajectory, [0, 0.25, null, 0.5]);
  assert.deepEqual(result.stocks[0].candles, []);
  assert.equal(result.stocks[0].metrics.rsi, 61.2);
  assert.equal(result.stocks[0].metrics.zones[0].direction, 'bullish');
  assert.equal(result.stocks[0].setup.entry, 4090);
  assert.equal(result.stocks[0].setup.target1, 4200);
  assert.equal(result.stocks[0].setup.eligible, false);
  assert.equal(result.stocks[0].confirmed_setup, null);
});

test('historical and incomplete session metadata survives normalization', () => {
  const result = normalizeMarket({ data_mode: 'PREVIOUS_SESSION', session_state: 'PREVIOUS', session_date: '2026-10-23', complete: 0, stocks: { TCS: stock } }, { mode: 'previous' });
  assert.equal(result.mode, 'previous');
  assert.equal(result.status, 'historical');
  assert.equal(result.session_date, '2026-10-23');
  assert.equal(result.stocks[0].historical, true);
  assert.equal(result.stocks[0].source, 'archive');
  assert.match(result.message, /Incomplete/);
});

test('candles retain real timestamps, boundaries and volume without filling gaps', () => {
  const candles = normalizeCandles([
    { t: 1792747200000, o: 21, h: 23, l: 20, c: 22, v: 44, closed: false },
    { t: 1792746900, o: 20, h: 22, l: 19, c: 21, v: 33, closed: true },
    { t: 1792747500, o: 21, h: 20, l: 19, c: 22, v: 1 },
  ]);
  assert.equal(candles.length, 2);
  assert.equal(candles[0].t, 1792746900);
  assert.equal(candles[1].t, 1792747200);
  assert.equal(candles[0].v, 33);
  assert.equal(candles[1].closed, false);
  assert.equal(timestampSeconds('2026-10-23 15:25:00 IST'), Date.parse('2026-10-23T15:25:00+05:30') / 1000);
});

test('AI prose uses the actual briefing and source fields', () => {
  assert.deepEqual(normalizeAI({ institutional_briefing: 'Provided briefing', source: 'local_llm' }), { institutional_briefing: 'Provided briefing', source: 'local_llm', summary: 'Provided briefing', text: 'Provided briefing', backend: 'ollama' });
  assert.equal(normalizeAI({ source: 'rules_engine', ai_pending: true }).backend, 'rules');
});

test('requests map login, protected configuration and previous-session parameters', async () => {
  const requests = [];
  const api = createWorkspaceAPI(() => ({ mode: 'live', csrf: 'csrf-token' }), { fetchImpl: async (path, options) => { requests.push({ path, options }); return { ok: true, json: async () => path.startsWith('/api/market') ? { data_mode: 'PREVIOUS_SESSION', stocks: {} } : { success: true } }; } });
  await api('/api/login', { method: 'POST', body: { email: 'owner@example.com', password: 'test-password', code: '123456' } });
  assert.deepEqual(JSON.parse(requests[0].options.body), { email: 'owner@example.com', password: 'test-password', totp_code: '123456' });
  assert.equal(requests[0].options.credentials, 'same-origin');
  assert.equal(requests[0].options.headers['X-CSRF-Token'], 'csrf-token');
  await api('/api/market?mode=previous&source=public&timeframe=15');
  assert.equal(requests[1].path, '/api/market?timeframe=15&session=previous');
  await api('/api/config', { method: 'POST', body: { rank_threshold: 70, ema_fast: 8 } });
  assert.deepEqual(JSON.parse(requests[2].options.body), { score_threshold: 70, fast_ema_period: 8 });
});

test('unsupported capabilities and backend errors cannot be mistaken for success', async () => {
  let requests = 0;
  const api = createWorkspaceAPI(() => ({ mode: 'live' }), { fetchImpl: async () => { requests++; return { ok: false, status: 503, json: async () => ({ error: 'feed_unavailable', detail: 'No real session data' }) }; } });
  await assert.rejects(api('/api/users'), /not available/);
  await assert.rejects(api('/api/fyers/status'), /not available/);
  await assert.rejects(api('/api/market?source=compare'), /unavailable/);
  await assert.rejects(api('/api/market?mode=demo'), /current feed/);
  await assert.rejects(api('/api/ai/pulse?mode=previous'), /cannot select a session date/);
  assert.equal(requests, 0);
  // The installed after-hours extension supports a previous-session analysis.
  // Its real backend error must survive; it is not an unsupported capability.
  await assert.rejects(api('/api/ai/analyze?mode=previous&symbol=TCS'), error => error.status === 503 && error.code === 'feed_unavailable');
  await assert.rejects(api('/api/market'), error => error.status === 503 && error.code === 'feed_unavailable' && error.message === 'No real session data');
  assert.throws(() => toBackendConfig({ formula_version: 'v2' }), /does not support/);
  assert.throws(() => toBackendConfig({ accent: '#ffffff' }), /does not support/);
});
