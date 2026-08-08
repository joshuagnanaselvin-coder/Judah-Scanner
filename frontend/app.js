// ═══════════════════════════════════════════════════════════════════════
// Judah — D1/D2 Hybrid Scanner Frontend (V5.2)
// Consumes signal_fusion.py payload via /ws-fusion
// ═══════════════════════════════════════════════════════════════════════

// ── State ──────────────────────────────────────────────────────────
let allSignals = [];
let ws = null;
let filters = { direction: 'all', signalType: 'all' };
const expandedCards = new Set();
let stats = { scanned: 0, d1_coins: 0, d2_signals: 0, last_d1_scan: 0, last_d2_scan: 0, last_d3_fusion: 0 };
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
  const stype = s.signal_type || 'D';
  const stypeColor = s.signal_type_color || '#6b7280';
  const stypeBg = STYPE_BG[stype] || '#6b728022';
  const d1Tier = s.d1_tier || '—';
  const d2Tier = s.d2_tier || '—';
  const d1Score = s.d1_score ?? 0;
  const d2Score = s.d2_score ?? 0;
  const dir = s.direction || 'NEUTRAL';
  const dirColor = DIR_COLORS[dir] || '#6b7280';
  const tierColor = TIER_COLORS[d2Tier] || '#6b7280';
  const spiral = mee.spiral || 'Neutral';
  const spiralColor = SPIRAL_COLORS[spiral] || '#6b7280';
  const ageLabel = fmtAge(s.born_at);
  const isNew = !s.born_at || (Date.now() - new Date(s.born_at).getTime()) < 6000;
  const isExpanded = expandedCards.has(s.signal_id);
  const action = s.action || 'WATCH';

  // D1 structure
  const d1s = s.d1_structure || {};
  const d1Tags = [
    d1s.msb_type ? `<span class="tag tag-msb">MSB ${d1s.msb_type.toUpperCase()}</span>` : '',
    d1s.ob_type ? `<span class="tag tag-ob">OB ${d1s.ob_type.replace(/_OB$/, '')} ${zoneLabel(d1s.ob_zone)}</span>` : '',
    d1s.fvg_type ? `<span class="tag tag-fvg">FVG ${d1s.fvg_type}</span>` : '',
    d1s.liq_swept ? `<span class="tag tag-liq">LIQ SWEPT</span>` : '',
    d1s.poc ? `<span class="tag tag-poc">POC ${fmtPrice(d1s.poc)}</span>` : '',
    (d1s.va_low && d1s.va_high) ? `<span class="tag tag-va">VA ${fmtPrice(d1s.va_low)}–${fmtPrice(d1s.va_high)}</span>` : '',
    d1s.premium_discount ? `<span class="tag tag-pd">${d1s.premium_discount}</span>` : '',
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
  const d1Dir = align.d1_dir || d1s.direction || dir;
  const d2Dir = align.d2_dir || dir;
  const aligned = d1Dir === d2Dir && d1Dir !== '?';
  const alignColor = alignScore >= 15 ? '#22c55e' : alignScore >= 8 ? '#f59e0b' : '#ef4444';

  // Score sparkline data
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

  return `<div class="card${isNew ? ' card-new' : ''}" id="card-${s.signal_id}">
    <!-- HEADER ROW -->
    <div class="card-header" onclick="toggleExpand('${s.signal_id}')">
      <div class="card-left">
        <span class="stype-dot" style="background:${stypeColor}"></span>
        <span class="coin-name">${s.coin}</span>
        <span class="stype-badge" style="background:${stypeBg};color:${stypeColor};border-color:${stypeColor}44">${stype} ${s.signal_type_name || ''}</span>
        <span class="action-badge action-${action.toLowerCase()}">${action}</span>
      </div>
      <div class="card-right">
        <span class="dir-badge dir-${dir.toLowerCase()}">${dir}</span>
        <div class="score-pair">
          <span class="score-d1" title="D1 HTF Score">D1 <b>${d1Score}</b></span>
          <span class="score-sep">→</span>
          <span class="score-d2" title="D2 15M Score">D2 <b>${d2Score}</b></span>
        </div>
        <span class="tier-badge tier-${d2Tier.toLowerCase()}">${d2Tier}</span>
        ${ageLabel ? `<span class="age-badge age-${ageLabel === 'LIVE' || ageLabel === 'NEW' ? 'live' : 'normal'}">${ageLabel}</span>` : ''}
        <span class="expand-icon">${isExpanded ? '▾' : '▸'}</span>
      </div>
    </div>

    <!-- EXPANDED BODY -->
    ${isExpanded ? `<div class="card-body">
      <!-- Market Evolution Banner -->
      <div class="me-banner" style="border-left:3px solid ${stypeColor}">
        <div class="me-cell">
          <span class="me-label">State</span>
          <span class="me-state">${mee.state || '—'}</span>
          <span class="me-evolution">${mee.evolution || ''}</span>
        </div>
        <div class="me-cell">
          <span class="me-label">Confidence</span>
          <div class="conf-bar-bg">
            <div class="conf-bar-fill" style="width:${mee.confidence ?? 0}%;background:${stypeColor}"></div>
          </div>
          <span class="conf-val">${mee.confidence ?? 0}%</span>
        </div>
        <div class="me-cell">
          <span class="me-label">Spiral</span>
          <span class="me-spiral" style="color:${spiralColor}">${spiralIcon(spiral)} ${spiral}</span>
        </div>
        <div class="me-cell">
          <span class="me-label">Decision</span>
          <span class="me-decision">${mee.tradingDecision || '—'}</span>
        </div>
        <div class="me-cell">
          <span class="me-label">Momentum</span>
          <span class="me-momentum">${(mee.momentumVelocity ?? 0).toFixed(1)}</span>
        </div>
      </div>

      <!-- D1 HTF + D2 15M Side by Side -->
      <div class="structure-row">
        <div class="struct-panel d1-panel">
          <div class="struct-header">
            <span class="struct-title">📊 D1 HTF</span>
            <span class="struct-tier tier-${d1Tier.toLowerCase()}">${d1Tier}</span>
            <span class="struct-score">${d1Score}</span>
          </div>
          <div class="struct-tags">${d1Tags}</div>
          <div class="tf-breakdown">${tfHtml}</div>
        </div>
        <div class="struct-panel d2-panel">
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
        <span class="align-label">🔗 HTF/LTF Alignment</span>
        <div class="align-bar-bg">
          <div class="align-bar-fill" style="width:${alignPct}%;background:${alignColor}"></div>
        </div>
        <span class="align-score" style="color:${alignColor}">${alignScore}/20</span>
        <span class="align-dirs">${d1Dir} → ${d2Dir} ${aligned ? '✓' : '✗'}</span>
      </div>

      <!-- Trade Levels -->
      <div class="levels-row">
        <div class="lvl-cell lvl-entry">
          <span class="lvl-lbl">Entry</span>
          <span class="lvl-val">${fmtPrice(s.entry)}</span>
        </div>
        <div class="lvl-cell lvl-sl">
          <span class="lvl-lbl">SL</span>
          <span class="lvl-val">${fmtPrice(s.sl)}</span>
        </div>
        <div class="lvl-cell lvl-tp">
          <span class="lvl-lbl">TP1</span>
          <span class="lvl-val">${fmtPrice(s.tp1)}</span>
        </div>
        <div class="lvl-cell lvl-tp">
          <span class="lvl-lbl">TP2</span>
          <span class="lvl-val">${fmtPrice(s.tp2)}</span>
        </div>
        <div class="lvl-cell lvl-rr">
          <span class="lvl-lbl">RR</span>
          <span class="lvl-val">${fmtRR(s.rr1)}</span>
          <span class="lvl-val2">${fmtRR(s.rr2)}</span>
        </div>
      </div>

      <!-- EV + Meta -->
      <div class="meta-row">
        <div class="ev-cell">
          <span class="ev-label">EV</span>
          <span class="ev-val" style="color:${evColor}">${fmtPct(evPct)}</span>
          <span class="ev-wr">WR ${(s.estimated_win_rate ?? 0).toFixed(0)}%</span>
        </div>
        ${s.nascent_move ? '<span class="nascent-badge">NASCENT</span>' : ''}
        ${s.entry_precision ? `<span class="ep-badge">EP ${s.entry_precision.toFixed(0)}</span>` : ''}
        <span class="born-time">${new Date(s.born_at).toLocaleTimeString([], {hour:'2-digit', minute:'2-digit'})}</span>
        <canvas class="sparkline" data-values="${sparkData}" width="80" height="20"></canvas>
      </div>
    </div>` : ''}
  </div>`;
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
    if (filters.signalType !== 'all' && s.signal_type !== filters.signalType) return false;
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
  document.querySelectorAll('.stype-chip').forEach(btn => {
    btn.addEventListener('click', () => {
      filters.signalType = btn.dataset.filterStype;
      document.querySelectorAll('.stype-chip').forEach(b => b.classList.remove('active'));
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
      document.getElementById('scannedCount').textContent = stats.scanned || stats.d1_coins || 0;
      const total = document.getElementById('totalSignals');
      if (total) total.textContent = allSignals.length;

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
