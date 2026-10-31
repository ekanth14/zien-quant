/**
 * MarketScope · NSE Flow Terminal Client Application
 * Handles session polling, trajectory SVG canvas rendering,
 * hover HUD situation panels, SVG candlestick charting engine,
 * scanner sorting/filtering, and owner configuration.
 */

class PublicFeedAdapter {
  constructor() {
    this.hasBackend = null;
  }

  async checkBackend() {
    if (this.hasBackend !== null) return this.hasBackend;
    try {
      const res = await fetch('/api/bootstrap', { method: 'GET', cache: 'no-cache' });
      if (res.ok) {
        this.hasBackend = true;
        return true;
      }
    } catch {
      // Backend not reached
    }
    this.hasBackend = false;
    return false;
  }

  async getBootstrap() {
    try {
      const res = await fetch('/api/bootstrap');
      if (res.ok) return await res.json();
    } catch (err) {
      console.error('Failed to fetch bootstrap:', err);
    }
    return {
      config: {
        brand_name: 'MarketScope',
        tagline: 'NSE Flow Terminal & Directional Intelligence',
        default_timeframe: 5,
        refresh_interval_ms: 2000,
      },
      has_owner: false,
      catalogue: {},
      session: { authenticated: false, email: null, role: null, csrf_token: null },
      provider_status: {
        live_available: false,
        reason: 'Backend service unreachable',
      },
    };
  }

  async getMarketSnapshot(mode = 'live', timeframe = 5, session = null) {
    try {
      let url = `/api/market?timeframe=${encodeURIComponent(timeframe)}`;
      if (session) {
        url += `&session=${encodeURIComponent(session)}`;
      }
      const res = await fetch(url);
      if (res.ok) return await res.json();
      if (res.status === 503) {
        const errData = await res.json().catch(() => ({}));
        return { error: 'feed_unavailable', status: 503, detail: errData.detail || 'Live feed unavailable' };
      }
      return { error: 'http_error', status: res.status };
    } catch (err) {
      return { error: 'feed_unavailable', status: 503, detail: err.message };
    }
  }

  async getStockCandles(symbol, timeframe = 5) {
    try {
      const res = await fetch(`/api/stock?symbol=${encodeURIComponent(symbol)}&timeframe=${encodeURIComponent(timeframe)}`);
      if (res.ok) return await res.json();
      return { error: 'candles_unavailable', status: res.status, candles: [] };
    } catch (err) {
      return { error: 'network_error', detail: err.message, candles: [] };
    }
  }

  async getSessionSummary(dateStr = null) {
    try {
      let url = '/api/session/summary';
      if (dateStr) {
        url += `?date=${encodeURIComponent(dateStr)}`;
      }
      const res = await fetch(url);
      if (res.ok) return await res.json();
      const err = await res.json().catch(() => ({}));
      return { error: err.error || 'session_not_found', detail: err.detail || 'Session summary unavailable', status: res.status };
    } catch (err) {
      return { error: 'network_error', detail: err.message, status: 503 };
    }
  }

  async getAIPulse(refresh = false) {
    try {
      const url = `/api/ai/pulse${refresh ? '?refresh=1' : ''}`;
      const res = await fetch(url);
      if (res.ok) return await res.json();
      return { error: 'pulse_unavailable', status: res.status };
    } catch (err) {
      return { error: 'network_error', detail: err.message };
    }
  }

  async getAIAnalyze(symbol, timeframe = 5, refresh = false, poll = false) {
    try {
      const params = new URLSearchParams({
        symbol: symbol,
        timeframe: timeframe.toString(),
      });
      if (refresh) params.append('refresh', '1');
      if (poll) params.append('poll', '1');

      const res = await fetch(`/api/ai/analyze?${params.toString()}`);
      if (res.ok) return await res.json();
      return { error: 'analysis_unavailable', status: res.status };
    } catch (err) {
      return { error: 'network_error', detail: err.message };
    }
  }

  async sendAIChat(message, symbol = null) {
    try {
      const res = await fetch('/api/ai/chat', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ message, symbol }),
      });
      return await res.json();
    } catch (err) {
      return { error: 'chat_network_error', detail: err.message };
    }
  }

  async searchStockDatabase(query, exchange = 'ALL', limit = 12) {
    try {
      const res = await fetch(`/api/stocks/database?q=${encodeURIComponent(query)}&exchange=${encodeURIComponent(exchange)}&limit=${limit}`);
      if (res.ok) return await res.json();
      return { stocks: [], total_matches: 0 };
    } catch (err) {
      return { stocks: [], total_matches: 0, error: err.message };
    }
  }

  async trackStock(symbol) {
    try {
      const res = await fetch('/api/stocks/track', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ symbol }),
      });
      return await res.json();
    } catch (err) {
      return { error: err.message };
    }
  }

  async untrackStock(symbol) {
    try {
      const res = await fetch('/api/stocks/untrack', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ symbol }),
      });
      return await res.json();
    } catch (err) {
      return { error: err.message };
    }
  }
}

const publicAPI = new PublicFeedAdapter();

// Application State
const state = {
  timeframe: 5,
  signalMode: 'provisional', // 'provisional' | 'confirmed'
  isPaused: false,
  motionEnabled: true,
  theme: localStorage.getItem('marketscope_theme') || 'dark',
  watchlist: new Set(JSON.parse(localStorage.getItem('marketscope_watchlist') || '["RELIANCE","TCS","HDFCBANK"]')),
  scannerFilter: 'all',
  scannerSearch: '',
  scannerSort: { col: 'score', desc: true },
  hoveredStock: null,
  selectedStock: null,
  latestSnapshot: null,
  activeCandles: null,
  pollTimer: null,
  animationFrameId: null,
  flowParticles: [],
  zoomLevel: 1.0,
  csrfToken: null,
  user: null,
  sessionState: 'LIVE',
  sessionDate: null,
  dataMode: 'LIVE',
  viewingPrevious: false,
  scorecardOutcomes: {},
};

// DOM Elements
const el = {
  feedPill: document.getElementById('feedPill'),
  feedModeLabel: document.getElementById('feedModeLabel'),
  clockDisplay: document.getElementById('clockDisplay'),
  btnPause: document.getElementById('btnPause'),
  pauseIconPath: document.getElementById('pauseIconPath'),
  btnMotion: document.getElementById('btnMotion'),
  btnTheme: document.getElementById('btnTheme'),
  btnExport: document.getElementById('btnExport'),
  btnMethodology: document.getElementById('btnMethodology'),
  btnSettings: document.getElementById('btnSettings'),
  settingsBtnLabel: document.getElementById('settingsBtnLabel'),
  btnTogglePrevious: document.getElementById('btnTogglePrevious'),
  btnTogglePreviousText: document.getElementById('btnTogglePreviousText'),
  sessionRecapPanel: document.getElementById('sessionRecapPanel'),
  recapTitle: document.getElementById('recapTitle'),
  recapSessionDate: document.getElementById('recapSessionDate'),
  recapCompleteness: document.getElementById('recapCompleteness'),
  recapSource: document.getElementById('recapSource'),
  recapAsOf: document.getElementById('recapAsOf'),
  recapTotalSignals: document.getElementById('recapTotalSignals'),
  recapWinRate: document.getElementById('recapWinRate'),
  recapWins: document.getElementById('recapWins'),
  recapLosses: document.getElementById('recapLosses'),
  recapExpired: document.getElementById('recapExpired'),
  recapAvgR: document.getElementById('recapAvgR'),
  recapBestWorst: document.getElementById('recapBestWorst'),
  recapNote: document.getElementById('recapNote'),
  btnModeProvisional: document.getElementById('btnModeProvisional'),
  btnModeConfirmed: document.getElementById('btnModeConfirmed'),

  // Breadth summary
  cntTotal: document.getElementById('cntTotal'),
  cntBuy: document.getElementById('cntBuy'),
  cntSell: document.getElementById('cntSell'),
  cntNeutral: document.getElementById('cntNeutral'),
  breadthBull: document.getElementById('breadthBull'),
  breadthBear: document.getElementById('breadthBear'),

  // Trajectories SVG
  canvasWrapper: document.getElementById('canvasWrapper'),
  trajectoriesSvg: document.getElementById('trajectoriesSvg'),
  svgGrid: document.getElementById('svgGrid'),
  svgZeroAxis: document.getElementById('svgZeroAxis'),
  svgOrderBlockRays: document.getElementById('svgOrderBlockRays'),
  svgTrajectories: document.getElementById('svgTrajectories'),
  svgParticles: document.getElementById('svgParticles'),
  svgGate: document.getElementById('svgGate'),
  svgEndpoints: document.getElementById('svgEndpoints'),
  svgCrosshair: document.getElementById('svgCrosshair'),
  histBarsBear: document.getElementById('histBarsBear'),
  histBarsBull: document.getElementById('histBarsBull'),

  // Floating HUD
  hudPanel: document.getElementById('hudPanel'),
  hudSym: document.getElementById('hudSym'),
  hudName: document.getElementById('hudName'),
  hudBadge: document.getElementById('hudBadge'),
  hudPrice: document.getElementById('hudPrice'),
  hudChg: document.getElementById('hudChg'),
  hudSparkline: document.getElementById('hudSparkline'),
  hudScore: document.getElementById('hudScore'),
  hudRsi: document.getElementById('hudRsi'),
  hudRvol: document.getElementById('hudRvol'),
  hudVwap: document.getElementById('hudVwap'),
  hudOi: document.getElementById('hudOi'),
  hudDelta: document.getElementById('hudDelta'),
  hudTime: document.getElementById('hudTime'),
  hudAiAction: document.getElementById('hudAiAction'),
  hudAiSl: document.getElementById('hudAiSl'),
  hudAiT1: document.getElementById('hudAiT1'),
  hudAiRr: document.getElementById('hudAiRr'),

  // Signal tiles
  buyTilesContainer: document.getElementById('buyTilesContainer'),
  sellTilesContainer: document.getElementById('sellTilesContainer'),

  // Spotlight & Sectors
  spotlightBuyContainer: document.getElementById('spotlightBuyContainer'),
  spotlightSellContainer: document.getElementById('spotlightSellContainer'),
  sectorChipsContainer: document.getElementById('sectorChipsContainer'),

  // Scanner Table
  searchInput: document.getElementById('searchInput'),
  searchDropdown: document.getElementById('searchDropdown'),
  marketTableBody: document.getElementById('marketTableBody'),

  // Modals
  chartModal: document.getElementById('chartModal'),
  btnCloseChartModal: document.getElementById('btnCloseChartModal'),
  modalStockTitle: document.getElementById('modalStockTitle'),
  modalSector: document.getElementById('modalSector'),
  modalSignalBadge: document.getElementById('modalSignalBadge'),
  candlestickSvg: document.getElementById('candlestickSvg'),
  candleTooltip: document.getElementById('candleTooltip'),
  chartGrid: document.getElementById('chartGrid'),
  chartZones: document.getElementById('chartZones'),
  chartAiLevels: document.getElementById('chartAiLevels'),
  chartCandles: document.getElementById('chartCandles'),
  chartIndicators: document.getElementById('chartIndicators'),
  chartVolume: document.getElementById('chartVolume'),
  chartCrosshair: document.getElementById('chartCrosshair'),

  // Diagnostics
  diagScore: document.getElementById('diagScore'),
  diagSignal: document.getElementById('diagSignal'),
  diagTrend: document.getElementById('diagTrend'),
  diagMom: document.getElementById('diagMom'),
  diagVwap: document.getElementById('diagVwap'),
  diagRsi: document.getElementById('diagRsi'),
  diagRes: document.getElementById('diagRes'),
  diagVwapLevel: document.getElementById('diagVwapLevel'),
  diagSup: document.getElementById('diagSup'),
  diagRvol: document.getElementById('diagRvol'),
  diagZonesList: document.getElementById('diagZonesList'),

  // AI Decision Copilot
  diagAiConviction: document.getElementById('diagAiConviction'),
  diagAiAction: document.getElementById('diagAiAction'),
  diagAiEntry: document.getElementById('diagAiEntry'),
  diagAiSl: document.getElementById('diagAiSl'),
  diagAiT1: document.getElementById('diagAiT1'),
  diagAiT2: document.getElementById('diagAiT2'),
  diagAiRr: document.getElementById('diagAiRr'),
  diagAiRiskPts: document.getElementById('diagAiRiskPts'),
  diagAiRationale: document.getElementById('diagAiRationale'),
  diagAiFactors: document.getElementById('diagAiFactors'),

  // Overlays & Zoom
  chkAiLevels: document.getElementById('chkAiLevels'),
  chkEma: document.getElementById('chkEma'),
  chkVwap: document.getElementById('chkVwap'),
  chkSupRes: document.getElementById('chkSupRes'),
  chkZones: document.getElementById('chkZones'),
  btnZoomIn: document.getElementById('btnZoomIn'),
  btnZoomOut: document.getElementById('btnZoomOut'),
  btnZoomReset: document.getElementById('btnZoomReset'),

  // Other Modals
  methodologyModal: document.getElementById('methodologyModal'),
  btnCloseMethodologyModal: document.getElementById('btnCloseMethodologyModal'),
  linkMethodologyFooter: document.getElementById('linkMethodologyFooter'),
  settingsModal: document.getElementById('settingsModal'),
  btnCloseSettingsModal: document.getElementById('btnCloseSettingsModal'),
  settingsForm: document.getElementById('settingsForm'),
  btnPreviewSettings: document.getElementById('btnPreviewSettings'),
  loginForm: document.getElementById('loginForm'),
  authStatusText: document.getElementById('authStatusText'),
  btnLogout: document.getElementById('btnLogout'),

  // Laya AI Elements
  layaPulseSection: document.getElementById('layaPulseSection'),
  pulseRegimeTag: document.getElementById('pulseRegimeTag'),
  pulseSummaryText: document.getElementById('pulseSummaryText'),
  pulseModelLabel: document.getElementById('pulseModelLabel'),
  btnRefreshPulse: document.getElementById('btnRefreshPulse'),

  btnLayaDeepAnalyze: document.getElementById('btnLayaDeepAnalyze'),
  btnLayaDeepText: document.getElementById('btnLayaDeepText'),
  layaLlmBriefingBox: document.getElementById('layaLlmBriefingBox'),
  layaLlmModelTag: document.getElementById('layaLlmModelTag'),
  layaLlmSourceTag: document.getElementById('layaLlmSourceTag'),
  layaLlmBriefContent: document.getElementById('layaLlmBriefContent'),

  btnOpenCopilot: document.getElementById('btnOpenCopilot'),
  layaCopilotDrawer: document.getElementById('layaCopilotDrawer'),
  btnCloseCopilotDrawer: document.getElementById('btnCloseCopilotDrawer'),
  drawerContextStock: document.getElementById('drawerContextStock'),
  drawerContextAction: document.getElementById('drawerContextAction'),
  drawerChatArea: document.getElementById('drawerChatArea'),
  copilotForm: document.getElementById('copilotForm'),
  copilotTextInput: document.getElementById('copilotTextInput'),
  layaFab: document.getElementById('layaFab'),

  // Market Schedule & Countdown Elements
  marketCountdownPill: document.getElementById('marketCountdownPill'),
  mktPulseDot: document.getElementById('mktPulseDot'),
  mktStatusText: document.getElementById('mktStatusText'),
  mktCountdownTimer: document.getElementById('mktCountdownTimer'),
  summaryMarketSession: document.getElementById('summaryMarketSession'),
  summarySessionText: document.getElementById('summarySessionText'),
  scheduleModal: document.getElementById('scheduleModal'),
  btnCloseScheduleModal: document.getElementById('btnCloseScheduleModal'),
  modalStatusDot: document.getElementById('modalStatusDot'),
  modalStatusBadge: document.getElementById('modalStatusBadge'),
  modalStatusDesc: document.getElementById('modalStatusDesc'),
  modalTimerLabel: document.getElementById('modalTimerLabel'),
  modalTimerDisplay: document.getElementById('modalTimerDisplay'),
  modalTimerTarget: document.getElementById('modalTimerTarget'),
  modalIstClock: document.getElementById('modalIstClock'),
  schedCardPre: document.getElementById('schedCardPre'),
  schedCardNormal: document.getElementById('schedCardNormal'),
  schedCardPost: document.getElementById('schedCardPost'),
};

