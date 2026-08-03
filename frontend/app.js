// ═══════════════════════════════════════════════════════════════════════
// Judah Scanner — Frontend
// D3 Fusion: HTF (1H/4H/1D) + 15M LTF → Bucket Signals
// ═══════════════════════════════════════════════════════════════════════

// ── State ──────────────────────────────────────────────────────────
let allSignals = [];
let ws = null;
let filters = { bucket: 'all', direction: 'all' };
const expandedCards = new Set();
let stats = { d1_coins: 0, d2_signals: 0, d3_fusion: 0, last_d1_scan: 0, last_d2_scan: 0, last_d3_fusion: 0 };
let wsReconnectTimer = null;

// Track previous timestamps to detect active scanning
let prevTimestamps = { d1: 0, d2: 0, d3: 0 };

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
        allSignals.sort((a, b) => (b.d2_score || 0) - (a.d2_score || 0));
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
        allSignals.sort((a, b) => (b.d2_score || 0) - (a.d2_score || 0));
        if (allSignals.length > 100) allSignals = allSignals.slice(0, 100);
        renderSignals();
        if (idx < 0) flashNew(sig.signal_id);
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
// Derives scanning state from timestamp changes via health polling + WS.
// When a timestamp moves → "scanning" for ~3s, then "done" with time-ago.
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

  // Timestamp changed → scanning just happened
  if (currentTs > prev && currentTs > 0) {
    prevTimestamps[key] = currentTs;

    // Show scanning state
    item.classList.remove('done', 'idle', 'error');
    item.classList.add('scanning');
    statusEl.classList.remove('done', 'error');
    statusEl.classList.add('scanning');
    statusEl.textContent = 'Scanning';
    if (timeEl) timeEl.textContent = '';

    // Clear previous timer, set new one
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

  // No scan yet — idle
  if (currentTs === 0 || prev === 0) {
    item.classList.remove('scanning', 'done', 'error');
    item.classList.add('idle');
    statusEl.classList.remove('scanning', 'done', 'error');
    if (timeEl) timeEl.textContent = '';
    return;
  }

  // Otherwise show time-ago (from the most recent scan)
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

// ── SMC Tag Builder ────────────────────────────────────────────────
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

// ── Build Card ─────────────────────────────────────────────────────
function buildCard(s) {
  const dirIcon = s.direction === 'BULLISH' ? '🟢' : '🔴';
  const dirLabel = s.direction === 'BULLISH' ? 'Long' : 'Short';
  const bucketColor = s.bucket_color || '#6b7280';
  const isExpanded = expandedCards.has(s.signal_id);
  const isNew = Date.now() - new Date(s.born_at || 0).getTime() < 5000;

  // TradingView chart
  const tvUrl = 'https://www.tradingview.com/chart/?symbol=' + encodeURIComponent('BINANCE:' + base + 'USDT');
  // Binance Futures trade page
  const binanceUrl = 'https://www.binance.com/en/futures/' + encodeURIComponent(base + 'USDT');

  // D1/D2 structure
  const d1s = s.d1_structure || {};
  const d2s = s.d2_structure || {};
  const tfs = s.d1_timeframes || {};

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

  // ── Card HTML ────────────────────────────────────────────────────
  return `
  <div class="signal-card ${isNew ? 'is-new' : ''} ${isExpanded ? 'is-expanded' : ''}"
       data-id="${s.signal_id}" data-bucket="${s.bucket}">
    <div class="card-header" onclick="toggleExpand('${s.signal_id}')">
      <div class="card-header-left">
        <span class="bucket-pip" style="background:${bucketColor}"></span>
        <a class="coin-link" href="${tvUrl}" target="_blank" rel="noopener"
           title="View ${base} on TradingView" onclick="event.stopPropagation()">
          ${base}
          <svg class="tv-svg" width="11" height="11" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round"><polyline points="22 7 13.5 15.5 8.5 10.5 2 17"/><polyline points="16 7 22 7 22 13"/></svg>
        </a>
        <a class="binance-link" href="${binanceUrl}" target="_blank" rel="noopener"
           title="Trade ${base} on Binance Futures" onclick="event.stopPropagation()">
          <svg width="11" height="11" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round"><path d="M12 5v14M5 12h14"/></svg>
        </a>
        <span class="bucket-pill" style="background:${bucketColor}18;color:${bucketColor};border-color:${bucketColor}30">
          ${s.bucket_icon || ''} ${s.bucket_label || s.bucket}
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
        <span class="tf-badge">15M</span>
      </div>
    </div>

    <div class="card-body ${isExpanded ? 'open' : ''}" id="detail-${s.signal_id}">

      <!-- SMC Structure: D1 HTF | D2 15M -->
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
            <span class="smc-title">🎯 D2 15M</span>
            <span class="smc-tier ${(s.d2_tier || '').toLowerCase()}">${s.d2_tier || '—'}</span>
          </div>
          <div class="smc-tags">${d2Tags}</div>
        </div>
      </div>

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

// ── Render ────────────────────────────────────────────────────────
function renderSignals() {
  const container = document.getElementById('signalsContainer');
  const empty = document.getElementById('emptyState');
  if (!container) return;

  const filtered = allSignals.filter(s => {
    if (filters.bucket !== 'all' && s.bucket !== filters.bucket) return false;
    if (filters.direction !== 'all' && s.direction !== filters.direction) return false;
    return true;
  });

  updateBucketCounts(filtered);

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
    msg.textContent = `${stats.d1_coins || 0} coins analyzed · HTF + 15M LTF`;
    if (spinner) spinner.style.display = 'block';
    if (progress) progress.style.width = Math.min(100, (stats.d1_coins / 529) * 100) + '%';
  } else if (state === 'idle') {
    title.textContent = 'Initializing Scanner...';
    msg.textContent = 'Fetching 529 coins · HTF + 15M LTF analysis';
    if (spinner) spinner.style.display = 'block';
    if (progress) progress.style.width = '0%';
  } else {
    title.textContent = 'Waiting for signals...';
    msg.textContent = 'Scanning 529 coins across 1H / 4H / 1D + 15M';
    if (spinner) spinner.style.display = 'none';
    if (progress) progress.style.width = '100%';
  }
}

function clearFilters() {
  filters = { bucket: 'all', direction: 'all' };
  document.querySelectorAll('.filter-chip, .dir-chip').forEach(b => b.classList.remove('active'));
  document.querySelector('[data-filter-bucket="all"]')?.classList.add('active');
  document.querySelector('[data-filter-dir="all"]')?.classList.add('active');
  renderSignals();
}

function clearFilters() {
  filters = { bucket: 'all', direction: 'all' };
  document.querySelectorAll('.filter-chip, .dir-chip').forEach(b => b.classList.remove('active'));
  document.querySelector('[data-filter-bucket="all"]')?.classList.add('active');
  document.querySelector('[data-filter-dir="all"]')?.classList.add('active');
  renderSignals();
}

// ── Bucket Counts ──────────────────────────────────────────────────
function updateBucketCounts(filtered) {
  const counts = {};
  filtered.forEach(s => { counts[s.bucket] = (counts[s.bucket] || 0) + 1; });

  const set = (id, bucket) => {
    const el = document.getElementById(id);
    if (el) el.textContent = counts[bucket] || 0;
  };
  set('countReady', 'READY');
  set('countBuilding', 'BUILDING');
  set('countEarly', 'EARLY');
  set('countDeveloping', 'DEVELOPING');
  set('countWait', 'WAIT');
  set('countMonitor', 'MONITOR');
  set('countTrap', 'TRAP');
  set('countIgnore', 'IGNORE');
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

    // Gradient fill
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

    // Fill
    const lastX = w, lastY = h - ((vals[vals.length-1] - min) / range) * (h - 4) - 2;
    ctx.lineTo(lastX, h);
    ctx.lineTo(0, h);
    ctx.closePath();
    ctx.fillStyle = grad;
    ctx.fill();

    // Line
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

    // End dot
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

      // Update empty state if no signals
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

// ── Filters ────────────────────────────────────────────────────────
function initFilters() {
  document.querySelectorAll('.filter-chip').forEach(btn => {
    btn.addEventListener('click', () => {
      filters.bucket = btn.dataset.filterBucket;
      document.querySelectorAll('.filter-chip').forEach(b => b.classList.remove('active'));
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

  // Health poll every 3s (faster than before for live status feel)
  setInterval(checkHealth, 3000);

  // Initial empty state
  const empty = document.getElementById('emptyState');
  if (empty) updateEmptyState('idle');
});
