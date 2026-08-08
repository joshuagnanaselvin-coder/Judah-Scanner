// ═══════════════════════════════════════════════════════════════════════
// Judah — D1/D2 Hybrid Scanner Frontend (V5.2)
// Consumes signal_fusion.py payload via /ws-fusion
// ═══════════════════════════════════════════════════════════════════════

// ── State ──────────────────────────────────────────────────────────
let allSignals = [];
let ws = null;
let filters = { direction: 'all', signalType: 'all' };
const expandedCards = new Set();
let stats = { d1_coins: 0, d2_signals: 0, d3_fusion: 0, last_d1_scan: 0, last_d2_scan: 0, last_d3_fusion: 0 };
let typeEAlerts = [];

// ── Helpers ────────────────────────────────────────────────────────
function getMEE(s) { return s.marketEvolution || {}; }

function fmtPrice(v) {
  if (v == null || isNaN(v)) return '—';
  if (v === 0) return '0';
  if (v >= 1000) return v.toFixed(1);
  if (v >= 1) return v.toFixed(4);
  return v.toFixed(6);
}

function fmtRR(v) { return (v && v > 0) ? v.toFixed(1) + 'x' : '—'; }

function fmtPct(v) {
  if (v == null) return '—';
  return (v > 0 ? '+' : '') + v.toFixed(1) + '%';
}

function timeAgo(ts) {
  if (!ts) return '—';
  const s = Math.floor((Date.now() - new Date(ts).getTime()) / 1000);
  if (s < 60) return s + 's';
  if (s < 3600) return Math.floor(s / 60) + 'm';
  return Math.floor(s / 3600) + 'h';
}

function fmtAge(ts) {
  if (!ts) return '';
  const s = Math.floor((Date.now() - new Date(ts).getTime()) / 1000);
  if (s < 5) return 'LIVE';
  if (s < 30) return 'NEW';
  if (s < 120) return Math.floor(s / 15) * 15 + 's';
  return Math.floor(s / 60) + 'm';
}

// ── Signal Type colors ────────────────────────────────────────────
const STYPE_COLORS = { A: '#eab308', B: '#3b82f6', C: '#22c55e', D: '#f97316', E: '#ef4444' };
const STYPE_BG = { A: '#eab30822', B: '#3b82f622', C: '#22c55e22', D: '#f9731622', E: '#ef444422' };
const TIER_COLORS = { SNIPER: '#eab308', OPPORTUNITY: '#22c55e', WATCH: '#3b82f6', REJECTED: '#6b7280' };
const SPIRAL_COLORS = { Expansion: '#22c55e', Correction: '#f59e0b', Failure: '#ef4444', Neutral: '#6b7280' };
const DIR_COLORS = { BULLISH: '#22c55e', BEARISH: '#ef4444', NEUTRAL: '#6b7280' };

