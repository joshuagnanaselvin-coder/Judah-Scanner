// ═══════════════════════════════════════════════════════════════════════
// Judah — Institutional Market Evolution Terminal (V5.2)
// The frontend CONSUMES the backend's Market Evolution output.
// It does NOT calculate evolution.
// ═══════════════════════════════════════════════════════════════════════

// ── State ──────────────────────────────────────────────────────────
let allSignals = [];
let ws = null;
let filters = { marketType: 'all', evolution: 'all', direction: 'all' };
const expandedCards = new Set();
let stats = { d1_coins: 0, d2_signals: 0, d3_fusion: 0, last_d1_scan: 0, last_d2_scan: 0, last_d3_fusion: 0 };
let wsReconnectTimer = null;
let prevTimestamps = { d1: 0, d2: 0, d3: 0 };

// ── V5.2 Institutional Categories (top filter) ─────────────────────
const MARKET_TYPE_COLORS = {
  TREND:     '#22c55e',  // institutional trend (expansion)
  RE_ENTRY:  '#f59e0b',  // institutional re-entry (pullback)
  REVERSAL:  '#ef4444',  // institutional reversal (failure)
  DORMANT:   '#6b7280',
};

const MARKET_TYPE_LABELS = {
  TREND:    '🏛 Institutional Trend',
  RE_ENTRY: '🏛 Institutional Re-Entry',
  REVERSAL: '🏛 Institutional Reversal',
  DORMANT:  '⚪ Dormant',
};

const TRADING_DECISION_COLORS = {
  'Trade With Trend':        '#22c55e',
  'Wait For Confirmation':   '#3b82f6',
  'Prepare Pullback Entry':  '#f59e0b',
  'Prepare Reversal':        '#ef4444',
  'Avoid':                   '#dc2626',
  'No Edge':                 '#6b7280',
};

// Spiral still used for accent coloring (kept for visual continuity)
const SPIRAL_COLORS = {
  Expansion: '#22c55e',
  Correction: '#f59e0b',
  Failure: '#ef4444',
  Neutral: '#6b7280',
};

// ── Time helpers ────────────────────────────────────────────────────
function timeAgo(ts) {
  if (!ts) return '—';
  const diff = Date.now() / 1000 - ts;
  if (diff < 5) return 'just now';
  if (diff < 60) return Math.floor(diff) + 's ago';
  if (diff < 3600) return Math.floor(diff / 60) + 'm ago';
  return Math.floor(diff / 3600) + 'h ago';
}

function fmtTime(ts) {
  if (!ts) return '--:--';
  return new Date(ts * 1000).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit', second: '2-digit' });
}

// ── WebSocket (D3 Fusion) ──────────────────────────────────────────
function connectWS() {
  const proto = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
  ws = new WebSocket(proto + '//' + window.location.host + '/ws-fusion');

  ws.onopen = () => {
    clearTimeout(wsReconnectTimer);
    setWsStatus('live');
    console.log('[ws-fusion] Connected');
  };

  ws.onmessage = (event) => {
    try {
      const data = JSON.parse(event.data);
      if (data.type === 'INITIAL') {
        allSignals = data.signals || [];
        sortSignals();
        if (data.stats) {
          stats = data.stats;
          updateStatsUI();
          updateScanActivity();
        }
        renderSignals();
      } else if (data.type === 'signal') {
        const sig = data.data;
        const idx = allSignals.findIndex(s => s.signal_id === sig.signal_id);
        if (idx >= 0) {
          allSignals[idx] = sig;
        } else {
          allSignals.unshift(sig);
        }
        sortSignals();
        if (allSignals.length > 100) allSignals = allSignals.slice(0, 100);
        renderSignals();
        if (idx < 0) flashNew(sig.signal_id);
      } else if (data.type === 'TYPE_E_ALERT') {
        handleTypeEAlert(data.data);
      }
    } catch (e) {
      console.error('[ws-fusion] Parse error:', e);
    }
  };

  ws.onclose = () => {
    setWsStatus('reconnecting');
    wsReconnectTimer = setTimeout(connectWS, 3000);
  };

  ws.onerror = () => {
    console.error('[ws-fusion] Connection error');
  };
}

