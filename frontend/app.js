// Frontend logic — WebSocket client, filters, signal rendering, expandable cards.
// Displays ALL institutional-grade backend data.

// === STATE ===
let allSignals = [];
let ws = null;
let filters = { tier: 'all', direction: 'all', timeframe: 'all', minScore: 0 };
const expandedCards = new Set();

// === WEBSOCKET CLIENT ===
function connectWS() {
  const proto = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
  ws = new WebSocket(proto + '//' + window.location.host + '/ws');

  ws.onopen = () => {
    document.getElementById('wsDot').classList.add('connected');
    document.getElementById('wsDot').classList.remove('disconnected');
    document.getElementById('wsLabel').textContent = 'Live';
    console.log('[ws] Connected');
  };

  ws.onmessage = (event) => {
    try {
      const data = JSON.parse(event.data);
      console.log('[ws] Received:', data.type, 'count:', (data.signals || []).length);
      if (data.type === 'INITIAL') {
        allSignals = data.signals || [];
        allSignals.sort((a, b) => (b.composite_score || 0) - (a.composite_score || 0));
        renderSignals();
      } else if (data.type === 'NEW_SIGNALS') {
        for (const s of data.signals || []) {
          if (!allSignals.some(x => x.id === s.id)) {
            allSignals.unshift(s);
          }
        }
        allSignals.sort((a, b) => (b.composite_score || 0) - (a.composite_score || 0));
        if (allSignals.length > 200) allSignals = allSignals.slice(0, 200);
        renderSignals(data.signals);
      } else if (data.type === 'REVALIDATED') {
        for (const s of data.signals || []) {
          liveTracker[s.id] = s.timestamp || Date.now();
          const idx = allSignals.findIndex(x => x.id === s.id);
          if (idx >= 0) {
            allSignals[idx] = s;
          } else {
            allSignals.unshift(s);
          }
          const card = document.querySelector('.signal-card[data-card-id="' + s.id + '"]');
          if (card) {
            card.classList.add('revalidated');
            setTimeout(() => card.classList.remove('revalidated'), 1500);
          }
        }
        allSignals.sort((a, b) => (b.composite_score || 0) - (a.composite_score || 0));
        renderSignals();
      } else if (data.type === 'REFRESH') {
        const refreshed = data.signals || [];
        for (const s of refreshed) {
          const idx = allSignals.findIndex(x => x.id === s.id);
          if (idx >= 0) allSignals[idx] = s;
          else allSignals.push(s);
        }
        allSignals.sort((a, b) => (b.composite_score || 0) - (a.composite_score || 0));
        if (allSignals.length > 200) allSignals = allSignals.slice(0, 200);
        renderSignals();
      }
    } catch (e) {
      console.error('[ws] Parse error:', e);
    }
  };

  ws.onclose = () => {
    document.getElementById('wsDot').classList.remove('connected');
    document.getElementById('wsDot').classList.add('disconnected');
    document.getElementById('wsLabel').textContent = 'Reconnecting...';
    setTimeout(connectWS, 3000);
  };

  ws.onerror = (err) => {
    console.error('[ws] Error:', err);
  };
}

// === RESTART ===
async function restartScanner() {
  const btn = document.getElementById('btnRestart');
  if (!btn || btn.classList.contains('spinning')) return;

  if (!confirm('Restart scanner?\n\nThis will:\n• Clear all current signals\n• Re-download fresh candle history\n• Reconnect the WebSocket\n\nNo data is permanently lost.')) return;

  btn.classList.add('spinning');
  btn.disabled = true;
  btn.querySelector('.restart-icon').textContent = '⟳';
  btn.querySelector('.restart-label').textContent = 'Restarting…';

  allSignals = [];
  renderSignals();

  try {
    const resp = await fetch('/api/restart', { method: 'POST' });
    const data = await resp.json();
    console.log('[restart] Response:', data);

    // Show brief confirmation, then wait for WS to re-populate
    btn.querySelector('.restart-label').textContent = 'Done ✓';
    setTimeout(() => {
      btn.classList.remove('spinning');
      btn.disabled = false;
      btn.querySelector('.restart-icon').textContent = '↻';
      btn.querySelector('.restart-label').textContent = 'Restart';
    }, 3000);
  } catch (e) {
    console.error('[restart] Failed:', e);
    btn.querySelector('.restart-label').textContent = 'Failed ✗';
    setTimeout(() => {
      btn.classList.remove('spinning');
      btn.disabled = false;
      btn.querySelector('.restart-icon').textContent = '↻';
      btn.querySelector('.restart-label').textContent = 'Restart';
    }, 3000);
  }
}