// Apply initial Theme
document.documentElement.setAttribute('data-theme', state.theme);

/* --------------------------------------------------------------------------
   Initialization
   -------------------------------------------------------------------------- */
async function init() {
  bindEvents();
  startClock();

  // Load bootstrap config
  try {
    const bootstrap = await publicAPI.getBootstrap();
    if (bootstrap.session?.authenticated) {
      state.user = bootstrap.session;
      state.csrfToken = bootstrap.session.csrf_token;
      updateAuthUI();
    }
    if (bootstrap.config?.default_timeframe) {
      state.timeframe = bootstrap.config.default_timeframe;
      updateTimeframeButtons(state.timeframe);
    }
    if (bootstrap.market_schedule) {
      marketScheduleData = bootstrap.market_schedule;
    }
  } catch (err) {
    console.warn('Bootstrap note:', err);
  }

  // Initial Fetch & Start Polling
  await fetchMarketData();
  startPolling(2000);
  initFlowParticles();
  startParticleAnimation();

  // Initialize Laya AI Market Pulse
  fetchMarketPulse();
  setInterval(() => {
    if (!state.isPaused && !document.hidden) {
      fetchMarketPulse();
    }
  }, 60000);
}

/* --------------------------------------------------------------------------
   Live Data Polling Loop
   -------------------------------------------------------------------------- */
function startPolling(intervalMs = 2000) {
  if (state.pollTimer) clearInterval(state.pollTimer);
  state.pollTimer = setInterval(async () => {
    if (!state.isPaused && !document.hidden) {
      await fetchMarketData();
    }
  }, intervalMs);
}

async function fetchMarketData() {
  const startT = performance.now();
  try {
    const urlParams = new URLSearchParams(window.location.search);
    const requestedSession = state.viewingPrevious ? 'previous' : (urlParams.get('session') || null);
    const snapshot = await publicAPI.getMarketSnapshot('live', state.timeframe, requestedSession);
    if (!snapshot) return;

    // Feed status banners and chips
    const sessionBanner = document.getElementById('sessionBanner');
    const sessionBannerText = document.getElementById('sessionBannerText');
    const unavailBanner = document.getElementById('feedUnavailableBanner');
    const unavailText = document.getElementById('feedUnavailableText');
    const chipDot = document.getElementById('feedStatusDot');
    const chipLabel = document.getElementById('feedStatusLabel');

    state.sessionState = snapshot.session_state || 'LIVE';
    state.sessionDate = snapshot.session_date || null;
    state.dataMode = snapshot.data_mode || 'LIVE';

    const isHistorical = state.sessionState === 'PREVIOUS' || state.sessionState === 'CLOSED_TODAY' || state.dataMode === 'PREVIOUS_SESSION' || state.dataMode === 'CLOSED_SESSION';

    if (snapshot.session_state === 'PREVIOUS' || snapshot.data_mode === 'PREVIOUS_SESSION') {
      if (sessionBanner) {
        sessionBanner.classList.remove('hidden');
        if (sessionBannerText) {
          sessionBannerText.textContent = `PREVIOUS SESSION - ${snapshot.session_date || 'Closed'} (closed) - last candle 15:30 IST. Historical data, not live.`;
        }
      }
      if (chipDot) chipDot.className = 'w-2 h-2 rounded-full bg-amber-400';
      if (chipLabel) chipLabel.textContent = 'PREVIOUS';
    } else if (snapshot.session_state === 'CLOSED_TODAY' || snapshot.data_mode === 'CLOSED_SESSION') {
      if (sessionBanner) {
        sessionBanner.classList.remove('hidden');
        if (sessionBannerText) {
          sessionBannerText.textContent = "TODAY'S SESSION - CLOSED";
        }
      }
      if (chipDot) chipDot.className = 'w-2 h-2 rounded-full bg-amber-400';
      if (chipLabel) chipLabel.textContent = 'CLOSED';
    }

    if (snapshot.session_state === 'LIVE' && !state.viewingPrevious) {
      if (chipDot) chipDot.className = 'w-2 h-2 rounded-full bg-emerald-500 animate-pulse';
      if (chipLabel) chipLabel.textContent = 'FEED: LIVE';
    }

    // Populate Session Recap Panel
    if (el.sessionRecapPanel) {
      if (isHistorical || state.viewingPrevious) {
        el.sessionRecapPanel.classList.remove('hidden');
        if (snapshot.scorecard) {
          const sc = snapshot.scorecard;
          if (el.recapTotalSignals) el.recapTotalSignals.textContent = sc.signals != null ? sc.signals : '0';
          if (el.recapWinRate) el.recapWinRate.textContent = sc.win_rate_pct != null ? `${sc.win_rate_pct}% (${sc.wins || 0}/${sc.signals || 0})` : '—';
          if (el.recapWins) el.recapWins.textContent = `${sc.t1_wins || 0} / ${sc.t2_wins || 0}`;
          if (el.recapLosses) el.recapLosses.textContent = sc.losses != null ? sc.losses : '0';
          if (el.recapExpired) el.recapExpired.textContent = sc.expired != null ? sc.expired : '0';
          if (el.recapAvgR) el.recapAvgR.textContent = sc.avg_r != null ? `${sc.avg_r > 0 ? '+' : ''}${sc.avg_r.toFixed(2)}R` : '—';
          if (el.recapBestWorst) el.recapBestWorst.textContent = sc.best_trade ? `${sc.best_trade} / ${sc.worst_trade || '—'}` : '—';
          if (el.recapNote) el.recapNote.textContent = sc.note || '';
          if (el.recapSessionDate) el.recapSessionDate.textContent = snapshot.session_date || '';
          if (el.recapSource) el.recapSource.textContent = snapshot.data_source || 'Archive';
          if (el.recapCompleteness) el.recapCompleteness.textContent = snapshot.complete ? 'Complete (≥90% bars)' : 'INCOMPLETE SESSION';
        }
      } else {
        el.sessionRecapPanel.classList.add('hidden');
      }
    } else if (snapshot.error || snapshot.status === 503) {
      if (sessionBanner) sessionBanner.classList.add('hidden');
      if (unavailBanner) {
        unavailBanner.classList.remove('hidden');
        if (unavailText) unavailText.textContent = `⚠️ LIVE MARKET FEED UNAVAILABLE: ${snapshot.detail || 'Connecting to scraper...'}`;
      }
      if (chipDot) chipDot.className = 'w-2 h-2 rounded-full bg-rose-500 animate-pulse';
      if (chipLabel) chipLabel.textContent = 'FEED: DOWN';
      if (el.marketTableBody) {
        el.marketTableBody.innerHTML = `<tr><td colspan="16" class="text-center py-8 font-mono text-sm text-rose-400">⚠️ Live feed unavailable. Please check exchange connection.</td></tr>`;
      }
      return;
    } else {
      if (sessionBanner) sessionBanner.classList.add('hidden');
      const feed = snapshot.feed || {};
      const staleCnt = feed.stale_count || 0;
      const failedCnt = feed.failed_count || 0;
      const okCnt = feed.ok_count || 0;
      const total = okCnt + staleCnt + failedCnt;

      if (failedCnt > 0 || staleCnt > 0) {
        if (unavailBanner) {
          unavailBanner.classList.remove('hidden');
          if (unavailText) unavailText.textContent = `⚠️ FEED DEGRADED: ${staleCnt} stale, ${failedCnt} failed of ${total} monitored symbols.`;
        }
  
        if (chipLabel) chipLabel.textContent = `FEED: DEGRADED (${staleCnt + failedCnt}/${total})`;
      } else {
  
        if (chipDot) chipDot.className = 'w-2 h-2 rounded-full bg-emerald-500';
        if (chipLabel) chipLabel.textContent = 'FEED: LIVE';
      }
    }

    state.latestSnapshot = snapshot;
    if (snapshot.market_schedule) {
      marketScheduleData = snapshot.market_schedule;
    }

    renderSummaryBar(snapshot.counts);
    renderTrajectories(snapshot.trajectories, snapshot.stocks);
    renderSignalTiles(snapshot.signal_tiles);
    renderSpotlight(snapshot.spotlight);
    renderSectors(snapshot.sectors);
    renderScannerTable(snapshot.stocks);

    // If modal is open for a stock, update diagnostics & chart
    if (state.selectedStock && snapshot.stocks[state.selectedStock]) {
      updateModalDiagnostics(snapshot.stocks[state.selectedStock]);
    }

    // Telemetry reporting (corrected contract: reports render duration)
    const renderDuration = performance.now() - startT;
    reportTelemetry(renderDuration);
  } catch (err) {
    console.error('Error fetching snapshot:', err);
  }
}

async function reportTelemetry(renderDurationMs) {
  try {
    await fetch('/api/metrics', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        render_ms: renderDurationMs,
        feed_latency_ms: 12.5,
        client_timestamp: new Date().toISOString(),
      }),
    });
  } catch {}
}

/* --------------------------------------------------------------------------
   Summary Bar Rendering
   -------------------------------------------------------------------------- */
function renderSummaryBar(counts) {
  if (!counts) return;
  if (el.cntTotal) el.cntTotal.textContent = counts.total || 24;
  if (el.cntBuy) el.cntBuy.textContent = counts.buy || 0;
  if (el.cntSell) el.cntSell.textContent = counts.sell || 0;
  if (el.cntNeutral) el.cntNeutral.textContent = counts.neutral || 0;

  const total = Math.max(1, (counts.buy || 0) + (counts.sell || 0));
  const bullPct = Math.round(((counts.buy || 0) / total) * 100);
  const bearPct = 100 - bullPct;

  if (el.breadthBull) el.breadthBull.style.width = `${bullPct}%`;
  if (el.breadthBear) el.breadthBear.style.width = `${bearPct}%`;

  const bullLbl = document.getElementById('breadthBullPct');
  const bearLbl = document.getElementById('breadthBearPct');
  if (bullLbl) bullLbl.textContent = `${bullPct}% Bull`;
  if (bearLbl) bearLbl.textContent = `${bearPct}% Bear`;
}

/* --------------------------------------------------------------------------
   Market Trajectories Canvas Engine (SVG) - Order Block Flow Terminal
   -------------------------------------------------------------------------- */