function sortSignals() {
  allSignals.sort((a, b) => {
    const aMee = getMEE(a);
    const bMee = getMEE(b);
    const aConf = aMee.evolutionConfidence || 0;
    const bConf = bMee.evolutionConfidence || 0;
    if (bConf !== aConf) return bConf - aConf;
    return (b.d2_score || 0) - (a.d2_score || 0);
  });
}

function setWsStatus(status) {
  const dot = document.getElementById('wsDot');
  const label = document.getElementById('wsLabel');
  if (!dot || !label) return;

  dot.classList.remove('pulse-on', 'pulse-off', 'pulse-reconnect');
  if (status === 'live') {
    dot.classList.add('pulse-on');
    label.textContent = 'Live';
  } else if (status === 'reconnecting') {
    dot.classList.add('pulse-reconnect');
    label.textContent = 'Reconnecting...';
  } else {
    dot.classList.add('pulse-off');
    label.textContent = 'Offline';
  }
}

// ── Scan Activity Bar ──────────────────────────────────────────────
let scanTimers = { d1: null, d2: null, d3: null };

function updateScanActivity() {
  updateScanItem('actD1', 'actD1Status', 'actD1Time', stats.last_d1_scan, 'd1');
  updateScanItem('actD2', 'actD2Status', 'actD2Time', stats.last_d2_scan, 'd2');
  updateScanItem('actD3', 'actD3Status', 'actD3Time', stats.last_d3_fusion, 'd3');
}

function updateScanItem(itemId, statusId, timeId, currentTs, key) {
  const item = document.getElementById(itemId);
  const statusEl = document.getElementById(statusId);
  const timeEl = document.getElementById(timeId);
  if (!item || !statusEl) return;

  const prev = prevTimestamps[key] || 0;

  if (currentTs > prev && currentTs > 0) {
    prevTimestamps[key] = currentTs;
    item.classList.remove('done', 'idle', 'error');
    item.classList.add('scanning');
    statusEl.classList.remove('done', 'error');
    statusEl.classList.add('scanning');
    statusEl.textContent = 'Scanning';
    if (timeEl) timeEl.textContent = '';

    if (scanTimers[key]) clearTimeout(scanTimers[key]);
    scanTimers[key] = setTimeout(() => {
      item.classList.remove('scanning');
      item.classList.add('done');
      statusEl.classList.remove('scanning');
      statusEl.classList.add('done');
      statusEl.textContent = 'Done';
      scanTimers[key] = null;
    }, 3000);
    return;
  }

  if (currentTs === 0 || prev === 0) {
    item.classList.remove('scanning', 'done', 'error');
    item.classList.add('idle');
    statusEl.classList.remove('scanning', 'done', 'error');
    if (timeEl) timeEl.textContent = '';
    return;
  }

  const diff = Date.now() / 1000 - currentTs;
  if (diff < 5) {
    statusEl.textContent = 'Just now';
  } else if (diff < 60) {
    statusEl.textContent = Math.floor(diff) + 's ago';
  } else if (diff < 3600) {
    statusEl.textContent = Math.floor(diff / 60) + 'm ago';
  } else {
    statusEl.textContent = Math.floor(diff / 3600) + 'h ago';
  }
  if (timeEl) timeEl.textContent = '';
}

// ── Stats ──────────────────────────────────────────────────────────
function updateStatsUI() {
  const el = (id, val) => { const e = document.getElementById(id); if (e) e.textContent = val || 0; };
  el('totalSignals', stats.d3_fusion);
  el('d1Count', stats.d1_coins);
  el('d2Count', stats.d2_signals);
}

// ── Restart ────────────────────────────────────────────────────────
async function restartScanner() {
  const btn = document.getElementById('btnRestart');
  if (!btn || btn.disabled) return;

  btn.disabled = true;
  btn.classList.add('spinning');

  try {
    const resp = await fetch('/api/restart', { method: 'POST' });
    const data = await resp.json();
    console.log('[restart]', data);
  } catch (e) {
    console.error('[restart] Failed:', e);
  }

  setTimeout(() => {
    btn.disabled = false;
    btn.classList.remove('spinning');
  }, 5000);
}