function initRestartButton() {
  const btn = document.getElementById('btnRestart');
  if (btn) {
    btn.addEventListener('click', restartScanner);
  }
}

// === FILTERS ===
function initFilters() {
  document.querySelectorAll('.filter-btn').forEach(btn => {
    btn.addEventListener('click', () => {
      const group = btn.parentElement;
      group.querySelectorAll('.filter-btn').forEach(b => b.classList.remove('active'));
      btn.classList.add('active');
      const tier = btn.dataset.tier;
      const dir = btn.dataset.dir;
      const tf = btn.dataset.tf;
      if (tier !== undefined) filters.tier = tier;
      if (dir !== undefined) filters.direction = dir;
      if (tf !== undefined) filters.timeframe = tf;
      renderSignals();
    });
  });

  const slider = document.getElementById('scoreFilter');
  const val = document.getElementById('scoreVal');
  if (slider) {
    slider.addEventListener('input', () => {
      filters.minScore = parseInt(slider.value);
      if (val) val.textContent = slider.value;
      renderSignals();
    });
  }
}

// === RENDER ===
function fmtPrice(p) {
  if (p == null || isNaN(p)) return '---';
  if (p >= 1000) return '$' + p.toFixed(2);
  if (p >= 1) return '$' + p.toFixed(3);
  return '$' + p.toFixed(5);
}

function renderSignals(newIds) {
  const container = document.getElementById('signalsContainer');
  const empty = document.getElementById('emptyState');
  if (!container) return;

  try {
    const filtered = allSignals.filter(s => {
      const tier = (s.tier || 'WATCH').toUpperCase();
      if (tier === 'REJECTED') return false;
      if (filters.tier !== 'all' && tier !== filters.tier) return false;
      if (filters.direction !== 'all' && s.direction !== filters.direction) return false;
      if (filters.timeframe !== 'all' && s.engine !== filters.timeframe) return false;
      if ((s.composite_score || 0) < filters.minScore) return false;
      return true;
    });

    for (const s of filtered) { trackSignalBirth(s); }

    const countEl = document.getElementById('signalCount');
    const updateEl = document.getElementById('lastUpdate');
    if (countEl) countEl.textContent = filtered.length + ' signals';
    if (updateEl) updateEl.textContent = 'Updated: ' + new Date().toLocaleTimeString();

    if (filtered.length === 0) {
      container.innerHTML = '';
      if (empty) container.appendChild(empty);
      return;
    }

    container.innerHTML = filtered.map(s => buildCard(s, newIds || [])).join('');

    // Apply expanded state
    for (const id of expandedCards) {
      const detail = document.getElementById('detail-' + id);
      if (detail) detail.classList.add('expanded');
    }
  } catch (e) {
    console.error('[render] Error:', e);
    container.innerHTML = '<div style="padding:20px;color:#ef4444;font-size:12px">Render error: ' + e.message + '<br>Open DevTools (F12) for details.</div>';
  }
}

