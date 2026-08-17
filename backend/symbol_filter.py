"""Symbol allow/deny-list for USDT-M futures only.

Blocks:
  - Stock tokens (AAPLUSDT, TSLAUSDT, etc.)
  - Leveraged / inverse tokens (BTCUP, ETHDOWN, etc.)
  - Sports / celebrity / non-crypto tokens
  - BUSD pairs (we only trade USDT-M)
  - Symbols ending in BEAR/UP/BULL/DOWN suffix

Policy: Only clean, liquid crypto pairs on USDT-M futures.

Note: The check uses SUFFIX matching (e.g. base endswith "UP"), not
substring, to avoid false positives like COMP, SUP, PEPE (PEPE contains
"PE", not a leveraged token pattern).
"""
from __future__ import annotations

import logging
from typing import Iterable

from backend.config import BLOCKED_SYMBOL_PREFIXES

logger = logging.getLogger("judah.symbol_filter")

# Leveraged token suffixes — checked against the BASE ASSET (after stripping USDT).
# A pair like "BTCUPUSDT" has base "BTCUP", which ends with "UP" → blocked.
# A pair like "PEPEUSDT" has base "PEPE", which doesn't end with these → allowed.
_LEVERAGED_SUFFIXES = ("UP", "DOWN", "BEAR", "BULL")


def is_valid_usdt_future(symbol: str) -> bool:
    """Return True if symbol is a clean USDT-M futures pair.

    Criteria:
      1. Ends with exactly "USDT" (no BUSD, no other quote asset)
      2. Base asset is not in the blocked prefix list
      3. Base asset doesn't end with a leveraged token suffix
      4. Base asset length is sane (2-15 chars)
    """
    if not symbol or not isinstance(symbol, str):
        return False

    sym = symbol.strip().upper()

    # Must end with USDT, but not be just "USDT"
    if not sym.endswith("USDT") or sym == "USDT":
        return False

    # Strip USDT to get the base asset
    base = sym[:-4]

    # Blocked full prefixes (exact match for the base asset)
    if base in BLOCKED_SYMBOL_PREFIXES:
        logger.debug(f"[filter] Blocked prefix: {symbol}")
        return False

    # Blocked suffixes — catches leveraged tokens (BTCUP, ETHDOWN, etc.)
    for suffix in _LEVERAGED_SUFFIXES:
        if base.endswith(suffix):
            logger.debug(f"[filter] Blocked suffix '{suffix}': {symbol}")
            return False

    # Length sanity — real crypto pairs have base assets 2-15 chars
    if len(base) < 2 or len(base) > 15:
        logger.debug(f"[filter] Suspicious base length: {symbol}")
        return False

    return True


def filter_usdt_futures(symbols: Iterable[str]) -> list[str]:
    """Filter a raw symbol list to only valid USDT-M futures pairs.

    Returns sorted, deduplicated list.
    """
    seen = set()
    result = []
    for sym in symbols:
        sym_clean = sym.strip().upper()
        if sym_clean in seen:
            continue
        seen.add(sym_clean)
        if is_valid_usdt_future(sym_clean):
            result.append(sym_clean)

    result.sort()
    logger.info(f"[filter] {len(result)} valid USDT-M futures pairs "
                f"(from {len(seen)} candidates)")
    return result


__all__ = [
    "is_valid_usdt_future",
    "filter_usdt_futures",
]