// ── Formatting ─────────────────────────────────────────────────────
function fmtPrice(p) {
  if (p == null || isNaN(p)) return '---';
  if (p >= 1000) return '$' + p.toFixed(2);
  if (p >= 1) return '$' + p.toFixed(3);
  if (p >= 0.01) return '$' + p.toFixed(4);
  return '$' + p.toFixed(6);
}

function fmtRR(rr) {
  if (!rr) return '---';
  return rr.toFixed(1) + 'R';
}

function freshnessIcon(f) {
  const icons = { 'HOT': '🔥', 'WARM': '🌡️', 'COOLING': '❄️', 'STALE': '🥶' };
  return icons[f] || '';
}

// ── Market Evolution helpers (V5.2) ────────────────────────────────
function getMEE(s) {
  return s.marketEvolution || s.mee || {};
}

function categoryColor(cat) {
  return MARKET_TYPE_COLORS[cat] || MARKET_TYPE_COLORS.DORMANT;
}

function categoryLabel(cat) {
  return MARKET_TYPE_LABELS[cat] || MARKET_TYPE_LABELS.DORMANT;
}

function decisionColor(decision) {
  return TRADING_DECISION_COLORS[decision] || TRADING_DECISION_COLORS['No Edge'];
}

function evolutionVelocityArrow(vel) {
  if (vel === 'improving') return { glyph: '↑', cls: 'evo-up' };
  if (vel === 'degrading') return { glyph: '↓', cls: 'evo-down' };
  return { glyph: '→', cls: 'evo-stable' };
}

function spiralColor(spiral) {
  return SPIRAL_COLORS[spiral] || SPIRAL_COLORS.Neutral;
}

function evolutionClass(evo) {
  const k = (evo || '').toLowerCase();
  if (k === 'improving') return 'evo-improving';
  if (k === 'degrading') return 'evo-degrading';
  return 'evo-stable';
}

function confidenceClass(c) {
  const n = Number(c) || 0;
  if (n >= 85) return 'conf-very-high';
  if (n >= 70) return 'conf-high';
  if (n >= 50) return 'conf-medium';
  return 'conf-low';
}

// ── SMC Tag Builder (preserved from V5.1) ─────────────────────────
function smcTag(label, value, opts = {}) {
  if (!value || value === '—' || value === 0) return '';
  const cls = opts.className || '';
  const extra = opts.extra ? ' ' + opts.extra : '';
  const labelStr = label ? `<span class="smc-label">${label}</span>` : '';
  return `<span class="smc-tag ${cls}">${labelStr}<strong>${value}</strong>${extra}</span>`;
}

function zoneBadge(tag) {
  if (!tag || tag === 'UNKNOWN') return '';
  const map = {
    'PREMIUM': '#ef4444', 'DISCOUNT': '#22c55e',
    'EQUILIBRIUM': '#f59e0b', 'UNKNOWN': '#6b7280'
  };
  const c = map[tag] || '#6b7280';
  return `<span class="zone-badge" style="color:${c};border-color:${c}30;background:${c}10">${tag}</span>`;
}

// ── V5.2: Build Evolution Journey Strip ───────────────────────────
function buildEvolutionJourney(s) {
  const mee = getMEE(s);
  const prev = mee.previousState || '—';
  const curr = mee.state || '—';
  const next = mee.nextProbableState || '—';
  const vel = mee.evolutionVelocity || 'stable';
  const cat = mee.institutionalCategory || 'DORMANT';
  const arrow = evolutionVelocityArrow(vel);
  const catColor = categoryColor(cat);

  return `
    <div class="evo-journey" data-cat="${cat}">
      <div class="evo-node evo-prev">
        <div class="evo-node-label">Previous</div>
        <div class="evo-node-name">${prev}</div>
      </div>
      <div class="evo-arrow-down" aria-hidden="true">↓</div>
      <div class="evo-node evo-curr" style="--accent:${catColor}">
        <div class="evo-node-label">Current</div>
        <div class="evo-node-name">${curr}</div>
      </div>
      <div class="evo-arrow-down" aria-hidden="true">↓</div>
      <div class="evo-node evo-next">
        <div class="evo-node-label">Expected</div>
        <div class="evo-node-name">${next}</div>
      </div>
      <div class="evo-status-row">
        <span class="evo-status ${arrow.cls}">${arrow.glyph} ${vel.charAt(0).toUpperCase() + vel.slice(1)}</span>
      </div>
    </div>
  `;
}