// ── Card Builder ──────────────────────────────────────────────────
function buildCard(s) {
  const mee = getMEE(s);
  const d1Score = s.d1_score ?? 0;
  const d2Score = s.d2_score ?? 0;
  const d1Tier = s.d1_tier || '—';
  const d2Tier = s.d2_tier || '—';
  const dir = s.direction || 'NEUTRAL';
  const spiral = mee.spiral || 'Neutral';
  const spiralColor = SPIRAL_COLORS[spiral] || '#6b7280';
  const meState = mee.state || '—';
  const meConf = mee.confidence ?? 0;
  const ageLabel = fmtAge(s.born_at);
  const isExpanded = expandedCards.has(s.signal_id);
  const tierColor = TIER_COLORS[d2Tier] || '#6b7280';

  // Chart links
  const rawCoin = s.coin || 'BTCUSDT';
  const base = rawCoin.replace(/USDT$/i, '').replace(/BINANCE:/i, '');
  const tvUrl = 'https://www.tradingview.com/chart/?symbol=' + encodeURIComponent('BINANCE:' + base + 'USDT.P');
  const binanceUrl = 'https://www.binance.com/en/futures/' + encodeURIComponent(base + 'USDT');

  // D1 structure
  const d1s = s.d1_structure || {};
  const d1Tags = [
    d1s.msb_type ? `<span class="tag tag-msb">MSB ${d1s.msb_type.toUpperCase()}</span>` : '',
    d1s.ob_type ? `<span class="tag tag-ob">OB ${d1s.ob_type.replace(/_OB$/, '')} ${zoneLabel(d1s.ob_zone)}</span>` : '',
    d1s.fvg_type ? `<span class="tag tag-fvg">FVG ${d1s.fvg_type}</span>` : '',
    d1s.liq_swept ? `<span class="tag tag-liq">LIQ SWEPT</span>` : '',
    d1s.poc ? `<span class="tag tag-poc">POC ${fmtPrice(d1s.poc)}</span>` : '',
    (d1s.va_low && d1s.va_high) ? `<span class="tag tag-va">VA ${fmtPrice(d1s.va_low)}–${fmtPrice(d1s.va_high)}</span>` : '',
  ].filter(Boolean).join('');

  // D2 structure
  const d2s = s.d2_structure || {};
  const d2Tags = [
    d2s.scenario ? `<span class="tag tag-scenario">${d2s.scenario}</span>` : '',
    d2s.msb_type ? `<span class="tag tag-msb">MSB ${d2s.msb_type.toUpperCase()}</span>` : '',
    d2s.ob_type ? `<span class="tag tag-ob">OB ${d2s.ob_type.replace(/_OB$/, '')} ${zoneLabel(d2s.ob_zone)}</span>` : '',
    d2s.fvg_type ? `<span class="tag tag-fvg">FVG ${d2s.fvg_type}</span>` : '',
    d2s.liq_swept ? `<span class="tag tag-liq">LIQ SWEPT</span>` : '',
    d2s.displacement_ratio ? `<span class="tag tag-disp">DISP ${d2s.displacement_ratio.toFixed(1)}x</span>` : '',
    s.nascent_move ? `<span class="tag tag-nascent">NAS</span>` : '',
  ].filter(Boolean).join('');

  // Alignment
  const align = s.alignment || {};
  const alignScore = align.alignment_score || 0;
  const alignPct = Math.round((alignScore / 20) * 100);
  const alignColor = alignScore >= 15 ? '#22c55e' : alignScore >= 8 ? '#f59e0b' : '#ef4444';

  // Score history sparkline
  const hist = (s.score_history || []).slice(-12);
  const sparkData = hist.map(h => h[1] || h.score || 0).join(',');

  // TF breakdown chips
  const tfs = s.d1_timeframes || {};
  const tfHtml = Object.entries(tfs).map(([tf, d]) => {
    const tc = TIER_COLORS[d.tier] || '#6b7280';
    return `<span class="tf-chip" style="color:${tc};border-color:${tc}33">${tf} <b>${d.score ?? 0}</b></span>`;
  }).join('');

  // EV color
  const evPct = s.expected_value_pct ?? 0;
  const evColor = evPct >= 1 ? '#22c55e' : evPct >= 0 ? '#f59e0b' : '#ef4444';

  return `<div class="card${isExpanded ? ' card-expanded' : ''}" id="card-${s.signal_id}">
    <!-- HEADER ROW -->
    <div class="card-header" onclick="toggleExpand('${s.signal_id}')">
      <div class="card-left">
        <a class="coin-link" href="${tvUrl}" target="_blank" rel="noopener" title="View ${base} on TradingView" onclick="event.stopPropagation()">
          ${s.coin}
          <svg class="tv-icon" width="11" height="11" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round"><polyline points="22 7 13.5 15.5 8.5 10.5 2 17"/><polyline points="16 7 22 7 22 13"/></svg>
        </a>
        <a class="binance-link" href="${binanceUrl}" target="_blank" rel="noopener" title="Trade ${base} on Binance Futures" onclick="event.stopPropagation()">
          <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round"><path d="M6 3v18M6 3l12 9-12 9"/><path d="M18 3v18"/></svg>
        </a>
        <span class="me-state-badge">${meState}</span>
        <span class="score-flow">
          <span class="score-d1">D1 ${d1Score}</span>
          <span class="score-arrow">→</span>
          <span class="score-d2">D2 ${d2Score}</span>
        </span>
        <span class="conf-badge conf-${confLevel(meConf)}">${meConf}%</span>
      </div>
      <div class="card-right">
        <span class="tier-badge tier-${d2Tier.toLowerCase()}">${d2Tier}</span>
        <span class="dir-dot dir-${dir.toLowerCase()}"></span>
        ${ageLabel ? `<span class="age-badge">${ageLabel}</span>` : ''}
        <span class="expand-icon">${isExpanded ? '▾' : '▸'}</span>
      </div>
    </div>

    <!-- EXPANDED BODY -->
    ${isExpanded ? `<div class="card-body">
      <!-- Market Evolution Detail -->
      <div class="me-detail-row">
        <div class="me-detail-cell">
          <span class="me-lbl">Evolution</span>
          <span class="me-evol">${mee.evolution || '—'}</span>
        </div>
        <div class="me-detail-cell">
          <span class="me-lbl">Spiral</span>
          <span class="me-spiral" style="color:${spiralColor}">${spiralIcon(spiral)} ${spiral}</span>
        </div>
        <div class="me-detail-cell">
          <span class="me-lbl">Decision</span>
          <span class="me-decision">${mee.tradingDecision || '—'}</span>
        </div>
        <div class="me-detail-cell">
          <span class="me-lbl">Momentum</span>
          <span class="me-momentum">${(mee.momentumVelocity ?? 0).toFixed(1)}</span>
        </div>
        <div class="me-detail-cell">
          <span class="me-lbl">Confidence</span>
          <div class="conf-bar-bg"><div class="conf-bar-fill conf-${confLevel(meConf)}" style="width:${meConf}%"></div></div>
        </div>
      </div>

      <!-- D1 HTF + D2 15M Side by Side -->
      <div class="structure-row">
        <div class="struct-panel">
          <div class="struct-header">
            <span class="struct-title">📊 D1 HTF</span>
            <span class="struct-tier tier-${d1Tier.toLowerCase()}">${d1Tier}</span>
            <span class="struct-score">${d1Score}</span>
          </div>
          <div class="struct-tags">${d1Tags}</div>
          <div class="tf-breakdown">${tfHtml}</div>
        </div>
        <div class="struct-panel">
          <div class="struct-header">
            <span class="struct-title">🎯 D2 15M</span>
            <span class="struct-tier tier-${d2Tier.toLowerCase()}">${d2Tier}</span>
            <span class="struct-score">${d2Score}</span>
          </div>
          <div class="struct-tags">${d2Tags}</div>
        </div>
      </div>

      <!-- Alignment -->
      <div class="align-row align-${alignPct >= 70 ? 'high' : alignPct >= 40 ? 'mid' : 'low'}">
        <span class="align-label">🔗 Alignment</span>
        <div class="align-bar-bg"><div class="align-bar-fill" style="width:${alignPct}%;background:${alignColor}"></div></div>
        <span class="align-score" style="color:${alignColor}">${alignScore}/20</span>
        <span class="align-dirs">${align.d1_dir || d1s.direction || '?'} → ${align.d2_dir || dir || '?'}</span>
      </div>

      <!-- Trade Levels -->
      <div class="levels-row">
        <div class="lvl-cell lvl-entry"><span class="lvl-lbl">Entry</span><span class="lvl-val">${fmtPrice(s.entry)}</span></div>
        <div class="lvl-cell lvl-sl"><span class="lvl-lbl">SL</span><span class="lvl-val">${fmtPrice(s.sl)}</span></div>
        <div class="lvl-cell lvl-tp"><span class="lvl-lbl">TP1</span><span class="lvl-val">${fmtPrice(s.tp1)}</span></div>
        <div class="lvl-cell lvl-tp"><span class="lvl-lbl">TP2</span><span class="lvl-val">${fmtPrice(s.tp2)}</span></div>
        <div class="lvl-cell lvl-rr"><span class="lvl-lbl">RR</span><span class="lvl-val">${fmtRR(s.rr1)}</span><span class="lvl-val2">${fmtRR(s.rr2)}</span></div>
      </div>

      <!-- EV + Meta -->
      <div class="meta-row">
        <div class="ev-cell">
          <span class="ev-label">EV</span>
          <span class="ev-val" style="color:${evColor}">${fmtPct(evPct)}</span>
          <span class="ev-wr">WR ${(s.estimated_win_rate ?? 0).toFixed(0)}%</span>
        </div>
        ${s.nascent_move ? '<span class="tag tag-nascent">NASCENT</span>' : ''}
        ${s.entry_precision ? `<span class="tag tag-ep">EP ${s.entry_precision.toFixed(0)}</span>` : ''}
        <span class="born-time">${new Date(s.born_at).toLocaleTimeString([], {hour:'2-digit', minute:'2-digit'})}</span>
        <canvas class="sparkline" data-values="${sparkData}" width="80" height="20"></canvas>
      </div>
    </div>` : ''}
  </div>`;
}

