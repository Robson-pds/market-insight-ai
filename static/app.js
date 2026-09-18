/* ============================================================
   Market Insight AI — Frontend Opções Binárias (MTF)
============================================================ */

let chart = null;
let candleSeries = null;
let ema10Series = null;
let ema20Series = null;
let bbUpperSeries = null;
let bbLowerSeries = null;

let currentAsset = "EURUSD";
let currentInterval = 300;   // timeframe visual do gráfico (5min default)
let currentExpiry = "1min";
let selectedStrategy = "trend_pullback";
let strategyCatalog = {};
let analysisConfirmed = false;
let refreshTimer = null;
let signalTimer = null;
let opportunitiesTimer = null;
let analysisRefreshInFlight = false;
const handledExpiries = new Set();
const expiryTimeouts = {};
const signalExpiries = {};
const expirySeconds = { "1min": 60, "5min": 300, "15min": 900 };
const radarPairs = new Map();
let radarSortExpiry = "1min";
let radarRunId = 0;

const $ = (id) => document.getElementById(id);

/* ============================================================
   Navegação por abas
============================================================ */
function switchView(view) {
  document.querySelectorAll(".view-tab").forEach((t) => {
    t.classList.toggle("active", t.dataset.view === view);
  });
  $("view-trade").classList.toggle("hidden", view !== "trade");
  $("view-radar").classList.toggle("hidden", view !== "radar");
  $("view-opportunities").classList.toggle("hidden", view !== "opportunities");
  $("view-news").classList.toggle("hidden", view !== "news");
  if (view === "radar" && !$("radar-output").dataset.loaded) runRadar();
  if (view === "news") loadNews();
  window.scrollTo({ top: 0, behavior: "smooth" });
}
$("gate-strategy-select").addEventListener("change", (event) => {
  selectedStrategy = event.target.value;
  renderGateDescription();
});

document.querySelectorAll(".view-tab").forEach((tab) => {
  tab.addEventListener("click", () => switchView(tab.dataset.view));
});

$("confirm-strategy").addEventListener("click", confirmStrategy);
$("login-button").addEventListener("click", () => $("login-modal").classList.remove("hidden"));
$("login-close").addEventListener("click", () => $("login-modal").classList.add("hidden"));
$("login-form").addEventListener("submit", loginAccount);
$("asset-search").addEventListener("input", (event) => renderAssets(event.target.value));
$("radar-strategy-select").addEventListener("change", (event) => {
  selectedStrategy = event.target.value;
  runRadar();
});
document.querySelectorAll(".radar-sort").forEach((button) => {
  button.addEventListener("click", () => {
    radarSortExpiry = button.dataset.radarSort;
    renderRadar({ pairs: Array.from(radarPairs.values()) });
  });
});
$("news-importance").addEventListener("change", loadNews);
$("btn-news-refresh").addEventListener("click", loadNews);
$("change-strategy").addEventListener("click", () => {
  analysisConfirmed = false;
  $("strategy-gate").classList.remove("hidden");
  $("main-navigation").classList.add("hidden");
  $("view-opportunities").classList.add("hidden");
  $("view-trade").classList.add("hidden");
});
/* ============================================================
  Seletor de timeframe do gráfico
============================================================ */
document.querySelectorAll(".tf-btn").forEach((btn) => {
  btn.addEventListener("click", () => {
    const newInterval = parseInt(btn.dataset.interval);
    if (newInterval === currentInterval) return;

    document.querySelectorAll(".tf-btn").forEach((b) => b.classList.remove("active"));
    btn.classList.add("active");

    currentInterval = newInterval;
    const labelMap = { 60: "1min", 300: "5min", 900: "15min" };
    $("chart-meta").textContent = `timeframe: ${labelMap[currentInterval] || currentInterval}`;

    loadChart();
  });
});

/* ============================================================
   Boot
============================================================ */
async function boot() {
  await ensureConnected();
  await loadAssets();
  await loadStrategies();
  await checkStatus();
  initChart();
  await loadChart();
  await loadBalance();

  if (refreshTimer) clearInterval(refreshTimer);
  refreshTimer = setInterval(loadChart, 3000);
  setInterval(loadBalance, 20000);
  setInterval(ensureConnected, 60000);

  // Relogio sempre ativo e independente da analise, para nunca congelar.
  if (signalTimer) clearInterval(signalTimer);
  signalTimer = setInterval(updateTimers, 250);
  document.addEventListener("visibilitychange", updateTimers);
}

