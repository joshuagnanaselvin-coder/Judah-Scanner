// ═══════════════════════════════════════════════════════════════════════
// Judah — Frontend (V5.2 Backend-Aligned)
// Consumes the exact payload from signal_fusion.py + ws_hub.py
// ═══════════════════════════════════════════════════════════════════════

// ── State ──────────────────────────────────────────────────────────
let allSignals = [];
let ws = null;
let filters = { marketType: 'all', evolution: 'all', direction: 'all' };
const expandedCards = new Set();
let stats = { d1_coins: 0, d2_signals: 0, d3_fusion: 0 };

// ── Backend field access helpers ────────────────────────────────────
// Backend sends: { coin, signal_type, signal_type_name, signal_type_color, signal_type_icon,
//   d1_tier, d1_score, d2_tier, d2_score, direction, action,
//   d1_structure: { direction, tier, score, ob_type, ob_zone, ob_low, ob_high,
//                   ob_strength, msb_type, msb_level, msb_direction,
//                   fvg_type, fvg_size_atr, fvg_filled_pct,
//                   liq_swept, liq_level, liq_direction,
//                   poc, va_high, va_low, premium_discount, session, session_label },
//   d2_structure: { scenario, entry_type, sl_method,
//                   ob_type, ob_zone, ob_low, ob_high, ob_strength,
//                   msb_type, msb_level, msb_direction,
//                   fvg_type, fvg_size_atr, fvg_filled_pct,
//                   liq_swept, liq_level, liq_direction,
//                   poc, va_high, va_low, session, session_label,
//                   premium_discount, price_position_pct, displacement_ratio, ssl, bsl },
//   alignment: { alignment_score, alignment_components, htf_dir, ltf_dir,
//                d1_dir, d2_dir, aligned, direction_match, same_direction, opposing },
//   entry, sl, tp1, tp2, rr1, rr2,
//   expected_value, expected_value_pct, estimated_win_rate,
//   freshness, score_history, born_at, last_scan,
//   nascent_move, entry_precision,
//   marketEvolution: { state, description, tradeStyle, action, confidence, risk,
//                      evolution, momentumVelocity, previousState, nextProbableState,
//                      spiral, transitionHistory, alignmentScore,
//                      institutionalCategory, tradingDecision,
//                      evolutionVelocity, evolutionConfidence }
// }

function getMEE(s) {
  return s.marketEvolution || {};
}

// ── Colors ──────────────────────────────────────────────────────────
const SIGNAL_TYPE_COLORS = {
  A: '#eab308', B: '#3b82f6', C: '#22c55e', D: '#f97316', E: '#ef4444',
};
const MARKET_TYPE_COLORS = {
  TREND: '#22c55e', RE_ENTRY: '#f59e0b', REVERSAL: '#ef4444', DORMANT: '#6b7280',
};
const SPIRAL_COLORS = {
  Expansion: '#22c55e', Correction: '#f59e0b', Failure: '#ef4444', Neutral: '#6b7280',
};
const TIER_COLORS = {
  SNIPER: '#eab308', OPPORTUNITY: '#22c55e', WATCH: '#3b82f6', REJECTED: '#6b7280',
};

// ── Time helpers ────────────────────────────────────────────────────
function timeAgo(ts) {
  if (!ts) return '—';
  const s = Math.floor((Date.now() - new Date(ts).getTime()) / 1000);
  if (s < 60) return s + 's';
  if (s < 3600) return Math.floor(s / 60) + 'm';
  return Math.floor(s / 3600) + 'h';
}

function fmtPrice(v) {
  if (v == null || isNaN(v)) return '—';
  if (v === 0) return '0';
  if (v >= 100) return v.toFixed(2);
  if (v >= 1) return v.toFixed(4);
  return v.toFixed(6);
}

function fmtRR(v) {
  if (!v) return '—';
  return v.toFixed(1) + 'x';
}

function fmtPct(v) {
  if (v == null) return '—';
  return (v > 0 ? '+' : '') + v.toFixed(1) + '%';
}

function evolutionArrow(vel) {
  if (vel === 'improving') return '↑';
  if (vel === 'degrading') return '↓';
  return '→';
}