function confLevel(c) {
  if (c >= 70) return 'high';
  if (c >= 40) return 'mid';
  return 'low';
}

function zoneLabel(z) {
  if (!z || z === 'UNKNOWN') return '';
  return z;
}

function spiralIcon(s) {
  if (s === 'Expansion') return '🟢';
  if (s === 'Correction') return '🟡';
  if (s === 'Failure') return '🔴';
  return '⚪';
}

// ── Expand / Flash ────────────────────────────────────────────────
function toggleExpand(id) {
  expandedCards.has(id) ? expandedCards.delete(id) : expandedCards.add(id);
  renderSignals();
  requestAnimationFrame(drawSparklines);
}

function flashNew(id) {
  expandedCards.add(id);
  renderSignals();
  setTimeout(() => { expandedCards.delete(id); renderSignals(); }, 5000);
}

// ── Type E Alerts ─────────────────────────────────────────────────
function handleTypeEAlert(alert) {
  typeEAlerts.unshift(alert);
  if (typeEAlerts.length > 10) typeEAlerts.length = 10;
  renderTypeEAlerts();
}

function renderTypeEAlerts() {
  const c = document.getElementById('typeEAlertContainer');
  if (!c) return;
  if (typeEAlerts.length === 0) { c.style.display = 'none'; return; }
  c.style.display = 'block';
  c.innerHTML = typeEAlerts.map(a => `<div class="type-e-alert">
    <span class="te-icon">⚠️</span>
    <span class="te-coin">${a.coin}</span>
    <span class="te-conflict">${a.d1_dir || '?'} vs ${a.d2_dir || '?'}</span>
    <span class="te-time">${new Date(a.timestamp).toLocaleTimeString()}</span>
  </div>`).join('');
}