async function loadStrategies() {
  try {
    const response = await fetch("/api/strategies");
    const data = await response.json();
    strategyCatalog = data.strategies || {};
    const select = $("gate-strategy-select");
    select.innerHTML = Object.entries(strategyCatalog).map(([key, item]) =>
      `<option value="${key}">${escapeHtml(item.name)}</option>`
    ).join("");
    select.value = selectedStrategy;
    const radarSelect = $("radar-strategy-select");
    radarSelect.innerHTML = select.innerHTML;
    radarSelect.value = selectedStrategy;
    renderGateDescription();
  } catch (error) {
    $("gate-strategy-description").textContent = "Não foi possível carregar as estratégias.";
  }
}

function renderGateDescription() {
  const selected = strategyCatalog[selectedStrategy];
  $("gate-strategy-description").textContent = selected ? selected.description : "";
}

async function confirmStrategy() {
  selectedStrategy = $("gate-strategy-select").value;
  analysisConfirmed = true;
  $("strategy-gate").classList.add("hidden");
  $("main-navigation").classList.remove("hidden");
  switchView("opportunities");
  await loadOpportunities();
  if (opportunitiesTimer) clearInterval(opportunitiesTimer);
  opportunitiesTimer = setInterval(() => {
    if (!$("view-opportunities").classList.contains("hidden")) loadOpportunities();
  }, 60000);
}

async function loadOpportunities() {
  const status = $("opportunities-status");
  const grid = $("opportunities-grid");
  status.className = "status loading";
  status.textContent = "Analisando os pares com a estratégia selecionada…";
  grid.innerHTML = "";
  try {
    const response = await fetch(`/api/opportunities?strategy=${encodeURIComponent(selectedStrategy)}`);
    if (!response.ok) throw new Error("Falha ao rastrear os pares");
    const data = await response.json();
    $("opportunities-title").textContent = `Melhores pares: ${data.strategy_name}`;
    $("opportunities-description").textContent = strategyCatalog[selectedStrategy]?.description || "";
    renderOpportunities(data.pairs || []);
    status.className = "status";
    status.textContent = `${data.pairs.length} pares analisados. Clique em um par para abrir o detalhe.`;
  } catch (error) {
    status.className = "status error";
    status.textContent = error.message;
  }
}

function renderOpportunities(pairs) {
  const grid = $("opportunities-grid");
  pairs.forEach((pair, index) => {
    const card = document.createElement("article");
    card.className = "opportunity-card";
    card.innerHTML = `
      <div class="opportunity-rank">#${index + 1}</div>
      <div class="opportunity-pair">${escapeHtml(pair.asset)}</div>
      <div class="opportunity-score"><span>Confluência técnica</span><strong>${pair.score} de ${pair.max_score || 12} critérios</strong></div>
      <div class="opportunity-signals">
        <span>${signalBadge(pair.signals?.["1min"], "1m", pair.proximity?.["1min"])}</span>
        <span>${signalBadge(pair.signals?.["5min"], "5m", pair.proximity?.["5min"])}</span>
        <span>${signalBadge(pair.signals?.["15min"], "15m", pair.proximity?.["15min"])}</span>
      </div>
      <p>${escapeHtml(pair.reason || "Sem confirmação suficiente.")}</p>
      <button class="btn-secondary">Abrir análise</button>
    `;
    card.querySelector("button").addEventListener("click", () => {
      selectAsset(pair.asset);
      switchView("trade");
      loadChart();
      loadAnalysis(true);
    });
    grid.appendChild(card);
  });
}

function signalBadge(signal, label, proximity) {
  const safe = signal || "AGUARDAR";
  const cls = safe === "CALL" ? "call" : safe === "PUT" ? "put" : "neutral";
  const displayStatus = safe === "CALL" || safe === "PUT" ? safe : (proximity?.label || "AGUARDAR");
  return `<b class="mini-signal ${cls}">${label} ${escapeHtml(displayStatus)}</b>`;
}

