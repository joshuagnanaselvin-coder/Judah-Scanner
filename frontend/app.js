// ═══════════════════════════════════════════════════════════════════════
// Judah — D1/D2/D3 Scanner Frontend (V6.0)
// Consumes signal_fusion.py payload via /ws-fusion
// ═══════════════════════════════════════════════════════════════════════

// ── State ──────────────────────────────────────────────────────────
let allSignals = [];
let ws = null;
let filters = { direction: 'all', signalType: 'all', freshness: 'all' };
const expandedCards = new Set();
let stats = { d1_coins: 0, d2_signals: 0, d3_fusion: 0, last_d1_scan: 0, last_d2_scan: 0, last_d3_fusion: 0 };
let typeEAlerts = [];
let nextD1Scan = 0;
let nextD2Scan = 0;
let timerInterval = null;
let d1TimerFmt = 'min';
let d2TimerFmt = 'sec';

// ── Helpers ────────────────────────────────────────────────────────
function getCookie(name) {
  const m = document.cookie.match(new RegExp('(^| )' + name + '=([^;]+)'));
  return m ? m[2] : null;
}
function getMEE(s) { return s.marketEvolution || {}; }

function fmtMomentum(v) {
  if (v == null || isNaN(v)) return '—';
  return Number(v).toFixed(1);
}

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
  const ms = (typeof ts === 'number') ? ts * 1000 : new Date(ts).getTime();
  if (isNaN(ms)) return '—';
  const s = Math.floor((Date.now() - ms) / 1000);
  if (s < 60) return s + 's';
  if (s < 3600) return Math.floor(s / 60) + 'm';
  return Math.floor(s / 3600) + 'h';
}

function fmtAge(ts) {
  if (!ts) return '';
  const ms = (typeof ts === 'number') ? ts * 1000 : new Date(ts).getTime();
  if (isNaN(ms)) return '';
  const s = Math.floor((Date.now() - ms) / 1000);
  if (s < 5) return 'LIVE';
  if (s < 30) return 'NEW';
  if (s < 120) return Math.floor(s / 15) * 15 + 's';
  return Math.floor(s / 60) + 'm';
}

// ── Signal Type colors ────────────────────────────────────────────
const STYPE_COLORS = { A: '#eab508', B: '#3b82f6', C: '#22c55e', D: '#f97316', E: '#ef4444', F: '#a855f7' };
const STYPE_BG = { A: '#eab50822', B: '#3b82f622', C: '#22c55e22', D: '#f9731622', E: '#ef444422', F: '#a855f722' };
const TIER_COLORS = { SNIPER: '#eab508', OPPORTUNITY: '#22c55e', WATCH: '#3b82f6', WEAK: '#a855f7', REJECTED: '#6b7280' };
const SPIRAL_COLORS = { Expansion: '#22c55e', Correction: '#f59e0b', Failure: '#ef4444', Neutral: '#6b7280' };
const DIR_COLORS = { BULLISH: '#22c55e', BEARISH: '#ef4444', NEUTRAL: '#6b7280' };
const FRESHNESS_COLORS = { HOT: '#22c55e', WARM: '#f59e0b', COOL: '#f97316', STALE: '#6b7280' };