function spiralIcon(spiral) {
  if (spiral === 'Expansion') return '🟢';
  if (spiral === 'Correction') return '🟡';
  if (spiral === 'Failure') return '🔴';
  return '⚪';
}

function signalTypeBadge(s) {
  const icon = s.signal_type_icon || '—';
  const name = s.signal_type_name || '—';
  const color = s.signal_type_color || '#6b7280';
  const action = s.action || '';
  return `<span class="stype-badge" style="background:${color}22;color:${color};border-color:${color}44">${icon} ${name} · ${action}</span>`;
}

function smcTag(label, value, opts = {}) {
  const cls = opts.className || '';
  const extra = opts.extra ? ` <span class="extra-tag">${opts.extra}</span>` : '';
  if (!value) return '';
  return `<span class="smc-tag ${cls}">${label} ${value}${extra}</span>`;
}

function zoneBadge(zone) {
  if (!zone || zone === 'UNKNOWN') return '';
  const colors = { PREMIUM: '#ef4444', DISCOUNT: '#22c55e', EQUILIBRIUM: '#3b82f6' };
  const c = colors[zone] || '#6b7280';
  return `<span class="zone-badge" style="color:${c}">${zone}</span>`;
}

// ── Card HTML ────────────────────────────────────────────────────────
function buildCard(s) {
  const mee = getMEE(s);
  const cat = mee.institutionalCategory || 'DORMANT';
  const vel = mee.evolutionVelocity || 'stable';
  const spiral = mee.spiral || 'Neutral';
  const stype = s.signal_type || '—';
  const stypeColor = s.signal_type_color || '#6b7280';
  const tierColor = TIER_COLORS[s.d2_tier] || '#6b7280';
  const catColor = MARKET_TYPE_COLORS[cat] || '#6b7280';
  const isNew = Date.now() - new Date(s.born_at || 0).getTime() < 8000;
  const isExpanded = expandedCards.has(s.signal_id);

  // D1 structure panel
  const d1s = s.d1_structure || {};
  const d1Tags = [
    smcTag('MSB', d1s.msb_type ? d1s.msb_type.toUpperCase() : '', { className: 'msb-' + (d1s.msb_type || '').toLowerCase() }),
    smcTag('OB', d1s.ob_type ? d1s.ob_type.replace(/_OB$/, '') : '', { extra: zoneBadge(d1s.ob_zone) }),
    smcTag('FVG', d1s.fvg_type ? (d1s.fvg_type[0] + (d1s.fvg_size_atr || 0).toFixed(1) + 'x') : '', { className: 'fvg-' + (d1s.fvg_type || '').toLowerCase() }),
    d1s.liq_swept ? smcTag('LIQ', 'SWEPT ' + fmtPrice(d1s.liq_level), { className: 'liq-swept' }) : '',
    d1s.ob_low ? `<span class="level-tag ssl">SSL ${fmtPrice(d1s.ob_low)}</span>` : '',
    d1s.ob_high ? `<span class="level-tag bsl">BSL ${fmtPrice(d1s.ob_high)}</span>` : '',
    d1s.poc ? smcTag('POC', fmtPrice(d1s.poc)) : '',
    (d1s.va_low && d1s.va_high) ? `<span class="level-tag va">VA ${fmtPrice(d1s.va_low)}–${fmtPrice(d1s.va_high)}</span>` : '',
    smcTag('PD', d1s.premium_discount, { className: 'pd-' + (d1s.premium_discount || '').toLowerCase() }),
    smcTag('SESS', d1s.session_label || d1s.session || '', { className: 'session' }),
  ].filter(Boolean).join('');

  // D2 structure panel
  const d2s = s.d2_structure || {};
  const d2Tags = [
    smcTag('SCENARIO', d2s.scenario || '', { className: 'scenario-' + (d2s.scenario || '').toLowerCase() }),
    smcTag('MSB', d2s.msb_type ? d2s.msb_type.toUpperCase() : '', { className: 'msb-' + (d2s.msb_type || '').toLowerCase() }),
    smcTag('OB', d2s.ob_type ? d2s.ob_type.replace(/_OB$/, '') : '', { extra: zoneBadge(d2s.ob_zone) }),
    smcTag('FVG', d2s.fvg_type ? (d2s.fvg_type[0] + (d2s.fvg_size_atr || 0).toFixed(1) + 'x') : '', { className: 'fvg-' + (d2s.fvg_type || '').toLowerCase() }),
    d2s.liq_swept ? smcTag('LIQ', 'SWEPT ' + fmtPrice(d2s.liq_level), { className: 'liq-swept' }) : '',
    d2s.poc ? smcTag('POC', fmtPrice(d2s.poc)) : '',
    (d2s.va_low && d2s.va_high) ? `<span class="level-tag va">VA ${fmtPrice(d2s.va_low)}–${fmtPrice(d2s.va_high)}</span>` : '',
    smcTag('DISP', (d2s.displacement_ratio || 0).toFixed(1) + 'x', { className: 'disp' }),
    smcTag('PD', d2s.premium_discount, { className: 'pd-' + (d2s.premium_discount || '').toLowerCase() }),
    s.nascent_move ? `<span class="nascent-badge">NAS</span>` : '',
  ].filter(Boolean).join('');

  // Alignment
  const align = s.alignment || {};
  const alignScore = align.alignment_score || 0;
  const alignCls = alignScore >= 15 ? 'aligned-high' : alignScore >= 8 ? 'aligned-mid' : 'aligned-low';
  const d1Dir = align.d1_dir || d1s.direction || '?';
  const d2Dir = align.d2_dir || s.direction || '?';
  const arrowsMatch = d1Dir === d2Dir && d1Dir !== '?' && d2Dir !== '?';
  const alignChips = [
    `<span class="align-chip dir-${(d1Dir || 'neutral').toLowerCase()}">D1 ${d1Dir || '—'}</span>`,
    `<span class="align-chip arrow ${arrowsMatch ? 'match' : 'clash'}">${arrowsMatch ? '→' : '↔⃡'}</span>`,
    `<span class="align-chip dir-${(d2Dir || 'neutral').toLowerCase()}">D2 ${d2Dir || '—'}</span>`,
  ].join('');

  // Volume Profile HTML
  let vpHtml = '';
  if (d1s.poc || d1s.va_low) {
    vpHtml = `<div class="vp-row">` +
      (d1s.poc ? `<span class="vp-chip"><span class="vp-lbl">POC</span> ${fmtPrice(d1s.poc)}</span>` : '') +
      (d1s.va_low && d1s.va_high ? `<span class="vp-chip"><span class="vp-lbl">VA</span> ${fmtPrice(d1s.va_low)}–${fmtPrice(d1s.va_high)}</span>` : '') +
      (d1s.liq_swept ? `<span class="vp-chip liq"><span class="vp-lbl">Liq</span> SWPT ${fmtPrice(d1s.liq_level)}</span>` : '') +
      `</div>`;
  }

  // Score history sparkline
  const hist = (s.score_history || []).slice(-12);
  const sparkData = hist.map(h => h[1] || h.score || 0).join(',');

  // TF breakdown
  const tfs = s.d1_timeframes || {};
  const tfHtml = Object.entries(tfs).map(([tf, d]) => {
    const tc = TIER_COLORS[d.tier] || '#6b7280';
    return `<span class="tf-chip" style="color:${tc}">${tf} <strong>${d.score ?? 0}</strong></span>`;
  }).join('');

  // ── Card ────────────────────────────────────────────────────────
  return `<div class="card ${isNew ? 'card-new' : ''} cat-${cat.toLowerCase()}" id="card-${s.signal_id}">
    <div class="card-header" onclick="toggleExpand('${s.signal_id}')">
      <div class="card-left">
        <span class="cat-dot" style="background:${catColor}"></span>
        <span class="coin-name">${s.coin}</span>
        ${s.timeframe ? `<span class="tf-badge">${s.timeframe}</span>` : ''}
        ${signalTypeBadge(s)}
      </div>
      <div class="card-right">
        <span class="freshness-badge ${(s.freshness || 'HOT').toLowerCase()}">${s.freshness || 'HOT'}</span>
        <span class="cat-label" style="color:${catColor}">${cat.replace(/_/g, ' ')}</span>
        <span class="spiral-label" style="color:${SPIRAL_COLORS[spiral] || '#6b7280'}">${spiralIcon(spiral)} ${spiral}</span>
        <span class="ev-vel">${evolutionArrow(vel)} ${vel}</span>
        <span class="tier-badge" style="background:${tierColor}22;color:${tierColor};border-color:${tierColor}44">${s.d2_tier || '—'}</span>
        <span class="d2-score">${s.d2_score ?? 0}</span>
        <span class="expand-icon">${isExpanded ? '▼' : '▶'}</span>
      </div>
    </div>

    ${isExpanded ? `<div class="card-body">
      <!-- Signal Type + Decision -->
      <div class="decision-row">
        <div class="decision-cell">
          <span class="decision-label">Signal Type</span>
          ${signalTypeBadge(s)}
        </div>
        <div class="decision-cell">
          <span class="decision-label">Decision</span>
          <span class="decision-action" style="color:${stypeColor}">${s.action || '—'}</span>
        </div>
        <div class="decision-cell">
          <span class="decision-label">Position</span>
          <span class="pos-mult">${(s.position_mult ?? 0).toFixed(2)}x</span>
          <span class="stop-mult">Stop ${s.stop_mult ?? 1.5}x</span>
        </div>
      </div>

      <!-- Technical Context: D1 + D2 + Alignment + VP -->
      <div class="smc-row">
        <div class="smc-panel d1-panel">
          <div class="smc-header">
            <span class="smc-title">\u{1F4CA} D1 HTF</span>
            <span class="smc-tier ${(d1s.tier || '').toLowerCase()}">${d1s.tier || '—'}</span>
            <span class="smc-score">${d1s.score ?? 0}</span>
          </div>
          <div class="smc-tags">${d1Tags}</div>
        </div>
        <div class="smc-panel d2-panel">
          <div class="smc-header">
            <span class="smc-title">\u{1F3AF} D2 ${s.timeframe || '15M'}</span>
            <span class="smc-tier ${(s.d2_tier || '').toLowerCase()}">${s.d2_tier || '—'}</span>
            <span class="smc-score">${s.d2_score ?? 0}</span>
          </div>
          <div class="smc-tags">${d2Tags}</div>
        </div>
      </div>

      <!-- Alignment -->
      <div class="alignment-row ${alignCls}">
        <div class="alignment-label">
          <span>\u{1F517} HTF/LTF Alignment</span>
          <span class="alignment-score">${alignScore}/20</span>
        </div>
        <div class="alignment-chips">${alignChips}</div>
      </div>

      <!-- Market Evolution -->
      <div class="me-row">
        <div class="me-cell">
          <span class="me-label">Evolution</span>
          <span class="me-state">${mee.state || '—'}</span>
          <span class="me-evolution">${mee.evolution || '—'}</span>
        </div>
        <div class="me-cell">
          <span class="me-label">Confidence</span>
          <span class="me-confidence">${mee.confidence ?? 0}%</span>
          <span class="me-vel-conf">${evolutionArrow(vel)} ${mee.evolutionConfidence ?? 0}%</span>
        </div>
        <div class="me-cell">
          <span class="me-label">Trading Decision</span>
          <span class="me-decision">${mee.tradingDecision || '—'}</span>
        </div>
        <div class="me-cell">
          <span class="me-label">Momentum</span>
          <span class="me-momentum">${mee.momentumVelocity?.toFixed(1) ?? 0}</span>
        </div>
      </div>

      <!-- Volume Profile / Liquidity -->
      ${vpHtml}

      <!-- Trade Levels -->
      <div class="levels-row">
        <div class="level-cell entry">
          <span class="lvl-lbl">Entry</span>
          <span class="lvl-val">${fmtPrice(s.entry)}</span>
        </div>
        <div class="level-cell sl">
          <span class="lvl-lbl">SL</span>
          <span class="lvl-val">${fmtPrice(s.sl)}</span>
        </div>
        <div class="level-cell tp">
          <span class="lvl-lbl">TP1</span>
          <span class="lvl-val">${fmtPrice(s.tp1)}</span>
        </div>
        <div class="level-cell tp2">
          <span class="lvl-lbl">TP2</span>
          <span class="lvl-val">${fmtPrice(s.tp2)}</span>
        </div>
        <div class="level-cell rr">
          <span class="lvl-lbl">RR</span>
          <span class="lvl-val">${fmtRR(s.rr1)}</span>
          <span class="lvl-val rr2">${fmtRR(s.rr2)}</span>
        </div>
      </div>

      <!-- EV + Nascent -->
      <div class="meta-row">
        <div class="ev-cell">
          <span class="ev-label">Expected Value</span>
          <span class="ev-val ${s.expected_value_pct >= 0 ? 'ev-pos' : 'ev-neg'}">${fmtPct(s.expected_value_pct)}</span>
          <span class="ev-winrate">WR ${s.estimated_win_rate ?? 0}%</span>
        </div>
        ${s.nascent_move ? `<span class="nascent-tag">NASCENT MOVE</span>` : ''}
        ${s.entry_precision ? `<span class="ep-tag">EP ${s.entry_precision.toFixed(0)}</span>` : ''}
        <span class="born-time">${new Date(s.born_at).toLocaleTimeString([], {hour:'2-digit', minute:'2-digit'})}</span>
      </div>

      <!-- D1 TF Breakdown + Score -->
      <div class="meta-row">
        <div class="tf-row">
          <span class="meta-label">D1 TFs</span>
          ${tfHtml}
        </div>
        <div class="spark-row">
          <canvas class="sparkline" data-values="${sparkData}" width="80" height="22"></canvas>
        </div>
      </div>
    </div>` : ''}
  </div>`;
}