async function ensureConnected() {
  try {
    const r = await fetch("/api/connect");
    const d = await r.json();
    const el = $("conn-status");
    if (d.ok) {
      el.classList.remove("err");
      el.classList.add("ok");
      $("conn-text").textContent = "Conectado à IQ Option";
    } else {
      el.classList.remove("ok");
      el.classList.add("err");
      $("conn-text").textContent = d.message || "Falha na conexão";
    }
  } catch {
    $("conn-status").classList.remove("ok");
    $("conn-status").classList.add("err");
    $("conn-text").textContent = "Erro de conexão";
  }
}

async function loginAccount(event) {
  event.preventDefault();
  const status = $("login-status");
  status.textContent = "Conectando…";
  try {
    const response = await fetch("/api/login", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ email: $("login-email").value, password: $("login-password").value }),
    });
    const data = await response.json();
    if (!response.ok || !data.ok) throw new Error(data.message || "Falha no login");
    $("login-password").value = "";
    $("login-modal").classList.add("hidden");
    await checkStatus();
    await loadBalance();
    await loadAnalysis();
    status.textContent = "";
  } catch (error) {
    status.textContent = error.message;
  }
}

async function checkStatus() {
  try {
    const r = await fetch("/api/status");
    const d = await r.json();
    const el = $("conn-status");
    if (d.connected) {
      el.classList.remove("err");
      el.classList.add("ok");
    }
  } catch {}
}

async function loadBalance() {
  try {
    const r = await fetch("/api/status");
    const d = await r.json();
    if (d.balance != null) {
      $("balance-value").textContent = `$ ${d.balance.toFixed(2)}`;
    }
  } catch {}
}

/* ============================================================
   Lista de ativos
============================================================ */
async function loadAssets() {
  try {
    const r = await fetch("/api/assets");
    const d = await r.json();
    window.availableAssets = d.assets || [];
    renderAssets();
  } catch {}
}

function renderAssets(filter = "") {
  const list = $("asset-list");
  const query = filter.trim().toUpperCase();
  list.innerHTML = "";
  (window.availableAssets || []).filter((asset) => asset.includes(query)).forEach((asset) => {
    const btn = document.createElement("button");
    btn.className = "asset-btn" + (asset === currentAsset ? " active" : "");
    btn.textContent = asset;
    btn.addEventListener("click", () => selectAsset(asset));
    list.appendChild(btn);
  });
}

function selectAsset(asset) {
  currentAsset = asset;
  document.querySelectorAll(".asset-btn").forEach((b) => {
    b.classList.toggle("active", b.textContent === asset);
  });
  $("chart-pair").textContent = asset;
  loadChart();
  loadAnalysis();
}

/* ============================================================
   Gráfico
============================================================ */
function initChart() {
  const container = $("chart");
  container.innerHTML = "";

  chart = LightweightCharts.createChart(container, {
    layout: {
      background: { color: "#0b1118" },
      textColor: "#8497b0",
      fontSize: 11,
    },
    grid: {
      vertLines: { color: "rgba(35,50,68,0.4)" },
      horzLines: { color: "rgba(35,50,68,0.4)" },
    },
    rightPriceScale: { borderColor: "#233244" },
    timeScale: {
      borderColor: "#233244",
      timeVisible: true,
      secondsVisible: false,
    },
    crosshair: {
      mode: LightweightCharts.CrosshairMode.Normal,
      vertLine: { color: "#4a9eff", width: 1, style: 2, labelBackgroundColor: "#4a9eff" },
      horzLine: { color: "#4a9eff", width: 1, style: 2, labelBackgroundColor: "#4a9eff" },
    },
  });

  candleSeries = chart.addCandlestickSeries({
    upColor: "#26de81",
    downColor: "#ff5c5c",
    borderUpColor: "#26de81",
    borderDownColor: "#ff5c5c",
    wickUpColor: "#26de81",
    wickDownColor: "#ff5c5c",
  });

  ema10Series = chart.addLineSeries({
    color: "#f7b731", lineWidth: 1.5,
    priceLineVisible: false, lastValueVisible: false,
  });
  ema20Series = chart.addLineSeries({
    color: "#a55eea", lineWidth: 1.5,
    priceLineVisible: false, lastValueVisible: false,
  });
  bbUpperSeries = chart.addLineSeries({
    color: "rgba(132,151,176,0.5)", lineWidth: 1,
    lineStyle: 2, priceLineVisible: false, lastValueVisible: false,
  });
  bbLowerSeries = chart.addLineSeries({
    color: "rgba(132,151,176,0.5)", lineWidth: 1,
    lineStyle: 2, priceLineVisible: false, lastValueVisible: false,
  });

  chart.subscribeClick(() => loadAnalysis(true));

  const ro = new ResizeObserver(() => {
    chart.applyOptions({
      width: container.clientWidth,
      height: container.clientHeight,
    });
  });
  ro.observe(container);
}