function buildCard(s, newIds) {
  const isNew = newIds.some(id => id === s.id);
  const tier = ((s.tier || 'WATCH').toLowerCase());
  const scoreVal = s.composite_score || 0;
  const crtPct = Math.min((s.crt_score || 0) / 25 * 100, 100);
  const smcPct = Math.min((s.smc_score || 0) / 20 * 100, 100);
  const flowScore = s.flow_score || s.flow_boost || 0;
  const momScore = s.momentum_score || s.fast_mover_boost || 0;
  const flowPct = Math.min(flowScore / 25 * 100, 100);
  const momPct = Math.min(momScore / 20 * 100, 100);
  const distVal = s.distance_to_entry_pct || 0;
  const distClass = distVal <= 1 ? 'near' : distVal <= 2 ? 'mid' : 'far';

  const vp = s.volume_profile || {};
  const pocPrice = vp.poc_price;
  const vah = vp.va_high;
  const val = vp.va_low;
  const pocNear = pocPrice ? Math.abs((s.current_price || s.entry || 1) - pocPrice) / (s.current_price || s.entry || 1) * 100 < 0.5 : false;

  const orderFlow = s.institutional_order_flow || {};
  const flowPct = orderFlow.buying_pct || 50;
  const flowClass = flowPct >= 65 ? 'strong-buy' : flowPct >= 55 ? 'buy' : flowPct <= 35 ? 'strong-sell' : flowPct <= 45 ? 'sell' : 'neutral';

  const msb = s.market_structure || {};
  const msbType = (msb.type || 'NONE').toUpperCase();
  const msbClass = msbType === 'CHOCH' ? 'choch' : msbType === 'BOS' ? 'bos' : 'none';

  const fvg = s.fvg || {};
  const ob = s.ob || {};
  const liq = s.liquidity_zones || {};
  const liqPools = s.liquidity_pools || {};
  const buysidePools = liqPools.buyside || [];
  const sellsidePools = liqPools.sellside || [];
  const vsp = s.vsp || {};
  const confBonus = s.confluence || [];
  const priBoost = s.priority_boosts || [];
  const freshnessState = (s.freshness_state || 'hot').toUpperCase();
  const isHot = freshnessState === 'HOT' || freshnessState === 'FRESH' || freshnessState === 'WARM';
  const freshnessColor = freshnessState === 'HOT' || freshnessState === 'FRESH' ? '#22c55e' :
                         freshnessState === 'WARM' ? '#84cc16' :
                         freshnessState === 'COOL' || freshnessState === 'COOLING' ? '#eab308' :
                         freshnessState === 'COLD' || freshnessState === 'STALE' ? '#f97316' : '#6b7280';
  const liqTarget = (s.direction === 'BULLISH') ? (liq.nearest_buyside) : (liq.nearest_sellside);
  const pd = s.premium_discount || 'EQUILIBRIUM';
  const pdClass = pd === 'PREMIUM' ? 'premium' : pd === 'DISCOUNT' ? 'discount' : 'equilibrium';
  const atrVal = s.atr_value || s.atr || 0;
  const atrPct = s.atr_percent || 0;
  const volClass = atrPct > 3 ? 'high-vol' : atrPct > 1.5 ? 'med-vol' : 'low-vol';

  // Build detail rows using string concat (no nested backticks)
  const detailRows = [];

  detailRows.push('<div class="detail-section"><div class="detail-title">CRT Analysis</div><div class="detail-grid">');
  const disp = s.displacement || {};
  detailRows.push('<div class="detail-item"><span class="detail-label">Displacement</span><span>' + (disp.ratio || '--') + 'x ' + (disp.direction || '--').toLowerCase() + '</span></div>');
  detailRows.push('<div class="detail-item"><span class="detail-label">Retracement</span><span>' + (s.retracement_percent ?? '--') + '%' + (s.in_optimal_ote ? ' Optimal OTE' : '') + '</span></div>');
  detailRows.push('<div class="detail-item"><span class="detail-label">Session</span><span>' + (s.session_label || '--') + ' (' + (s.session || '--') + ')</span></div>');
  detailRows.push('<div class="detail-item"><span class="detail-label">Zone</span><span class="pd-badge-inline ' + pdClass + '">' + pd + ' ' + (s.price_position_pct ?? '') + '%</span></div>');
  detailRows.push('</div></div>');

  detailRows.push('<div class="detail-section"><div class="detail-title">SMC Analysis</div><div class="detail-grid">');
  const msbStr = msb.confirmed ? msb.type + ' ' + msb.direction + ' @ ' + fmtPrice(msb.level) : 'None';
  const obStr = ob.low != null ? fmtPrice(ob.low) + '-' + fmtPrice(ob.high) : '---';
  detailRows.push('<div class="detail-item"><span class="detail-label">MSB</span><span>' + msbStr + '</span></div>');
  detailRows.push('<div class="detail-item"><span class="detail-label">OB</span><span>' + (ob.type || 'None') + ' @ ' + obStr + ' | Str ' + (ob.strength || 0) + '/10 | ' + (ob.touches || 0) + 'x</span></div>');
  detailRows.push('<div class="detail-item"><span class="detail-label">FVG</span><span>' + (fvg.type || 'None') + ' @ ' + (fvg.bottom != null ? fmtPrice(fvg.bottom) + '-' + fmtPrice(fvg.top) : '---') + (fvg.size_atr ? ' | ' + fvg.size_atr.toFixed(1) + 'x ATR' : '') + '</span></div>');
  detailRows.push('<div class="detail-item"><span class="detail-label">VSP</span><span>' + (vsp.type ? vsp.type + ' @ ' + fmtPrice(vsp.price) : 'None') + '</span></div>');
  if (buysidePools.length > 0 || sellsidePools.length > 0) {
    detailRows.push('<div class="detail-item"><span class="detail-label">Liquidity Pools</span><span>');
    if (buysidePools.length > 0) detailRows.push('<span style="color:#f59e0b">BSL: ' + buysidePools.map(p => fmtPrice(p.price) + '(' + p.strength + ')').join(', ') + '</span> ');
    if (sellsidePools.length > 0) detailRows.push('<span style="color:#3b82f6">SSL: ' + sellsidePools.map(p => fmtPrice(p.price) + '(' + p.strength + ')').join(', ') + '</span>');
    detailRows.push('</span></div>');
  }
  detailRows.push('<div class="detail-item"><span class="detail-label">Liquidity</span><span>' + (s.liquidity && s.liquidity.swept ? fmtPrice(s.liquidity.level) + ' (' + s.liquidity.direction + ')' : 'None') + '</span></div>');
  detailRows.push('<div class="detail-item"><span class="detail-label">Swings</span><span>' + (s.swing_count?.highs || 0) + 'H / ' + (s.swing_count?.lows || 0) + 'L</span></div>');
  detailRows.push('</div></div>');

  detailRows.push('<div class="detail-section"><div class="detail-title">Volatility &amp; Setup</div><div class="detail-grid">');
  detailRows.push('<div class="detail-item"><span class="detail-label">ATR</span><span>' + fmtPrice(s.atr_value || s.atr || 0) + '</span></div>');
  detailRows.push('<div class="detail-item"><span class="detail-label">ATR %</span><span class="vol-badge ' + volClass + '">' + atrPct.toFixed(2) + '%</span></div>');
  detailRows.push('<div class="detail-item"><span class="detail-label">Scenario</span><span>' + (s.scenario || '---').replace(/_/g, ' ') + '</span></div>');
  detailRows.push('</div></div>');

  const detailHTML = detailRows.join('');

  // Short info rows below card
  let shortHTML = '';
  if (s.volume_profile) {
    shortHTML += '<div class="inst-row"><span class="inst-label">VP</span><span>POC:' + fmtPrice(s.volume_profile.poc_price) + ' VAH:' + fmtPrice(s.volume_profile.va_high) + ' VAL:' + fmtPrice(s.volume_profile.va_low) + '</span></div>';
  }
  shortHTML += '<div class="inst-row"><span class="inst-label">Zone</span><span class="zone-badge ' + pdClass + '">' + pd + '</span><span class="inst-label" style="margin-left:10px">Flow</span><span class="flow-badge ' + flowClass + '">' + (orderFlow.net_pressure || 'N/A') + '</span><span style="margin-left:8px">' + flowPct + '%</span></div>';
  if (vsp.type) {
    shortHTML += '<div class="inst-row"><span class="inst-label">VSP</span><span>' + vsp.type + ' @ ' + fmtPrice(vsp.price) + '</span></div>';
  }
  if (ob.type) {
    shortHTML += '<div class="inst-row"><span class="inst-label">OB</span><span>' + ob.type + ' @ ' + obStr + ' | ' + (ob.touches || 0) + 'x | Str ' + (ob.strength || 0) + '</span></div>';
  }
  if (fvg.type) {
    shortHTML += '<div class="inst-row"><span class="inst-label">FVG</span><span>' + fvg.type + ' ' + (fvg.size_atr || 0).toFixed(1) + 'x ATR | ' + (fvg.filled_pct || 0).toFixed(0) + '%' + (fvg.proximity !== undefined ? ' | ' + fvg.proximity.toFixed(1) + '% away' : '') + '</span></div>';
  }
  if (buysidePools.length > 0 || sellsidePools.length > 0) {
    shortHTML += '<div class="inst-row"><span class="inst-label">Liq Pools</span><span>';
    if (buysidePools.length > 0) shortHTML += '<span style="color:#f59e0b">Buy:' + buysidePools.map(p => fmtPrice(p.price) + '(' + p.strength + ')').join(', ') + '</span> ';
    if (sellsidePools.length > 0) shortHTML += '<span style="color:#3b82f6">Sell:' + sellsidePools.map(p => fmtPrice(p.price) + '(' + p.strength + ')').join(', ') + '</span>';
    shortHTML += '</span></div>';
  }
  if (s.liquidity && s.liquidity.swept) {
    shortHTML += '<div class="inst-row"><span class="inst-label">Liq</span><span>' + fmtPrice(s.liquidity.level) + ' (' + s.liquidity.direction + ')</span></div>';
  }
  if (confBonus.length > 0) {
    shortHTML += '<div class="inst-row"><span class="inst-label">MTF</span><span>' + confBonus.map(t => t.toUpperCase()).join(', ') + '</span></div>';
  }
  if (priBoost.length > 0) {
    shortHTML += '<div class="inst-row"><span class="inst-label">Boost</span><span>' + priBoost.join(', ') + '</span></div>';
  }

  return '<div class="signal-card' + (isNew ? ' new' : '') + '" data-card-id="' + s.id + '">' +
    '<div class="card-header" onclick="toggleCard(\'' + s.id + '\')">' +
      '<div class="symbol-info">' +
        '<span class="symbol-name">' + (s.symbol || '---') + '</span>' +
        '<span class="timeframe-badge">' + (s.engine || '?') + '</span>' +
        '<a href="https://www.tradingview.com/chart/?symbol=BINANCE:' + encodeURIComponent(s.symbol || '') + '" target="_blank" rel="noopener" class="tv-link" title="Open on TradingView" onclick="event.stopPropagation();">' +
          '<svg class="tv-icon" viewBox="0 0 24 24" width="14" height="14" fill="none" stroke="currentColor" stroke-width="2"><polyline points="22 12 18 12 15 21 9 3 6 12 2 12"/></svg>' +
        '</a>' +
      '</div>' +
      '<div style="display:flex;gap:6px;align-items:center;flex-wrap:wrap;">' +
        '<span class="tier-badge ' + tier + '">' + (s.tier || 'WATCH').toUpperCase() + '</span>' +
        '<span class="direction-badge ' + (s.direction || 'BULLISH') + '">' + ((s.direction || 'BULLISH') === 'BULLISH' ? 'BULL' : 'BEAR') + '</span>' +
        '<span class="expand-toggle" id="toggle-' + s.id + '" onclick="event.stopPropagation();toggleCard(\'' + s.id + '\')">&#9662;</span>' +
      '</div>' +
    '</div>' +

    '<div class="score-section">' +
      '<div class="score-bar-container">' +
        '<span class="score-number">' + scoreVal + '/90</span>' +
        '<div style="flex:1;min-width:0;">' +
          '<div class="score-bar-bg"><div class="score-bar-fill ' + tier + '" style="width:' + (scoreVal/90*100).toFixed(1) + '%"></div></div>' +
          '<div class="crt-smc-bar">' +
            '<div class="crt-segment" style="width:' + crtPct.toFixed(1) + '%" title="CRT: ' + (s.crt_score || 0) + '/25"></div>' +
            '<div class="smc-segment" style="width:' + smcPct.toFixed(1) + '%" title="SMC: ' + (s.smc_score || 0) + '/20"></div>' +
            '<div class="flow-segment" style="width:' + flowPct.toFixed(1) + '%" title="Flow: ' + flowScore + '/25"></div>' +
            '<div class="mom-segment" style="width:' + momPct.toFixed(1) + '%" title="Momentum: ' + momScore + '/20"></div>' +
          '</div>' +
        '</div>' +
      '</div>' +
      '<div class="score-labels">' +
        '<span>CRT:' + (s.crt_score || 0) + '/25</span>' +
        '<span>SMC:' + (s.smc_score || 0) + '/20</span>' +
        '<span>Flow:' + flowScore + '/25</span>' +
        '<span>Mom:' + momScore + '/20</span>' +
        '<span style="color:var(--accent)">=' + scoreVal + '/90</span>' +
        '<span class="freshness-badge" style="color:' + freshnessColor + ';font-weight:600">' + freshnessState + '</span>' +
        '<span class="live-tracker" data-id="' + s.id + '" style="font-size:11px;margin-left:auto;white-space:nowrap"></span>' +
      '</div>' +
    '</div>' +

    '<div class="inst-strip">' +
      '<div class="inst-item"><div class="inst-label">VP POC</div><div class="inst-value' + (pocNear ? ' highlight' : '') + '">' + (pocPrice ? fmtPrice(pocPrice) : 'N/A') + (pocNear ? '<span class="poc-dot"></span>' : '') + '</div></div>' +
      '<div class="inst-item"><div class="inst-label">VP VAH</div><div class="inst-value">' + (vah ? fmtPrice(vah) : 'N/A') + '</div></div>' +
      '<div class="inst-item"><div class="inst-label">VP VAL</div><div class="inst-value">' + (val ? fmtPrice(val) : 'N/A') + '</div></div>' +
      '<div class="inst-item"><div class="inst-label">ATR</div><div class="inst-value">' + fmtPrice(atrVal) + ' <span class="atr-pct ' + volClass + '">' + atrPct.toFixed(2) + '%</span></div></div>' +
      '<div class="inst-item"><div class="inst-label">Zone</div><div class="inst-value ' + pdClass + '">' + pd + '</div></div>' +
      '<div class="inst-item"><div class="inst-label">Flow</div>' +
      (s.killzone ? '<div class="inst-value flow-badge ' + (s.killzone.relevant ? 'strong-buy' : '') + '">' + (s.killzone.zone || '') + ' x' + s.killzone.multiplier + '</div>' : '<div class="inst-value">N/A</div>') +
      (s.flow && s.flow.triggers ? '<div class="inst-value" style="font-size:10px;color:var(--accent)">' + s.flow.triggers.map(t => t.name).join(', ') + '</div>' : '') +
    '</div>' +
      '<div class="inst-item"><div class="inst-label">OB</div><div class="inst-value">' + (ob.touches > 0 ? ob.touches + 'x' : 'None') + '</div></div>' +
      '<div class="inst-item"><div class="inst-label">FVG</div><div class="inst-value">' + (fvg.size_atr > 0 ? fvg.size_atr.toFixed(1) + 'x' : 'None') + '</div></div>' +
      '<div class="inst-item"><div class="inst-label">Liq</div><div class="inst-value">' + (liqTarget ? fmtPrice(liqTarget) : 'None') + '</div></div>' +
    '</div>' +
    shortHTML +

    buildTradeLevels(s) +

    '<div class="freshness-bar">' +
      '<span class="freshness-dot ' + (s.freshness_state || 'hot') + '"></span>' +
      '<span>Dist: ' + distVal.toFixed(2) + '%</span>' +
      '<span class="freshness-score">' + (s.base_score || 0) + ' -> ' + scoreVal + ' | ' + (s.freshness_state || 'hot') + '</span>' +
    '</div>' +
    '<div class="distance-bar"><div class="distance-fill ' + distClass + '" style="width:' + Math.min(distVal / 3 * 100, 100) + '%"></div></div>' +

    '<div class="card-meta">' +
      '<span class="timestamp">Born: ' + new Date(s.timestamp || Date.now()).toLocaleString() + '</span>' +
    '</div>' +

    '<div class="card-detail" id="detail-' + s.id + '">' +
      '<div class="detail-divider"></div>' +
      detailHTML +
    '</div>' +
  '</div>';
}