// ── Card Builder ──────────────────────────────────────────────────
function buildCard(s) {
  const mee = getMEE(s);
  const d1Score = s.d1_score ?? 0;
  const d2Score = s.d2_score ?? 0;
  const d1Tier = s.d1_tier || '—';
  const d2Tier = s.d2_tier || '—';
  const dir = s.direction || 'NEUTRAL';
  const signalType = s.signal_type || 'F';
  const spiral = mee.spiral || 'Neutral';
  const spiralColor = SPIRAL_COLORS[spiral] || '#6b7280';
  const meState = mee.state || '—';
  const meConf = mee.confidence ?? 0;
  const ageLabel = fmtAge(s.born_at);
  const freshness = s._freshness || 'COOL';
  const freshnessColor = FRESHNESS_COLORS[freshness] || '#6b7280';
  const isExpanded = expandedCards.has(s.signal_id);
  const tierColor = TIER_COLORS[d2Tier] || '#6b7280';
  const stypeColor = STYPE_COLORS[signalType] || '#6b7280';

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
    return `<span class="tf-chip" style="color:${tc};border-color:${tc};background:${tc}22"><b>${tf}</b> ${d.score ?? 0}</span>`;
  }).join('');

  // EV color
  const evPct = s.expected_value_pct ?? 0;
  const evColor = evPct >= 1 ? '#22c55e' : evPct >= 0 ? '#f59e0b' : '#ef4444';

  return `<div class="card${isExpanded ? ' card-expanded' : ''}" id="card-${s.signal_id}"
              data-signal-type="${signalType}" data-freshness="${freshness}" data-direction="${dir}">
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
        <span class="stype-dot" style="background:${stypeColor}" title="Type ${signalType}"></span>
        <span class="me-state-badge">${meState}</span>
        <span class="score-flow">
          <span class="score-d1">D1 ${d1Score}</span>
          <span class="score-arrow">→</span>
          <span class="score-d2">D2 ${d2Score}</span>
        </span>
        <span class="conf-badge conf-${confLevel(meConf)}">${meConf}%</span>
        <span class="freshness-badge" style="background:${freshnessColor}22;color:${freshnessColor};border-color:${freshnessColor}44">${freshness}</span>
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
          <span class="me-momentum">${fmtMomentum(mee.momentumVelocity)}</span>
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
        ${s.born_at ? '<span class="born-time">' + new Date(s.born_at).toLocaleTimeString([], {hour:'2-digit', minute:'2-digit'}) + '</span>' : ''}
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
  const existing = typeEAlerts.findIndex(a => a.coin === alert.coin);
  if (existing >= 0) {
    typeEAlerts[existing] = alert;
  } else {
    typeEAlerts.unshift(alert);
  }
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
    if (filters.freshness !== 'all' && s._freshness !== filters.freshness) return false;
    return true;
  });
}

function initFilters() {
  // Direction filters
  document.querySelectorAll('.dir-chip').forEach(btn => {
    btn.addEventListener('click', () => {
      filters.direction = btn.dataset.filterDir;
      document.querySelectorAll('.dir-chip').forEach(b => b.classList.remove('active'));
      btn.classList.add('active');
      renderSignals();
    });
  });

  // Signal type filters
  document.querySelectorAll('.stype-filter').forEach(btn => {
    btn.addEventListener('click', () => {
      filters.signalType = btn.dataset.filterStype;
      document.querySelectorAll('.stype-filter').forEach(b => b.classList.remove('active'));
      btn.classList.add('active');
      renderSignals();
    });
  });

  // Freshness filters
  document.querySelectorAll('.fresh-filter').forEach(btn => {
    btn.addEventListener('click', () => {
      filters.freshness = btn.dataset.filterFresh;
      document.querySelectorAll('.fresh-filter').forEach(b => b.classList.remove('active'));
      btn.classList.add('active');
      renderSignals();
    });
  });

  // Restart button
  const btn = document.getElementById('btnRestart');
  if (btn) btn.addEventListener('click', async () => {
    await fetch('/api/restart', { method: 'POST' }).catch(() => {});
  });

  // Admin panel button
  const btnAdmin = document.getElementById('btnAdmin');
  if (btnAdmin) btnAdmin.addEventListener('click', () => {
    window.location.href = '/admin';
  });

  // Timer format selectors (per-chip)
  const d1Fmt = document.getElementById('d1TimerFmt');
  if (d1Fmt) d1Fmt.addEventListener('change', () => { d1TimerFmt = d1Fmt.value; });
  const d2Fmt = document.getElementById('d2TimerFmt');
  if (d2Fmt) d2Fmt.addEventListener('change', () => { d2TimerFmt = d2Fmt.value; });
}

// ── Signal Type Banner Update ─────────────────────────────────────
function updateHealthBadge(data, stats) {
  const dot = document.getElementById('healthBadgeDot');
  const text = document.getElementById('healthBadgeText');
  if (!dot || !text) return;

  const status = data.status || 'initializing';
  const isOk = status === 'ok';
  const isWarn = status === 'initializing';

  dot.className = 'health-badge-dot' + (isOk ? ' ok' : isWarn ? ' warn' : '');
  text.textContent = isOk ? 'Live' : isWarn ? 'Starting' : 'Error';

  const coins = document.getElementById('healthBadgeCoins');
  if (coins) {
    const d1 = stats.d1_coins || 0;
    const d2 = stats.d2_signals || 0;
    coins.textContent = d1 > 0 ? `D1:${d1} D2:${d2}` : '';
  }
}

function updateStypeBanner(signals) {
  const counts = { A: 0, B: 0, C: 0, D: 0, E: 0, F: 0 };
  const freshnessCounts = { HOT: 0, WARM: 0, COOL: 0, STALE: 0 };
  const totalScanned = 500;

  for (const s of signals) {
    const st = s.signal_type || 'F';
    if (counts[st] !== undefined) counts[st]++;

    const f = s._freshness || 'COOL';
    if (freshnessCounts[f] !== undefined) freshnessCounts[f]++;
  }

  const processed = signals.length;
  const pending = totalScanned - processed;

  // Update counts
  for (const [type, count] of Object.entries(counts)) {
    const el = document.getElementById('stype' + type);
    if (el) el.textContent = count;
  }

  // Update bars — relative to total scanned
  for (const type of ['A', 'B', 'C', 'D', 'E', 'F']) {
    const bar = document.getElementById('stypeBar' + type);
    if (bar) {
      const pct = totalScanned > 0 ? (counts[type] / totalScanned) * 100 : 0;
      bar.style.width = pct + '%';
    }
  }

  // Totals
  const totalEl = document.getElementById('stypeTotal');
  const processedEl = document.getElementById('stypeProcessed');
  const pendingEl = document.getElementById('stypePending');
  if (totalEl) totalEl.textContent = totalScanned;
  if (processedEl) processedEl.textContent = processed;
  if (pendingEl) pendingEl.textContent = Math.max(0, pending);

  // Draw pie chart
  drawPieChart(counts, totalScanned);
}

function drawPieChart(counts, total) {
  const canvas = document.getElementById('pieChart');
  if (!canvas) return;
  const ctx = canvas.getContext('2d');
  const w = canvas.width;
  const h = canvas.height;
  const cx = w / 2;
  const cy = h / 2;
  const r = Math.max(1, Math.min(w, h) / 2 - 6);

  ctx.clearRect(0, 0, w, h);

  if (total === 0) {
    ctx.beginPath();
    ctx.arc(cx, cy, r, 0, Math.PI * 2);
    ctx.fillStyle = '#1e293b';
    ctx.fill();
    ctx.fillStyle = '#546178';
    ctx.font = '9px sans-serif';
    ctx.textAlign = 'center';
    ctx.textBaseline = 'middle';
    ctx.fillText('No data', cx, cy);
    return;
  }

  const slices = [];
  let totalNonZero = 0;
  const order = ['C', 'A', 'B', 'D', 'E', 'F'];
  for (const type of order) {
    const c = counts[type] || 0;
    if (c > 0) {
      slices.push({ type, count: c, color: STYPE_COLORS[type] });
      totalNonZero += c;
    }
  }

  let startAngle = -Math.PI / 2;
  const sliceAngle = (Math.PI * 2) / Math.max(1, totalNonZero);

  for (const slice of slices) {
    const sweep = slice.count * sliceAngle;
    ctx.beginPath();
    ctx.moveTo(cx, cy);
    ctx.arc(cx, cy, r, startAngle, startAngle + sweep);
    ctx.closePath();
    ctx.fillStyle = slice.color;
    ctx.fill();
    startAngle += sweep;
  }

  // Center hole (donut)
  ctx.beginPath();
  ctx.arc(cx, cy, Math.max(1, r * 0.5), 0, Math.PI * 2);
  ctx.fillStyle = '#0f1520';
  ctx.fill();

  // Center text
  ctx.fillStyle = '#e2e8f0';
  ctx.font = 'bold 14px sans-serif';
  ctx.textAlign = 'center';
  ctx.textBaseline = 'middle';
  ctx.fillText(totalNonZero, cx, cy - 2);
  ctx.fillStyle = '#546178';
  ctx.font = '7px sans-serif';
  ctx.fillText('active', cx, cy + 9);
}

// ── Scan Timers ───────────────────────────────────────────────────
function updateScanTimers() {
  const now = Date.now();

  // D1 timer — next 4H candle close
  const d1El = document.getElementById('d1Timer');
  if (d1El) {
    const msLeft = Math.max(0, nextD1Scan - now);
    if (msLeft <= 0) {
      d1El.textContent = 'scanning';
      d1El.classList.add('timer-scanning');
    } else {
      d1El.textContent = formatCountdown(msLeft, d1TimerFmt);
      d1El.classList.remove('timer-scanning');
    }
  }

  // D2 timer — next 15M candle close
  const d2El = document.getElementById('d2Timer');
  if (d2El) {
    const msLeft = Math.max(0, nextD2Scan - now);
    if (msLeft <= 0) {
      d2El.textContent = 'scanning';
      d2El.classList.add('timer-scanning');
    } else {
      d2El.textContent = formatCountdown(msLeft, d2TimerFmt);
      d2El.classList.remove('timer-scanning');
    }
  }
}

function formatCountdown(ms, fmt) {
  const totalSec = Math.ceil(ms / 1000);
  if (fmt === 'sec') return totalSec + 's';
  if (fmt === 'min') return Math.ceil(totalSec / 60) + 'm';
  if (fmt === 'hm') return Math.floor(totalSec / 3600) + 'h ' + Math.floor((totalSec % 3600) / 60) + 'm';
  // auto
  if (totalSec < 60) return totalSec + 's';
  const m = Math.floor(totalSec / 60);
  const s = totalSec % 60;
  if (m < 60) return m + 'm ' + s + 's';
  const h = Math.floor(m / 60);
  return h + 'h ' + (m % 60) + 'm';
}

function calculateNextScanTimes() {
  const now = new Date();

  // D1: next 4H candle close (00:00, 04:00, 08:00, 12:00, 16:00, 20:00 UTC)
  const d1Hours = [0, 4, 8, 12, 16, 20];
  let nextD1 = new Date(now);
  nextD1.setUTCMinutes(0, 0, 0);
  for (const h of d1Hours) {
    const candidate = new Date(now);
    candidate.setUTCHours(h, 0, 0, 0);
    if (candidate > now) {
      nextD1 = candidate;
      break;
    }
  }
  // If no future hour found today, use tomorrow 00:00
  if (nextD1 <= now) {
    nextD1 = new Date(now);
    nextD1.setUTCDate(nextD1.getUTCDate() + 1);
    nextD1.setUTCHours(0, 0, 0, 0);
  }
  nextD1Scan = nextD1.getTime();

  // D2: next 15M candle close
  const nowMin = now.getUTCMinutes();
  const next15Min = Math.ceil(nowMin / 15) * 15;
  const nextD2 = new Date(now);
  if (next15Min >= 60) {
    nextD2.setUTCHours(nextD2.getUTCHours() + 1, 0, 0, 0);
  } else {
    nextD2.setUTCMinutes(next15Min, 0, 0);
  }
  nextD2Scan = nextD2.getTime();
}

function startTimerUpdates() {
  calculateNextScanTimes();
  updateScanTimers();
  if (timerInterval) clearInterval(timerInterval);
  timerInterval = setInterval(() => {
    updateScanTimers();
    // Recalculate at the top of each hour for D2 timer alignment
    if (new Date().getUTCMinutes() === 0) calculateNextScanTimes();
  }, 1000);
}

// ── Data Layer Status ─────────────────────────────────────────────
async function updateDLStatus() {
  try {
    const resp = await fetch('/api/health');
    const data = await resp.json();
    if (data.stats) {
      stats = data.stats;
      document.getElementById('d1Count').textContent = stats.d1_coins || 0;
      document.getElementById('d2Count').textContent = stats.d2_signals || 0;
      document.getElementById('d3Count').textContent = stats.d3_fusion || 0;

      setActivity('actD1', stats.last_d1_scan);
      setActivity('actD2', stats.last_d2_scan);
      setActivity('actD3', stats.last_d3_fusion);
      setActivity('actDL', stats.last_dl_cleanup || stats.last_d3_fusion, 'DL');
    }
  } catch (e) { /* silent */ }
}

function setActivity(prefix, ts, label) {
  const status = document.getElementById(prefix + 'Status');
  if (!status) return;
  if (!ts || ts < 1000000) {
    status.textContent = '—';
    status.className = 'act-status';
    return;
  }
  const age = (Date.now() - new Date(ts * 1000).getTime()) / 1000;
  if (age < 10) {
    status.textContent = '• Live';
    status.className = 'act-status act-live';
  } else if (age < 30) {
    status.textContent = '• Recent';
    status.className = 'act-status act-recent';
  } else {
    status.textContent = '○ ' + timeAgo(ts);
    status.className = 'act-status';
  }
}

// ── Render ────────────────────────────────────────────────────────
function renderSignals() {
  const container = document.getElementById('signalsContainer');
  const empty = document.getElementById('emptyState');
  if (!container) return;

  const filtered = applyFilters();

  // Sort by ME confidence (desc), then composite score (desc), then D2 tier
  const tierRank = { SNIPER: 3, OPPORTUNITY: 2, WATCH: 1, WEAK: 0, REJECTED: -1, '—': 0 };
  filtered.sort((a, b) => {
    const ca = (a.marketEvolution || {}).confidence ?? 0;
    const cb = (b.marketEvolution || {}).confidence ?? 0;
    if (cb !== ca) return cb - ca;
    const sa = b.composite_score ?? b.d2_score ?? 0;
    const sb = a.composite_score ?? a.d2_score ?? 0;
    if (sb !== sa) return sa - sb;
    const ta = tierRank[b.d2_tier] || 0;
    const tb = tierRank[a.d2_tier] || 0;
    if (ta !== tb) return ta - tb;
    return 0;
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
    msg.textContent = `${stats.d1_coins || 0} HTF coins · 15M LTF · Fusion · 500 coins`;
    if (spinner) spinner.style.display = '';
  } else {
    title.textContent = 'Waiting for signals...';
    msg.textContent = 'D1 4H HTF + D2 15M LTF + D3 Fusion — scanning 500 coins';
    if (spinner) spinner.style.display = 'none';
  }
}

function clearFilters() {
  filters = { direction: 'all', signalType: 'all', freshness: 'all' };
  document.querySelectorAll('.dir-chip, .stype-filter, .fresh-filter').forEach(b => b.classList.remove('active'));
  document.querySelector('[data-filter-dir="all"]')?.classList.add('active');
  document.querySelector('[data-filter-stype="all"]')?.classList.add('active');
  document.querySelector('[data-filter-fresh="all"]')?.classList.add('active');
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

      setActivity('actD1', stats.last_d1_scan);
      setActivity('actD2', stats.last_d2_scan);
      setActivity('actD3', stats.last_d3_fusion);

      // Update top-right health badge
      updateHealthBadge(data, stats);

      // Update DL activity
      const dlStatus = document.getElementById('actDLStatus');
      if (dlStatus) {
        const dlCleanup = stats.last_dl_cleanup;
        if (dlCleanup && dlCleanup > 1000000) {
          const age = (Date.now() - new Date(dlCleanup * 1000).getTime()) / 1000;
          if (age < 30) {
            dlStatus.textContent = '• Active';
            dlStatus.className = 'act-status act-live';
          } else {
            dlStatus.textContent = '○ ' + timeAgo(dlCleanup);
            dlStatus.className = 'act-status';
          }
        } else {
          dlStatus.textContent = '—';
          dlStatus.className = 'act-status';
        }
      }
    }
  } catch (e) { /* silent */ }
}

// ── WebSocket ─────────────────────────────────────────────────────
function connectWS() {
  const proto = location.protocol === 'https:' ? 'wss:' : 'ws:';
  const token = getCookie("session_token");
  const tokenParam = token ? `?token=${encodeURIComponent(token)}` : "";
  ws = new WebSocket(`${proto}//${location.host}/ws-fusion${tokenParam}`);

  ws.onopen = () => {
    document.getElementById('wsDot').style.background = '#22c55e';
    document.getElementById('wsLabel').textContent = 'Live';
    document.getElementById('scanDot').style.background = '#22c55e';
    document.getElementById('scanText').textContent = 'Receiving';
  };

  ws.onerror = (err) => {
    document.getElementById('wsDot').style.background = '#f59e0b';
    document.getElementById('wsLabel').textContent = 'Error';
    document.getElementById('scanDot').style.background = '#f59e0b';
    document.getElementById('scanText').textContent = 'Retrying...';
    ws.close();
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
        updateStypeBanner(allSignals);
        if (allSignals.length > 0) {
          const empty = document.getElementById('emptyState');
          if (empty && empty.parentNode) empty.parentNode.removeChild(empty);
        }
      }

      if (msg.type === 'SIGNALS_BATCH' && msg.signals) {
        for (const s of msg.signals) {
          const idx = allSignals.findIndex(x => x.signal_id === s.signal_id);
          if (idx >= 0) allSignals[idx] = s;
          else { allSignals.unshift(s); flashNew(s.signal_id); }
        }
        renderSignals();
        updateStypeBanner(allSignals);
      }

      if (msg.type === 'signal' && msg.data) {
        const s = msg.data;
        const idx = allSignals.findIndex(x => x.signal_id === s.signal_id);
        if (idx >= 0) allSignals[idx] = s;
        else { allSignals.unshift(s); flashNew(s.signal_id); }
        renderSignals();
        updateStypeBanner(allSignals);
      }

      if (msg.type === 'TYPE_E_ALERT' && msg.data) {
        handleTypeEAlert(msg.data);
      }

      if (msg.type === 'REMOVE_SIGNALS' && msg.signal_ids) {
        msg.signal_ids.forEach(id => {
          const idx = allSignals.findIndex(x => x.signal_id === id);
          if (idx >= 0) allSignals.splice(idx, 1);
        });
        renderSignals();
        updateStypeBanner(allSignals);
      }
    } catch (e) { console.error('[WS]', e); }
  };
}