async function loadChart() {
  try {
    const r = await fetch(`/api/candles/${currentAsset}?interval=${currentInterval}&count=200`);
    if (!r.ok) return;
    const d = await r.json();
    if (!d.candles || d.candles.length === 0) return;

    candleSeries.setData(d.candles.map((c) => ({
      time: c.time, open: c.open, high: c.high, low: c.low, close: c.close,
    })));
    if (d.ema10) ema10Series.setData(d.ema10.map((p) => ({ time: p.time, value: p.value })));
    if (d.ema20) ema20Series.setData(d.ema20.map((p) => ({ time: p.time, value: p.value })));
    if (d.bb_upper) bbUpperSeries.setData(d.bb_upper.map((p) => ({ time: p.time, value: p.value })));
    if (d.bb_lower) bbLowerSeries.setData(d.bb_lower.map((p) => ({ time: p.time, value: p.value })));

    const last = d.candles[d.candles.length - 1];
    const prev = d.candles[d.candles.length - 2] || last;
    const change = prev.close ? ((last.close - prev.close) / prev.close) * 100 : 0;

    $("chart-price").textContent = last.close.toFixed(5);
    const chgEl = $("chart-change");
    chgEl.textContent = `${change >= 0 ? "+" : ""}${change.toFixed(3)}%`;
    chgEl.className = `price-change ${change >= 0 ? "up" : "down"}`;
  } catch (e) {
    console.error("Erro ao carregar gráfico:", e);
  }
}

/* ============================================================
   Análise Multi-Timeframe
============================================================ */
async function loadAnalysis(highlight = false) {
  try {
    const r = await fetch(`/api/analyze/${currentAsset}?strategy=${encodeURIComponent(selectedStrategy)}`);
    if (!r.ok) {
      const err = await r.json().catch(() => ({}));
      console.error("Erro /api/analyze:", err);
      $("analysis-note").textContent = `Erro ao atualizar a análise: ${err.detail || r.status}`;
      return;
    }
    const d = await r.json();

    // Diagnóstico: se a estratégia vier vazia ou em branco, avisa
    if (!d.strategy) {
      $("analysis-note").textContent = "O backend não retornou uma estratégia válida.";
      return;
    }

    window.latestAnalysis = d;
    renderAnalysis(d, highlight);
  } catch (e) {
    console.error("Erro análise:", e);
    $("analysis-note").textContent = `Erro ao atualizar a análise: ${e.message}`;
  }
}

function renderAnalysis(d, highlight = false) {
  $("active-strategy").textContent = `Estratégia: ${d.strategy_name}`;
  $("analysis-note").textContent = d.warning || (d.news && d.news.warning) || "O sinal só muda quando o prazo correspondente expirar.";
  Object.entries(d.signals || {}).forEach(([expiry, signal]) => renderSignal(expiry, signal));
  if (!signalTimer) signalTimer = setInterval(() => updateTimers(window.latestAnalysis), 1000);
  updateTimers(d);
  scheduleSignalRefresh(d);
}

/**
 * Formata segundos restantes em MM:SS.
 */
function formatCountdown(seconds) {
  const safe = Math.max(0, Math.floor(seconds || 0));
  const minutes = Math.floor(safe / 60).toString().padStart(2, "0");
  const secs = (safe % 60).toString().padStart(2, "0");
  return `${minutes}:${secs}`;
}