function buildTradeLevels(s) {
  const entry = s.entry || 0;
  const sl = s.stop_loss || 0;
  const tp1 = s.take_profit_1 || s.take_profit;
  const tp2 = s.take_profit_2;
  const rr = s.risk_reward != null ? s.risk_reward : (s.rr || 0);
  const isStructural = s.sl_type === 'structural';

  const entryBadge = (s.direction || 'BULLISH') === 'BULLISH'
    ? '<span class="dir-dot bullish"></span>'
    : '<span class="dir-dot bearish"></span>';
  const entryDisplay =
    '<div class="signal-entry">' +
      entryBadge +
      '<span class="level-label">Entry</span>' +
      '<span class="level-value entry">' + fmtPrice(entry) + '</span>' +
    '</div>';

  const slBadge = isStructural ? '<span class="badge structural">STRUCTURAL</span>' : '';
  const slDisplay =
    '<div class="signal-sl">' +
      '<span class="level-label">SL</span>' +
      '<span class="level-value sl">' + fmtPrice(sl) + '</span>' +
      slBadge +
    '</div>';

  if (tp2 && tp2 !== tp1) {
    const rr2 = entry && sl ? (Math.abs(tp2 - entry) / Math.abs(entry - sl)).toFixed(2) : rr.toFixed(2);
    const tpDisplay =
      '<div class="signal-tp-dual">' +
        '<div class="tp-row">' +
          '<span class="label">🎯 TP1</span>' +
          '<span class="value tp1">' + fmtPrice(tp1) + '</span>' +
          '<span class="rr tp1-rr">' + rr.toFixed(2) + ':1</span>' +
        '</div>' +
        '<div class="tp-row">' +
          '<span class="label">🚀 TP2</span>' +
          '<span class="value tp2">' + fmtPrice(tp2) + '</span>' +
          '<span class="rr tp2-rr">' + rr2 + ':1</span>' +
        '</div>' +
      '</div>';
    return '<div class="trade-block">' + entryDisplay + slDisplay + tpDisplay + '</div>';
  }

  const tpDisplay =
    '<div class="signal-tp-single">' +
      '<span class="label">TP</span>' +
      '<span class="value tp">' + fmtPrice(tp1) + '</span>' +
      '<span class="rr">' + rr.toFixed(2) + ':1</span>' +
    '</div>';
  return '<div class="trade-block">' + entryDisplay + slDisplay + tpDisplay + '</div>';
}