function renderTrajectories(trajectories, stocks) {
  if (!trajectories || trajectories.length === 0) return;

  const W = 1000;
  const H = 500;
  const padLeft = 60;
  // Keep room for the company label, optional margin star and percentage.
  const endpointPillWidth = t => {
    const changeText = (t.change_pct >= 0 ? '+' : '') + t.change_pct.toFixed(2) + '%';
    return Math.max(94, Math.ceil(24 + String(t.symbol).length * 5.8 + changeText.length * 5.8));
  };
  const padRight = Math.max(130, ...trajectories.map(t => endpointPillWidth(t) + 16));
  const padTop = 30;
  const padBottom = 30;

  const graphW = W - padLeft - padRight;
  const graphH = H - padTop - padBottom;
  const centerY = padTop + graphH / 2;

  // Find max absolute percentage change to scale Y-axis dynamically
  let maxAbsChg = 1.8;
  let bullCount = 0;
  let bearCount = 0;
  for (const t of trajectories) {
    if (t.change_pct >= 0) bullCount++;
    else bearCount++;
    const absVal = Math.abs(t.change_pct);
    if (absVal > maxAbsChg) maxAbsChg = absVal;
    if (t.points) {
      for (const p of t.points) {
        if (Math.abs(p) > maxAbsChg) maxAbsChg = Math.abs(p);
      }
    }
  }
  // Add 15% headroom
  const yRange = Math.ceil(maxAbsChg * 1.15 * 10) / 10;

  function toY(pct) {
    return centerY - (pct / yRange) * (graphH / 2);
  }

  // 1. Render Top Mini Breadth Distribution Histogram
  if (el.histBarsBear && el.histBarsBull) {
    let bearBars = '';
    const bearHeights = [6, 11, 8, 14, 9, 12, 7, 10];
    for (let i = 0; i < 8; i++) {
      const h = Math.round((bearHeights[i] * Math.max(bearCount, 1)) / (trajectories.length || 1));
      bearBars += `<div class="hist-mini-bar bear" style="height:${Math.max(3, Math.min(14, h))}px;"></div>`;
    }
    el.histBarsBear.innerHTML = bearBars;

    let bullBars = '';
    const bullHeights = [7, 12, 10, 14, 8, 13, 9, 11];
    for (let i = 0; i < 8; i++) {
      const h = Math.round((bullHeights[i] * Math.max(bullCount, 1)) / (trajectories.length || 1));
      bullBars += `<div class="hist-mini-bar bull" style="height:${Math.max(3, Math.min(14, h))}px;"></div>`;
    }
    el.histBarsBull.innerHTML = bullBars;
  }

  // 2. Render Grid & Axes
  let gridHtml = '';
  const gridSteps = [-2.0, -1.5, -1.0, -0.5, 0.5, 1.0, 1.5, 2.0];
  for (const step of gridSteps) {
    if (Math.abs(step) <= yRange) {
      const y = toY(step);
      gridHtml += `
        <line x1="${padLeft}" y1="${y}" x2="${W - padRight + 20}" y2="${y}" stroke="var(--border-subtle)" stroke-dasharray="3,4" stroke-width="0.8"/>
        <text x="${padLeft - 10}" y="${y + 3.5}" fill="var(--text-dim)" font-family="var(--font-mono)" font-size="9" text-anchor="end">${step > 0 ? '+' : ''}${step.toFixed(1)}%</text>
      `;
    }
  }
  el.svgGrid.innerHTML = gridHtml;

  // Zero Center Axis
  el.svgZeroAxis.innerHTML = `
    <line x1="${padLeft}" y1="${centerY}" x2="${W - padRight + 20}" y2="${centerY}" stroke="var(--text-dim)" stroke-width="1.2" stroke-dasharray="4,4"/>
    <text x="${padLeft - 10}" y="${centerY + 3.5}" fill="var(--text-main)" font-family="var(--font-mono)" font-size="10" font-weight="700" text-anchor="end">0.0%</text>
  `;

  // 3. Render Glowing Cyan Order Block Horizontal Rays
  if (el.svgOrderBlockRays) {
    let obHtml = '';
    const obLevels = [1.2, 0.6, -0.6, -1.2];
    for (const lvl of obLevels) {
      if (Math.abs(lvl) <= yRange) {
        const y = toY(lvl);
        obHtml += `
          <line x1="${padLeft}" y1="${y}" x2="${W - padRight + 14}" y2="${y}"
            stroke="var(--ob-cyan)" stroke-width="1.6" opacity="0.8" filter="url(#obGlow)"/>
          <line x1="${padLeft}" y1="${y}" x2="${W - padRight + 14}" y2="${y}"
            stroke="#ffffff" stroke-width="0.6" opacity="0.85"/>
          <text x="${W - padRight + 18}" y="${y + 3}" fill="var(--ob-cyan)" font-family="var(--font-mono)" font-size="7.5" font-weight="700">OB</text>
        `;
      }
    }
    el.svgOrderBlockRays.innerHTML = obHtml;
  }

  // 4. Render Vertical Scanning Gate & Connector Fibers to Right Zones
  if (el.svgGate) {
    const gateX = W - padRight;
    let gateHtml = `
      <!-- Vertical Gate Column -->
      <line x1="${gateX}" y1="${padTop}" x2="${gateX}" y2="${H - padBottom}" stroke="rgba(255,255,255,0.2)" stroke-width="1" stroke-dasharray="2,3"/>
      <rect x="${gateX - 2}" y="${padTop}" width="16" height="${H - padTop - padBottom}" fill="rgba(0, 229, 255, 0.03)" stroke="rgba(0, 229, 255, 0.2)" stroke-width="0.8"/>
    `;
    // Dense scanning grid lines inside gate
    for (let y = padTop; y < H - padBottom; y += 8) {
      gateHtml += `<line x1="${gateX - 2}" y1="${y}" x2="${gateX + 14}" y2="${y}" stroke="rgba(255, 255, 255, 0.15)" stroke-width="0.5"/>`;
    }
    // Top collector funnel & connector fiber to Bullish Zone (top-right)
    gateHtml += `
      <circle cx="${gateX + 14}" cy="${padTop + 30}" r="3" fill="var(--bull-primary)" filter="url(#glow)"/>
      <path d="M ${gateX + 14} ${padTop + 30} C ${gateX + 60} ${padTop + 30}, ${gateX + 70} 6, ${W} 6"
        fill="none" stroke="var(--bull-primary)" stroke-width="2.2" opacity="0.9" filter="url(#glow)"/>
    `;
    // Bottom collector funnel & connector fiber to Bearish Zone (bottom-right)
    gateHtml += `
      <circle cx="${gateX + 14}" cy="${H - padBottom - 30}" r="3" fill="var(--bear-primary)" filter="url(#glow)"/>
      <path d="M ${gateX + 14} ${H - padBottom - 30} C ${gateX + 60} ${H - padBottom - 30}, ${gateX + 70} ${H - 6}, ${W} ${H - 6}"
        fill="none" stroke="var(--bear-primary)" stroke-width="2.2" opacity="0.9" filter="url(#glow)"/>
    `;
    el.svgGate.innerHTML = gateHtml;
  }

  // 5. Render Trajectory Curves & Endpoints
  let trajHtml = '';
  let endpHtml = '';
  state.flowPaths = [];

  for (const t of trajectories) {
    const isBull = t.change_pct >= 0;
    const colorClass = isBull ? 'bull' : 'bear';
    const isHovered = state.hoveredStock === t.symbol;
    const isDimmed = state.hoveredStock && !isHovered;

    const points = t.points || [0, t.change_pct];
    const n = points.length;
    const xStep = graphW / (n - 1);

    // Build SVG path with cubic smoothing
    let d = `M ${padLeft} ${toY(points[0])}`;
    const coordPoints = [{ x: padLeft, y: toY(points[0]) }];

    for (let i = 1; i < n; i++) {
      const prevX = padLeft + (i - 1) * xStep;
      const prevY = toY(points[i - 1]);
      const curX = padLeft + i * xStep;
      const curY = toY(points[i]);
      coordPoints.push({ x: curX, y: curY });

      const midX = (prevX + curX) / 2;
      d += ` C ${midX} ${prevY}, ${midX} ${curY}, ${curX} ${curY}`;
    }

    state.flowPaths.push({ symbol: t.symbol, coords: coordPoints, isBull });

    trajHtml += `
      <path id="traj_${t.symbol}" class="traj-path ${colorClass} ${isHovered ? 'highlighted' : ''} ${isDimmed ? 'dimmed' : ''}"
        d="${d}" data-symbol="${t.symbol}"/>
    `;

    // Endpoint Pill at rightmost edge
    const lastX = padLeft + (n - 1) * xStep;
    const lastY = toY(points[n - 1]);
    const chgStr = (t.change_pct >= 0 ? '+' : '') + t.change_pct.toFixed(2) + '%';
    const pillColor = isBull ? 'var(--bull-primary)' : 'var(--bear-primary)';
    const pillWidth = endpointPillWidth(t);

    endpHtml += `
      <g class="endpoint-group ${isHovered ? 'highlighted' : ''} ${isDimmed ? 'dimmed' : ''}" data-symbol="${t.symbol}" transform="translate(${lastX + 6}, ${lastY})">
        <circle cx="0" cy="0" r="3.5" fill="${pillColor}"/>
        <rect x="6" y="-9" width="${pillWidth}" height="18" rx="4" class="endpoint-bg" stroke="${pillColor}" stroke-opacity="0.4"/>
        <text x="12" y="0" class="endpoint-text" data-margin-symbol="${escapeHtml(t.symbol)}" fill="${pillColor}">${t.symbol}</text>
        <text x="${pillWidth}" y="0" class="endpoint-text" fill="var(--text-main)" text-anchor="end">${chgStr}</text>
      </g>
    `;
  }

  el.svgTrajectories.innerHTML = trajHtml;
  el.svgEndpoints.innerHTML = endpHtml;
}

/* --------------------------------------------------------------------------
   Light Packets Particle Animation
   -------------------------------------------------------------------------- */
function initFlowParticles() {
  state.flowParticles = [];
  for (let i = 0; i < 30; i++) {
    state.flowParticles.push({
      pathIndex: i % 24,
      progress: ((i * 37) % 100) / 100,
      speed: 0.003 + ((i * 13) % 40) * 0.0001,
    });
  }
}

function startParticleAnimation() {
  if (state.animationFrameId) cancelAnimationFrame(state.animationFrameId);

  function step() {
    if (state.motionEnabled && !state.isPaused && state.flowPaths && state.flowPaths.length > 0) {
      let particleSvg = '';
      for (const p of state.flowParticles) {
        p.progress += p.speed;
        if (p.progress >= 1.0) {
          p.progress = 0;
          p.pathIndex = (p.pathIndex + 7) % state.flowPaths.length;
        }

        const pathData = state.flowPaths[p.pathIndex];
        if (pathData && pathData.coords.length > 1) {
          const totalSegments = pathData.coords.length - 1;
          const currentSegFrac = p.progress * totalSegments;
          const segIdx = Math.min(totalSegments - 1, Math.floor(currentSegFrac));
          const tFrac = currentSegFrac - segIdx;

          const p0 = pathData.coords[segIdx];
          const p1 = pathData.coords[segIdx + 1];
          const x = p0.x + (p1.x - p0.x) * tFrac;
          const y = p0.y + (p1.y - p0.y) * tFrac;

          const fillColor = pathData.isBull ? '#38bdf8' : '#f43f5e';
          particleSvg += `<circle cx="${x.toFixed(1)}" cy="${y.toFixed(1)}" r="2.2" fill="${fillColor}" opacity="0.85" filter="drop-shadow(0 0 3px ${fillColor})"/>`;
        }
      }
      el.svgParticles.innerHTML = particleSvg;
    } else {
      el.svgParticles.innerHTML = '';
    }
    state.animationFrameId = requestAnimationFrame(step);
  }
  state.animationFrameId = requestAnimationFrame(step);
}

/* --------------------------------------------------------------------------
   Floating HUD Situation Panel
   -------------------------------------------------------------------------- */
function showStockHUD(symbol, clientX, clientY) {
  if (!state.latestSnapshot?.stocks) return;
  const stock = state.latestSnapshot.stocks[symbol];
  if (!stock) return;

  state.hoveredStock = symbol;
  highlightStockEverywhere(symbol);

  const isConfirmed = state.signalMode === 'confirmed';
  const metrics = isConfirmed && stock.confirmed?.score !== null ? stock.confirmed : stock.metrics;
  const score = metrics.score !== null ? metrics.score : 'N/A';
  const signal = metrics.signal || 'NEUTRAL';
  const isPos = stock.change_pct >= 0;

  el.hudSym.textContent = stock.symbol;
  el.hudSym.dataset.marginSymbol = stock.symbol;
  el.hudName.textContent = stock.name;
  el.hudName.dataset.marginSymbol = stock.symbol;
  el.hudBadge.textContent = signal;
  el.hudBadge.className = `hud-signal-badge ${signal.toLowerCase()}`;

  el.hudPrice.textContent = `₹${stock.price.toLocaleString('en-IN', { minimumFractionDigits: 2 })}`;
  el.hudChg.textContent = `${isPos ? '+' : ''}${stock.change_pct.toFixed(2)}%`;
  el.hudChg.className = `hud-chg ${isPos ? 'pos' : 'neg'}`;

  el.hudScore.textContent = score;
  el.hudScore.style.color = signal === 'BUY' ? 'var(--bull-primary)' : signal === 'SELL' ? 'var(--bear-primary)' : 'var(--neutral-primary)';
  el.hudRsi.textContent = metrics.raw?.rsi !== undefined ? metrics.raw.rsi.toFixed(1) : '—';
  el.hudRvol.textContent = `${stock.rvol.toFixed(2)}x`;
  el.hudVwap.textContent = stock.vwap ? `₹${stock.vwap.toFixed(1)}` : '—';
  el.hudTime.textContent = `Updated ${stock.updated_at.slice(11, 19)} IST`;

  // Algorithmic AI Decision HUD Data
  const ai = stock.ai_decision;
  if (ai && el.hudAiAction) {
    el.hudAiAction.textContent = ai.action;
    el.hudAiAction.className = `ai-act-pill ${ai.signal_type.toLowerCase()}`;
    el.hudAiSl.textContent = `₹${ai.stop_loss}`;
    el.hudAiT1.textContent = `₹${ai.target_1}`;
    el.hudAiRr.textContent = `R:R 1:${ai.risk_reward_ratio}`;
  }

  // Mini sparkline inside HUD
  renderSparkline(el.hudSparkline, stock.candles_preview || [], isPos ? '#38bdf8' : '#f43f5e');

  // Positioning
  const wrapperRect = el.canvasWrapper.getBoundingClientRect();
  let relX = clientX - wrapperRect.left + 16;
  let relY = clientY - wrapperRect.top - 40;

  // Constrain inside bounds
  if (relX + 230 > wrapperRect.width) relX = clientX - wrapperRect.left - 235;
  if (relY + 220 > wrapperRect.height) relY = wrapperRect.height - 230;
  if (relY < 10) relY = 10;

  el.hudPanel.style.transform = `translate(${relX}px, ${relY}px)`;
  el.hudPanel.style.display = 'block';
}

function hideStockHUD() {
  state.hoveredStock = null;
  el.hudPanel.style.display = 'none';
  highlightStockEverywhere(null);
}

function highlightStockEverywhere(symbol) {
  // Update Trajectories
  const paths = el.svgTrajectories.querySelectorAll('.traj-path');
  const endpoints = el.svgEndpoints.querySelectorAll('.endpoint-group');
  paths.forEach(p => {
    const sym = p.getAttribute('data-symbol');
    if (!symbol) {
      p.classList.remove('highlighted', 'dimmed');
    } else if (sym === symbol) {
      p.classList.add('highlighted');
      p.classList.remove('dimmed');
    } else {
      p.classList.add('dimmed');
      p.classList.remove('highlighted');
    }
  });

  endpoints.forEach(ep => {
    const sym = ep.getAttribute('data-symbol');
    if (!symbol) {
      ep.classList.remove('highlighted', 'dimmed');
    } else if (sym === symbol) {
      ep.classList.add('highlighted');
      ep.classList.remove('dimmed');
    } else {
      ep.classList.add('dimmed');
      ep.classList.remove('highlighted');
    }
  });

  // Highlight Cards
  document.querySelectorAll('.signal-card').forEach(c => {
    const sym = c.getAttribute('data-symbol');
    c.classList.toggle('highlighted', symbol && sym === symbol);
  });
}

function renderSparkline(svgEl, candles, color) {
  if (!candles || candles.length < 2) {
    svgEl.innerHTML = '';
    return;
  }
  const W = 160;
  const H = 36;
  const closes = candles.map(c => c.c);
  const minC = Math.min(...closes);
  const maxC = Math.max(...closes);
  const range = maxC - minC || 1;

  const points = closes.map((c, i) => {
    const x = (i / (closes.length - 1)) * (W - 10) + 5;
    const y = H - 5 - ((c - minC) / range) * (H - 10);
    return `${x.toFixed(1)},${y.toFixed(1)}`;
  });

  svgEl.innerHTML = `
    <polyline fill="none" stroke="${color}" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round" points="${points.join(' ')}"/>
  `;
}