function renderSignal(expiry, signal) {
  const card = document.querySelector(`[data-expiry-card="${expiry}"]`);
  const direction = signal.signal || "AGUARDAR";
  const proximity = signal.proximity?.label || "AGUARDAR";
  const displayStatus = direction === "CALL" || direction === "PUT" ? direction : proximity;
  const statusClass = displayStatus === "ATENÇÃO" ? "attention" : displayStatus === "SINAL MUITO PRÓXIMO" ? "near" : "";
  card.className = `expiry-card ${direction === "CALL" ? "call" : direction === "PUT" ? "put" : "neutral"} ${statusClass}`.trim();
  $( `signal-${expiry}` ).textContent = displayStatus;
  const accuracy = signal.historical_accuracy;
  const accuracyText = accuracy && accuracy.rate !== null
    ? `Estimativa histórica: ${accuracy.rate.toFixed(1)}% (${accuracy.sample_size} casos)`
    : "Estimativa histórica: sem amostra suficiente";
  $( `reason-${expiry}` ).textContent = `${signal.reason || "Sem confirmação suficiente."} ${accuracyText}`;
  $( `lock-${expiry}` ).textContent = signal.locked ? "SINAL FIXADO" : "NOVO SINAL";
  card.dataset.expiresAt = signal.expires_at || "";
  const expiresAt = Number(signal.expires_at);
  if (Number.isFinite(expiresAt) && expiresAt > 0) signalExpiries[expiry] = expiresAt;
}

function updateTimers(data) {
  if (data && data.signals) {
    Object.entries(data.signals).forEach(([expiry, signal]) => {
      const expiresAt = Number(signal && signal.expires_at);
      if (Number.isFinite(expiresAt) && expiresAt > 0) signalExpiries[expiry] = expiresAt;
    });
  }

  Object.entries(signalExpiries).forEach(([expiry, storedExpiresAt]) => {
    const timerEl = $(`timer-${expiry}`);
    const interval = expirySeconds[expiry];
    if (!timerEl || !interval) return;

    const now = Date.now() / 1000;
    let expiresAt = Number(storedExpiresAt);
    if (expiresAt <= now) {
      const expiredAt = expiresAt;
      const elapsedCycles = Math.floor((now - expiresAt) / interval) + 1;
      expiresAt += elapsedCycles * interval;
      signalExpiries[expiry] = expiresAt;

      const expiryKey = `${expiry}:${expiredAt}`;
      if (!handledExpiries.has(expiryKey)) {
        handledExpiries.add(expiryKey);
        const lock = $(`lock-${expiry}`);
        if (lock) lock.textContent = "ATUALIZANDO";
        refreshAnalysisAfterExpiry();
      }
    }

    timerEl.textContent = formatCountdown(Math.ceil(expiresAt - now));
  });
}

function scheduleSignalRefresh(data) {
  Object.entries(data.signals || {}).forEach(([expiry, signal]) => {
    const expiresAt = Number(signal.expires_at);
    if (!Number.isFinite(expiresAt)) return;
    if (expiryTimeouts[expiry]) clearTimeout(expiryTimeouts[expiry]);
    const delay = Math.max(0, expiresAt * 1000 - Date.now() + 150);
    expiryTimeouts[expiry] = setTimeout(() => {
      const expiryKey = `${expiry}:${expiresAt}`;
      if (!handledExpiries.has(expiryKey)) {
        handledExpiries.add(expiryKey);
        refreshAnalysisAfterExpiry();
      }
    }, delay);
  });
}

async function refreshAnalysisAfterExpiry() {
  if (analysisRefreshInFlight) return;
  analysisRefreshInFlight = true;
  try {
    await loadAnalysis();
  } finally {
    analysisRefreshInFlight = false;
  }
}

/* ---- Card de Estratégia ---- */
function renderStrategy(strategy) {
  if (!strategy) return;
  const el = $("strategy-card");
  const expiryLabels = { "1min": "1 MINUTO", "5min": "5 MINUTOS", "15min": "15 MINUTOS" };
  $("strategy-label").textContent = `RECOMENDAÇÃO PARA ${expiryLabels[currentExpiry] || currentExpiry}`;

  const rec = strategy.recommendation || "AGUARDAR";
  let cls = "neutral";
  let arrow = "●";
  if (rec === "CALL") { cls = "call"; arrow = "▲"; }
  else if (rec === "PUT") { cls = "put"; arrow = "▼"; }

  el.className = `strategy-card ${cls}`;
  el.querySelector(".strategy-arrow").textContent = arrow;
  $("strategy-rec").textContent = rec === "AGUARDAR" ? "AGUARDAR" : rec;
  $("strategy-reason").textContent = strategy.reason || "";
  $("strategy-warning").textContent = strategy.warning || "";

  const conf = strategy.confidence || 0;
  $("strategy-conf-fill").style.width = `${conf}%`;
  $("strategy-conf-num").textContent = `${conf.toFixed(0)}% score de confluência · ${strategy.strength || ""}`;
}