// ── Filtering ────────────────────────────────────────────────────
function applyFilters() {
  return allSignals.filter(s => {
    if (filters.direction !== 'all' && s.direction !== filters.direction) return false;
    return true;
  });
}

function initFilters() {
  document.querySelectorAll('.dir-chip').forEach(btn => {
    btn.addEventListener('click', () => {
      filters.direction = btn.dataset.filterDir;
      document.querySelectorAll('.dir-chip').forEach(b => b.classList.remove('active'));
      btn.classList.add('active');
      renderSignals();
    });
  });
  const btn = document.getElementById('btnRestart');
  if (btn) btn.addEventListener('click', async () => {
    await fetch('/api/restart', { method: 'POST' }).catch(() => {});
  });
}

// ── Render ────────────────────────────────────────────────────────
function renderSignals() {
  const container = document.getElementById('signalsContainer');
  const empty = document.getElementById('emptyState');
  if (!container) return;

  const filtered = applyFilters();

  // Sort by confidence descending — highest confidence first
  filtered.sort((a, b) => {
    const ca = (a.marketEvolution || {}).confidence ?? 0;
    const cb = (b.marketEvolution || {}).confidence ?? 0;
    return cb - ca;
  });

  if (allSignals.length === 0) {
    container.innerHTML = '';
    if (empty) { empty.style.display = ''; container.appendChild(empty); }
    updateEmptyState('waiting');
    return;
  }

  if (filtered.length === 0) {
    container.innerHTML = `<div class="no-results"><span>No signals match filters</span>
      <button class="clear-btn" onclick="clearFilters()">Clear</button></div>`;
    return;
  }

  if (empty && empty.parentNode) empty.parentNode.removeChild(empty);
  container.innerHTML = filtered.map(s => buildCard(s)).join('');
  requestAnimationFrame(drawSparklines);
}

function updateEmptyState(state) {
  const title = document.getElementById('emptyTitle');
  const msg = document.getElementById('emptyMsg');
  const spinner = document.getElementById('emptySpinner');
  if (!title) return;
  if (state === 'scanning') {
    title.textContent = 'Scanning Markets...';
    msg.textContent = `${stats.d1_coins || 0} HTF coins · 15M LTF · Decision Layer`;
    if (spinner) spinner.style.display = '';
  } else {
    title.textContent = 'Waiting for signals...';
    msg.textContent = 'D1 HTF + D2 15M + Decision Layer active';
    if (spinner) spinner.style.display = 'none';
  }
}

function clearFilters() {
  filters = { direction: 'all', signalType: 'all' };
  document.querySelectorAll('.dir-chip, .stype-chip').forEach(b => b.classList.remove('active'));
  document.querySelector('[data-filter-dir="all"]')?.classList.add('active');
  document.querySelector('[data-filter-stype="all"]')?.classList.add('active');
  renderSignals();
}

