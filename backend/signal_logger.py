"""CSV signal logger — captures every scan cycle for performance analysis.

Gated by `ENABLE_SIGNAL_LOGGING` in `backend/config.py`:
  - False (production): all functions are no-ops, no disk I/O
  - True  (dev/backtest): writes each signal to LOG_FILE (CSV)
"""
import csv
import logging
import os
from datetime import datetime

from backend.config import ENABLE_SIGNAL_LOGGING, LOG_FILE

logger = logging.getLogger("judah.logger")

__all__ = ['init_log', 'log_signal', 'get_recent_logs']

if not ENABLE_SIGNAL_LOGGING:
    # ─── Production: zero disk I/O, same public API ─────────────────────
    def init_log():
        """No-op in production."""
        return

    def log_signal(signal, action='new'):
        """No-op in production."""
        return

    def get_recent_logs(limit=100):
        """No-op in production."""
        return []

else:
    # ─── Dev / backtest mode: write every signal to CSV ─────────────────
    FIELDS = [
        'timestamp', 'symbol', 'timeframe', 'direction', 'tier',
        'composite_score', 'crt_score', 'smc_score',
        'session', 'session_bullish', 'session_bearish',
        'entry', 'stop_loss', 'take_profit', 'sl_source',
        'rr', 'risk', 'reward',
        'current_price', 'distance_to_entry_pct',
        'scenario', 'freshness_state', 'age_minutes',
        'displacement_ratio', 'retracement_pct', 'in_optimal_ote',
        'premium_discount', 'ob_type', 'ob_touches',
        'fvg_type', 'fvg_filled', 'msb_confirmed', 'msb_direction',
        'liquidity_swept', 'liquidity_direction',
        'confluence_count', 'signals_list',
        'action', 'outcome', 'atr', 'atr_sl_distance',
        'mtf_alignment', 'mtf_details',
    ]

    def init_log():
        """Create CSV with headers if it doesn't exist."""
        if not os.path.exists(LOG_FILE):
            with open(LOG_FILE, 'w', newline='', encoding='utf-8') as f:
                writer = csv.DictWriter(f, fieldnames=FIELDS)
                writer.writeheader()
            logger.info(f"[logger] Created {LOG_FILE}")

    def log_signal(signal, action='new'):
        """Write one signal row to CSV."""
        init_log()

        row = {
            'timestamp': datetime.now().isoformat(),
            'symbol': signal.get('symbol', ''),
            'timeframe': signal.get('engine', signal.get('timeframe', '')),
            'direction': signal.get('direction', ''),
            'tier': signal.get('tier', ''),
            'composite_score': signal.get('composite_score', ''),
            'crt_score': signal.get('crt_score', ''),
            'smc_score': signal.get('smc_score', ''),
            'session': signal.get('session', ''),
            'session_bullish': signal.get('session_bullish', ''),
            'session_bearish': signal.get('session_bearish', ''),
            'entry': signal.get('entry', ''),
            'stop_loss': signal.get('stop_loss', ''),
            'take_profit': signal.get('take_profit', ''),
            'sl_source': signal.get('sl_source', ''),
            'rr': signal.get('rr', ''),
            'risk': signal.get('risk', ''),
            'reward': signal.get('reward', ''),
            'current_price': signal.get('current_price', ''),
            'distance_to_entry_pct': signal.get('distance_to_entry_pct', ''),
            'scenario': signal.get('scenario', ''),
            'freshness_state': signal.get('freshness_state', ''),
            'age_minutes': signal.get('age_minutes', ''),
            'displacement_ratio': signal.get('displacement', {}).get('ratio', '') if signal.get('displacement') else '',
            'retracement_pct': signal.get('retracement_percent', ''),
            'in_optimal_ote': signal.get('in_optimal_ote', ''),
            'premium_discount': signal.get('supply_demand', ''),
            'ob_type': signal.get('ob', {}).get('type', '') if signal.get('ob') else '',
            'ob_touches': signal.get('ob', {}).get('touches', '') if signal.get('ob') else '',
            'fvg_type': signal.get('fvg', {}).get('type', '') if signal.get('fvg') else '',
            'fvg_filled': signal.get('fvg', {}).get('filled', '') if signal.get('fvg') else '',
            'msb_confirmed': signal.get('msb', {}).get('confirmed', '') if signal.get('msb') else '',
            'msb_direction': signal.get('msb', {}).get('direction', '') if signal.get('msb') else '',
            'liquidity_swept': signal.get('liquidity', {}).get('swept', '') if signal.get('liquidity') else '',
            'liquidity_direction': signal.get('liquidity', {}).get('direction', '') if signal.get('liquidity') else '',
            'confluence_count': len(signal.get('confluence', [])),
            'signals_list': '|'.join(signal.get('confluence', [])),
            'action': action,
            'outcome': signal.get('outcome', ''),
            'atr': signal.get('atr', ''),
            'atr_sl_distance': signal.get('atr_sl_distance', ''),
            'mtf_alignment': signal.get('mtf_alignment', ''),
            'mtf_details': '|'.join(signal.get('mtf_details', [])) if signal.get('mtf_details') else '',
        }

        try:
            logger.info(f"[CSV-WRITE] {row.get('symbol')} {row.get('direction')}: "
                        f"composite_from_signal={row.get('composite_score')} "
                        f"score_in_csv={row.get('score')}")
            with open(LOG_FILE, 'a', newline='', encoding='utf-8') as f:
                writer = csv.DictWriter(f, fieldnames=FIELDS)
                writer.writerow(row)
            logger.info(f"[logger] Logged {action}: {row['symbol']} {row['direction']} score={row['composite_score']}")
        except Exception as e:
            logger.error(f"[logger] Write failed: {e}")

    def get_recent_logs(limit=100):
        """Read recent log entries for API serving."""
        if not os.path.exists(LOG_FILE):
            return []

        rows = []
        try:
            with open(LOG_FILE, 'r', encoding='utf-8') as f:
                reader = csv.DictReader(f)
                for row in reader:
                    rows.append(row)
        except Exception:
            pass

        return rows[-limit:]