/* ---- Cartões de contexto (15min e 5min) ---- */
function renderContextCards(context) {
  const wrap = $("context-cards");
  wrap.innerHTML = "";
  const order = ["30min", "15min", "5min"];
  const labels = { "30min": "30 MINUTOS", "15min": "15 MINUTOS", "5min": "5 MINUTOS" };

  order.forEach((tfKey) => {
    const tf = context[tfKey];
    if (!tf) return;
    wrap.appendChild(buildSignalCard(tfKey, labels[tfKey], tf));
  });
}

/* ---- Cartão de gatilho (1min) ---- */
function renderTriggerCard(trigger) {
  const wrap = $("trigger-cards");
  wrap.innerHTML = "";
  if (!trigger) return;
  wrap.appendChild(buildSignalCard("1min", "1 MINUTO", trigger));
}

function buildSignalCard(tfKey, label, tf) {
  const cls = tf.signal === "CALL" ? "call" : tf.signal === "PUT" ? "put" : "neutral";
  const arrow = tf.signal === "CALL" ? "▲" : tf.signal === "PUT" ? "▼" : "●";
  const sigLabel = tf.signal === "CALL" ? "CALL" : tf.signal === "PUT" ? "PUT" : "NEUTRO";

  const card = document.createElement("div");
  card.className = `signal-card ${cls}`;
  card.innerHTML = `
    <div class="signal-head">
      <span class="signal-tf">${label}</span>
      <span class="signal-strength">${tf.strength}</span>
    </div>
    <div class="signal-main">
      <div class="signal-label">
        <span class="signal-arrow">${arrow}</span>
        <span>${sigLabel}</span>
      </div>
      <div class="signal-conf-big">${tf.confidence.toFixed(0)}%</div>
    </div>
    <div class="signal-bar">
      <div class="signal-bar-fill" style="width:${tf.confidence}%"></div>
    </div>
    <div class="signal-votes">
      <span class="vote-bull">${tf.bulls} CALL</span>
      <span class="vote-bear">${tf.bears} PUT</span>
      <span class="vote-neu">${tf.neutrals} neutros</span>
    </div>
  `;
  return card;
}

/* ---- Indicadores detalhados ---- */
function renderIndicators(context, trigger) {
  const wrap = $("indicators-wrap");
  wrap.innerHTML = "";

  const all = [
    ["15min", context["15min"], "15 minutos"],
    ["5min",  context["5min"],  "5 minutos"],
    ["1min",  trigger,           "1 minuto"],
  ];

  all.forEach(([key, tf, label]) => {
    if (!tf) return;
    const cls = tf.signal === "CALL" ? "call" : tf.signal === "PUT" ? "put" : "neutral";
    const block = document.createElement("div");
    block.className = "ind-tf-block";

    const header = document.createElement("div");
    header.className = "ind-tf-header";
    header.innerHTML = `
      <strong>⏱ ${label}</strong>
      <span class="ind-tf-badge ${cls}">
        ${tf.signal} · ${tf.confidence.toFixed(0)}%
      </span>
    `;
    block.appendChild(header);

    (tf.indicators || []).forEach((ind) => {
      const vCls = ind.vote > 0 ? "pos" : ind.vote < 0 ? "neg" : "zero";
      const arrow = ind.vote > 0 ? "▲" : ind.vote < 0 ? "▼" : "●";
      const row = document.createElement("div");
      row.className = "ind-row";
      row.innerHTML = `
        <div class="ind-name">${escapeHtml(ind.name)}</div>
        <div class="ind-arrow ${vCls}">${arrow}</div>
        <div class="ind-reason">${escapeHtml(ind.reason)}</div>
      `;
      block.appendChild(row);
    });

    wrap.appendChild(block);
  });
}