/* --------------------------------------------------------------------------
   Order Block Build-Up Matrices Rendering (8 Long, 8 Short Cards)
   -------------------------------------------------------------------------- */
function renderSignalTiles(tiles) {
  if (!tiles) return;

  function buildMatrixCard(s, type) {
    const isConfirmed = state.signalMode === 'confirmed';
    const metrics = isConfirmed && s.confirmed?.score !== null ? s.confirmed : s.metrics;
    const score = metrics.score !== null ? metrics.score : '—';
    const isBull = type === 'buy';
    const isPos = s.change_pct >= 0;

    // Generate 5-bar volume profile distribution for volume visual
    const candles = s.candles_preview || [];
    let vols = [];
    if (candles.length >= 5) {
      vols = candles.slice(-5).map(c => c.v || c.volume || Math.abs((c.high || 1) - (c.low || 0)) * 100 || 50);
    } else {
      const code = (s.symbol || 'A').charCodeAt(0) * 13;
      vols = [(code * 7) % 70 + 30, (code * 11) % 90 + 20, (code * 3) % 85 + 25, (code * 17) % 95 + 15, (code * 5) % 65 + 35];
    }
    const maxV = Math.max(...vols, 1);
    const barPcts = vols.map(v => Math.max(16, Math.min(100, Math.round((v / maxV) * 100))));

    const oiVal = (Math.abs(s.change_pct) * 2.8 + 3.2).toFixed(1);

    return `
      <div class="matrix-card ${isBull ? 'bull-card' : 'bear-card'}" data-symbol="${s.symbol}">
        <div class="m-card-top">
          <div class="m-sym-group">
            <span class="m-sym" data-margin-symbol="${escapeHtml(s.symbol)}">${s.symbol}</span>
            <span class="m-name" data-margin-symbol="${escapeHtml(s.symbol)}">${escapeHtml(s.name || s.symbol)}</span>
          </div>
          <span class="m-tag ${isBull ? 'bull' : 'bear'}">
            ${isBull ? 'LONG BUILD-UP' : 'SHORT BUILD-UP'}
          </span>
          <span class="m-price">₹${s.price.toLocaleString('en-IN', { minimumFractionDigits: 1, maximumFractionDigits: 2 })}</span>
        </div>
        <div class="m-vol-bars">
          ${barPcts.map(pct => `<div class="m-bar-row"><span class="m-bar-fill" style="width: ${pct}%;"></span></div>`).join('')}
        </div>
        <div class="m-card-bot">
          <span class="m-chg ${isPos ? 'pos' : 'neg'}">${isPos ? '+' : ''}${s.change_pct.toFixed(2)}%</span>
          <span class="m-oi">OI +${oiVal}%</span>
          <span class="m-score">Score ${score}</span>
        </div>
      </div>
    `;
  }

  if (el.buyTilesContainer) {
    el.buyTilesContainer.innerHTML = (tiles.buy || []).slice(0, 8).map(s => buildMatrixCard(s, 'buy')).join('');
  }
  if (el.sellTilesContainer) {
    el.sellTilesContainer.innerHTML = (tiles.sell || []).slice(0, 8).map(s => buildMatrixCard(s, 'sell')).join('');
  }
}

/* --------------------------------------------------------------------------
   Hero Order Block Cards Rendering (3 Bullish, 3 Bearish with VWAP & Zone Slider)
   -------------------------------------------------------------------------- */
function renderSpotlight(spotlight) {
  if (!spotlight) return;

  function buildHeroCard(s, type) {
    const isConfirmed = state.signalMode === 'confirmed';
    const metrics = isConfirmed && s.confirmed?.score !== null ? s.confirmed : s.metrics;
    const isBull = type === 'buy';
    const isPos = s.change_pct >= 0;

    // Calculate Zone slider thumb position between Support and Resistance
    const sup = s.support || Math.round(s.price * 0.98);
    const res = s.resistance || Math.round(s.price * 1.02);
    let zonePct = 50;
    if (res > sup) {
      zonePct = Math.round(((s.price - sup) / (res - sup)) * 100);
      zonePct = Math.max(12, Math.min(88, zonePct));
    }

    const oiVal = (Math.abs(s.change_pct) * 3.4 + 4.1).toFixed(1);
    const deltaVal = Math.round(Math.abs(s.change_pct) * 110 + 65) + 'K';
    const vwapVal = s.vwap ? s.vwap.toFixed(1) : s.price.toFixed(1);
    const rrVal = s.ai_decision?.risk_reward_ratio || (isBull ? '2.4' : '2.1');

    return `
      <div class="hero-stock-card ${isBull ? 'bull' : 'bear'}" data-symbol="${s.symbol}">
        <div class="hero-top-row">
          <div class="hero-ident">
            <span class="hero-badge ${isBull ? 'bull' : 'bear'}">
              <span class="radar-dot ${isBull ? 'bull' : 'bear'}"></span>
              <span>${isBull ? 'BULLISH' : 'BEARISH'}</span>
            </span>
            <div class="hero-name-block">
              <span class="hero-sym" data-margin-symbol="${escapeHtml(s.symbol)}">${s.symbol}</span>
              <span class="hero-full-name" data-margin-symbol="${escapeHtml(s.symbol)}">${escapeHtml(s.name || s.symbol)}</span>
            </div>
          </div>
          <div class="hero-chart-area">
            <div class="hero-chart-label">
              <span class="hero-vwap-tag">VWAP ₹${vwapVal}</span>
              <span class="hero-chg ${isPos ? 'pos' : 'neg'}">${isPos ? '+' : ''}${s.change_pct.toFixed(2)}%</span>
            </div>
            <svg id="hero_chart_${s.symbol}" class="hero-chart-svg" viewBox="0 0 130 36"></svg>
          </div>
        </div>

        <div class="hero-mid-row">
          <div class="hero-tag-pill ${isBull ? 'bull' : 'bear'}">${isBull ? 'LONG BUILD-UP' : 'SHORT BUILD-UP'}</div>
          <div class="hero-oi-stat">
            <span>OI: <strong>+${oiVal}%</strong></span>
            <span>Δ: <strong class="${isPos ? 'pos' : 'neg'}">${isPos ? '+' : '-'}${deltaVal}</strong></span>
          </div>
          <div class="hero-price-display">₹${s.price.toLocaleString('en-IN', { minimumFractionDigits: 1, maximumFractionDigits: 2 })}</div>
        </div>

        <div class="hero-zone-slider">
          <span class="zone-bound">${isBull ? 'S1' : 'R1'} ₹${isBull ? sup : res}</span>
          <div class="zone-track">
            <div class="zone-level-grid"></div>
            <span class="zone-thumb ${isBull ? 'bull' : 'bear'}" style="left: ${zonePct}%;" title="Order Block Zone Position"></span>
          </div>
          <span class="zone-bound">${isBull ? 'R1' : 'S1'} ₹${isBull ? res : sup}</span>
          <span class="zone-rr-tag">R:R 1:${rrVal}</span>
        </div>
        ${(state.sessionState === 'PREVIOUS' || state.sessionState === 'CLOSED_TODAY' || state.dataMode === 'PREVIOUS_SESSION' || state.dataMode === 'CLOSED_SESSION') ? `
        <div class="text-[10px] font-mono text-amber-400 font-bold bg-amber-950/60 px-2 py-0.5 rounded border border-amber-800/60 mt-1 flex items-center justify-between">
          <span>Historical setup - not actionable</span>
        </div>` : ''}
      </div>
    `;
  }

  if (el.spotlightBuyContainer) {
    el.spotlightBuyContainer.innerHTML = (spotlight.buy || []).slice(0, 3).map(s => buildHeroCard(s, 'buy')).join('');
  }
  if (el.spotlightSellContainer) {
    el.spotlightSellContainer.innerHTML = (spotlight.sell || []).slice(0, 3).map(s => buildHeroCard(s, 'sell')).join('');
  }

  // Render hero charts
  const allSpot = [...(spotlight.buy || []).slice(0, 3), ...(spotlight.sell || []).slice(0, 3)];
  for (const s of allSpot) {
    const svgChart = document.getElementById(`hero_chart_${s.symbol}`);
    if (svgChart) {
      renderHeroChart(svgChart, s.candles_preview || [], s.vwap, s.change_pct >= 0);
    }
  }
}

function renderHeroChart(svgEl, candles, vwap, isBull) {
  if (!svgEl) return;
  const W = 130;
  const H = 36;
  const pad = 3;

  let prices = [];
  if (candles && candles.length >= 2) {
    prices = candles.map(c => (typeof c === 'object' ? (c.close || c.c || c[4] || c.price || 100) : c));
  } else {
    // Generate visual trend line
    prices = isBull ? [98, 99.2, 98.8, 100.5, 101.4, 102.3] : [102, 101.1, 101.5, 99.8, 98.9, 98.2];
  }

  let minP = Math.min(...prices);
  let maxP = Math.max(...prices);
  if (vwap) {
    if (vwap < minP) minP = vwap;
    if (vwap > maxP) maxP = vwap;
  }
  const range = maxP - minP || 1;
  const toY = (p) => H - pad - ((p - minP) / range) * (H - pad * 2);
  const xStep = (W - pad * 2) / (prices.length - 1);

  // Line path
  let pathD = `M ${pad} ${toY(prices[0]).toFixed(1)}`;
  for (let i = 1; i < prices.length; i++) {
    const x = pad + i * xStep;
    const y = toY(prices[i]);
    pathD += ` L ${x.toFixed(1)} ${y.toFixed(1)}`;
  }

  // VWAP reference line
  let vwapHtml = '';
  if (vwap && vwap >= minP && vwap <= maxP) {
    const vwapY = toY(vwap);
    vwapHtml = `<line x1="${pad}" y1="${vwapY.toFixed(1)}" x2="${W - pad}" y2="${vwapY.toFixed(1)}" stroke="#fbbf24" stroke-width="1" stroke-dasharray="2,2" opacity="0.8"/>`;
  }

  const strokeColor = isBull ? 'var(--bull-primary)' : 'var(--bear-primary)';
  svgEl.innerHTML = `
    ${vwapHtml}
    <path d="${pathD}" fill="none" stroke="${strokeColor}" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"/>
  `;
}

/* --------------------------------------------------------------------------
   Sector Performance Strip Rendering
   -------------------------------------------------------------------------- */
function renderSectors(sectors) {
  if (!sectors) return;
  el.sectorChipsContainer.innerHTML = sectors.map(sec => {
    const isPos = sec.avg_change_pct >= 0;
    return `
      <div class="sector-chip" data-sector="${sec.sector}">
        <span class="sec-name">${sec.sector}</span>
        <span class="sec-chg ${isPos ? 'pos' : 'neg'}">${isPos ? '+' : ''}${sec.avg_change_pct.toFixed(2)}%</span>
      </div>
    `;
  }).join('');
}

/* --------------------------------------------------------------------------
   Stock Scanner & Heatmap Table
   -------------------------------------------------------------------------- */
function renderScannerTable(stocksDict) {
  if (!stocksDict) return;
  let list = Object.values(stocksDict);

  // Filter by Tab
  if (state.scannerFilter === 'buy') {
    list = list.filter(s => (s.metrics.signal === 'BUY' || s.confirmed?.signal === 'BUY'));
  } else if (state.scannerFilter === 'sell') {
    list = list.filter(s => (s.metrics.signal === 'SELL' || s.confirmed?.signal === 'SELL'));
  } else if (state.scannerFilter === 'neutral') {
    list = list.filter(s => (s.metrics.signal === 'NEUTRAL' || s.confirmed?.signal === 'NEUTRAL'));
  } else if (state.scannerFilter === 'watchlist') {
    list = list.filter(s => state.watchlist.has(s.symbol));
  }

  // Filter by Search Query
  if (state.scannerSearch) {
    const q = state.scannerSearch.toLowerCase();
    list = list.filter(s =>
      s.symbol.toLowerCase().includes(q) ||
      s.name.toLowerCase().includes(q) ||
      s.sector.toLowerCase().includes(q)
    );
  }

  // Sorting
  const { col, desc } = state.scannerSort;
  list.sort((a, b) => {
    let valA = a[col];
    let valB = b[col];

    if (col === 'score') {
      valA = a.metrics.score ?? -1;
      valB = b.metrics.score ?? -1;
    } else if (col === 'rsi') {
      valA = a.metrics.raw?.rsi ?? 50;
      valB = b.metrics.raw?.rsi ?? 50;
    }

    if (typeof valA === 'string') {
      return desc ? valB.localeCompare(valA) : valA.localeCompare(valB);
    }
    return desc ? (valB - valA) : (valA - valB);
  });

  const isConfirmed = state.signalMode === 'confirmed';
  const html = list.map(s => {
    const isStarred = state.watchlist.has(s.symbol);

    if (s.status && s.status !== 'ok') {
      const reason = s.error || (s.status === 'stale' ? 'DATA STALE' : 'DATA UNAVAILABLE');
      return `
      <tr data-symbol="${s.symbol}" class="opacity-50 grayscale bg-slate-900/30">
        <td class="th-fav">
          <button class="star-btn ${isStarred ? 'starred' : ''}" data-symbol="${s.symbol}" title="Add to Watchlist">★</button>
        </td>
        <td class="sym-col font-bold" data-margin-symbol="${escapeHtml(s.symbol)}">${s.symbol}</td>
        <td data-margin-symbol="${escapeHtml(s.symbol)}">${escapeHtml(s.name || s.symbol)}</td>
        <td><span class="sector-tag">${escapeHtml(s.sector || 'N/A')}</span></td>
        <td colspan="11" class="text-center font-mono text-xs text-rose-400 font-semibold py-2">
          ⚠️ DATA UNAVAILABLE: ${reason}
        </td>
        <td class="text-center">
          <button class="tbl-btn btn-open-chart opacity-50" data-symbol="${s.symbol}" disabled>Chart</button>
        </td>
      </tr>
      `;
    }

    const metrics = isConfirmed && s.confirmed?.score !== null ? s.confirmed : s.metrics;
    const score = metrics.score !== null ? metrics.score : 0;
    const signal = metrics.signal || 'NEUTRAL';
    const dayPos = s.change_pct >= 0;
    const openPos = s.open_change_pct >= 0;
    const zone = s.order_blocks && s.order_blocks.length > 0 ? s.order_blocks[s.order_blocks.length - 1] : null;

    let fillClass = 'neutral';
    if (signal === 'BUY') fillClass = 'buy';
    else if (signal === 'SELL') fillClass = 'sell';

    const ai = s.ai_decision;
    const aiAction = ai ? ai.action : 'WAIT';
    const aiType = ai ? ai.signal_type.toLowerCase() : 'neutral';
    const aiSl = ai ? `₹${ai.stop_loss}` : '—';
    const aiT1 = ai ? `₹${ai.target_1}` : '—';
    const aiRr = ai ? `1:${ai.risk_reward_ratio}` : '1:1';

    return `
      <tr data-symbol="${s.symbol}">
        <td class="th-fav">
          <button class="star-btn ${isStarred ? 'starred' : ''}" data-symbol="${s.symbol}" title="Add to Watchlist">★</button>
        </td>
        <td class="sym-col" data-margin-symbol="${escapeHtml(s.symbol)}">${s.symbol}</td>
        <td data-margin-symbol="${escapeHtml(s.symbol)}">${escapeHtml(s.name || s.symbol)}</td>
        <td><span class="sector-tag">${escapeHtml(s.sector || 'N/A')}</span></td>
        <td class="text-right mono-val">₹${s.price.toLocaleString('en-IN', { minimumFractionDigits: 2 })}</td>
        <td class="text-right mono-val ${dayPos ? 'pos' : 'neg'}" style="color:${dayPos ? 'var(--bull-primary)' : 'var(--bear-primary)'}">${dayPos ? '+' : ''}${s.change_pct.toFixed(2)}%</td>
        <td class="text-right mono-val" style="color:${openPos ? 'var(--bull-primary)' : 'var(--bear-primary)'}">${openPos ? '+' : ''}${s.open_change_pct.toFixed(2)}%</td>
        <td>
          <div class="score-bar-inline">
            <span>${metrics.score ?? '—'}</span>
            <div class="score-progress">
              <div class="score-progress-fill ${fillClass}" style="width:${Math.min(100, Math.max(0, score))}%"></div>
            </div>
          </div>
        </td>
        <td><span class="hud-signal-badge ${signal.toLowerCase()}">${signal}</span></td>
        <td class="text-right mono-val">${metrics.raw?.rsi ? metrics.raw.rsi.toFixed(1) : '—'}</td>
        <td class="text-right mono-val">${s.rvol.toFixed(2)}x</td>
        <td class="text-right mono-val">₹${s.vwap || '—'}</td>
        <td class="text-right mono-val">₹${s.support} / ₹${s.resistance}</td>
        <td>${zone ? `<span class="zone-chip ${zone.type}">₹${zone.low} - ₹${zone.high}</span>` : '—'}</td>
        <td>
          <div class="tbl-ai-cell">
            <span class="ai-act-pill ${aiType}" style="font-size:0.65rem;">${aiAction}</span>
            <div class="tbl-ai-levels">
              <span class="sl">SL: ${aiSl}</span>
              <span class="t1">T1: ${aiT1}</span>
              <span class="ai-rr-tag" style="font-size:0.62rem;">${aiRr}</span>
            </div>
          </div>
        </td>
        <td class="text-center">
          <button class="tbl-btn btn-open-chart" data-symbol="${s.symbol}">Chart</button>
        </td>
      </tr>
    `;
  }).join('');

  el.marketTableBody.innerHTML = html;
}