// ── Sparklines ────────────────────────────────────────────────────
function drawSparklines() {
  document.querySelectorAll('canvas.sparkline').forEach(canvas => {
    const vals = (canvas.dataset.values || '').split(',').map(Number).filter(v => !isNaN(v) && v > 0);
    if (vals.length < 2) return;
    const ctx = canvas.getContext('2d');
    const w = canvas.width, h = canvas.height;
    ctx.clearRect(0, 0, w, h);
    const min = Math.min(...vals), max = Math.max(...vals);
    const range = max - min || 1;
    const lastY = h - ((vals[vals.length - 1] - min) / range) * (h - 4) - 2;

    const grad = ctx.createLinearGradient(0, 0, 0, h);
    grad.addColorStop(0, 'rgba(59,130,246,0.2)');
    grad.addColorStop(1, 'rgba(59,130,246,0)');
    ctx.beginPath();
    vals.forEach((v, i) => {
      const x = (i / (vals.length - 1)) * w;
      const y = h - ((v - min) / range) * (h - 4) - 2;
      i === 0 ? ctx.moveTo(x, y) : ctx.lineTo(x, y);
    });
    ctx.lineTo(w, h); ctx.lineTo(0, h); ctx.closePath();
    ctx.fillStyle = grad; ctx.fill();

    ctx.beginPath();
    ctx.strokeStyle = '#3b82f6'; ctx.lineWidth = 1.5; ctx.lineJoin = 'round';
    vals.forEach((v, i) => {
      const x = (i / (vals.length - 1)) * w;
      const y = h - ((v - min) / range) * (h - 4) - 2;
      i === 0 ? ctx.moveTo(x, y) : ctx.lineTo(x, y);
    });
    ctx.stroke();

    ctx.beginPath();
    ctx.arc(w, lastY, 2, 0, Math.PI * 2);
    ctx.fillStyle = '#3b82f6'; ctx.fill();
  });
}

// ── Health Poll ────────────────────────────────────────────────────
async function checkHealth() {
  try {
    const resp = await fetch('/api/health');
    const data = await resp.json();
    if (data.stats) {
      stats = data.stats;
      document.getElementById('d1Count').textContent = stats.d1_coins || 0;
      document.getElementById('d2Count').textContent = stats.d2_signals || 0;
      document.getElementById('d3Count').textContent = stats.d3_fusion || 0;

      // Activity indicators
      setActivity('actD1', stats.last_d1_scan);
      setActivity('actD2', stats.last_d2_scan);
      setActivity('actD3', stats.last_d3_fusion);

      if (allSignals.length === 0) updateEmptyState('scanning');
    }
  } catch (e) { /* silent */ }
}

function setActivity(prefix, ts) {
  const status = document.getElementById(prefix + 'Status');
  if (!status || !ts) return;
  const age = (Date.now() - new Date(ts).getTime()) / 1000;
  status.textContent = age < 10 ? '● Live' : age < 30 ? '● Recent' : '○ ' + timeAgo(ts);
}

// ── WebSocket ─────────────────────────────────────────────────────
function connectWS() {
  const proto = location.protocol === 'https:' ? 'wss:' : 'ws:';
  ws = new WebSocket(`${proto}//${location.host}/ws-fusion`);

  ws.onopen = () => {
    document.getElementById('wsDot').style.background = '#22c55e';
    document.getElementById('wsLabel').textContent = 'Live';
    document.getElementById('scanDot').style.background = '#22c55e';
    document.getElementById('scanText').textContent = 'Receiving';
  };

  ws.onclose = () => {
    document.getElementById('wsDot').style.background = '#ef4444';
    document.getElementById('wsLabel').textContent = 'Offline';
    document.getElementById('scanDot').style.background = '#ef4444';
    document.getElementById('scanText').textContent = 'Reconnecting...';
    setTimeout(connectWS, 3000);
  };

  ws.onmessage = (ev) => {
    try {
      const msg = JSON.parse(ev.data);
      if (msg.type === 'INITIAL') {
        allSignals = msg.signals || [];
        renderSignals();
        if (allSignals.length > 0) {
          const empty = document.getElementById('emptyState');
          if (empty && empty.parentNode) empty.parentNode.removeChild(empty);
        }
      }
      if (msg.type === 'signal' && msg.data) {
        const s = msg.data;
        const idx = allSignals.findIndex(x => x.signal_id === s.signal_id);
        if (idx >= 0) allSignals[idx] = s;
        else { allSignals.unshift(s); flashNew(s.signal_id); }
        renderSignals();
      }
      if (msg.type === 'TYPE_E_ALERT' && msg.data) {
        handleTypeEAlert(msg.data);
      }
    } catch (e) { console.error('[WS]', e); }
  };
}

// ── Init ───────────────────────────────────────────────────────────
document.addEventListener('DOMContentLoaded', () => {
  connectWS();
  initFilters();
  setInterval(checkHealth, 3000);
});