// ── Observability Panel ─────────────────────────────────────────────
let obsVisible = false;
let obsTab = 'logs';
let logRefreshTimer = null;
let healthRefreshTimer = null;

function toggleObsPanel() {
  obsVisible = !obsVisible;
  const panel = document.getElementById('obsPanel');
  const toggle = document.getElementById('obsToggle');
  if (obsVisible) {
    panel.style.display = '';
    toggle.style.background = 'var(--accent)';
    toggle.style.color = '#fff';
    switchObsTab(obsTab);
    startObsRefresh();
  } else {
    panel.style.display = 'none';
    toggle.style.background = '';
    toggle.style.color = '';
    stopObsRefresh();
  }
}

function toggleObsPanelCollapse() {
  const panel = document.getElementById('obsPanel');
  panel.classList.toggle('collapsed');
}

function switchObsTab(tab) {
  obsTab = tab;
  document.querySelectorAll('.obs-tab').forEach(t => t.classList.toggle('active', t.dataset.tab === tab));
  document.querySelectorAll('.obs-tab-panel').forEach(p => p.style.display = 'none');
  const panel = document.getElementById('tab-' + tab);
  if (panel) panel.style.display = '';

  if (tab === 'logs') loadLogs();
  if (tab === 'health') loadHealthDetail();
}

