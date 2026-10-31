/**
 * MarketScope Flow River Terminal Frontend Controller
 * Vanilla ES module, zero external packages, zero CDN scripts, zero fonts loaded over network.
 * Strict CSP compliant: no unsafe-eval, all dynamic content safely rendered via textContent or escaped HTML.
 */

// State
let flowData = null;
let pinnedSymbol = null;
let hoveredSymbol = null;
const currentTimeframe = 5;
let selectedSession = "auto";
let sessionState = "PREVIOUS";
let sessionDate = "";
let pollTimer = null;
let flowRequestSequence = 0;
let isTabVisible = true;

// DOM references
let riverBaseCanvas, riverBaseCtx;
let riverHighlightCanvas, riverHighlightCtx;
let distCanvas, distCtx;
let tooltipEl;

// Colors
const COLOR_MINT = "#2be4a0";
const COLOR_MINT_DIM = "#0f7a54";
const COLOR_PINK = "#ff4d6a";
const COLOR_PINK_DIM = "#8c2438";
const COLOR_BLUE = "#3a63ff";
const COLOR_AMBER = "#f2b632";
const COLOR_MUTED = "#7d8a99";

function escapeHtml(str) {
  if (str === null || str === undefined) return "";
  return String(str)
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;")
    .replace(/'/g, "&#039;");
}

document.addEventListener("DOMContentLoaded", () => {
  initElements();
  initEventListeners();

  const urlParams = new URLSearchParams(window.location.search);
  const mockFeed = urlParams.get("feed");
  const mockDrawer = urlParams.get("drawer");
  const mockPin = urlParams.get("pin");

  if (mockFeed === "down") {
    renderFeedDownState();
  } else {
    fetchFlowData("auto").then(() => {
      if (mockPin) {
        setTimeout(() => {
          pinStock(mockPin);
          showTooltip(mockPin, 480, 260);
        }, 300);
      }
      if (mockDrawer === "recap") {
        setTimeout(() => {
          const rd = document.getElementById("recapDrawer");
          if (rd) {
            rd.classList.add("open");
            loadRecapPanel();
          }
        }, 300);
      }
    });
  }
});

function initElements() {
  riverBaseCanvas = document.getElementById("riverBaseCanvas");
  riverHighlightCanvas = document.getElementById("riverHighlightCanvas");
  distCanvas = document.getElementById("distCanvas");
  tooltipEl = document.getElementById("flowTooltip");

  if (riverBaseCanvas) riverBaseCtx = riverBaseCanvas.getContext("2d");
  if (riverHighlightCanvas) riverHighlightCtx = riverHighlightCanvas.getContext("2d");
  if (distCanvas) distCtx = distCanvas.getContext("2d");

  setupCanvasResolutions();
}

function setupCanvasResolutions() {
  const dpr = window.devicePixelRatio || 1;
  const setupOne = (canvas, ctx) => {
    if (!canvas || !ctx) return;
    const rect = canvas.getBoundingClientRect();
    canvas.width = Math.floor(rect.width * dpr);
    canvas.height = Math.floor(rect.height * dpr);
    ctx.resetTransform?.();
    ctx.scale(dpr, dpr);
  };
  setupOne(riverBaseCanvas, riverBaseCtx);
  setupOne(riverHighlightCanvas, riverHighlightCtx);
  setupOne(distCanvas, distCtx);
}

function initEventListeners() {
  window.addEventListener("resize", debounce(() => {
    setupCanvasResolutions();
    renderAll();
  }, 150));

  document.addEventListener("visibilitychange", () => {
    isTabVisible = (document.visibilityState === "visible");
    if (isTabVisible && sessionState === "LIVE" && !pollTimer) {
      startLivePolling();
    }
  });

  // The flow payload has fixed five-minute alignment. Other intervals belong
  // to the workspace chart; do not display nonfunctional interval controls.
  document.querySelectorAll(".tf-btn").forEach(btn => {
    if (Number(btn.dataset.tf) !== currentTimeframe) {
      btn.remove();
      return;
    }
    btn.classList.add("active");
    btn.setAttribute("aria-pressed", "true");
    btn.title = "Flow River uses five-minute candles";
    btn.addEventListener("click", (e) => {
      fetchFlowData(selectedSession);
    });
  });

  // Previous Session Toggle button
  const prevToggleBtn = document.getElementById("prevSessionToggleBtn");
  if (prevToggleBtn) {
    prevToggleBtn.addEventListener("click", () => {
      fetchFlowData(selectedSession === "previous" ? "auto" : "previous");
    });
  }

  // Drawers
  setupDrawer("scannerBtn", "scannerDrawer", "closeScannerBtn", loadScannerTable);
  setupDrawer("recapBtn", "recapDrawer", "closeRecapBtn", loadRecapPanel);
  setupDrawer("layaBtn", "layaDrawer", "closeLayaBtn", () => loadLayaPanel(pinnedSymbol || "RELIANCE"));

  // Modals
  const stockModal = document.getElementById("stockChartModal");
  const closeStockModalBtn = document.getElementById("closeStockModalBtn");
  if (closeStockModalBtn && stockModal) {
    closeStockModalBtn.addEventListener("click", () => stockModal.classList.remove("open"));
  }

  // Escape clears pin & modals
  document.addEventListener("keydown", (e) => {
    if (e.key === "Escape") {
      clearPin();
      document.querySelectorAll(".modal-overlay").forEach(m => m.classList.remove("open"));
      document.querySelectorAll(".slide-drawer").forEach(d => d.classList.remove("open"));
    }
  });

  // Search input
  const searchInput = document.getElementById("symbolSearchInput");
  if (searchInput) {
    searchInput.addEventListener("keydown", (e) => {
      if (e.key === "Enter") {
        const sym = searchInput.value.trim().toUpperCase();
        if (sym) {
          pinStock(sym);
          openStockModal(sym);
        }
      }
    });
  }

  // River Canvas mouse events
  if (riverHighlightCanvas) {
    riverHighlightCanvas.addEventListener("mousemove", handleRiverMouseMove);
    riverHighlightCanvas.addEventListener("mouseleave", handleRiverMouseLeave);
    riverHighlightCanvas.addEventListener("click", handleRiverClick);
  }
}

function setupDrawer(openBtnId, drawerId, closeBtnId, onOpen) {
  const openBtn = document.getElementById(openBtnId);
  const drawer = document.getElementById(drawerId);
  const closeBtn = document.getElementById(closeBtnId);
  if (!openBtn || !drawer) return;

  openBtn.addEventListener("click", () => {
    drawer.classList.add("open");
    if (onOpen) onOpen();
  });
  if (closeBtn) {
    closeBtn.addEventListener("click", () => drawer.classList.remove("open"));
  }
}

function debounce(fn, ms) {
  let timeout;
  return function (...args) {
    clearTimeout(timeout);
    timeout = setTimeout(() => fn.apply(this, args), ms);
  };
}

// Data Fetching
async function fetchFlowData(sessionParam = "auto") {
  selectedSession = sessionParam;
  const requestSequence = ++flowRequestSequence;
  if (sessionParam === "previous") stopLivePolling();
  try {
    const url = `/api/flow?session=${encodeURIComponent(sessionParam)}&timeframe=${currentTimeframe}`;
    const res = await fetch(url, { credentials: "same-origin" });
    if (requestSequence !== flowRequestSequence) return;
    if (!res.ok) {
      if (res.status === 503) {
        renderFeedDownState();
        return;
      }
      throw new Error(`HTTP ${res.status}`);
    }
    const data = await res.json();
    if (requestSequence !== flowRequestSequence) return;
    flowData = data;
    sessionState = data.session_state || "PREVIOUS";
    sessionDate = data.session_date || "";

    updateTopBarAndBanner();
    renderAll();

    if (sessionState === "LIVE") {
      startLivePolling();
    } else {
      stopLivePolling();
    }
  } catch (err) {
    if (requestSequence !== flowRequestSequence) return;
    console.error("Error loading flow data:", err);
    renderFeedDownState();
  }
}

function startLivePolling() {
  stopLivePolling();
  pollTimer = setInterval(() => {
    if (isTabVisible && sessionState === "LIVE" && selectedSession === "auto") {
      fetchFlowData("auto");
    }
  }, 5000);
}

function stopLivePolling() {
  if (pollTimer) {
    clearInterval(pollTimer);
    pollTimer = null;
  }
}

// UI Updating
function updateTopBarAndBanner() {
  const stateChip = document.getElementById("sessionStateChip");
  const stateLabel = document.getElementById("sessionStateLabel");
  const feedChip = document.getElementById("feedStatusChip");
  const banner = document.getElementById("sessionBanner");
  const bannerText = document.getElementById("sessionBannerText");
  const prevToggle = document.getElementById("prevSessionToggleBtn");

  if (!flowData) return;

  if (stateChip && stateLabel) {
    stateChip.className = "state-chip " + (sessionState === "LIVE" ? "live" : "closed");
    stateLabel.textContent = sessionState === "LIVE" ? "LIVE" : (sessionState === "CLOSED_TODAY" ? "CLOSED TODAY" : "PREVIOUS SESSION");
  }

  if (feedChip) {
    const ok = flowData.feed?.ok_count || 0;
    const failed = flowData.feed?.failed_count || 0;
    feedChip.textContent = ok === 0 ? "FEED: DOWN" : sessionState !== "LIVE" ? (failed ? "ARCHIVE: PARTIAL" : "SESSION ARCHIVE") : failed === 0 ? "PUBLIC CANDLES" : "FEED: DEGRADED";
    feedChip.className = "feed-chip " + (failed === 0 ? "ok" : "down");
  }

  if (banner && bannerText) {
    if (sessionState === "PREVIOUS") {
      banner.style.display = "flex";
      bannerText.textContent = `PREVIOUS SESSION - ${flowData.session_date} (closed) - last candle ${flowData.as_of}. Historical data, not live.`;
    } else if (sessionState === "CLOSED_TODAY") {
      banner.style.display = "flex";
      bannerText.textContent = `TODAY'S SESSION - CLOSED - last candle ${flowData.as_of}.`;
    } else {
      banner.style.display = "none";
    }
  }

  if (prevToggle) {
    prevToggle.style.display = "inline-flex";
    prevToggle.textContent = selectedSession === "previous" ? "Return to auto session" : "Previous session";
    prevToggle.setAttribute("aria-pressed", String(selectedSession === "previous"));
  }
}

function renderAll() {
  if (!flowData || flowData.empty) {
    renderEmptyState();
    return;
  }
  renderDistributionStrip();
  renderRiverBase();
  renderRiverHighlight();
  renderFunnelTiles();
  renderSetupCards();
}

function renderEmptyState() {
  const funnelBody = document.getElementById("funnelGridBody");
  const cardsPanel = document.getElementById("cardsPanel");
  if (funnelBody) {
    funnelBody.innerHTML = `<div class="stage-empty-msg" style="grid-column: span 4;">No session data available.</div>`;
  }
  if (cardsPanel) {
    cardsPanel.innerHTML = `<div class="cards-empty-state">No setups available for this session.</div>`;
  }
}

function renderFeedDownState() {
  flowData = null;
  sessionState = "UNAVAILABLE";
  stopLivePolling();
  for (const [canvas, context] of [[riverBaseCanvas, riverBaseCtx], [riverHighlightCanvas, riverHighlightCtx], [distCanvas, distCtx]]) {
    if (canvas && context) {
      const rect = canvas.getBoundingClientRect();
      context.clearRect(0, 0, rect.width, rect.height);
    }
  }
  const stateLabel = document.getElementById("sessionStateLabel");
  const feedChip = document.getElementById("feedStatusChip");
  if (stateLabel) stateLabel.textContent = "UNAVAILABLE";
  if (feedChip) {
    feedChip.textContent = "FEED: DOWN";
    feedChip.className = "feed-chip down";
  }
  const funnelBody = document.getElementById("funnelGridBody");
  const cardsPanel = document.getElementById("cardsPanel");
  if (cardsPanel) cardsPanel.innerHTML = '<div class="cards-empty-state">Received data is unavailable. Retry the session before reviewing setups.</div>';
  if (funnelBody) {
    funnelBody.innerHTML = `
      <div class="stage-empty-msg" style="grid-column: span 4; color: var(--pink-red);">
        Market data feed currently unavailable.<br/>
        <button id="errLoadPrevBtn" class="nav-btn primary" style="margin-top: 10px;">Load Previous Session</button>
      </div>`;
    const btn = document.getElementById("errLoadPrevBtn");
    if (btn) btn.onclick = () => fetchFlowData("previous");
  }
}

// 4.4 Top Distribution Strip
function renderDistributionStrip() {
  if (!distCanvas || !distCtx || !flowData?.histogram) return;
  const dpr = window.devicePixelRatio || 1;
  const rect = distCanvas.getBoundingClientRect();
  const w = rect.width;
  const h = rect.height;

  distCtx.clearRect(0, 0, w, h);

  const hist = flowData.histogram;
  const buckets = hist.buckets || [];
  if (buckets.length === 0) return;

  const maxCount = Math.max(1, ...buckets.map(b => b.count));
  const numBuckets = buckets.length;
  const barWidth = Math.max(2, (w / numBuckets) - 1);

  // Center vertical zero line
  const zeroX = w / 2;
  distCtx.strokeStyle = "rgba(58, 99, 255, 0.3)";
  distCtx.lineWidth = 1;
  distCtx.beginPath();
  distCtx.moveTo(zeroX, 0);
  distCtx.lineTo(zeroX, h);
  distCtx.stroke();

  // Draw bars
  buckets.forEach((b, i) => {
    const x = i * (w / numBuckets);
    const barH = (b.count / maxCount) * (h - 10);
    const y = h - barH - 4;

    distCtx.fillStyle = b.bullish ? "rgba(43, 228, 160, 0.7)" : "rgba(255, 77, 106, 0.7)";
    distCtx.fillRect(x, y, barWidth, barH);

    // Glowing top line
    distCtx.fillStyle = b.bullish ? COLOR_MINT : COLOR_PINK;
    distCtx.fillRect(x, y, barWidth, 2);
  });

  // Counts
  const bullCountEl = document.getElementById("distBullCount");
  const bearCountEl = document.getElementById("distBearCount");
  if (bullCountEl) bullCountEl.textContent = `BULLISH ${flowData.counts?.bullish || 0} ▲`;
  if (bearCountEl) bearCountEl.textContent = `BEARISH ${flowData.counts?.bearish || 0} ▼`;
}

// 4.3 River Canvas
let riverLayout = {
  w: 0,
  h: 0,
  paddingLeft: 60,
  paddingRight: 40,
  paddingTop: 20,
  paddingBottom: 30,
  yMin: -3.0,
  yMax: 3.0,
  numBars: 75,
};

function renderRiverBase() {
  if (!riverBaseCanvas || !riverBaseCtx || !flowData?.stocks) return;
  const rect = riverBaseCanvas.getBoundingClientRect();
  const w = rect.width;
  const h = rect.height;
  riverLayout.w = w;
  riverLayout.h = h;

  riverBaseCtx.clearRect(0, 0, w, h);

  const stocks = Object.values(flowData.stocks);
  if (stocks.length === 0) return;

  // Auto-scale symmetric Y-axis
  let maxAbsPct = 1.0;
  stocks.forEach(s => {
    (s.pct || []).forEach(val => {
      if (val !== null && typeof val === "number") {
        maxAbsPct = Math.max(maxAbsPct, Math.abs(val));
      }
    });
  });
  maxAbsPct = Math.ceil(maxAbsPct * 1.15); // Add 15% headroom
  riverLayout.yMin = -maxAbsPct;
  riverLayout.yMax = maxAbsPct;

  const pL = riverLayout.paddingLeft;
  const pR = riverLayout.paddingRight;
  const pT = riverLayout.paddingTop;
  const pB = riverLayout.paddingBottom;
  const plotW = w - pL - pR;
  const plotH = h - pT - pB;

  // Draw Grid & Axes
  const zeroY = pT + plotH / 2;

  // 0% Center Axis
  riverBaseCtx.strokeStyle = "rgba(58, 99, 255, 0.4)";
  riverBaseCtx.lineWidth = 1;
  riverBaseCtx.setLineDash([4, 4]);
  riverBaseCtx.beginPath();
  riverBaseCtx.moveTo(pL, zeroY);
  riverBaseCtx.lineTo(w - pR, zeroY);
  riverBaseCtx.stroke();
  riverBaseCtx.setLineDash([]);

  // Time labels
  riverBaseCtx.fillStyle = COLOR_MUTED;
  riverBaseCtx.font = "9px Cascadia Mono, monospace";
  riverBaseCtx.textAlign = "center";
  const timeLabels = [
    { label: "09:15", barIdx: 0 },
    { label: "11:00", barIdx: 21 },
    { label: "13:00", barIdx: 45 },
    { label: "15:30", barIdx: 74 },
  ];
  timeLabels.forEach(t => {
    const x = pL + (t.barIdx / 74) * plotW;
    riverBaseCtx.fillText(t.label, x, h - 8);
    // Vertical subtle tick
    riverBaseCtx.strokeStyle = "rgba(18, 32, 43, 0.8)";
    riverBaseCtx.beginPath();
    riverBaseCtx.moveTo(x, pT);
    riverBaseCtx.lineTo(x, h - pB);
    riverBaseCtx.stroke();
  });

  // Vertical gradient scale bar on far left
  const grad = riverBaseCtx.createLinearGradient(0, pT, 0, h - pB);
  grad.addColorStop(0, COLOR_MINT);
  grad.addColorStop(0.5, "rgba(58, 99, 255, 0.5)");
  grad.addColorStop(1, COLOR_PINK);
  riverBaseCtx.fillStyle = grad;
  riverBaseCtx.fillRect(15, pT, 3, plotH);

  // Y-axis labels
  riverBaseCtx.textAlign = "left";
  riverBaseCtx.fillStyle = COLOR_MINT;
  riverBaseCtx.fillText(`+${maxAbsPct.toFixed(1)}%`, 22, pT + 8);
  riverBaseCtx.fillStyle = COLOR_MUTED;
  riverBaseCtx.fillText(" 0.0%", 22, zeroY + 3);
  riverBaseCtx.fillStyle = COLOR_PINK;
  riverBaseCtx.fillText(`-${maxAbsPct.toFixed(1)}%`, 22, h - pB);

  // Blue Sector Guide lines
  const sectors = flowData.sectors || {};
  riverBaseCtx.strokeStyle = "rgba(58, 99, 255, 0.25)";
  riverBaseCtx.lineWidth = 1;
  Object.entries(sectors).forEach(([sec, avgPct]) => {
    const y = zeroY - (avgPct / maxAbsPct) * (plotH / 2);
    if (y >= pT && y <= h - pB) {
      riverBaseCtx.beginPath();
      riverBaseCtx.moveTo(pL, y);
      riverBaseCtx.lineTo(w - pR, y);
      riverBaseCtx.stroke();
    }
  });

  // Draw Stock Trajectories
  riverBaseCtx.save();
  riverBaseCtx.globalCompositeOperation = "screen"; // Additive blending for luminous glow

  stocks.forEach(s => {
    const pts = s.pct || [];
    const vols = s.vol_rel || [];
    if (pts.length === 0) return;

    const isBull = (s.change_pct >= 0);
    riverBaseCtx.strokeStyle = isBull ? "rgba(43, 228, 160, 0.35)" : "rgba(255, 77, 106, 0.35)";
    riverBaseCtx.lineWidth = 1.2;

    let drawing = false;
    riverBaseCtx.beginPath();

    for (let i = 0; i < pts.length; i++) {
      const val = pts[i];
      if (val === null || val === undefined) {
        drawing = false;
        continue; // Gaps = no line, no interpolation
      }

      const x = pL + (i / 74) * plotW;
      const y = zeroY - (val / maxAbsPct) * (plotH / 2);

      if (!drawing) {
        riverBaseCtx.moveTo(x, y);
        drawing = true;
      } else {
        riverBaseCtx.lineTo(x, y);
      }

      // Horizontal volume ticks proportional to vol_rel
      const vRel = vols[i] || 1.0;
      if (vRel > 1.2) {
        const tickLen = Math.min(8, vRel * 2);
        riverBaseCtx.stroke(); // Flush current path
        riverBaseCtx.beginPath();
        riverBaseCtx.moveTo(x - tickLen / 2, y);
        riverBaseCtx.lineTo(x + tickLen / 2, y);
        riverBaseCtx.stroke();
        riverBaseCtx.beginPath();
        riverBaseCtx.moveTo(x, y);
      }
    }
    riverBaseCtx.stroke();
  });

  riverBaseCtx.restore();

  // Vertical Right End Ladder & Bezier curves to Funnel
  const ladderX = w - pR + 10;
  stocks.sort((a, b) => b.change_pct - a.change_pct);

  const stage1Syms = new Set([
    ...(flowData.stages?.stage1_movers?.bullish || []),
    ...(flowData.stages?.stage1_movers?.bearish || []),
  ]);

  stocks.forEach((s, idx) => {
    const lastVal = s.pct?.[s.pct.length - 1] ?? s.change_pct;
    const y = zeroY - (lastVal / maxAbsPct) * (plotH / 2);

    // Ladder tick
    riverBaseCtx.fillStyle = (s.change_pct >= 0) ? COLOR_MINT : COLOR_PINK;
    riverBaseCtx.fillRect(ladderX, y - 1, 6, 2);

    // Bezier curve to funnel if in stage 1
    if (stage1Syms.has(s.symbol)) {
      riverBaseCtx.strokeStyle = (s.change_pct >= 0) ? "rgba(43, 228, 160, 0.15)" : "rgba(255, 77, 106, 0.15)";
      riverBaseCtx.lineWidth = 1;
      riverBaseCtx.beginPath();
      riverBaseCtx.moveTo(ladderX + 6, y);
      const cpX1 = ladderX + 25;
      const cpY1 = y;
      const cpX2 = w;
      const targetY = (s.change_pct >= 0) ? (zeroY / 2) : (zeroY + zeroY / 2);
      riverBaseCtx.bezierCurveTo(cpX1, cpY1, cpX2, targetY, w, targetY);
      riverBaseCtx.stroke();
    }
  });
}

function renderRiverHighlight() {
  if (!riverHighlightCanvas || !riverHighlightCtx || !flowData?.stocks) return;
  const w = riverLayout.w;
  const h = riverLayout.h;
  riverHighlightCtx.clearRect(0, 0, w, h);

  const targetSym = pinnedSymbol || hoveredSymbol;
  if (!targetSym) return;

  const stock = flowData.stocks[targetSym];
  if (!stock || !stock.pct) return;

  const pL = riverLayout.paddingLeft;
  const pR = riverLayout.paddingRight;
  const pT = riverLayout.paddingTop;
  const pB = riverLayout.paddingBottom;
  const plotW = w - pL - pR;
  const plotH = h - pT - pB;
  const zeroY = pT + plotH / 2;
  const maxAbsPct = riverLayout.yMax;

  // Bright highlighted path
  riverHighlightCtx.save();
  riverHighlightCtx.strokeStyle = (stock.change_pct >= 0) ? COLOR_MINT : COLOR_PINK;
  riverHighlightCtx.lineWidth = 3;
  riverHighlightCtx.shadowColor = (stock.change_pct >= 0) ? COLOR_MINT : COLOR_PINK;
  riverHighlightCtx.shadowBlur = 10;

  riverHighlightCtx.beginPath();
  let drawing = false;
  for (let i = 0; i < stock.pct.length; i++) {
    const val = stock.pct[i];
    if (val === null || val === undefined) {
      drawing = false;
      continue;
    }
    const x = pL + (i / 74) * plotW;
    const y = zeroY - (val / maxAbsPct) * (plotH / 2);
    if (!drawing) {
      riverHighlightCtx.moveTo(x, y);
      drawing = true;
    } else {
      riverHighlightCtx.lineTo(x, y);
    }
  }
  riverHighlightCtx.stroke();

  // End label
  const lastX = pL + plotW;
  const lastY = zeroY - ((stock.pct[stock.pct.length - 1] ?? 0) / maxAbsPct) * (plotH / 2);
  riverHighlightCtx.fillStyle = "#fff";
  riverHighlightCtx.font = "bold 11px Cascadia Mono, monospace";
  riverHighlightCtx.textAlign = "left";
  riverHighlightCtx.fillText(stock.symbol, lastX + 15, lastY + 4);

  riverHighlightCtx.restore();
}

function handleRiverMouseMove(e) {
  if (!flowData?.stocks) return;
  const rect = riverHighlightCanvas.getBoundingClientRect();
  const mouseX = e.clientX - rect.left;
  const mouseY = e.clientY - rect.top;

  const pL = riverLayout.paddingLeft;
  const pR = riverLayout.paddingRight;
  const plotW = riverLayout.w - pL - pR;

  // Find closest stock trajectory to mouse
  let closestSym = null;
  let minDist = 18; // 18px hit radius

  const barIdx = Math.max(0, Math.min(74, Math.round(((mouseX - pL) / plotW) * 74)));

  Object.values(flowData.stocks).forEach(s => {
    const val = s.pct?.[barIdx];
    if (val === null || val === undefined) return;
    const zeroY = riverLayout.paddingTop + (riverLayout.h - riverLayout.paddingTop - riverLayout.paddingBottom) / 2;
    const y = zeroY - (val / riverLayout.yMax) * ((riverLayout.h - riverLayout.paddingTop - riverLayout.paddingBottom) / 2);
    const dist = Math.abs(mouseY - y);
    if (dist < minDist) {
      minDist = dist;
      closestSym = s.symbol;
    }
  });

  if (closestSym !== hoveredSymbol) {
    hoveredSymbol = closestSym;
    renderRiverHighlight();
    if (hoveredSymbol) {
      showTooltip(hoveredSymbol, e.clientX, e.clientY);
    } else {
      hideTooltip();
    }
  }
}

function handleRiverMouseLeave() {
  hoveredSymbol = null;
  renderRiverHighlight();
  hideTooltip();
}

function handleRiverClick(e) {
  if (hoveredSymbol) {
    pinStock(hoveredSymbol);
  }
}

function pinStock(symbol) {
  pinnedSymbol = (pinnedSymbol === symbol) ? null : symbol;
  renderRiverHighlight();
  highlightPinnedInDom();
}

function clearPin() {
  pinnedSymbol = null;
  renderRiverHighlight();
  highlightPinnedInDom();
}

function highlightPinnedInDom() {
  document.querySelectorAll(".flow-tile").forEach(t => {
    if (pinnedSymbol && t.dataset.sym === pinnedSymbol) {
      t.classList.add("pinned");
    } else {
      t.classList.remove("pinned");
    }
  });
  if (pinnedSymbol) {
    const card = document.querySelector(`.setup-card[data-sym="${pinnedSymbol}"]`);
    if (card) card.scrollIntoView({ behavior: "smooth", block: "center" });
  }
}

// Tooltip
function showTooltip(symbol, clientX, clientY) {
  if (!tooltipEl || !flowData?.stocks?.[symbol]) return;
  const s = flowData.stocks[symbol];

  const symEl = tooltipEl.querySelector(".tt-sym");
  const nameEl = tooltipEl.querySelector(".tt-name");
  const priceEl = tooltipEl.querySelector(".tt-price");
  const chgEl = tooltipEl.querySelector(".tt-chg");
  const scoreEl = tooltipEl.querySelector(".tt-score");
  const rvolEl = tooltipEl.querySelector(".tt-rvol");
  const gradeEl = tooltipEl.querySelector(".tt-grade");
  const actionEl = tooltipEl.querySelector(".tt-action");
  const gateEl = tooltipEl.querySelector(".tt-gate");

  if (symEl) symEl.textContent = s.symbol;
  if (nameEl) nameEl.textContent = s.name;
  if (priceEl) priceEl.textContent = `Rs ${s.last.toFixed(2)}`;
  if (chgEl) {
    chgEl.textContent = `${s.change_pct >= 0 ? "+" : ""}${s.change_pct.toFixed(2)}%`;
    chgEl.style.color = (s.change_pct >= 0) ? COLOR_MINT : COLOR_PINK;
  }
  if (scoreEl) scoreEl.textContent = String(s.score);
  if (rvolEl) rvolEl.textContent = s.rvol.toFixed(2);
  if (gradeEl) gradeEl.textContent = s.grade;
  if (actionEl) actionEl.textContent = s.action;

  if (gateEl) {
    const passesRr = (s.rr >= 1.5);
    gateEl.textContent = passesRr ? `GATE: Passes R:R (${s.rr.toFixed(2)})` : `GATE: Fails R:R (${s.rr.toFixed(2)} < 1.5)`;
    gateEl.className = "tt-gate " + (passesRr ? "pass" : "fail");
  }

  tooltipEl.style.display = "block";
  tooltipEl.style.left = `${Math.min(window.innerWidth - 230, clientX + 15)}px`;
  tooltipEl.style.top = `${Math.min(window.innerHeight - 200, clientY + 15)}px`;
}

function hideTooltip() {
  if (tooltipEl) tooltipEl.style.display = "none";
}

// 4.5 Funnel Tiles
function renderFunnelTiles() {
  const stages = flowData.stages;
  if (!stages) return;

  const renderCol = (containerId, symList, isBull, stageNum) => {
    const el = document.getElementById(containerId);
    if (!el) return;
    el.innerHTML = "";

    if (symList.length === 0) {
      el.innerHTML = `<div class="stage-empty-msg">No stock passes filter</div>`;
      return;
    }

    symList.forEach(sym => {
      const s = flowData.stocks[sym];
      if (!s) return;

      const tile = document.createElement("div");
      tile.className = `flow-tile ${isBull ? "bull" : "bear"} ${pinnedSymbol === sym ? "pinned" : ""}`;
      tile.dataset.sym = sym;
      tile.tabIndex = 0;

      // Top row
      const topRow = document.createElement("div");
      topRow.className = "tile-row-top";
      topRow.innerHTML = `
        <span class="tile-sym">${escapeHtml(s.symbol)}</span>
        <span class="tile-pct ${isBull ? "bull" : "bear"}">${s.change_pct >= 0 ? "+" : ""}${s.change_pct.toFixed(2)}%</span>
      `;
      tile.appendChild(topRow);

      // Mid row
      const midRow = document.createElement("div");
      midRow.className = "tile-row-mid";
      midRow.innerHTML = `
        <span>Rs ${s.last.toFixed(1)}</span>
        <span class="tile-badge-pill">${escapeHtml(s.grade)}</span>
      `;
      tile.appendChild(midRow);

      // Stage-specific additions
      if (stageNum === 2) {
        // RVOL bar
        const rvolBar = document.createElement("div");
        rvolBar.className = "tile-rvol-bar";
        const wPct = Math.min(100, (s.rvol / 3.0) * 100);
        rvolBar.innerHTML = `<div class="tile-rvol-fill" style="width: ${wPct}%;"></div>`;
        tile.appendChild(rvolBar);
      } else if (stageNum === 3 || stageNum === 4) {
        // Volume Profile 14 buckets
        const prof = flowData.profiles?.[sym];
        if (prof && prof.length > 0) {
          const profWrap = document.createElement("div");
          profWrap.className = "tile-vol-profile";
          prof.slice(0, 7).forEach(b => { // Compact view
            const bBar = document.createElement("div");
            bBar.className = `vol-prof-bar ${b.is_poc ? "poc" : ""} ${b.is_current ? "cur" : ""}`;
            bBar.style.width = `${Math.max(4, b.pct)}%`;
            profWrap.appendChild(bBar);
          });
          tile.appendChild(profWrap);
        }
        if (stageNum === 4) {
          const actEl = document.createElement("div");
          actEl.style.fontSize = "8px";
          actEl.style.color = isBull ? COLOR_MINT : COLOR_PINK;
          actEl.style.fontWeight = "700";
          actEl.style.marginTop = "2px";
          actEl.textContent = s.action;
          tile.appendChild(actEl);
        }
      }

      // Event listeners
      tile.addEventListener("mouseenter", (e) => {
        hoveredSymbol = sym;
        renderRiverHighlight();
        showTooltip(sym, e.clientX, e.clientY);
      });
      tile.addEventListener("mouseleave", () => {
        hoveredSymbol = null;
        renderRiverHighlight();
        hideTooltip();
      });
      tile.addEventListener("click", () => {
        pinStock(sym);
        openStockModal(sym);
      });
      tile.addEventListener("keydown", (e) => {
        if (e.key === "Enter") openStockModal(sym);
      });

      el.appendChild(tile);
    });
  };

  renderCol("funnelS1Bull", stages.stage1_movers?.bullish || [], true, 1);
  renderCol("funnelS1Bear", stages.stage1_movers?.bearish || [], false, 1);
  renderCol("funnelS2Bull", stages.stage2_volume?.bullish || [], true, 2);
  renderCol("funnelS2Bear", stages.stage2_volume?.bearish || [], false, 2);
  renderCol("funnelS3Bull", stages.stage3_pressure?.bullish || [], true, 3);
  renderCol("funnelS3Bear", stages.stage3_pressure?.bearish || [], false, 3);
  renderCol("funnelS4Bull", stages.stage4_setups?.bullish || [], true, 4);
  renderCol("funnelS4Bear", stages.stage4_setups?.bearish || [], false, 4);
}

// 4.6 Setup Cards
function renderSetupCards() {
  const panel = document.getElementById("cardsPanel");
  if (!panel || !flowData?.cards) return;

  panel.innerHTML = "";
  const cards = flowData.cards;

  if (cards.length === 0) {
    const msg = (sessionState === "LIVE") ? "No A/B setups right now" : "No setups at the close";
    panel.innerHTML = `<div class="cards-empty-state">${escapeHtml(msg)}</div>`;
    return;
  }

  const isHistorical = (sessionState !== "LIVE");

  cards.forEach(c => {
    const card = document.createElement("div");
    card.className = `setup-card ${c.type === "BULL" ? "bull" : "bear"}`;
    card.dataset.sym = c.symbol;

    // Mini chart SVG
    const closes = c.mini_series?.closes || [];
    let svgPolyline = "";
    let vwapLine = "";
    if (closes.length > 1) {
      const minC = Math.min(...closes);
      const maxC = Math.max(...closes);
      const rng = Math.max(0.01, maxC - minC);
      const pts = closes.map((p, idx) => {
        const x = (idx / (closes.length - 1)) * 260;
        const y = 50 - ((p - minC) / rng) * 40;
        return `${x.toFixed(1)},${y.toFixed(1)}`;
      }).join(" ");
      svgPolyline = `<polyline points="${pts}" fill="none" stroke="${c.type === "BULL" ? COLOR_MINT : COLOR_PINK}" stroke-width="1.8" />`;

      const vwapY = 50 - (((c.mini_series?.vwap || closes[0]) - minC) / rng) * 40;
      vwapLine = `<line x1="0" y1="${vwapY.toFixed(1)}" x2="260" y2="${vwapY.toFixed(1)}" stroke="rgba(255,255,255,0.4)" stroke-dasharray="3,3" stroke-width="1" />`;
    }

    // Range bar position
    const sl = c.sl || (c.last * 0.98);
    const t2 = c.t2 || (c.last * 1.04);
    const totalDist = Math.max(0.01, Math.abs(t2 - sl));
    const curDist = Math.max(0, Math.min(totalDist, Math.abs(c.last - sl)));
    const markerPct = ((curDist / totalDist) * 100).toFixed(1);

    card.innerHTML = `
      <div class="card-header">
        <div class="card-header-left">
          <span class="card-direction-badge ${c.type === "BULL" ? "bull" : "bear"}">${c.grade === "A+" ? c.type : (c.type === "BULL" ? "BULLISH" : "BEARISH")} ${c.type === "BULL" ? "▲" : "▼"}</span>
          <div>
            <div class="card-sym-title">${escapeHtml(c.symbol)}</div>
            <div class="card-company-name">${escapeHtml(c.name)}</div>
          </div>
        </div>
        <div class="card-price-block">
          <div class="card-last-price">Rs ${c.last.toFixed(2)}</div>
          <div class="card-change-pct ${c.change_pct >= 0 ? "bull" : "bear"}">${c.change_pct >= 0 ? "+" : ""}${c.change_pct.toFixed(2)}%</div>
        </div>
      </div>

      <div class="card-mini-chart-wrap">
        <svg class="card-mini-chart-svg" viewBox="0 0 260 60" preserveAspectRatio="none">
          ${vwapLine}
          ${svgPolyline}
        </svg>
      </div>

      <div class="card-fields-grid">
        <div class="card-field">
          <span class="card-field-label">ZONE</span>
          <span class="card-field-val">${c.zone ? `${c.zone[0]}-${c.zone[1]}` : "N/A"}</span>
        </div>
        <div class="card-field">
          <span class="card-field-label">SL</span>
          <span class="card-field-val">${c.sl ? c.sl.toFixed(1) : "N/A"}</span>
        </div>
        <div class="card-field">
          <span class="card-field-label">T1 / T2</span>
          <span class="card-field-val">${c.t1 ? c.t1.toFixed(1) : ""}/${c.t2 ? c.t2.toFixed(1) : ""}</span>
        </div>
        <div class="card-field">
          <span class="card-field-label">R:R</span>
          <span class="card-field-val">${c.rr ? c.rr.toFixed(2) : "N/A"}</span>
        </div>
        <div class="card-field">
          <span class="card-field-label">RISK</span>
          <span class="card-field-val">${c.risk_pct ? `${c.risk_pct}%` : "N/A"}</span>
        </div>
        <div class="card-field">
          <span class="card-field-label">RVOL</span>
          <span class="card-field-val">${c.rvol ? c.rvol.toFixed(2) : "N/A"}</span>
        </div>
        <div class="card-field">
          <span class="card-field-label">RSI</span>
          <span class="card-field-val">${c.rsi ? c.rsi.toFixed(1) : "N/A"}</span>
        </div>
        <div class="card-field">
          <span class="card-field-label">CONV</span>
          <span class="card-field-val">${c.conviction}%</span>
        </div>
      </div>

      <div class="card-range-bar-wrap">
        <div class="card-range-bar">
          <div class="card-range-marker" style="left: ${markerPct}%;"></div>
        </div>
        <div class="card-range-labels">
          <span>SL: ${c.sl ? c.sl.toFixed(1) : "-"}</span>
          <span>CUR: ${c.last.toFixed(1)}</span>
          <span>T2: ${c.t2 ? c.t2.toFixed(1) : "-"}</span>
        </div>
      </div>

      ${isHistorical ? `<div class="historical-not-actionable-tag">HISTORICAL SETUP - NOT ACTIONABLE (${c.outcome ? `OUTCOME: ${escapeHtml(c.outcome)}` : "no signal logged"})</div>` : ""}

      <div class="card-actions-row">
        <button class="ai-brief-btn" data-sym="${escapeHtml(c.symbol)}">
          <span>⚡ AI Brief</span>
        </button>
        <span style="font-size: 9px; color: var(--text-muted); font-family: var(--font-mono);">${escapeHtml(c.action)}</span>
      </div>
    `;

    // AI brief button listener
    const aiBtn = card.querySelector(".ai-brief-btn");
    if (aiBtn) {
      aiBtn.addEventListener("click", (e) => {
        e.stopPropagation();
        loadLayaPanel(c.symbol);
      });
    }

    card.addEventListener("click", () => {
      pinStock(c.symbol);
      openStockModal(c.symbol);
    });

    panel.appendChild(card);
  });
}

// 4.7 Stock Modal & Chart
function openStockModal(symbol) {
  const modal = document.getElementById("stockChartModal");
  if (!modal) return;

  const titleEl = document.getElementById("modalStockTitle");
  const subEl = document.getElementById("modalStockSub");
  const s = flowData?.stocks?.[symbol];

  if (titleEl) { titleEl.textContent = symbol; titleEl.setAttribute("data-margin-symbol", symbol); }
  if (subEl && s) subEl.textContent = `${s.name} - Rs ${s.last.toFixed(2)} (${s.change_pct >= 0 ? "+" : ""}${s.change_pct.toFixed(2)}%)`;

  renderModalChart(symbol);
  modal.classList.add("open");
}

function renderModalChart(symbol) {
  const canvas = document.getElementById("modalChartCanvas");
  if (!canvas) return;
  const ctx = canvas.getContext("2d");
  const rect = canvas.getBoundingClientRect();
  canvas.width = rect.width;
  canvas.height = rect.height;

  ctx.clearRect(0, 0, canvas.width, canvas.height);
  const s = flowData?.stocks?.[symbol];
  if (!s || !s.pct) return;

  const closes = s.pct;
  const minV = Math.min(...closes.filter(v => v !== null));
  const maxV = Math.max(...closes.filter(v => v !== null));
  const rng = Math.max(0.01, maxV - minV);

  ctx.strokeStyle = s.change_pct >= 0 ? COLOR_MINT : COLOR_PINK;
  ctx.lineWidth = 2;
  ctx.beginPath();
  let started = false;
  for (let i = 0; i < closes.length; i++) {
    const v = closes[i];
    if (v === null) continue;
    const x = (i / 74) * (canvas.width - 20) + 10;
    const y = canvas.height - 20 - ((v - minV) / rng) * (canvas.height - 40);
    if (!started) { ctx.moveTo(x, y); started = true; }
    else { ctx.lineTo(x, y); }
  }
  ctx.stroke();
}

// Drawers Content
async function loadScannerTable() {
  const tbody = document.getElementById("scannerTableBody");
  if (!tbody || !flowData?.stocks) return;

  tbody.innerHTML = "";
  Object.values(flowData.stocks)
    .sort((a, b) => b.change_pct - a.change_pct)
    .forEach(s => {
      const tr = document.createElement("tr");
      tr.innerHTML = `
        <td style="font-weight: 800; color: #fff;">${escapeHtml(s.symbol)}</td>
        <td style="color: ${s.change_pct >= 0 ? COLOR_MINT : COLOR_PINK}; font-weight: 700;">${s.change_pct >= 0 ? "+" : ""}${s.change_pct.toFixed(2)}%</td>
        <td>Rs ${s.last.toFixed(2)}</td>
        <td>${s.rvol.toFixed(2)}</td>
        <td>${s.score}</td>
        <td>${escapeHtml(s.grade)}</td>
        <td style="font-size: 10px;">${escapeHtml(s.action)}</td>
      `;
      tr.style.cursor = "pointer";
      tr.onclick = () => {
        pinStock(s.symbol);
        openStockModal(s.symbol);
      };
      tbody.appendChild(tr);
    });
}

async function loadRecapPanel() {
  const container = document.getElementById("recapContentArea");
  if (!container) return;

  container.innerHTML = `<div style="color: var(--text-muted); padding: 12px;">Loading session recap...</div>`;

  try {
    const targetDate = flowData?.session_date;
    if (!targetDate) {
      container.textContent = "No received session is selected. Load a session before opening its recap.";
      return;
    }
    const res = await fetch(`/api/session/summary?date=${encodeURIComponent(targetDate)}`);
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    const data = await res.json();

    const sc = data.scorecard || {};
    const snap = data.snapshot || {};

    container.innerHTML = `
      <div style="font-family: var(--font-mono); font-size: 11px; display: flex; flex-direction: column; gap: 14px;">
        <div style="background: rgba(0,0,0,0.3); padding: 12px; border-radius: 6px; border: 1px solid var(--border-hairline);">
          <div style="font-size: 13px; font-weight: 800; color: #fff; margin-bottom: 6px;">SESSION SUMMARY - ${escapeHtml(snap.session_date || targetDate)}</div>
          <div>Data Source: ${escapeHtml(snap.data_source || "candles.db archive")}</div>
          <div>Completeness: ${snap.complete ? "<span style='color: var(--mint-green);'>Complete (>=90% bars)</span>" : "<span style='color: var(--amber-warn);'>INCOMPLETE</span>"}</div>
          <div>Total Tracked Symbols: ${Object.keys(snap.stocks || {}).length}</div>
          <div>Advancers: ${snap.breadth?.advancers || 0} | Decliners: ${snap.breadth?.decliners || 0}</div>
        </div>

        <div style="background: rgba(0,0,0,0.3); padding: 12px; border-radius: 6px; border: 1px solid var(--border-hairline);">
          <div style="font-size: 13px; font-weight: 800; color: #fff; margin-bottom: 6px;">HONEST SIGNAL SCORECARD</div>
          ${sc.signals === 0 ? `<div style="color: var(--text-muted);">${escapeHtml(sc.note || "No signals were logged in this session")}</div>` : `
            <div>Total Logged Signals: ${sc.signals}</div>
            <div>Wins (T1/T2): ${sc.wins} | Losses (SL): ${sc.losses} | Expired: ${sc.expired}</div>
            <div>Win Rate: ${sc.win_rate_pct}% (Sample: ${sc.sample_size})</div>
            <div>Avg R: ${sc.avg_r}</div>
          `}
        </div>
      </div>
    `;
  } catch (e) {
    container.innerHTML = `<div style="color: var(--pink-red); padding: 12px;">Failed to load session summary.</div>`;
  }
}

async function loadLayaPanel(symbol) {
  const drawer = document.getElementById("layaDrawer");
  const titleEl = document.getElementById("layaTitleSym");
  const contentEl = document.getElementById("layaContentArea");
  if (!drawer || !contentEl) return;

  drawer.classList.add("open");
  if (titleEl) { titleEl.textContent = symbol; titleEl.setAttribute("data-margin-symbol", symbol); }
  if (sessionState !== "LIVE") {
    const stock = flowData?.stocks?.[symbol];
    const summary = stock
      ? `${symbol} · ${flowData.session_date} · received five-minute session. Directional score ${stock.score ?? "unavailable"}/100. ${stock.action || "No qualifying setup"}. RSI ${stock.rsi ?? "unavailable"}, relative volume ${stock.rvol ?? "unavailable"}. This is a deterministic explanation of the selected historical observation.`
      : "No received stock observation is available for the selected session.";
    contentEl.innerHTML = `<div style="padding:12px;line-height:1.7"><strong>SELECTED SESSION · RULES</strong><p>${escapeHtml(summary)}</p></div>`;
    return;
  }
  contentEl.innerHTML = `
    <div style="color: var(--text-muted); font-size: 11px; padding: 12px;">
      <span class="pulse-dot" style="display: inline-block; background: var(--ref-blue); margin-right: 6px;"></span>
      Laya AI generating technical brief for ${escapeHtml(symbol)}...
    </div>
  `;

  try {
    const res = await fetch(`/api/ai/analyze?symbol=${encodeURIComponent(symbol)}`);
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    const data = await res.json();

    contentEl.innerHTML = `
      <div style="font-family: var(--font-mono); font-size: 11px; display: flex; flex-direction: column; gap: 12px;">
        <div style="display: flex; justify-content: space-between; align-items: center; border-bottom: 1px solid var(--border-hairline); padding-bottom: 8px;">
          <span style="font-weight: 800; color: #fff; font-size: 13px;">${escapeHtml(data.symbol)}</span>
          <span style="background: rgba(58,99,255,0.2); border: 1px solid var(--ref-blue); color: #8da6ff; padding: 2px 6px; border-radius: 4px; font-size: 10px;">
            ${escapeHtml(data.source || "rules_engine")}
          </span>
        </div>
        <div style="line-height: 1.6; color: var(--text-primary); white-space: pre-wrap;">${escapeHtml(data.institutional_briefing || data.summary || data.analysis || "No analysis available.")}</div>
      </div>
    `;
  } catch (e) {
    contentEl.innerHTML = `<div style="color: var(--pink-red); padding: 12px;">Failed to generate AI brief: ${escapeHtml(e.message)}</div>`;
  }
}