/* --------------------------------------------------------------------------
   Interactive SVG Candlestick Chart Modal
   -------------------------------------------------------------------------- */
async function openChartModal(symbol) {
  state.selectedStock = symbol;
  const stock = state.latestSnapshot?.stocks[symbol];
  if (!stock) return;

  el.modalStockTitle.textContent = `${stock.symbol} · ${stock.name}`;
  el.modalStockTitle.dataset.marginSymbol = stock.symbol;
  el.modalSector.textContent = stock.sector;

  updateModalDiagnostics(stock);
  updateCopilotContext(stock);
  if (el.layaLlmBriefingBox) el.layaLlmBriefingBox.style.display = 'none';
  el.chartModal.style.display = 'flex';

  // Fetch full candles for this stock
  await loadStockCandles(symbol, state.timeframe);
  fetchLayaDeepAnalysis(symbol, state.timeframe);
}

function closeChartModal() {
  state.selectedStock = null;
  el.chartModal.style.display = 'none';
  if (el.layaLlmBriefingBox) el.layaLlmBriefingBox.style.display = 'none';
  if (analysisPollTimer) {
    clearInterval(analysisPollTimer);
    analysisPollTimer = null;
  }
}

function updateModalDiagnostics(stock) {
  const isConfirmed = state.signalMode === 'confirmed';
  const metrics = isConfirmed && stock.confirmed?.score !== null ? stock.confirmed : stock.metrics;
  const score = metrics.score ?? '—';
  const signal = metrics.signal || 'NEUTRAL';

  el.modalSignalBadge.textContent = `${signal} · Score ${score}`;
  el.modalSignalBadge.className = `signal-tag ${signal.toLowerCase()}`;

  // Update AI Decision Copilot Card
  const ai = stock.ai_decision;
  if (ai && el.diagAiAction) {
    el.diagAiAction.textContent = ai.action;
    el.diagAiAction.className = `ai-action-banner ${ai.signal_type.toLowerCase()}`;
    el.diagAiConviction.textContent = `${ai.conviction_pct}% Conviction`;
    el.diagAiEntry.textContent = `₹${ai.entry_low} - ₹${ai.entry_high}`;
    el.diagAiSl.textContent = `₹${ai.stop_loss}`;
    el.diagAiT1.textContent = `₹${ai.target_1}`;
    el.diagAiT2.textContent = `₹${ai.target_2}`;
    el.diagAiRr.textContent = `1 : ${ai.risk_reward_ratio}`;
    el.diagAiRiskPts.textContent = `Risk: ₹${ai.risk_amount} | Reward: ₹${ai.reward_amount}`;
    el.diagAiRationale.textContent = ai.rationale;
    if (ai.factors && el.diagAiFactors) {
      el.diagAiFactors.innerHTML = ai.factors.map(f => `<li>${escapeHtml(String(f))}</li>`).join('');
    }
  }

  el.diagScore.textContent = score;
  el.diagSignal.textContent = signal === 'BUY' ? 'BULLISH / BUY' : signal === 'SELL' ? 'BEARISH / SELL' : 'NEUTRAL';
  el.diagScore.style.color = signal === 'BUY' ? 'var(--bull-primary)' : signal === 'SELL' ? 'var(--bear-primary)' : 'var(--neutral-primary)';

  const comp = metrics.components || {};
  el.diagTrend.textContent = comp.trend !== undefined ? `${comp.trend > 0 ? '+' : ''}${comp.trend}` : '—';
  el.diagMom.textContent = comp.momentum !== undefined ? `${comp.momentum > 0 ? '+' : ''}${comp.momentum}` : '—';
  el.diagVwap.textContent = comp.vwap !== undefined ? `${comp.vwap > 0 ? '+' : ''}${comp.vwap}` : '—';
  el.diagRsi.textContent = comp.rsi !== undefined ? `${comp.rsi > 0 ? '+' : ''}${comp.rsi}` : '—';

  el.diagRes.textContent = `₹${stock.resistance.toFixed(2)}`;
  el.diagVwapLevel.textContent = stock.vwap ? `₹${stock.vwap.toFixed(2)}` : '—';
  el.diagSup.textContent = `₹${stock.support.toFixed(2)}`;
  el.diagRvol.textContent = `${stock.rvol.toFixed(2)}x`;

  if (stock.order_blocks && stock.order_blocks.length > 0) {
    el.diagZonesList.innerHTML = stock.order_blocks.map(z => `
      <div class="zone-chip ${z.type}">
        <strong>${z.type.toUpperCase()}</strong>: ₹${z.low} – ₹${z.high}
      </div>
    `).join('');
  } else {
    el.diagZonesList.innerHTML = '<span style="color:var(--text-dim)">No active order-block zones</span>';
  }
}

async function loadStockCandles(symbol, timeframe) {
  try {
    const res = await publicAPI.getStockCandles(symbol, timeframe);
    if (res && res.candles) {
      state.activeCandles = res.candles;
      renderCandlestickChart(res.candles);
    }
  } catch (err) {
    console.error('Failed to load stock candles:', err);
  }
}

/* --------------------------------------------------------------------------
   Laya AI Intelligence Suite & Local LLM Services
   -------------------------------------------------------------------------- */