function startObsRefresh() {
  stopObsRefresh();
  logRefreshTimer = setInterval(() => { if (obsTab === 'logs') loadLogs(false); }, 5000);
  healthRefreshTimer = setInterval(() => { if (obsTab === 'health') loadHealthDetail(false); }, 3000);
}

function stopObsRefresh() {
  if (logRefreshTimer) { clearInterval(logRefreshTimer); logRefreshTimer = null; }
  if (healthRefreshTimer) { clearInterval(healthRefreshTimer); healthRefreshTimer = null; }
}

async function loadLogs(scroll = true) {
  const container = document.getElementById('logContainer');
  if (!container) return;
  const source = document.getElementById('logSource')?.value || 'all';
  const lines = parseInt(document.getElementById('logLines')?.value || '200', 10);

  try {
    const resp = await fetch(`/api/logs?lines=${lines}&source=${encodeURIComponent(source)}`);
    const data = await resp.json();
    if (data.error) { container.innerHTML = `<div class="obs-log-loading">${data.error}</div>`; return; }

    const info = document.getElementById('logFilterInfo');
    if (info) info.textContent = data.filtered !== undefined ? `${data.filtered} of ${data.total} lines` : `${data.lines?.length || 0} lines`;

    if (!data.lines || data.lines.length === 0) {
      container.innerHTML = '<div class="obs-log-loading">No log lines match filter</div>';
      return;
    }

    container.innerHTML = data.lines.map(line => {
      let cls = 'log-line';
      const up = line.toUpperCase();
      if (up.includes('ERROR') || up.includes('CRITICAL')) cls += ' error-line';
      else if (up.includes('WARNING')) cls += ' warn-line';

      // Extract level
      let level = '';
      const levelMatch = line.match(/(DEBUG|INFO|WARNING|ERROR|CRITICAL)/);
      if (levelMatch) level = levelMatch[1].toLowerCase();

      // Highlight [judah.xxx] logger prefix
      let msg = escapeHtml(line);
      msg = msg.replace(/\[judah\.(\w+)\]/g, '<strong>[judah.$1]</strong>');

      // Extract timestamp (HH:MM:SS at start)
      const tsMatch = line.match(/^(\d{2}:\d{2}:\d{2})/);
      const ts = tsMatch ? tsMatch[1] : '';

      return `<div class="${cls}"><span class="log-ts">${ts}</span><span class="log-level ll-${level}">${level || ''}</span><span class="log-msg">${msg}</span></div>`;
    }).join('');

    if (scroll) container.scrollTop = container.scrollHeight;
  } catch (e) {
    container.innerHTML = '<div class="obs-log-loading">Failed to load logs</div>';
  }
}