/* ============================================================
   Radar
============================================================ */
async function runRadar() {
  const runId = ++radarRunId;
  const strategy = selectedStrategy;
  const btn = $("btn-radar");
  btn.disabled = true;
  btn.textContent = "Atualizando...";
  const assets = window.availableAssets || [];
  assets.forEach((asset) => {
    const previous = radarPairs.get(asset) || { asset, signals: {}, scores: {}, accuracy: {} };
    radarPairs.set(asset, { ...previous, loading: true, error: null });
  });
  $("radar-output").classList.remove("hidden");
  $("radar-output").dataset.loaded = "true";
  renderRadar({ pairs: Array.from(radarPairs.values()) });
  showRadarStatus("loading", `Analisando 0 de ${assets.length} pares...`);

  try {
    let nextIndex = 0;
    let completed = 0;
    const analyzeNext = async () => {
      while (nextIndex < assets.length && runId === radarRunId) {
        const asset = assets[nextIndex++];
        try {
          const response = await fetch(`/api/opportunity/${encodeURIComponent(asset)}?strategy=${encodeURIComponent(strategy)}`);
          if (!response.ok) {
            const error = await response.json().catch(() => ({}));
            throw new Error(error.detail || `Falha ao analisar ${asset}`);
          }
          const pair = await response.json();
          if (runId !== radarRunId) return;
          radarPairs.set(asset, { ...pair, loading: false, error: null });
        } catch (error) {
          if (runId !== radarRunId) return;
          const previous = radarPairs.get(asset) || { asset, signals: {}, scores: {}, accuracy: {} };
          radarPairs.set(asset, { ...previous, loading: false, error: error.message });
        }
        if (runId !== radarRunId) return;
        completed += 1;
        renderRadar({ pairs: Array.from(radarPairs.values()) });
        showRadarStatus("loading", `Analisando ${completed} de ${assets.length} pares...`);
      }
    };

    const workers = Array.from({ length: Math.min(3, assets.length) }, analyzeNext);
    await Promise.all(workers);
    if (runId === radarRunId) {
      hideRadarStatus();
    }
  } catch (e) {
    showRadarStatus("error", `Erro: ${e.message}`);
  } finally {
    if (runId === radarRunId) {
      btn.disabled = false;
      btn.textContent = "Atualizar pares";
    }
  }
}

async function loadNews() {
  const status = $("news-status");
  const list = $("news-list");
  const importance = $("news-importance").value;
  status.className = "status loading";
  status.textContent = "Consultando Biquote…";
  try {
    const response = await fetch(`/api/news?hours=24&importance=${importance}`);
    if (!response.ok) throw new Error("Não foi possível consultar as notícias");
    const data = await response.json();
    list.innerHTML = "";
    if (!data.available) throw new Error(data.warning || "Biquote indisponível");
    renderNews(data.events || []);
    $("news-meta").textContent = `${data.events.length} eventos nas próximas 24 horas · Fonte: Biquote`;
    status.className = "status";
    status.textContent = data.events.length ? "Eventos ordenados por horário." : "Nenhum evento encontrado para este filtro.";
  } catch (error) {
    status.className = "status error";
    status.textContent = error.message;
  }
}

function renderNews(events) {
  const list = $("news-list");
  events.forEach((event) => {
    const item = document.createElement("article");
    const impact = event.importance || "low";
    const time = new Date(event.time);
    item.className = `news-item impact-${impact}`;
    item.innerHTML = `
      <div class="news-time">${time.toLocaleTimeString("pt-BR", { hour: "2-digit", minute: "2-digit" })}<small>${time.toLocaleDateString("pt-BR", { day: "2-digit", month: "2-digit" })}</small></div>
      <div class="news-currency">${escapeHtml(event.currency || "—")}</div>
      <div class="news-content"><strong>${escapeHtml(event.title)}</strong><span>${escapeHtml(event.sector || "Indicador econômico")}</span><small>${escapeHtml(event.description || "Evento macroeconômico agendado.")}</small></div>
      <div class="news-impact">${impact.toUpperCase()}</div>
      <div class="news-values"><span>Prev. ${formatValue(event.forecast)}</span><span>Ant. ${formatValue(event.previous)}</span></div>
    `;
    list.appendChild(item);
  });
}

function formatValue(value) {
  return value === null || value === undefined ? "—" : String(value);
}

function showRadarStatus(kind, msg) {
  const el = $("radar-status");
  el.className = `status ${kind}`;
  el.textContent = msg;
  el.classList.remove("hidden");
}
function hideRadarStatus() { $("radar-status").classList.add("hidden"); }