async function fetchMarketPulse(refresh = false) {
  try {
    if (el.btnRefreshPulse) el.btnRefreshPulse.style.opacity = '0.5';
    const data = await publicAPI.getAIPulse(refresh);
    if (!data) return;

    if (el.pulseRegimeTag) {
      el.pulseRegimeTag.textContent = data.regime || 'MARKET ACTIVE';
      const reg = (data.regime || '').toLowerCase();
      el.pulseRegimeTag.className = `pulse-regime-tag ${reg.includes('bull') ? 'bullish' : reg.includes('bear') ? 'bearish' : ''}`;
    }

    if (el.pulseSummaryText) {
      el.pulseSummaryText.textContent = data.summary ? data.summary.replace(/\*\*/g, '').replace(/###/g, '') : 'Live NSE Market Pulse active.';
    }

    if (el.pulseModelLabel) {
      el.pulseModelLabel.textContent = data.model || 'Local LLM · gemma4:12b';
    }
  } catch (err) {
    console.warn('Market pulse error:', err);
  } finally {
    if (el.btnRefreshPulse) el.btnRefreshPulse.style.opacity = '1';
  }
}

let analysisPollTimer = null;

async function fetchLayaDeepAnalysis(symbol, timeframe, refresh = false) {
  if (!symbol || !el.btnLayaDeepAnalyze) return;
  el.btnLayaDeepAnalyze.classList.add('loading');
  if (el.btnLayaDeepText) el.btnLayaDeepText.textContent = 'Synthesizing analysis...';

  if (analysisPollTimer) {
    clearInterval(analysisPollTimer);
    analysisPollTimer = null;
  }

  try {
    const data = await publicAPI.getAIAnalyze(symbol, timeframe, refresh, false);
    renderLayaBriefing(data);

    // If background AI is pending, poll every 5s up to 12 times (60s)
    if (data && data.ai_pending) {
      let pollCount = 0;
      analysisPollTimer = setInterval(async () => {
        pollCount++;
        if (pollCount > 12) {
          clearInterval(analysisPollTimer);
          analysisPollTimer = null;
          return;
        }
        try {
          const pollData = await publicAPI.getAIAnalyze(symbol, timeframe, false, true);
          if (pollData && !pollData.ai_pending && pollData.source === 'local_llm') {
            renderLayaBriefing(pollData);
            clearInterval(analysisPollTimer);
            analysisPollTimer = null;
          }
        } catch {}
      }, 5000);
    }
  } catch (err) {
    console.error('Deep analyze error:', err);
  } finally {
    el.btnLayaDeepAnalyze.classList.remove('loading');
    if (el.btnLayaDeepText) el.btnLayaDeepText.textContent = 'Quantitative Briefing';
  }
}

function renderLayaBriefing(data) {
  if (!data || !el.layaLlmBriefingBox || !el.layaLlmBriefContent) return;

  const isLLM = data.source === 'local_llm';
  if (el.layaLlmModelTag) {
    if (isLLM) {
      const modelName = data.model ? data.model.replace(' (Local Ollama)', '').split(' ')[0] : 'laya-ai';
      el.layaLlmModelTag.textContent = `AI - ${modelName}`;
    } else {
      el.layaLlmModelTag.textContent = 'Rules engine';
    }
  }
  if (el.layaLlmSourceTag) {
    el.layaLlmSourceTag.textContent = isLLM ? 'Local LLM' : 'Rules Engine';
    el.layaLlmSourceTag.style.background = isLLM ? 'rgba(16, 185, 129, 0.25)' : 'rgba(100, 116, 139, 0.25)';
    el.layaLlmSourceTag.style.color = isLLM ? '#34d399' : '#94a3b8';
  }

  // Clear container
  el.layaLlmBriefContent.replaceChildren();

  // If Laya decision model provided System 1 classification, display badge
  if (data.laya_decision && data.laya_decision.choice) {
    const layaDec = data.laya_decision;
    const decChip = document.createElement('div');
    const bias = String(layaDec.choice).toUpperCase();
    const isBull = bias === 'BULLISH';
    const isBear = bias === 'BEARISH';
    const bg = isBull ? 'bg-emerald-950/80 text-emerald-300 border-emerald-800/60' : (isBear ? 'bg-rose-950/80 text-rose-300 border-rose-800/60' : 'bg-slate-800/80 text-slate-300 border-slate-700/60');
    decChip.className = `inline-flex items-center gap-1.5 px-2 py-0.5 rounded text-[11px] font-mono font-bold border mb-2 mr-2 ${bg}`;
    const confPct = Math.round((layaDec.confidence || 0) * 100);
    decChip.textContent = `⚡ Laya Decision: ${bias} (${confPct}% conf)`;
    el.layaLlmBriefContent.appendChild(decChip);
  }

  // If pending, show pulsing indicator
  if (data.ai_pending) {
    const pendingChip = document.createElement('div');
    pendingChip.className = 'inline-flex items-center gap-1.5 px-2 py-0.5 rounded text-[11px] font-mono font-bold bg-cyan-950/80 text-cyan-300 border border-cyan-800/60 mb-2 animate-pulse';
    pendingChip.textContent = '⏳ AI generating... (showing rules summary)';
    el.layaLlmBriefContent.appendChild(pendingChip);
  }

  // Render text safely using textContent (XSS prevention)
  const rawText = data.quantitative_briefing || data.institutional_briefing || '';
  const paragraphs = rawText.split('\n\n');
  for (const pText of paragraphs) {
    const pEl = document.createElement('p');
    pEl.className = 'text-xs text-slate-300 leading-relaxed mb-2 font-mono';
    pEl.textContent = pText.replace(/\*\*/g, '');
    el.layaLlmBriefContent.appendChild(pEl);
  }

  // Standing footer
  const footerEl = document.createElement('div');
  footerEl.className = 'text-[10px] text-slate-500 font-mono mt-3 pt-2 border-t border-slate-800 select-none';
  footerEl.textContent = 'Technical analysis for information only. Not investment advice.';
  el.layaLlmBriefContent.appendChild(footerEl);

  el.layaLlmBriefingBox.style.display = 'block';
}

function updateCopilotContext(stock) {
  if (!stock) return;
  if (el.drawerContextStock) {
    el.drawerContextStock.textContent = `${stock.symbol} · ₹${stock.price.toFixed(2)} (${stock.change_pct >= 0 ? '+' : ''}${stock.change_pct.toFixed(2)}%)`;
    el.drawerContextStock.dataset.marginSymbol = stock.symbol;
  }
  const ai = stock.ai_decision;
  if (ai && el.drawerContextAction) {
    el.drawerContextAction.textContent = ai.action;
    el.drawerContextAction.className = `ctx-pill ${ai.signal_type.toLowerCase()}`;
  }
}

function openCopilotDrawer() {
  if (el.layaCopilotDrawer) {
    el.layaCopilotDrawer.classList.add('open');
    el.layaCopilotDrawer.setAttribute('aria-hidden', 'false');
    if (state.selectedStock && state.latestSnapshot?.stocks[state.selectedStock]) {
      updateCopilotContext(state.latestSnapshot.stocks[state.selectedStock]);
    }
    if (el.copilotTextInput) el.copilotTextInput.focus();
  }
}

function closeCopilotDrawer() {
  if (el.layaCopilotDrawer) {
    el.layaCopilotDrawer.classList.remove('open');
    el.layaCopilotDrawer.setAttribute('aria-hidden', 'true');
  }
}

async function sendCopilotMessage(query) {
  const text = (query || '').trim();
  if (!text || !el.drawerChatArea) return;

  const userMsg = document.createElement('div');
  userMsg.className = 'chat-msg user-msg';
  userMsg.innerHTML = `
    <div class="chat-avatar">👤</div>
    <div class="chat-bubble"><p>${escapeHtml(text)}</p></div>
  `;
  el.drawerChatArea.appendChild(userMsg);

  const loadingMsg = document.createElement('div');
  loadingMsg.className = 'chat-msg ai-msg loading-msg';
  const loadAvatar = document.createElement('div');
  loadAvatar.className = 'chat-avatar';
  loadAvatar.textContent = '🤖';
  const loadBubble = document.createElement('div');
  loadBubble.className = 'chat-bubble';
  const loadP = document.createElement('p');
  const loadEm = document.createElement('em');
  loadEm.textContent = 'Laya AI is evaluating live telemetry & reasoning via local LLM...';
  loadP.appendChild(loadEm);
  loadBubble.appendChild(loadP);
  loadingMsg.appendChild(loadAvatar);
  loadingMsg.appendChild(loadBubble);
  el.drawerChatArea.appendChild(loadingMsg);
  el.drawerChatArea.scrollTop = el.drawerChatArea.scrollHeight;

  try {
    const res = await publicAPI.sendAIChat(text, state.selectedStock);
    loadingMsg.remove();

    const aiMsg = document.createElement('div');
    aiMsg.className = 'chat-msg ai-msg';
    const avatar = document.createElement('div');
    avatar.className = 'chat-avatar';
    avatar.textContent = '🤖';
    const bubble = document.createElement('div');
    bubble.className = 'chat-bubble';

    if (res.error) {
      const errP = document.createElement('p');
      errP.style.color = 'var(--bear-primary)';
      errP.textContent = `⚠️ ${res.detail || res.error || 'Local LLM connection busy or timed out.'}`;
      bubble.appendChild(errP);
    } else {
      const textP = document.createElement('p');
      textP.textContent = res.reply || 'Analysis completed.';
      bubble.appendChild(textP);

      const metaSpan = document.createElement('span');
      metaSpan.style.cssText = 'display:block;font-size:0.62rem;color:#64748b;margin-top:6px;';
      metaSpan.textContent = `${res.model || 'gemma4:12b (Local)'} • ${res.source || 'Laya AI'}`;
      bubble.appendChild(metaSpan);

      const footerSpan = document.createElement('span');
      footerSpan.style.cssText = 'display:block;font-size:0.58rem;color:#475569;margin-top:4px;';
      footerSpan.textContent = 'Technical analysis for information only. Not investment advice.';
      bubble.appendChild(footerSpan);
    }

    aiMsg.appendChild(avatar);
    aiMsg.appendChild(bubble);
    el.drawerChatArea.appendChild(aiMsg);
    el.drawerChatArea.scrollTop = el.drawerChatArea.scrollHeight;
  } catch (err) {
    loadingMsg.remove();
    const errBubble = document.createElement('div');
    errBubble.className = 'chat-msg ai-msg';
    const avatar = document.createElement('div');
    avatar.className = 'chat-avatar';
    avatar.textContent = '🤖';
    const bubble = document.createElement('div');
    bubble.className = 'chat-bubble';
    const p = document.createElement('p');
    p.style.color = 'var(--bear-primary)';
    p.textContent = 'Local LLM connection busy. Algorithmic trade signals remain live.';
    bubble.appendChild(p);
    errBubble.appendChild(avatar);
    errBubble.appendChild(bubble);
    el.drawerChatArea.appendChild(errBubble);
  }
}

function escapeHtml(str) {
  return str.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;');
}

function renderCandlestickChart(candles) {
  if (!candles || candles.length === 0) return;

  const W = 850;
  const H = 420;
  const padLeft = 20;
  const padRight = 65;
  const padTop = 20;
  const padBottom = 75; // Space for volume subchart

  const chartW = W - padLeft - padRight;
  const priceH = H - padTop - padBottom - 10;
  const volH = 55;
  const volTop = H - padBottom + 10;

  // Zoom slicing
  const visibleCount = Math.max(15, Math.floor(candles.length / state.zoomLevel));
  const visibleCandles = candles.slice(-visibleCount);

  // Price Extrema
  let minP = Math.min(...visibleCandles.map(c => c.l));
  let maxP = Math.max(...visibleCandles.map(c => c.h));
  const pRange = (maxP - minP) || 1;
  minP -= pRange * 0.05;
  maxP += pRange * 0.05;

  function toY(p) {
    return padTop + priceH - ((p - minP) / (maxP - minP)) * priceH;
  }

  // Volume Extrema
  const maxV = Math.max(...visibleCandles.map(c => c.v || 0)) || 1;
  function toVolY(v) {
    return volTop + volH - (v / maxV) * volH;
  }

  const n = visibleCandles.length;
  const barW = Math.max(2, (chartW / n) * 0.7);
  const xStep = chartW / n;

  // 1. Grid & Price Labels
  let gridHtml = '';
  const priceSteps = 5;
  for (let i = 0; i <= priceSteps; i++) {
    const p = minP + (i / priceSteps) * (maxP - minP);
    const y = toY(p);
    gridHtml += `
      <line x1="${padLeft}" y1="${y}" x2="${W - padRight}" y2="${y}" stroke="var(--border-subtle)" stroke-dasharray="2,3" stroke-width="0.8"/>
      <text x="${W - padRight + 6}" y="${y + 3}" fill="var(--text-dim)" font-family="var(--font-mono)" font-size="9.5">₹${p.toFixed(1)}</text>
    `;
  }
  el.chartGrid.innerHTML = gridHtml;

  // 2. Order Block Zones Overlays
  let zonesHtml = '';
  const stock = state.latestSnapshot?.stocks[state.selectedStock];
  if (el.chkZones.checked && stock?.order_blocks) {
    for (const z of stock.order_blocks) {
      if (z.high >= minP && z.low <= maxP) {
        const yTop = toY(z.high);
        const yBottom = toY(z.low);
        const zoneH = Math.max(2, yBottom - yTop);
        const fillCol = z.type === 'bullish' ? 'rgba(56, 189, 248, 0.15)' : 'rgba(244, 63, 94, 0.15)';
        const strokeCol = z.type === 'bullish' ? 'rgba(56, 189, 248, 0.5)' : 'rgba(244, 63, 94, 0.5)';
        zonesHtml += `
          <rect x="${padLeft}" y="${yTop}" width="${chartW}" height="${zoneH}" fill="${fillCol}" stroke="${strokeCol}" stroke-width="1" stroke-dasharray="3,3"/>
          <text x="${padLeft + 6}" y="${yTop + 11}" fill="${strokeCol}" font-family="var(--font-mono)" font-size="8" font-weight="700">${z.type.toUpperCase()} ZONE</text>
        `;
      }
    }
  }
  el.chartZones.innerHTML = zonesHtml;

  // 2b. AI Trade Levels Overlays (SL, Target 1, Target 2, Entry)
  let aiLevelsHtml = '';
  if (el.chkAiLevels && el.chkAiLevels.checked && stock?.ai_decision) {
    const ai = stock.ai_decision;
    // Stop Loss Line (Red dashed)
    if (ai.stop_loss >= minP && ai.stop_loss <= maxP) {
      const slY = toY(ai.stop_loss);
      aiLevelsHtml += `
        <line x1="${padLeft}" y1="${slY}" x2="${W - padRight}" y2="${slY}" stroke="#ef4444" stroke-width="1.4" stroke-dasharray="3,3"/>
        <text x="${W - padRight + 6}" y="${slY + 3}" fill="#ef4444" font-family="var(--font-mono)" font-size="8.5" font-weight="700">SL ₹${ai.stop_loss}</text>
      `;
    }
    // Target 1 Line (Green dashed)
    if (ai.target_1 >= minP && ai.target_1 <= maxP) {
      const t1Y = toY(ai.target_1);
      aiLevelsHtml += `
        <line x1="${padLeft}" y1="${t1Y}" x2="${W - padRight}" y2="${t1Y}" stroke="#22c55e" stroke-width="1.4" stroke-dasharray="3,3"/>
        <text x="${W - padRight + 6}" y="${t1Y + 3}" fill="#22c55e" font-family="var(--font-mono)" font-size="8.5" font-weight="700">T1 ₹${ai.target_1}</text>
      `;
    }
    // Target 2 Line (Emerald dashed)
    if (ai.target_2 >= minP && ai.target_2 <= maxP) {
      const t2Y = toY(ai.target_2);
      aiLevelsHtml += `
        <line x1="${padLeft}" y1="${t2Y}" x2="${W - padRight}" y2="${t2Y}" stroke="#10b981" stroke-width="1.2" stroke-dasharray="2,2"/>
        <text x="${W - padRight + 6}" y="${t2Y + 3}" fill="#10b981" font-family="var(--font-mono)" font-size="8.5" font-weight="700">T2 ₹${ai.target_2}</text>
      `;
    }
    // Entry Midpoint Line (Cyan dashed)
    const entryMid = (ai.entry_low + ai.entry_high) / 2;
    if (entryMid >= minP && entryMid <= maxP) {
      const entY = toY(entryMid);
      aiLevelsHtml += `
        <line x1="${padLeft}" y1="${entY}" x2="${W - padRight}" y2="${entY}" stroke="#38bdf8" stroke-width="1.2" stroke-dasharray="4,3"/>
        <text x="${W - padRight + 6}" y="${entY + 3}" fill="#38bdf8" font-family="var(--font-mono)" font-size="8.5" font-weight="700">ENTRY ₹${entryMid.toFixed(1)}</text>
      `;
    }
  }
  if (el.chartAiLevels) el.chartAiLevels.innerHTML = aiLevelsHtml;

  // 3. Candlesticks & Volume Bars
  let candleHtml = '';
  let volHtml = '';

  for (let i = 0; i < n; i++) {
    const c = visibleCandles[i];
    const x = padLeft + i * xStep + xStep / 2;
    const isUp = c.c >= c.o;
    const col = isUp ? 'var(--bull-primary)' : 'var(--bear-primary)';

    const yO = toY(c.o);
    const yC = toY(c.c);
    const yH = toY(c.h);
    const yL = toY(c.l);

    const bodyY = Math.min(yO, yC);
    const bodyH = Math.max(1.5, Math.abs(yC - yO));

    // Wick
    candleHtml += `<line x1="${x}" y1="${yH}" x2="${x}" y2="${yL}" stroke="${col}" stroke-width="1.2"/>`;
    // Body
    candleHtml += `<rect x="${x - barW / 2}" y="${bodyY}" width="${barW}" height="${bodyH}" fill="${col}" rx="1"/>`;

    // Volume bar
    const vY = toVolY(c.v);
    const vH = volTop + volH - vY;
    volHtml += `<rect x="${x - barW / 2}" y="${vY}" width="${barW}" height="${vH}" fill="${col}" opacity="0.35"/>`;
  }
  el.chartCandles.innerHTML = candleHtml;
  el.chartVolume.innerHTML = volHtml;

  // 4. Overlays: Fast EMA, Slow EMA, VWAP, Support / Resistance
  let indHtml = '';
  if (el.chkEma.checked) {
    // Fast EMA 9
    indHtml += renderIndicatorPath(visibleCandles, 9, padLeft, xStep, toY, '#38bdf8');
    // Slow EMA 21
    indHtml += renderIndicatorPath(visibleCandles, 21, padLeft, xStep, toY, '#f59e0b');
  }

  if (el.chkVwap.checked && stock?.vwap && stock.vwap >= minP && stock.vwap <= maxP) {
    const vwapY = toY(stock.vwap);
    indHtml += `
      <line x1="${padLeft}" y1="${vwapY}" x2="${W - padRight}" y2="${vwapY}" stroke="#eab308" stroke-width="1.5" stroke-dasharray="4,2"/>
      <text x="${W - padRight + 6}" y="${vwapY + 3}" fill="#eab308" font-family="var(--font-mono)" font-size="9">VWAP</text>
    `;
  }

  if (el.chkSupRes.checked && stock) {
    if (stock.support >= minP && stock.support <= maxP) {
      const supY = toY(stock.support);
      indHtml += `
        <line x1="${padLeft}" y1="${supY}" x2="${W - padRight}" y2="${supY}" stroke="#22c55e" stroke-width="1.2" stroke-dasharray="2,2"/>
        <text x="${W - padRight + 6}" y="${supY + 3}" fill="#22c55e" font-family="var(--font-mono)" font-size="9">SUP</text>
      `;
    }
    if (stock.resistance >= minP && stock.resistance <= maxP) {
      const resY = toY(stock.resistance);
      indHtml += `
        <line x1="${padLeft}" y1="${resY}" x2="${W - padRight}" y2="${resY}" stroke="#f43f5e" stroke-width="1.2" stroke-dasharray="2,2"/>
        <text x="${W - padRight + 6}" y="${resY + 3}" fill="#f43f5e" font-family="var(--font-mono)" font-size="9">RES</text>
      `;
    }
  }

  el.chartIndicators.innerHTML = indHtml;
}

function renderIndicatorPath(candles, period, padLeft, xStep, toY, color) {
  if (candles.length < 2) return '';
  const alpha = 2.0 / (period + 1.0);
  let ema = candles[0].c;
  const points = [];

  for (let i = 0; i < candles.length; i++) {
    ema = alpha * candles[i].c + (1.0 - alpha) * ema;
    const x = padLeft + i * xStep + xStep / 2;
    points.push(`${x.toFixed(1)},${toY(ema).toFixed(1)}`);
  }

  return `<polyline fill="none" stroke="${color}" stroke-width="1.4" points="${points.join(' ')}"/>`;
}

/* --------------------------------------------------------------------------
   Event Listeners & Interaction Binding
   -------------------------------------------------------------------------- */
function bindEvents() {
  // Timeframe buttons (Header)
  document.querySelectorAll('.timeframe-group .tf-btn').forEach(btn => {
    btn.addEventListener('click', async () => {
      const tf = parseInt(btn.getAttribute('data-tf'));
      state.timeframe = tf;
      updateTimeframeButtons(tf);
      await fetchMarketData();
      if (state.selectedStock) loadStockCandles(state.selectedStock, tf);
    });
  });

  // Modal timeframe buttons
  document.querySelectorAll('#modalTfGroup .tf-btn').forEach(btn => {
    btn.addEventListener('click', async () => {
      const tf = parseInt(btn.getAttribute('data-tf'));
      document.querySelectorAll('#modalTfGroup .tf-btn').forEach(b => b.classList.toggle('active', b === btn));
      state.timeframe = tf;
      if (state.selectedStock) loadStockCandles(state.selectedStock, tf);
    });
  });

  // Provisional vs Confirmed Mode
  el.btnModeProvisional.addEventListener('click', () => {
    state.signalMode = 'provisional';
    el.btnModeProvisional.classList.add('active');
    el.btnModeConfirmed.classList.remove('active');
    fetchMarketData();
  });

  el.btnModeConfirmed.addEventListener('click', () => {
    state.signalMode = 'confirmed';
    el.btnModeConfirmed.classList.add('active');
    el.btnModeProvisional.classList.remove('active');
    fetchMarketData();
  });

  // Pause / Resume Feed
  el.btnPause.addEventListener('click', () => {
    state.isPaused = !state.isPaused;
    if (state.isPaused) {
      el.pauseIconPath.setAttribute('d', 'M8 5v14l11-7z'); // Play icon
      el.feedModeLabel.textContent = 'PAUSED';
      el.feedPill.style.borderColor = 'var(--accent-gold)';
    } else {
      el.pauseIconPath.setAttribute('d', 'M6 4h4v16H6zm8 0h4v16h-4z'); // Pause icon
      el.feedModeLabel.textContent = 'LIVE (NSE & BSE)';
      el.feedPill.style.borderColor = 'var(--border-subtle)';
    }
  });

  // Motion toggle
  el.btnMotion.addEventListener('click', () => {
    state.motionEnabled = !state.motionEnabled;
    el.btnMotion.style.color = state.motionEnabled ? 'var(--bull-primary)' : 'var(--text-dim)';
  });

  // Theme toggle
  el.btnTheme.addEventListener('click', () => {
    state.theme = state.theme === 'dark' ? 'light' : 'dark';
    document.documentElement.setAttribute('data-theme', state.theme);
    localStorage.setItem('marketscope_theme', state.theme);
  });

  // Export CSV
  el.btnExport.addEventListener('click', () => {
    window.location.href = `/api/export?timeframe=${state.timeframe}`;
  });

  // Methodology Modal
  el.btnMethodology.addEventListener('click', () => el.methodologyModal.style.display = 'flex');
  el.linkMethodologyFooter.addEventListener('click', (e) => {
    e.preventDefault();
    el.methodologyModal.style.display = 'flex';
  });
  el.btnCloseMethodologyModal.addEventListener('click', () => el.methodologyModal.style.display = 'none');

  // Settings Modal
  el.btnSettings.addEventListener('click', () => el.settingsModal.style.display = 'flex');
  el.btnCloseSettingsModal.addEventListener('click', () => el.settingsModal.style.display = 'none');

  // Schedule Modal
  const openScheduleModal = () => {
    if (el.scheduleModal) el.scheduleModal.style.display = 'flex';
  };
  const closeScheduleModal = () => {
    if (el.scheduleModal) el.scheduleModal.style.display = 'none';
  };
  if (el.marketCountdownPill) el.marketCountdownPill.addEventListener('click', openScheduleModal);
  if (el.summaryMarketSession) el.summaryMarketSession.addEventListener('click', openScheduleModal);
  if (el.btnCloseScheduleModal) el.btnCloseScheduleModal.addEventListener('click', closeScheduleModal);

  // Close chart modal
  el.btnCloseChartModal.addEventListener('click', closeChartModal);

  // Close modals on escape key or clicking backdrop
  window.addEventListener('keydown', (e) => {
    if (e.key === 'Escape') {
      closeChartModal();
      el.methodologyModal.style.display = 'none';
      el.settingsModal.style.display = 'none';
      if (el.scheduleModal) el.scheduleModal.style.display = 'none';
      hideStockHUD();
    }
  });

  [el.chartModal, el.methodologyModal, el.settingsModal, el.scheduleModal].filter(Boolean).forEach(modal => {
    modal.addEventListener('click', (e) => {
      if (e.target === modal) modal.style.display = 'none';
    });
  });

  // Trajectories Canvas Hover / Click Events
  el.canvasWrapper.addEventListener('mousemove', (e) => {
    const target = e.target.closest('[data-symbol]');
    if (target) {
      const sym = target.getAttribute('data-symbol');
      showStockHUD(sym, e.clientX, e.clientY);
    } else {
      hideStockHUD();
    }
  });

  el.canvasWrapper.addEventListener('mouseleave', hideStockHUD);

  el.canvasWrapper.addEventListener('click', (e) => {
    const target = e.target.closest('[data-symbol]');
    if (target) {
      const sym = target.getAttribute('data-symbol');
      openChartModal(sym);
    }
  });

  // Signal Cards and Spotlight Click & Hover
  document.addEventListener('mouseover', (e) => {
    const card = e.target.closest('.signal-card, .spotlight-card');
    if (card) {
      const sym = card.getAttribute('data-symbol');
      state.hoveredStock = sym;
      highlightStockEverywhere(sym);
    }
  });

  document.addEventListener('mouseout', (e) => {
    const card = e.target.closest('.signal-card, .spotlight-card');
    if (card && !el.canvasWrapper.contains(e.relatedTarget)) {
      highlightStockEverywhere(null);
    }
  });

  document.addEventListener('click', (e) => {
    const card = e.target.closest('.signal-card, .spotlight-card');
    if (card) {
      const sym = card.getAttribute('data-symbol');
      openChartModal(sym);
      return;
    }

    const chartBtn = e.target.closest('.btn-open-chart');
    if (chartBtn) {
      const sym = chartBtn.getAttribute('data-symbol');
      openChartModal(sym);
      return;
    }

    const starBtn = e.target.closest('.star-btn');
    if (starBtn) {
      const sym = starBtn.getAttribute('data-symbol');
      toggleWatchlist(sym);
      renderScannerTable(state.latestSnapshot?.stocks);
      return;
    }

    const secChip = e.target.closest('.sector-chip');
    if (secChip) {
      const sec = secChip.getAttribute('data-sector');
      el.searchInput.value = sec;
      state.scannerSearch = sec;
      renderScannerTable(state.latestSnapshot?.stocks);
    }
  });

  // Scanner Search & Master Database Autocomplete (2,600+ Stocks across NSE/BSE)
  let searchDebounceTimer = null;
  el.searchInput.addEventListener('input', (e) => {
    const val = e.target.value.trim();
    state.scannerSearch = val;
    renderScannerTable(state.latestSnapshot?.stocks);

    if (searchDebounceTimer) clearTimeout(searchDebounceTimer);
    if (!el.searchDropdown) return;

    if (val.length < 2) {
      el.searchDropdown.style.display = 'none';
      return;
    }

    searchDebounceTimer = setTimeout(async () => {
      try {
        const res = await publicAPI.searchStockDatabase(val, 'ALL', 12);
        if (!res || !res.stocks || res.stocks.length === 0) {
          el.searchDropdown.innerHTML = '<div class="search-dropdown-header">No matches in NSE/BSE database</div>';
          el.searchDropdown.style.display = 'block';
          return;
        }

        let html = `<div class="search-dropdown-header">NSE & BSE Master Database (${res.total_matches} matches)</div>`;
        html += res.stocks.map(s => `
          <div class="search-dropdown-item" data-sym="${s.symbol}">
            <div class="search-item-left">
              <span class="search-item-sym" data-margin-symbol="${escapeHtml(s.symbol)}">${s.symbol}</span>
              <span class="search-item-name" data-margin-symbol="${escapeHtml(s.symbol)}" title="${escapeHtml(s.name || s.symbol)}">${escapeHtml(s.name || s.symbol)}</span>
            </div>
            <div class="search-item-right">
              <span class="search-badge-sec">${s.sector}</span>
              <span class="search-badge-exch">${s.exchange || 'NSE/BSE'}</span>
              <button class="search-item-track-btn" data-sym="${s.symbol}">+ Track Live</button>
            </div>
          </div>
        `).join('');

        el.searchDropdown.innerHTML = html;
        el.searchDropdown.style.display = 'block';
      } catch (err) {
        console.warn('Autocomplete search error:', err);
      }
    }, 200);
  });

  // Clicking an autocomplete item to track & open live chart
  if (el.searchDropdown) {
    el.searchDropdown.addEventListener('click', async (e) => {
      const item = e.target.closest('[data-sym]');
      if (!item) return;
      const sym = item.getAttribute('data-sym');
      if (!sym) return;

      el.searchDropdown.style.display = 'none';
      el.searchInput.value = '';
      state.scannerSearch = '';

      // Add to live scraper tracking
      await publicAPI.trackStock(sym);
      await fetchMarketData();
      openChartModal(sym);
    });
  }

  // Click outside search-box closes dropdown
  document.addEventListener('click', (e) => {
    if (el.searchDropdown && !e.target.closest('.search-box')) {
      el.searchDropdown.style.display = 'none';
    }
  });

  // Global Header Search & 2,600+ Stock Master Database Autocomplete
  const headerSearchInput = document.getElementById('headerSearchInput');
  const headerSearchDropdown = document.getElementById('headerSearchDropdown');
  let headerSearchTimer = null;

  if (headerSearchInput && headerSearchDropdown) {
    headerSearchInput.addEventListener('input', (e) => {
      const val = e.target.value.trim();
      if (headerSearchTimer) clearTimeout(headerSearchTimer);

      if (val.length < 2) {
        headerSearchDropdown.style.display = 'none';
        return;
      }

      headerSearchTimer = setTimeout(async () => {
        try {
          const res = await publicAPI.searchStockDatabase(val, 'ALL', 10);
          if (!res || !res.stocks || res.stocks.length === 0) {
            headerSearchDropdown.innerHTML = '<div class="search-dropdown-header">No matches in NSE/BSE database</div>';
            headerSearchDropdown.style.display = 'block';
            return;
          }

          let html = `<div class="search-dropdown-header">Master Database (${res.total_matches} matches)</div>`;
          html += res.stocks.map(s => `
            <div class="search-dropdown-item" data-sym="${s.symbol}">
              <div class="search-item-left">
                <span class="search-item-sym" data-margin-symbol="${escapeHtml(s.symbol)}">${s.symbol}</span>
                <span class="search-item-name" data-margin-symbol="${escapeHtml(s.symbol)}" title="${escapeHtml(s.name || s.symbol)}">${escapeHtml(s.name || s.symbol)}</span>
              </div>
              <div class="search-item-right">
                <span class="search-badge-sec">${s.sector}</span>
                <span class="search-badge-exch">${s.exchange || 'NSE/BSE'}</span>
                <button class="search-item-track-btn" data-sym="${s.symbol}">+ Track Live</button>
              </div>
            </div>
          `).join('');

          headerSearchDropdown.innerHTML = html;
          headerSearchDropdown.style.display = 'block';
        } catch (err) {
          console.warn('Header search error:', err);
        }
      }, 150);
    });

    headerSearchDropdown.addEventListener('click', async (e) => {
      const trackBtn = e.target.closest('.search-item-track-btn');
      const item = e.target.closest('.search-dropdown-item');
      if (!item) return;

      const sym = item.getAttribute('data-sym');
      if (!sym) return;

      if (trackBtn) {
        e.stopPropagation();
        trackBtn.textContent = 'Tracking...';
        await publicAPI.trackStock(sym);
        trackBtn.textContent = '✓ Active';
        await fetchMarketData();
        return;
      }

      headerSearchDropdown.style.display = 'none';
      headerSearchInput.value = '';
      await publicAPI.trackStock(sym);
      await fetchMarketData();
      openChartModal(sym);
    });

    // Keyboard shortcut: Ctrl+K or '/' focuses header search
    window.addEventListener('keydown', (e) => {
      if ((e.ctrlKey && e.key === 'k') || (e.key === '/' && document.activeElement.tagName !== 'INPUT')) {
        e.preventDefault();
        headerSearchInput.focus();
        headerSearchInput.select();
      }
    });

    document.addEventListener('click', (e) => {
      if (!e.target.closest('.header-search-box')) {
        headerSearchDropdown.style.display = 'none';
      }
    });
  }

  document.querySelectorAll('.filter-tabs .tab-btn').forEach(btn => {
    btn.addEventListener('click', () => {
      document.querySelectorAll('.filter-tabs .tab-btn').forEach(b => b.classList.toggle('active', b === btn));
      state.scannerFilter = btn.getAttribute('data-filter');
      renderScannerTable(state.latestSnapshot?.stocks);
    });
  });

  // Scanner Table Sort Headers
  document.querySelectorAll('.market-table th.sortable').forEach(th => {
    th.addEventListener('click', () => {
      const col = th.getAttribute('data-sort');
      if (state.scannerSort.col === col) {
        state.scannerSort.desc = !state.scannerSort.desc;
      } else {
        state.scannerSort.col = col;
        state.scannerSort.desc = true;
      }
      renderScannerTable(state.latestSnapshot?.stocks);
    });
  });

  // Candlestick Chart Overlays & Zoom
  [el.chkAiLevels, el.chkEma, el.chkVwap, el.chkSupRes, el.chkZones].forEach(chk => {
    if (chk) {
      chk.addEventListener('change', () => {
        if (state.activeCandles) renderCandlestickChart(state.activeCandles);
      });
    }
  });

  el.btnZoomIn.addEventListener('click', () => {
    state.zoomLevel = Math.min(4.0, state.zoomLevel * 1.3);
    if (state.activeCandles) renderCandlestickChart(state.activeCandles);
  });

  el.btnZoomOut.addEventListener('click', () => {
    state.zoomLevel = Math.max(0.5, state.zoomLevel / 1.3);
    if (state.activeCandles) renderCandlestickChart(state.activeCandles);
  });

  el.btnZoomReset.addEventListener('click', () => {
    state.zoomLevel = 1.0;
    if (state.activeCandles) renderCandlestickChart(state.activeCandles);
  });

  // Settings Form
  el.settingsForm.addEventListener('submit', async (e) => {
    e.preventDefault();
    if (!state.user || state.user.role !== 'owner') {
      alert('Only authenticated owners can persist server configuration. Use "Preview Sample" for local test.');
      return;
    }

    const payload = {
      fast_ema_period: parseInt(document.getElementById('cfgFastEma').value),
      slow_ema_period: parseInt(document.getElementById('cfgSlowEma').value),
      rsi_period: parseInt(document.getElementById('cfgRsi').value),
      score_threshold: parseInt(document.getElementById('cfgThreshold').value),
      refresh_interval_ms: parseInt(document.getElementById('cfgRefresh').value),
    };

    try {
      const res = await fetch('/api/config', {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
          'X-CSRF-Token': state.csrfToken || '',
        },
        body: JSON.stringify(payload),
      });
      const data = await res.json();
      if (res.ok) {
        alert('Configuration saved successfully!');
        el.settingsModal.style.display = 'none';
        fetchMarketData();
      } else {
        alert(`Error: ${data.error}`);
      }
    } catch (err) {
      alert(`Network error: ${err.message}`);
    }
  });

  el.btnPreviewSettings.addEventListener('click', async () => {
    const payload = {
      fast_ema_period: parseInt(document.getElementById('cfgFastEma').value),
      slow_ema_period: parseInt(document.getElementById('cfgSlowEma').value),
      rsi_period: parseInt(document.getElementById('cfgRsi').value),
      score_threshold: parseInt(document.getElementById('cfgThreshold').value),
    };
    try {
      const res = await fetch('/api/config/preview', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(payload),
      });
      const data = await res.json();
      if (res.ok && data.snapshot) {
        state.latestSnapshot = data.snapshot;
        renderTrajectories(data.snapshot.trajectories, data.snapshot.stocks);
        renderSignalTiles(data.snapshot.signal_tiles);
        renderSpotlight(data.snapshot.spotlight);
        renderScannerTable(data.snapshot.stocks);
        el.settingsModal.style.display = 'none';
      }
    } catch (err) {
      console.warn('Preview fallback:', err);
    }
  });

  // Owner Login Form
  el.loginForm.addEventListener('submit', async (e) => {
    e.preventDefault();
    const email = document.getElementById('loginEmail').value.trim();
    const password = document.getElementById('loginPassword').value;
    const totp_code = document.getElementById('loginTotp').value.trim();

    try {
      const res = await fetch('/api/login', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ email, password, totp_code }),
      });
      const data = await res.json();
      if (res.ok) {
        state.user = data.user;
        state.csrfToken = data.csrf_token;
        updateAuthUI();
        alert('Signed in as owner!');
      } else {
        alert(data.error || 'Authentication failed');
      }
    } catch (err) {
      alert(`Login failed: ${err.message}`);
    }
  });

  el.btnLogout.addEventListener('click', async () => {
    try {
      await fetch('/api/logout', { method: 'POST' });
    } catch {}
    state.user = null;
    state.csrfToken = null;
    updateAuthUI();
  });

  // Laya AI Pulse Refresh
  if (el.btnRefreshPulse) {
    el.btnRefreshPulse.addEventListener('click', () => fetchMarketPulse(true));
  }

  // Laya AI Deep Analyze in Chart Modal
  if (el.btnLayaDeepAnalyze) {
    el.btnLayaDeepAnalyze.addEventListener('click', () => {
      if (state.selectedStock) {
        fetchLayaDeepAnalysis(state.selectedStock, state.timeframe, true);
      }
    });
  }

  // Laya AI Copilot Drawer & FAB
  if (el.btnOpenCopilot) {
    el.btnOpenCopilot.addEventListener('click', openCopilotDrawer);
  }
  if (el.layaFab) {
    el.layaFab.addEventListener('click', openCopilotDrawer);
  }
  if (el.btnCloseCopilotDrawer) {
    el.btnCloseCopilotDrawer.addEventListener('click', closeCopilotDrawer);
  }

  // Copilot Form Submit
  if (el.copilotForm) {
    el.copilotForm.addEventListener('submit', (e) => {
      e.preventDefault();
      const val = el.copilotTextInput.value;
      if (val) {
        el.copilotTextInput.value = '';
        sendCopilotMessage(val);
      }
    });
  }

  // Quick Chips in Copilot Drawer
  document.querySelectorAll('.drawer-chips .quick-chip').forEach(chip => {
    chip.addEventListener('click', () => {
      const q = chip.getAttribute('data-q');
      if (q) sendCopilotMessage(q);
    });
  });
}