// === EXPANDABLE CARDS ===
function toggleCard(id) {
  if (expandedCards.has(id)) {
    expandedCards.delete(id);
  } else {
    expandedCards.add(id);
  }
  renderSignals();
}

// === LIVE TRACKER ===
const liveTracker = {};

function trackSignalBirth(signal) {
  if (!signal || !signal.id) return;
  if (!liveTracker[signal.id]) {
    liveTracker[signal.id] = signal.timestamp || Date.now();
  }
}

function pollSignals() {
  fetch('/api/signals').then(r => r.json()).then(data => {
    allSignals = data.signals || [];
    allSignals.sort((a, b) => (b.composite_score || 0) - (a.composite_score || 0));
    renderSignals();
  }).catch(() => {});
}

// === INIT ===
initFilters();
initRestartButton();
connectWS();
setInterval(pollSignals, 15000);

// Update live tracker every second
setInterval(() => {
  document.querySelectorAll('.live-tracker').forEach(el => {
    const key = el.dataset.id;
    const birth = liveTracker[key];
    if (!birth) return;
    const now = Date.now();
    const diffMs = now - birth;
    const totalSec = Math.floor(diffMs / 1000);
    const mins = Math.floor(totalSec / 60);
    const secs = totalSec % 60;
    el.textContent = mins > 0 ? mins + 'm ' + secs + 's ago' : secs + 's ago';
  });
}, 1000);