// ── Build Card (V5.2 institutional layout) ──────────────────────────
function buildCard(s) {
  const mee = getMEE(s);
  const dirIcon = s.direction === 'BULLISH' ? '🟢' : '🔴';
  const dirLabel = s.direction === 'BULLISH' ? 'Long' : 'Short';

  // V5.2 fields
  const cat = mee.institutionalCategory || 'DORMANT';
  const decision = mee.tradingDecision || 'No Edge';
  const catColor = categoryColor(cat);
  const decisionColorVal = decisionColor(decision);
  const confidence = mee.evolutionConfidence ?? mee.confidence ?? 0;

  const isExpanded = expandedCards.has(s.signal_id);
  const isNew = Date.now() - new Date(s.born_at || 0).getTime() < 5000;

  // TradingView / Binance links
  const rawCoin = s.coin || 'BTCUSDT';
  const base = rawCoin.replace(/USDT$/i, '').replace(/BINANCE:/i, '');
  const tvUrl = 'https://www.tradingview.com/chart/?symbol=' + encodeURIComponent('BINANCE:' + base + 'USDT.P');
  const binanceUrl = 'https://www.binance.com/en/futures/' + encodeURIComponent(base + 'USDT');

  // D1/D2 structure
  const d1s = s.d1_structure || {};
  const d2s = s.d2_structure || {};
  const tfs = s.d1_timeframes || {};
  const alignment = s.alignment || {};

  // Score sparkline
  const hist = (s.score_history || []).slice(-12);
  const sparkData = hist.map(h => h[1] || h.score || 0).join(',');

  // ── D1 SMC Tags ──────────────────────────────────────────────────
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
    smcTag('SESS', d1s.session || '', { className: 'session' }),
  ].filter(Boolean).join('') || '<span class="no-data">No HTF data</span>';

  // ── D2 SMC Tags ──────────────────────────────────────────────────
  const d2Tags = [
    d2s.scenario ? `<span class="scenario-tag">${d2s.scenario.replace(/_/g, ' ')}</span>` : '',
    smcTag('MSB', d2s.msb_type ? d2s.msb_type.toUpperCase() : '', { className: 'msb-' + (d2s.msb_type || '').toLowerCase() }),
    smcTag('OB', d2s.ob_type ? d2s.ob_type.replace(/_OB$/, '') : '', { extra: zoneBadge(d2s.ob_zone) }),
    smcTag('FVG', d2s.fvg_type ? (d2s.fvg_type[0] + (d2s.fvg_size_atr || 0).toFixed(1) + 'x') : '', { className: 'fvg-' + (d2s.fvg_type || '').toLowerCase() }),
    d2s.liq_swept ? smcTag('LIQ', 'SWEPT ' + fmtPrice(d2s.liq_level), { className: 'liq-swept' }) : '',
    (d2s.ssl && d2s.ssl.level) ? `<span class="level-tag ssl">SSL ${fmtPrice(d2s.ssl.level)}</span>` : '',
    (d2s.bsl && d2s.bsl.level) ? `<span class="level-tag bsl">BSL ${fmtPrice(d2s.bsl.level)}</span>` : '',
    d2s.sl_method ? smcTag('SL', d2s.sl_method.toUpperCase(), { className: 'sl-method' }) : '',
    d2s.entry_type ? smcTag('ENTRY', d2s.entry_type.replace(/_/g, ' '), { className: 'entry-type' }) : '',
    smcTag('PD', d2s.premium_discount, { className: 'pd-' + (d2s.premium_discount || '').toLowerCase() }),
    smcTag('SESS', d2s.session_label || '', { className: 'session' }),
  ].filter(Boolean).join('') || '<span class="no-data">No LTF data</span>';

  // ── TF Breakdown ─────────────────────────────────────────────────
  const tfHtml = Object.entries(tfs).map(([tf, d]) => {
    const cls = (d.tier || '').toLowerCase();
    return `<span class="tf-chip ${cls}">${tf} <strong>${d.score ?? 0}</strong></span>`;
  }).join('') || '<span class="tf-chip">—</span>';

  // ── Alignment strip (V5.2) ────────────────────────────────────────
  const alignScore = alignment.alignment_score || 0;
  const alignCls = alignScore >= 15 ? 'align-high' : alignScore >= 10 ? 'align-med' : alignScore >= 5 ? 'align-low' : 'align-none';
  const alignChecks = (alignment.components || {});
  const alignChips = [
    alignChecks.direction_agreement ? '<span class="align-chip ok">Dir ✓</span>' : '<span class="align-chip">Dir ✗</span>',
    alignChecks.htf_ob_alignment ? '<span class="align-chip ok">HTF OB ✓</span>' : '<span class="align-chip">HTF OB ✗</span>',
    alignChecks.htf_zone_alignment ? '<span class="align-chip ok">Zone ✓</span>' : '<span class="align-chip">Zone ✗</span>',
    alignChecks.htf_liquidity_proximity ? '<span class="align-chip ok">LiQ ✓</span>' : '<span class="align-chip">LiQ ✗</span>',
  ].join('');

  // ── Volume Profile + Liquidity aggregate (preserved) ──────────────
  const vpHtml = `
    <div class="vp-row">
      ${d1s.poc ? `<span class="vp-cell"><span class="vp-lbl">POC</span><span class="vp-val">${fmtPrice(d1s.poc)}</span></span>` : ''}
      ${(d1s.va_low && d1s.va_high) ? `<span class="vp-cell"><span class="vp-lbl">VA</span><span class="vp-val">${fmtPrice(d1s.va_low)}–${fmtPrice(d1s.va_high)}</span></span>` : ''}
      ${d1s.liq_swept ? `<span class="vp-cell"><span class="vp-lbl">Liq</span><span class="vp-val swept">SWPT ${fmtPrice(d1s.liq_level)}</span></span>` : ''}
    </div>
  `;

  // ── Card HTML ────────────────────────────────────────────────────
  const stypeColor = s.signal_type_color || '#6b7280';
  const stypeIcon = s.signal_type_icon || '';
  const stypeName = s.signal_type_name || '—';
  return `
  <div class="signal-card v52 ${isNew ? 'is-new' : ''} ${isExpanded ? 'is-expanded' : ''}"
       data-id="${s.signal_id}" data-cat="${cat}" data-vel="${mee.evolutionVelocity || 'stable'}"
       data-stype="${s.signal_type || '—'}">
    <div class="card-header" onclick="toggleExpand('${s.signal_id}')">
      <div class="card-header-left">
        <span class="cat-pip" style="background:${catColor}"></span>
        <span class="signal-type-badge" style="color:${stypeColor};border-color:${stypeColor}40;background:${stypeColor}10">
          ${stypeIcon} ${stypeName}
        </span>
        <a class="coin-link" href="${tvUrl}" target="_blank" rel="noopener"
           title="View ${base} on TradingView" onclick="event.stopPropagation()">
          ${base}
          <svg class="tv-svg" width="11" height="11" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round"><polyline points="22 7 13.5 15.5 8.5 10.5 2 17"/><polyline points="16 7 22 7 22 13"/></svg>
        </a>
        <a class="binance-link" href="${binanceUrl}" target="_blank" rel="noopener"
           title="Trade ${base} on Binance Futures" onclick="event.stopPropagation()">
          <svg width="11" height="11" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round"><path d="M12 5v14M5 12h14"/></svg>
        </a>
        <span class="cat-pill" style="color:${catColor};border-color:${catColor}40;background:${catColor}10">
          ${categoryLabel(cat)}
        </span>
        <span class="dir-pill ${s.direction.toLowerCase()}">${dirIcon} ${dirLabel}</span>
        <span class="freshness-pill">${freshnessIcon(s.freshness)} ${s.freshness || 'HOT'}</span>
      </div>
      <div class="card-header-right">
        <div class="score-pair">
          <div class="score-block d1">
            <span class="score-lbl">D1</span>
            <span class="score-val">${s.d1_score ?? '--'}</span>
            <span class="tier-lbl ${(s.d1_tier || '').toLowerCase()}">${s.d1_tier || '—'}</span>
          </div>
          <div class="score-divider"></div>
          <div class="score-block d2">
            <span class="score-lbl">D2</span>
            <span class="score-val">${s.d2_score ?? '--'}</span>
            <span class="tier-lbl ${(s.d2_tier || '').toLowerCase()}">${s.d2_tier || '—'}</span>
          </div>
        </div>
        <span class="tf-badge">${s.timeframe || '15M'}</span>
      </div>
    </div>

    <!-- V5.2: Institutional Interpretation Strip -->
    <div class="institutional-strip" style="--cat-color:${catColor}">
      <div class="inst-pill inst-interpretation">
        <span class="inst-label">Interpretation</span>
        <span class="inst-value">${categoryLabel(cat)}</span>
      </div>
      <div class="inst-pill inst-decision" style="--decision-color:${decisionColorVal}">
        <span class="inst-label">Trading Decision</span>
        <span class="inst-value" style="color:${decisionColorVal}">${decision}</span>
      </div>
      <div class="inst-pill inst-confidence">
        <span class="inst-label">Evolution Confidence</span>
        <span class="inst-value ${confidenceClass(confidence)}">${confidence}%</span>
      </div>
    </div>

    <!-- V5.2: Evolution Journey -->
    ${buildEvolutionJourney(s)}

    <div class="card-body ${isExpanded ? 'open' : ''}" id="detail-${s.signal_id}">

      <!-- Technical Context: D1 + D2 + Alignment + VP -->
      <div class="smc-row">
        <div class="smc-panel d1-panel">
          <div class="smc-header">
            <span class="smc-title">📊 D1 HTF</span>
            <span class="smc-tier ${(d1s.tier || '').toLowerCase()}">${d1s.tier || '—'}</span>
          </div>
          <div class="smc-tags">${d1Tags}</div>
        </div>
        <div class="smc-panel d2-panel">
          <div class="smc-header">
            <span class="smc-title">🎯 D2 ${s.timeframe || '15M'}</span>
            <span class="smc-tier ${(s.d2_tier || '').toLowerCase()}">${s.d2_tier || '—'}</span>
          </div>
          <div class="smc-tags">${d2Tags}</div>
        </div>
      </div>

      <!-- Alignment (V5.2) -->
      <div class="alignment-row ${alignCls}">
        <div class="alignment-label">
          <span>🔗 HTF/LTF Alignment</span>
          <span class="alignment-score">${alignScore}/20</span>
        </div>
        <div class="alignment-chips">${alignChips}</div>
      </div>

      <!-- Volume Profile / Liquidity (preserved) -->
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

      <!-- D1 TF Breakdown + Score -->
      <div class="meta-row">
        <div class="tf-row">
          <span class="meta-label">D1 TFs</span>
          ${tfHtml}
        </div>
        <div class="spark-row">
          <canvas class="sparkline" data-values="${sparkData}" width="80" height="22"></canvas>
          <span class="born-time">${new Date(s.born_at).toLocaleTimeString([], {hour:'2-digit', minute:'2-digit'})}</span>
        </div>
      </div>
    </div>
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
      <span class="type-e-conflict">${a.d1_dir} vs ${a.d2_dir}</span>
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
    // Market Type filter
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
    msg.textContent = `${stats.d1_coins || 0} coins analyzed · HTF + 15M LTF · 16-State Matrix`;
    if (spinner) spinner.style.display = 'block';
    if (progress) progress.style.width = Math.min(100, (stats.d1_coins / 529) * 100) + '%';
  } else if (state === 'idle') {
    title.textContent = 'Initializing Market Evolution Engine...';
    msg.textContent = 'Scanning 529 coins · HTF + 15M LTF · 16-State Matrix';
    if (spinner) spinner.style.display = 'block';
    if (progress) progress.style.width = '0%';
  } else {
    title.textContent = 'Waiting for signals...';
    msg.textContent = '529 coins across 1H / 4H / 1D + 15M · 16-State Matrix';
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

// ── Health Poll (fallback for scan timestamps) ─────────────────────
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

// ── Filters (V5.2: market type + evolution velocity + direction) ──
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

// ── Init ───────────────────────────────────────────────────────────
document.addEventListener('DOMContentLoaded', () => {
  connectWS();
  initFilters();

  setInterval(checkHealth, 3000);

  const empty = document.getElementById('emptyState');
  if (empty) updateEmptyState('idle');
});