function updateTimeframeButtons(tf) {
  document.querySelectorAll('.timeframe-group .tf-btn').forEach(btn => {
    btn.classList.toggle('active', parseInt(btn.getAttribute('data-tf')) === tf);
  });
}

function toggleWatchlist(symbol) {
  if (state.watchlist.has(symbol)) {
    state.watchlist.delete(symbol);
  } else {
    state.watchlist.add(symbol);
  }
  localStorage.setItem('marketscope_watchlist', JSON.stringify(Array.from(state.watchlist)));
}

function updateAuthUI() {
  if (state.user) {
    el.authStatusText.textContent = `Signed in: ${state.user.email} (${state.user.role.toUpperCase()})`;
    el.settingsBtnLabel.textContent = state.user.role === 'owner' ? 'Owner' : 'Account';
    el.loginForm.style.display = 'none';
    el.btnLogout.style.display = 'inline-flex';
  } else {
    el.authStatusText.textContent = 'Signed out · Live Scraper Feed Active (NSE & BSE)';
    el.settingsBtnLabel.textContent = 'Console';
    el.loginForm.style.display = 'flex';
    el.btnLogout.style.display = 'none';
  }
}

let marketScheduleData = null;

function formatCountdown(totalSec) {
  totalSec = Math.max(0, Math.floor(totalSec));
  const days = Math.floor(totalSec / 86400);
  const hours = Math.floor((totalSec % 86400) / 3600);
  const minutes = Math.floor((totalSec % 3600) / 60);
  const seconds = totalSec % 60;
  const pad = (n) => String(n).padStart(2, '0');

  if (days > 0) {
    return `${days}d ${pad(hours)}h ${pad(minutes)}m ${pad(seconds)}s`;
  }
  return `${pad(hours)}h ${pad(minutes)}m ${pad(seconds)}s`;
}