function renderRadar(d) {
  const pairs = [...(d.pairs || [])].sort((first, second) => {
    const firstRawRate = first.accuracy?.[radarSortExpiry]?.rate;
    const secondRawRate = second.accuracy?.[radarSortExpiry]?.rate;
    const firstRate = firstRawRate === null || firstRawRate === undefined ? -1 : Number(firstRawRate);
    const secondRate = secondRawRate === null || secondRawRate === undefined ? -1 : Number(secondRawRate);
    const safeFirstRate = Number.isFinite(firstRate) ? firstRate : -1;
    const safeSecondRate = Number.isFinite(secondRate) ? secondRate : -1;
    if (safeSecondRate !== safeFirstRate) return safeSecondRate - safeFirstRate;
    const scoreDifference = (second.scores?.[radarSortExpiry] || 0) - (first.scores?.[radarSortExpiry] || 0);
    return scoreDifference || first.asset.localeCompare(second.asset);
  });
  const selectedSignals = pairs.map((pair) => pair.signals?.[radarSortExpiry] || "AGUARDAR");
  $("sum-call").textContent = selectedSignals.filter((signal) => signal === "CALL").length;
  $("sum-neu").textContent = selectedSignals.filter((signal) => signal === "AGUARDAR").length;
  $("sum-put").textContent = selectedSignals.filter((signal) => signal === "PUT").length;
  const shortExpiry = radarSortExpiry.replace("min", " min");
  $("sum-call-label").textContent = `CALL (${shortExpiry})`;
  $("sum-neutral-label").textContent = `AGUARDAR (${shortExpiry})`;
  $("sum-put-label").textContent = `PUT (${shortExpiry})`;
  $("radar-meta").textContent = `${pairs.length} pares · ordenado por assertividade em ${shortExpiry}`;
  document.querySelectorAll(".radar-sort").forEach((button) => {
    const active = button.dataset.radarSort === radarSortExpiry;
    button.classList.toggle("active", active);
    button.setAttribute("aria-sort", active ? "descending" : "none");
  });

  const tbody = $("radar-body");
  tbody.innerHTML = "";

  if (pairs.length === 0) {
    tbody.innerHTML = `<tr><td colspan="4" class="muted" style="text-align:center;padding:24px">Nenhum par disponível.</td></tr>`;
    return;
  }

  pairs.forEach((p) => {
    const signals = p.signals || {};
    const tr = document.createElement("tr");
    tr.className = `radar-row${p.loading ? " loading" : ""}`;
    tr.title = p.error || p.reason || "";
    tr.innerHTML = `
      <td class="pair">${escapeHtml(p.asset)}${p.error ? `<small>${escapeHtml(p.error)}</small>` : ""}</td>
      <td>${tfBadge(signals["1min"], p.proximity?.["1min"], p.accuracy?.["1min"], p.loading)}</td>
      <td>${tfBadge(signals["5min"], p.proximity?.["5min"], p.accuracy?.["5min"], p.loading)}</td>
      <td>${tfBadge(signals["15min"], p.proximity?.["15min"], p.accuracy?.["15min"], p.loading)}</td>
    `;
    tr.addEventListener("click", () => {
      selectAsset(p.asset);
      switchView("trade");
    });
    tbody.appendChild(tr);
  });
}

function tfBadge(signal, proximity, accuracy, loading = false) {
  if (!signal) return `<span class="tf-badge analyzing">ANALISANDO</span>`;
  const safe = signal || "AGUARDAR";
  const cls = safe === "CALL" ? "call" : safe === "PUT" ? "put" : "neutral";
  const arrow = safe === "CALL" ? "▲" : safe === "PUT" ? "▼" : "●";
  const label = safe === "CALL" || safe === "PUT" ? safe : (proximity?.label || "AGUARDAR");
  const rate = accuracy && accuracy.rate !== null ? `${Number(accuracy.rate).toFixed(1)}%` : "sem amostra";
  const state = loading ? " · atualizando" : "";
  return `<span class="tf-badge ${cls}">${arrow} ${escapeHtml(label)}<em>${rate}${state}</em></span>`;
}

/* ============================================================
   Utils
============================================================ */
function escapeHtml(s) {
  return String(s || "").replace(/[&<>"']/g, (c) => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;",
  }[c]));
}

$("btn-radar").addEventListener("click", runRadar);

boot();