// ── Toggle Expand ──────────────────────────────────────────────────
function toggleExpand(id) {
  if (expandedCards.has(id)) {
    expandedCards.delete(id);
  } else {
    expandedCards.add(id);
  }
  renderSignals();
  requestAnimationFrame(drawSparklines);
}

function flashNew(id) {
  expandedCards.add(id);
  renderSignals();
  setTimeout(() => {
    expandedCards.delete(id);
    renderSignals();
  }, 4000);
}

// ── Type E Conflict Alert ────────────────────────────────────────────
let typeEAlerts = [];

function handleTypeEAlert(alert) {
  typeEAlerts.unshift(alert);
  if (typeEAlerts.length > 20) typeEAlerts = typeEAlerts.slice(0, 20);
  renderTypeEAlerts();
}

function renderTypeEAlerts() {
  const container = document.getElementById('typeEAlertContainer');
  if (!container || typeEAlerts.length === 0) {
    if (container) container.style.display = 'none';
    return;
  }
  container.style.display = 'block';
  container.innerHTML = typeEAlerts.map(a => `
    <div class="type-e-alert">
      <span class="type-e-icon">⚠️</span>
      <span class="type-e-coin">${a.coin}</span>
      <span class="type-e-conflict">${a.d1_dir || '?'} vs ${a.d2_dir || '?'}</span>
      <span class="type-e-time">${new Date(a.timestamp).toLocaleTimeString()}</span>
    </div>
  `).join('');
}

