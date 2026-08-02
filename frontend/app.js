// Frontend logic — WebSocket client, bucket filters, signal rendering.
// Displays ONLY D3 fusion (D2 SNIPER signals with buckets).

// === STATE ===
let allSignals = [];
let ws = null;
let filters = { bucket: 'all', direction: 'all' };
const expandedCards = new Set();

// === WEBSOCKET CLIENT (D3 Fusion) ===
function connectWS() {
  const proto = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
  ws = new WebSocket(proto + '//' + window.location.host + '/ws-fusion');

  ws.onopen = () => {
    const dot = document.getElementById('wsDot');
    if (dot) {
      dot.classList.add('connected');
      dot.classList.remove('disconnected');
    }
    const label = document.getElementById('wsLabel');
    if (label) label.textContent = 'Live';
    console.log('[ws-fusion] Connected');
  };

  ws.onmessage = (event) => {
    try {
      const data = JSON.parse(event.data);
      if (data.type === 'INITIAL') {
        allSignals = data.signals || [];
        allSignals.sort((a, b) => (b.d2_score || 0) - (a.d2_score || 0));
        renderSignals();
        updateStats(data.stats);
      } else if (data.type === 'signal') {
        const sig = data.data;
        // Update existing or add new
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
    const dot = document.getElementById('wsDot');
    if (dot) {
      dot.classList.remove('connected');
      dot.classList.add('disconnected');
    }
    const label = document.getElementById('wsLabel');
    if (label) label.textContent = 'Reconnecting...';
    setTimeout(connectWS, 3000);
  };

  ws.onerror = (err) => {
    console.error('[ws-fusion] Error:', err);
  };
}

// === RESTART ===
async function restartScanner() {
  const btn = document.getElementById('btnRestart');
  if (!btn || btn.classList.contains('spinning')) return;

  if (!confirm('Restart scanner?\n\nThis will:\n• Clear all current signals\n• Re-download fresh candle history\n• Reconnect the WebSocket')) return;

  btn.classList.add('spinning');
  btn.disabled = true;

  try {
    const resp = await fetch('/api/restart', { method: 'POST' });
    const data = await resp.json();
    console.log('[restart]', data);
    btn.classList.remove('spinning');
    btn.disabled = false;
  } catch (e) {
    console.error('[restart] Failed:', e);
    btn.classList.remove('spinning');
    btn.disabled = false;
  }
}

// === FILTERS ===
function initFilters() {
  // Bucket filter
  document.querySelectorAll('[data-filter-bucket]').forEach(btn => {
    btn.addEventListener('click', () => {
      filters.bucket = btn.dataset.filterBucket;
      document.querySelectorAll('[data-filter-bucket]').forEach(b => b.classList.remove('active'));
      btn.classList.add('active');
      renderSignals();
    });
  });

  // Direction filter
  document.querySelectorAll('[data-filter-dir]').forEach(btn => {
    btn.addEventListener('click', () => {
      filters.direction = btn.dataset.filterDir;
      document.querySelectorAll('[data-filter-dir]').forEach(b => b.classList.remove('active'));
      btn.classList.add('active');
      renderSignals();
    });
  });

  // Restart button
  const btn = document.getElementById('btnRestart');
  if (btn) btn.addEventListener('click', restartScanner);
}

// === RENDER ===
function fmtPrice(p) {
  if (p == null || isNaN(p)) return '---';
  if (p >= 1000) return '$' + p.toFixed(2);
  if (p >= 1) return '$' + p.toFixed(3);
  return '$' + p.toFixed(5);
}

function fmtRR(rr) {
  if (!rr) return '---';
  return rr.toFixed(1) + ':1';
}

function freshnessIcon(f) {
  const icons = { 'HOT': '🔥', 'WARM': '🌡️', 'COOLING': '❄️', 'STALE': '🥶' };
  return icons[f] || '';
}

function renderSignals() {
  const container = document.getElementById('signalsContainer');
  const empty = document.getElementById('emptyState');
  const countEl = document.getElementById('signalCount');
  const updateEl = document.getElementById('lastUpdate');

  if (!container) return;

  // Filter
  const filtered = allSignals.filter(s => {
    if (filters.bucket !== 'all' && s.bucket !== filters.bucket) return false;
    if (filters.direction !== 'all' && s.direction !== filters.direction) return false;
    return true;
  });

  if (countEl) countEl.textContent = filtered.length + ' setups';
  if (updateEl) updateEl.textContent = 'Updated: ' + new Date().toLocaleTimeString();

  // Bucket counts
  updateBucketCounts(filtered);

  if (filtered.length === 0) {
    container.innerHTML = '';
    if (empty) container.appendChild(empty);
    return;
  }

  container.innerHTML = filtered.map(s => buildCard(s)).join('');
}

function updateBucketCounts(filtered) {
  const counts = { READY: 0, EARLY: 0, TRAP: 0, BUILDING: 0, DEVELOPING: 0, IGNORE: 0, WAIT: 0, MONITOR: 0 };
  filtered.forEach(s => { if (counts[s.bucket] !== undefined) counts[s.bucket]++; });
  const updateCount = (id, val) => {
    const el = document.getElementById(id);
    if (el) el.textContent = val;
  };
  updateCount('countReady', counts.READY);
  updateCount('countEarly', counts.EARLY);
  updateCount('countTrap', counts.TRAP);
  updateCount('countBuilding', counts.BUILDING);
  updateCount('countDeveloping', counts.DEVELOPING);
  updateCount('countIgnore', counts.IGNORE);
  updateCount('countWait', counts.WAIT);
  updateCount('countMonitor', counts.MONITOR);
}

function buildCard(s) {
  const dirIcon = s.direction === 'BULLISH' ? '🟢' : '🔴';
  const dirLabel = s.direction === 'BULLISH' ? 'Long' : 'Short';
  const bucketColor = s.bucket_color || '#6b7280';
  const isExpanded = expandedCards.has(s.signal_id);
  const isNew = Date.now() - new Date(s.born_at || 0).getTime() < 5000;

  // D1 timeframe breakdown
  const tfBreakdown = s.d1_timeframes || {};
  const tfRows = Object.entries(tfBreakdown).map(([tf, d]) => {
    const tierClass = d.tier.toLowerCase();
    return `<span class="tf-tag ${tierClass}">${tf}: ${d.tier} ${d.score}</span>`;
  }).join(' ');

  // Score history (sparkline data)
  const hist = (s.score_history || []).slice(-10);
  const sparklineData = hist.map(h => h[1] || h.score || 0).join(',');

  return `
  <div class="signal-card ${isNew ? 'flash' : ''} ${isExpanded ? 'expanded' : ''}"
       data-card-id="${s.signal_id}" data-bucket="${s.bucket}">
    <div class="card-header" onclick="toggleExpand('${s.signal_id}')">
      <div class="header-left">
        <span class="bucket-dot" style="background:${bucketColor}"></span>
        <span class="coin-name">${s.coin.replace('USDT', '')}</span>
        <span class="bucket-badge" style="background:${bucketColor}20;color:${bucketColor};border-color:${bucketColor}40">${s.bucket_label || s.bucket}</span>
        <span class="dir-badge ${s.direction.toLowerCase()}">${dirIcon} ${dirLabel}</span>
        <span class="freshness">${freshnessIcon(s.freshness)} ${s.freshness || 'HOT'}</span>
      </div>
      <div class="header-right">
        <span class="score-block d1-block">
          <span class="score-label">D1</span>
          <span class="score-big d1-score">${s.d1_score ?? '--'}</span>
          <span class="tier-label d1-tier">${s.d1_tier || '--'}</span>
        </span>
        <span class="score-block d2-block">
          <span class="score-label">D2</span>
          <span class="score-big d2-score">${s.d2_score}</span>
          <span class="tier-label d2-tier">${s.d2_tier || 'SNIPER'}</span>
        </span>
        <span class="tf-label">${s.timeframe}</span>
      </div>
    </div>

    <div class="card-body ${isExpanded ? 'show' : ''}" id="detail-${s.signal_id}">
      <div class="trade-levels">
        <div class="level entry">
          <span class="level-label">Entry</span>
          <span class="level-val">${fmtPrice(s.entry)}</span>
        </div>
        <div class="level sl">
          <span class="level-label">SL</span>
          <span class="level-val">${fmtPrice(s.sl)}</span>
        </div>
        <div class="level tp">
          <span class="level-label">TP1</span>
          <span class="level-val">${fmtPrice(s.tp1)}</span>
        </div>
        <div class="level tp2">
          <span class="level-label">TP2</span>
          <span class="level-val">${fmtPrice(s.tp2)}</span>
        </div>
        <div class="level rr">
          <span class="level-label">RR</span>
          <span class="level-val rr1">${fmtRR(s.rr1)}</span>
          <span class="level-val rr2">${fmtRR(s.rr2)}</span>
        </div>
      </div>

      <div class="d1-breakdown">
        <span class="breakdown-label">D1 Context:</span>
        ${tfBreakdown && Object.keys(tfBreakdown).length > 0 ? tfRows : '<span class="tf-tag watch">No D1 data</span>'}
      </div>

      <div class="score-trajectory">
        <span class="trajectory-label">Score:</span>
        <canvas class="sparkline" data-values="${sparklineData}" width="100" height="24"></canvas>
        <span class="born">Born ${new Date(s.born_at).toLocaleTimeString()}</span>
      </div>
    </div>
  </div>`;
}

function toggleExpand(id) {
  if (expandedCards.has(id)) {
    expandedCards.delete(id);
  } else {
    expandedCards.add(id);
  }
  renderSignals();
  // Draw sparklines for visible cards
  requestAnimationFrame(drawSparklines);
}

function flashNew(id) {
  expandedCards.add(id);
  renderSignals();
  setTimeout(() => {
    expandedCards.delete(id);
    renderSignals();
  }, 3000);
}

function updateStats(stats) {
  if (!stats) return;
  const el = document.getElementById('totalSignals');
  if (el) el.textContent = stats.d3_fusion || 0;
  const d1El = document.getElementById('d1Count');
  if (d1El) d1El.textContent = stats.d1_coins || 0;
  const d2El = document.getElementById('d2Count');
  if (d2El) d2El.textContent = stats.d2_signals || 0;
}

// === SPARKLINE ===
function drawSparklines() {
  document.querySelectorAll('canvas.sparkline').forEach(canvas => {
    const vals = (canvas.dataset.values || '').split(',').map(Number).filter(v => !isNaN(v));
    if (vals.length < 2) return;
    const ctx = canvas.getContext('2d');
    const w = canvas.width;
    const h = canvas.height;
    ctx.clearRect(0, 0, w, h);

    const min = Math.min(...vals);
    const max = Math.max(...vals);
    const range = max - min || 1;

    ctx.beginPath();
    ctx.strokeStyle = '#3b82f6';
    ctx.lineWidth = 1.5;
    vals.forEach((v, i) => {
      const x = (i / (vals.length - 1)) * w;
      const y = h - ((v - min) / range) * (h - 4) - 2;
      if (i === 0) ctx.moveTo(x, y);
      else ctx.lineTo(x, y);
    });
    ctx.stroke();
  });
}

// === PERIODIC HEALTH CHECK ===
async function checkHealth() {
  try {
    const resp = await fetch('/api/health');
    const data = await resp.json();
    updateStats(data.stats);
  } catch (e) {
    // silently fail
  }
}

// === BUCKET MATRIX TOGGLE ===
function initMatrix() {
  const toggle = document.getElementById('matrixToggle');
  const panel = document.getElementById('matrixPanel');
  if (!toggle || !panel) return;

  toggle.addEventListener('click', () => {
    panel.classList.toggle('open');
  });

  // Matrix cell clicks filter same as bucket buttons
  panel.querySelectorAll('[data-filter-bucket]').forEach(btn => {
    btn.addEventListener('click', () => {
      const bucket = btn.dataset.filterBucket;
      filters.bucket = bucket;
      // Update active states across ALL bucket buttons (bar + matrix)
      document.querySelectorAll('[data-filter-bucket]').forEach(b => b.classList.remove('active'));
      if (bucket === 'all') {
        document.querySelector('[data-filter-bucket="all"]')?.classList.add('active');
      } else {
        btn.classList.add('active');
      }
      renderSignals();
      panel.classList.remove('open');
    });
  });
}

// === INIT ===
document.addEventListener('DOMContentLoaded', () => {
  connectWS();
  initFilters();
  initMatrix();
  setInterval(drawSparklines, 2000);
  setInterval(checkHealth, 10000);
});