function escapeHtml(s) {
  return s.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');
}

async function loadHealthDetail(smooth = true) {
  const grid = document.getElementById('healthGrid');
  if (!grid) return;

  try {
    const [hResp, healthResp] = await Promise.all([
      fetch('/api/health/detail'),
      fetch('/api/health'),
    ]);
    const h = await hResp.json();
    const health = await healthResp.json();

    function statusClass(val) {
      if (val === 'live') return 'ok';
      if (val === 'stale' || val === 'initializing') return 'warn';
      if (val === 'never') return 'err';
      return 'neutral';
    }

    function statusLabel(val) {
      if (val === 'live') return '🟢 Live';
      if (val === 'stale') return '🟡 Stale';
      if (val === 'initializing') return '🟡 Starting';
      if (val === 'never') return '🔴 Never';
      return '⚪ ' + val;
    }

    function dotClass(val) {
      if (val === 'live') return 'live';
      if (val === 'stale') return 'stale';
      if (val === 'never') return 'never';
      return 'unknown';
    }

    const d1 = h.d1 || {};
    const d2 = h.d2 || {};
    const d3 = h.d3 || {};

    grid.innerHTML = `
      <!-- Connection -->
      <div class="health-card">
        <div class="health-card-title">🔌 Connection</div>
        <div class="health-row"><span class="health-key">WS Connected</span><span class="health-val ${(h.ws?.connected) ? 'ok' : 'err'}">${h.ws?.connected ? 'Yes' : 'No'}</span></div>
        <div class="health-row"><span class="health-key">WS Clients</span><span class="health-val neutral">${h.ws?.clients || 0}</span></div>
        <div class="health-row"><span class="health-key">Server Status</span><span class="health-val ${h.status === 'ok' ? 'ok' : 'warn'}">${h.status || '?'}</span></div>
        <div class="health-row"><span class="health-key">Uptime</span><span class="health-val neutral">${h.uptime_s != null ? Math.round(h.uptime_s / 60) + ' min' : '—'}</span></div>
      </div>

      <!-- D1 -->
      <div class="health-card">
        <div class="health-card-title">📊 D1 — HTF Scanner (4H)</div>
        <div class="health-row"><span class="health-key">Status</span><span class="health-val ${statusClass(d1.status)}"><span class="status-dot ${dotClass(d1.status)}"></span>${statusLabel(d1.status)}</span></div>
        <div class="health-row"><span class="health-key">Last Scan</span><span class="health-val neutral">${d1.age_s != null ? d1.age_s + 's ago' : 'never'}</span></div>
        <div class="health-row"><span class="health-key">Coins in Tier</span><span class="health-val neutral">${d1.coins != null ? d1.coins : '—'}</span></div>
        <div class="health-row"><span class="health-key">Signals</span><span class="health-val neutral">${h.signals || 0}</span></div>
        <div class="health-bar"><div class="health-bar-fill" style="width:${d1.age_s ? Math.max(0, 100 - d1.age_s) : 0}%;background:${d1.status === 'live' ? 'var(--green)' : d1.status === 'never' ? 'var(--red)' : 'var(--amber)'}"></div></div>
      </div>

      <!-- D2 -->
      <div class="health-card">
        <div class="health-card-title">🎯 D2 — 15M LTF Engine</div>
        <div class="health-row"><span class="health-key">Status</span><span class="health-val ${statusClass(d2.status)}"><span class="status-dot ${dotClass(d2.status)}"></span>${statusLabel(d2.status)}</span></div>
        <div class="health-row"><span class="health-key">Last Cycle</span><span class="health-val neutral">${d2.age_s != null ? d2.age_s + 's ago' : 'never'}</span></div>
        <div class="health-row"><span class="health-key">Signals</span><span class="health-val neutral">${d2.signals != null ? d2.signals : '—'}</span></div>
        <div class="health-bar"><div class="health-bar-fill" style="width:${d2.age_s ? Math.max(0, 100 - d2.age_s) : 0}%;background:${d2.status === 'live' ? 'var(--green)' : d2.status === 'never' ? 'var(--red)' : 'var(--amber)'}"></div></div>
      </div>

      <!-- D3 -->
      <div class="health-card">
        <div class="health-card-title">⚡ D3 — Fusion Engine</div>
        <div class="health-row"><span class="health-key">Status</span><span class="health-val ${statusClass(d3.status)}"><span class="status-dot ${dotClass(d3.status)}"></span>${statusLabel(d3.status)}</span></div>
        <div class="health-row"><span class="health-key">Last Fusion</span><span class="health-val neutral">${d3.age_s != null ? d3.age_s + 's ago' : 'never'}</span></div>
        <div class="health-row"><span class="health-key">Decisions</span><span class="health-val neutral">${d3.decisions != null ? d3.decisions : '—'}</span></div>
        <div class="health-bar"><div class="health-bar-fill" style="width:${d3.age_s ? Math.max(0, 100 - d3.age_s) : 0}%;background:${d3.status === 'live' ? 'var(--green)' : d3.status === 'never' ? 'var(--red)' : 'var(--amber)'}"></div></div>
      </div>

      <!-- Data Layer -->
      <div class="health-card">
        <div class="health-card-title">📊 Data Layer</div>
        <div class="health-row"><span class="health-key">D1 Valid/Stale</span><span class="health-val ${(health.d1_valid || 0) > 0 ? 'ok' : 'warn'}">${health.d1_valid || 0} / ${health.d1_stale || 0}</span></div>
        <div class="health-row"><span class="health-key">D2 Valid/Stale</span><span class="health-val ${(health.d2_valid || 0) > 0 ? 'ok' : 'warn'}">${health.d2_valid || 0} / ${health.d2_stale || 0}</span></div>
        <div class="health-row"><span class="health-key">D3 Decisions</span><span class="health-val neutral">${health.d3_total || 0}</span></div>
        <div class="health-row"><span class="health-key">D1 Freshness</span><span class="health-val neutral">${typeof health.d1_freshness === 'object' && health.d1_freshness ? Object.entries(health.d1_freshness).map(([k,v]) => k+'='+v).join(', ') : (health.d1_freshness || '—')}</span></div>
      </div>

      <!-- Errors -->
      <div class="health-card">
        <div class="health-card-title">⚠️ Recent Errors</div>
        <div class="health-row"><span class="health-key">Errors (recent)</span><span class="health-val ${(h.errors_1h || 0) > 0 ? 'err' : 'ok'}">${h.errors_1h || 0}</span></div>
        <div class="health-row"><span class="health-key">Warnings (recent)</span><span class="health-val ${(h.warnings_1h || 0) > 0 ? 'warn' : 'ok'}">${h.warnings_1h || 0}</span></div>
        <div class="health-row"><span class="health-key">D1 Scan Age</span><span class="health-val neutral">${d1.age_s != null ? d1.age_s + 's' : '—'}</span></div>
        <div class="health-row"><span class="health-key">D2 Scan Age</span><span class="health-val neutral">${d2.age_s != null ? d2.age_s + 's' : '—'}</span></div>
        <div class="health-row"><span class="health-key">D3 Fusion Age</span><span class="health-val neutral">${d3.age_s != null ? d3.age_s + 's' : '—'}</span></div>
      </div>

      <!-- Stats -->
      <div class="health-card">
        <div class="health-card-title">📈 Pipeline Stats</div>
        <div class="health-row"><span class="health-key">Total Signals</span><span class="health-val neutral">${h.signals || 0}</span></div>
        <div class="health-row"><span class="health-key">Fusion Decisions</span><span class="health-val neutral">${h.decisions || 0}</span></div>
      </div>
    `;
  } catch (e) {
    grid.innerHTML = '<div class="obs-log-loading">Failed to load health data</div>';
  }
}

// ── Init ───────────────────────────────────────────────────────────
document.addEventListener('DOMContentLoaded', () => {
  connectWS();
  initFilters();
  startTimerUpdates();
  updateDLStatus();
  setInterval(checkHealth, 3000);
  // Recalculate scan timers every minute
  setInterval(calculateNextScanTimes, 60000);
});