function computeMarketSchedule(serverData) {
  const now = new Date();
  // Get time in IST (UTC + 5:30)
  const utc = now.getTime() + (now.getTimezoneOffset() * 60000);
  const ist = new Date(utc + (3600000 * 5.5));

  const pad = (n) => String(n).padStart(2, '0');
  const h = pad(ist.getHours());
  const m = pad(ist.getMinutes());
  const s = pad(ist.getSeconds());
  const istClockStr = `${h}:${m}:${s} IST`;

  // If serverData is provided with target_timestamp, calculate accurate time from target_timestamp
  if (serverData && serverData.target_timestamp) {
    const currentEpoch = Math.floor(now.getTime() / 1000);
    const targetEpoch = serverData.target_timestamp;
    const remainingSec = Math.max(0, targetEpoch - currentEpoch);
    const countdownFormatted = formatCountdown(remainingSec);
    const action = serverData.action || (serverData.is_open ? 'closes' : 'opens');

    return {
      isOpen: !!serverData.is_open,
      state: serverData.state || (serverData.is_open ? 'OPEN' : 'CLOSED'),
      statusBadge: serverData.status_badge || (serverData.is_open ? 'LIVE OPEN' : 'CLOSED'),
      action: action,
      remainingSeconds: remainingSec,
      countdownFormatted: countdownFormatted,
      phrase: `${action.charAt(0).toUpperCase() + action.slice(1)} in ${countdownFormatted}`,
      targetTimeIst: serverData.target_time_ist || '',
      sessionDescription: serverData.session_description || '',
      currentIstTime: istClockStr,
    };
  }

  // Pure Client-side fallback calculation if server data not yet loaded or offline
  const dayOfWeek = ist.getDay(); // 0 = Sun, 1 = Mon, ..., 5 = Fri, 6 = Sat
  const timeSec = ist.getHours() * 3600 + ist.getMinutes() * 60 + ist.getSeconds();
  const isWeekend = (dayOfWeek === 0 || dayOfWeek === 6);

  let state = "CLOSED";
  let statusBadge = "CLOSED";
  let action = "opens";
  let targetIst = new Date(ist);
  let sessionDesc = "Exchange closed for the session.";

  if (!isWeekend) {
    if (timeSec < 32400) { // 00:00 - 08:59:59
      state = "CLOSED";
      statusBadge = "PRE-MKT CLOSED";
      action = "opens";
      targetIst.setHours(9, 15, 0, 0);
      sessionDesc = "Pre-market order discovery opens at 09:00 IST. Normal trading begins at 09:15 IST.";
    } else if (timeSec < 33300) { // 09:00 - 09:14:59
      state = "PRE_OPEN";
      statusBadge = "PRE-OPEN";
      action = "opens";
      targetIst.setHours(9, 15, 0, 0);
      sessionDesc = "Pre-open order discovery & equilibrium matching in progress. Normal trading opens at 09:15 IST.";
    } else if (timeSec < 55800) { // 09:15 - 15:30:00
      state = "OPEN";
      statusBadge = "LIVE OPEN";
      action = "closes";
      targetIst.setHours(15, 30, 0, 0);
      sessionDesc = "Regular trading session is actively underway across NSE & BSE.";
    } else if (timeSec < 57600) { // 15:30 - 16:00:00
      state = "POST_CLOSE";
      statusBadge = "POST-CLOSE";
      action = "opens";
      const addDays = (dayOfWeek === 5) ? 3 : 1;
      targetIst.setDate(targetIst.getDate() + addDays);
      targetIst.setHours(9, 15, 0, 0);
      sessionDesc = "Post-market settlement session. Next session opens tomorrow at 09:15 IST.";
    } else { // >= 16:00
      state = "CLOSED";
      statusBadge = "AFTER HOURS";
      action = "opens";
      const addDays = (dayOfWeek === 5) ? 3 : 1;
      targetIst.setDate(targetIst.getDate() + addDays);
      targetIst.setHours(9, 15, 0, 0);
      sessionDesc = "Exchange closed for the day. Next regular session opens at 09:15 IST.";
    }
  } else {
    state = "WEEKEND";
    statusBadge = "WEEKEND";
    action = "opens";
    const daysToMonday = (dayOfWeek === 6) ? 2 : 1;
    targetIst.setDate(targetIst.getDate() + daysToMonday);
    targetIst.setHours(9, 15, 0, 0);
    sessionDesc = "Exchange closed for weekend. Next regular session opens Monday at 09:15 IST.";
  }

  const remainingSec = Math.max(0, Math.floor((targetIst.getTime() - ist.getTime()) / 1000));
  const countdownFormatted = formatCountdown(remainingSec);

  return {
    isOpen: state === "OPEN",
    state,
    statusBadge,
    action,
    remainingSeconds: remainingSec,
    countdownFormatted,
    phrase: `${action.charAt(0).toUpperCase() + action.slice(1)} in ${countdownFormatted}`,
    targetTimeIst: targetIst.toLocaleString('en-IN', { weekday: 'short', month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit', second: '2-digit', hour12: false }) + " IST",
    sessionDescription: sessionDesc,
    currentIstTime: istClockStr,
  };
}

function startClock() {
  function tick() {
    const sched = computeMarketSchedule(marketScheduleData);

    // Update Clock display
    if (el.clockDisplay) {
      el.clockDisplay.textContent = sched.currentIstTime;
    }

    // Update Header Countdown Pill
    if (el.marketCountdownPill) {
      if (el.mktStatusText) el.mktStatusText.textContent = sched.statusBadge;
      if (el.mktCountdownTimer) el.mktCountdownTimer.textContent = sched.phrase;

      el.marketCountdownPill.classList.remove('open', 'closed', 'preopen', 'postclose');
      if (sched.isOpen) {
        el.marketCountdownPill.classList.add('open');
      } else if (sched.state === 'PRE_OPEN') {
        el.marketCountdownPill.classList.add('preopen');
      } else if (sched.state === 'POST_CLOSE') {
        el.marketCountdownPill.classList.add('postclose');
      } else {
        el.marketCountdownPill.classList.add('closed');
      }
    }

    // Update Summary Bar Session text
    if (el.summarySessionText) {
      const stateIcon = sched.isOpen ? '🟢' : sched.state === 'PRE_OPEN' ? '🟡' : '🔴';
      el.summarySessionText.textContent = `${stateIcon} ${sched.phrase}`;
    }

    // Update Schedule Modal elements if visible
    if (el.scheduleModal && el.scheduleModal.style.display !== 'none') {
      if (el.modalIstClock) el.modalIstClock.textContent = sched.currentIstTime;
      if (el.modalTimerDisplay) el.modalTimerDisplay.textContent = sched.countdownFormatted;
      if (el.modalTimerLabel) el.modalTimerLabel.textContent = sched.action === 'closes' ? 'CLOSES IN' : 'OPENS IN';
      if (el.modalTimerTarget) el.modalTimerTarget.textContent = `Target: ${sched.targetTimeIst}`;
      if (el.modalStatusBadge) {
        el.modalStatusBadge.textContent = sched.statusBadge;
        el.modalStatusBadge.className = `schedule-status-badge ${sched.isOpen ? 'open' : sched.state === 'PRE_OPEN' ? 'preopen' : ''}`;
      }
      if (el.modalStatusDot) {
        el.modalStatusDot.className = `schedule-glow-dot ${sched.isOpen ? 'open' : sched.state === 'PRE_OPEN' ? 'preopen' : ''}`;
      }
      if (el.modalStatusDesc) el.modalStatusDesc.textContent = sched.sessionDescription;

      // Active card highlighting
      if (el.schedCardPre) el.schedCardPre.classList.toggle('active-session', sched.state === 'PRE_OPEN');
      if (el.schedCardNormal) el.schedCardNormal.classList.toggle('active-session', sched.isOpen);
      if (el.schedCardPost) el.schedCardPost.classList.toggle('active-session', sched.state === 'POST_CLOSE');
    }
  }

  tick();
  setInterval(tick, 1000);
}

// Start application
window.addEventListener('DOMContentLoaded', init);