function dismissTypeEAlert(idx) {
  typeEAlerts.splice(idx, 1);
  renderTypeEAlerts();
}

// ── Render ────────────────────────────────────────────────────────
function renderSignals() {
  const container = document.getElementById('signalsContainer');
  const empty = document.getElementById('emptyState');
  if (!container) return;

  const filtered = allSignals.filter(s => {
    const mee = getMEE(s);
    // Market Type filter (institutionalCategory)
    if (filters.marketType !== 'all') {
      const sigCat = mee.institutionalCategory || 'DORMANT';
      if (sigCat !== filters.marketType) return false;
    }
    // Evolution velocity filter
    if (filters.evolution !== 'all') {
      if ((mee.evolutionVelocity || 'stable') !== filters.evolution) return false;
    }
    // Direction filter
    if (filters.direction !== 'all' && s.direction !== filters.direction) return false;
    return true;
  });

  updateMarketTypeCounts();

  if (filtered.length === 0 && allSignals.length === 0) {
    container.innerHTML = '';
    if (empty) {
      updateEmptyState('idle');
      container.appendChild(empty);
    }
    return;
  }

  if (filtered.length === 0) {
    container.innerHTML = `<div class="no-results">
      <span class="no-results-icon">🔍</span>
      <span>No signals match this filter</span>
      <button class="clear-filter-btn" onclick="clearFilters()">Clear Filters</button>
    </div>`;
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
  const progress = document.getElementById('progressBar');

  if (!title) return;

  if (state === 'scanning') {
    title.textContent = 'Scanning Markets...';
    msg.textContent = `${stats.d1_coins || 0} coins analyzed · HTF + 15M LTF · Decision Layer`;
    if (spinner) spinner.style.display = 'block';
    if (progress) progress.style.width = Math.min(100, (stats.d1_coins / 529) * 100) + '%';
  } else if (state === 'idle') {
    title.textContent = 'Initializing Market Evolution Engine...';
    msg.textContent = 'Scanning 529 coins · HTF + 15M LTF · Decision Layer';
    if (spinner) spinner.style.display = 'block';
    if (progress) progress.style.width = '0%';
  } else {
    title.textContent = 'Waiting for signals...';
    msg.textContent = '529 coins across 1H / 4H / 1D + 15M · Decision Layer';
    if (spinner) spinner.style.display = 'none';
    if (progress) progress.style.width = '100%';
  }
}

function clearFilters() {
  filters = { marketType: 'all', evolution: 'all', direction: 'all' };
  document.querySelectorAll('.filter-chip, .dir-chip').forEach(b => b.classList.remove('active'));
  document.querySelector('[data-filter-mt="all"]')?.classList.add('active');
  document.querySelector('[data-filter-evo="all"]')?.classList.add('active');
  document.querySelector('[data-filter-dir="all"]')?.classList.add('active');
  renderSignals();
}

// ── V5.2 Market Type Counts ───────────────────────────────────────
function updateMarketTypeCounts() {
  const counts = { TREND: 0, RE_ENTRY: 0, REVERSAL: 0, DORMANT: 0 };
  allSignals.forEach(s => {
    const cat = getMEE(s).institutionalCategory || 'DORMANT';
    if (counts[cat] != null) counts[cat]++;
  });
  const set = (id, v) => { const e = document.getElementById(id); if (e) e.textContent = v; };
  set('countTrend', counts.TREND);
  set('countReentry', counts.RE_ENTRY);
  set('countReversal', counts.REVERSAL);
  set('countDormant', counts.DORMANT);
}

// ── Sparklines ─────────────────────────────────────────────────────
function drawSparklines() {
  document.querySelectorAll('canvas.sparkline').forEach(canvas => {
    const vals = (canvas.dataset.values || '').split(',').map(Number).filter(v => !isNaN(v));
    if (vals.length < 2) return;
    const ctx = canvas.getContext('2d');
    const w = canvas.width, h = canvas.height;
    ctx.clearRect(0, 0, w, h);

    const min = Math.min(...vals), max = Math.max(...vals);
    const range = max - min || 1;

    const grad = ctx.createLinearGradient(0, 0, 0, h);
    grad.addColorStop(0, 'rgba(59,130,246,0.15)');
    grad.addColorStop(1, 'rgba(59,130,246,0)');

    ctx.beginPath();
    vals.forEach((v, i) => {
      const x = (i / (vals.length - 1)) * w;
      const y = h - ((v - min) / range) * (h - 4) - 2;
      if (i === 0) ctx.moveTo(x, y);
      else ctx.lineTo(x, y);
    });
    const lastX = w, lastY = h - ((vals[vals.length-1] - min) / range) * (h - 4) - 2;
    ctx.lineTo(lastX, h);
    ctx.lineTo(0, h);
    ctx.closePath();
    ctx.fillStyle = grad;
    ctx.fill();

    ctx.beginPath();
    ctx.strokeStyle = '#3b82f6';
    ctx.lineWidth = 1.5;
    ctx.lineJoin = 'round';
    vals.forEach((v, i) => {
      const x = (i / (vals.length - 1)) * w;
      const y = h - ((v - min) / range) * (h - 4) - 2;
      if (i === 0) ctx.moveTo(x, y);
      else ctx.lineTo(x, y);
    });
    ctx.stroke();

    ctx.beginPath();
    ctx.arc(lastX, lastY, 2, 0, Math.PI * 2);
    ctx.fillStyle = '#3b82f6';
    ctx.fill();
  });
}

// ── Health Poll ─────────────────────────────────────────────────────
async function checkHealth() {
  try {
    const resp = await fetch('/api/health');
    const data = await resp.json();
    if (data.stats) {
      stats = data.stats;
      updateStatsUI();
      updateScanActivity();

      if (allSignals.length === 0) {
        const empty = document.getElementById('emptyState');
        if (empty && empty.parentNode) {
          updateEmptyState(data.ready ? 'scanning' : 'idle');
        }
      }
    }
  } catch (e) {
    // silent
  }
}

function updateStatsUI() {
  const d1 = document.getElementById('d1Count');
  const d2 = document.getElementById('d2Count');
  const fusion = document.getElementById('totalSignals');
  if (d1) d1.textContent = stats.d1_coins || 0;
  if (d2) d2.textContent = stats.d2_signals || 0;
  if (fusion) fusion.textContent = stats.d3_fusion || allSignals.length || 0;
}

function updateScanActivity() {
  setAct('actD1', stats.last_d1_scan);
  setAct('actD2', stats.last_d2_scan);
  setAct('actD3', stats.last_d3_fusion);
}

function setAct(prefix, ts) {
  const status = document.getElementById(prefix + 'Status');
  const time = document.getElementById(prefix + 'Time');
  if (!status || !time) return;
  if (!ts) { status.textContent = '—'; time.textContent = ''; return; }
  const age = (Date.now() - new Date(ts).getTime()) / 1000;
  if (age < 5) status.textContent = 'Live';
  else if (age < 30) status.textContent = 'Recent';
  else status.textContent = timeAgo(ts);
  time.textContent = timeAgo(ts);
}

// ── Filters ────────────────────────────────────────────────────────
function initFilters() {
  document.querySelectorAll('.mt-chip').forEach(btn => {
    btn.addEventListener('click', () => {
      filters.marketType = btn.dataset.filterMt;
      document.querySelectorAll('.mt-chip').forEach(b => b.classList.remove('active'));
      btn.classList.add('active');
      renderSignals();
    });
  });

  document.querySelectorAll('.evo-chip').forEach(btn => {
    btn.addEventListener('click', () => {
      filters.evolution = btn.dataset.filterEvo;
      document.querySelectorAll('.evo-chip').forEach(b => b.classList.remove('active'));
      btn.classList.add('active');
      renderSignals();
    });
  });

  document.querySelectorAll('.dir-chip').forEach(btn => {
    btn.addEventListener('click', () => {
      filters.direction = btn.dataset.filterDir;
      document.querySelectorAll('.dir-chip').forEach(b => b.classList.remove('active'));
      btn.classList.add('active');
      renderSignals();
    });
  });

  const btn = document.getElementById('btnRestart');
  if (btn) btn.addEventListener('click', restartScanner);
}

// ── WebSocket ─────────────────────────────────────────────────────
function connectWS() {
  const proto = location.protocol === 'https:' ? 'wss:' : 'ws:';
  ws = new WebSocket(`${proto}//${location.host}/ws-fusion`);

  ws.onopen = () => {
    console.log('[WS] connected');
    const dot = document.getElementById('wsDot');
    const label = document.getElementById('wsLabel');
    if (dot) { dot.style.background = '#22c55e'; }
    if (label) { label.textContent = 'Live'; }
  };

  ws.onclose = () => {
    console.log('[WS] disconnected, reconnecting...');
    const dot = document.getElementById('wsDot');
    const label = document.getElementById('wsLabel');
    if (dot) { dot.style.background = '#ef4444'; }
    if (label) { label.textContent = 'Offline'; }
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
        return;
      }

      if (msg.type === 'signal' && msg.data) {
        const s = msg.data;
        // Replace or add
        const idx = allSignals.findIndex(x => x.signal_id === s.signal_id);
        if (idx >= 0) allSignals[idx] = s;
        else allSignals.unshift(s);

        // Flash new signals
        if (idx < 0) flashNew(s.signal_id);

        renderSignals();
        return;
      }

      if (msg.type === 'TYPE_E_ALERT' && msg.data) {
        handleTypeEAlert(msg.data);
        return;
      }
    } catch (e) {
      console.error('[WS] parse error', e);
    }
  };
}

async function restartScanner() {
  try {
    await fetch('/api/restart', { method: 'POST' });
  } catch (e) { /* silent */ }
}

// ── Init ───────────────────────────────────────────────────────────
document.addEventListener('DOMContentLoaded', () => {
  connectWS();
  initFilters();
  setInterval(checkHealth, 3000);

  const empty = document.getElementById('emptyState');
  if (empty) updateEmptyState('idle');
});
