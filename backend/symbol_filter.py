"""Symbol allow/deny-list for USDT-M futures only.

Blocks:
  - Stock tokens (AAPLUSDT, TSLAUSDT, etc.)
  - B-stock tokens (AAPLBUSDT, TSLABUSDT, etc. — Binance stock wrappers)
  - Leveraged / inverse tokens (BTCUP, ETHDOWN, etc.)
  - Sports / celebrity / non-crypto tokens
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

# Known B-stock bases that are NOT in BLOCKED_SYMBOL_PREFIXES but must be blocked.
# These are Binance stock wrappers like AAPLBUSDT, TSLABUSDT, MSFTBUSDT.
# The 'B' suffix in the base asset (AAPLB, TSLAB) is the tell.
_B_STOCK_BASES = frozenset({
    "AAPLB", "TSLAB", "MSFTB", "NVDAB", "GOOGLB", "METAB", "AMZNB", "NFLXB",
    "COINB", "MSTRB", "PYPLB", "PLTRB", "INTCB", "ARMB", "AVGOB", "DELLB",
    "GSB", "HOODB", "SMCIB", "GMEB", "QQQB", "SPYB", "SOXLB", "SOXSB",
    "SMHB", "SQB", "SHOPB", "WDCB", "SKHYB", "AOIB", "QNTB", "BABAB",
    "RKLBB", "MUUB", "MVLLB", "BEB", "FLNCB", "AMATB", "AMDAB", "IRENB",
    "ORCLB", "NOKB", "TSMB", "AEROB", "MRVLB",
    # Below have base that equals a known stock ticker directly:
    "META", "IBM",  # META→FB, IBM→IBM (already in blocklist, included for safety)
    # Known B-stocks not caught by the strip-B heuristic:
    "AAOIB", "ASMLB", "ASTLB", "BMNRB", "CRDOB",
})

# Known legit tokens ending in B — do NOT block these.
_B_ENDING_WHITELIST = frozenset({
    "COIN",   # COINBUSDT — legit crypto exchange token
    "COINB",  # COINBUSDT base — "B" suffix on COIN, strip-B heuristic would block
    "MSTR",   # MSTRBUSDT — legit crypto proxy token
    "MSTRB",  # MSTRBUSDT base — same reason
    "PYPL",   # PYPLBUSDT — legit fintech token
    "PYPLB",  # PYPLBUSDT base — same reason
    "DOGE",   # DOGEUSDT — meme coin (no B suffix, safety)
})


def is_valid_usdt_future(symbol: str) -> bool:
    """Return True if symbol is a clean USDT-M perpetual futures pair.

    Criteria:
      1. Ends with exactly "USDT" (no BUSD, no other quote asset)
      2. Base asset is not in the blocked prefix list
      3. Base asset doesn't end with a leveraged token suffix
      4. B-stock variants are detected and blocked
      5. Base asset starts with a letter (no numeric/symbol prefixes)
      6. Base asset length is sane (2-15 chars)

    Note: contractType=="PERPETUAL" filtering is done at the bootstrap level
    in main.py before passing symbols here.
    """
    if not symbol or not isinstance(symbol, str):
        return False

    sym = symbol.strip().upper()

    # Must end with exactly USDT (not BUSD, not bare "USDT")
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

    # B-stock tokens: AAPLBUSDT, TSLABUSDT, MSFTBUSDT, NVDABUSDT etc.
    # These have a 'B' suffix on the base (AAPLB, TSLAB).
    # Direct blocked-bases check first, then heuristic for unknown ones.
    # Whitelist check first — COINB/MSTRB/PYPLB are legit crypto that happen to end in B
    if base in _B_ENDING_WHITELIST:
        pass  # allowed — skip B-stock blocking
    elif base in _B_STOCK_BASES:
        logger.debug(f"[filter] Blocked B-stock base: {symbol}")
        return False

    # Heuristic: if base ends with 'B' and stripping it gives a known stock ticker
    if len(base) > 2 and base.endswith('B'):
        stripped = base[:-1]
        if stripped in BLOCKED_SYMBOL_PREFIXES and base not in _B_ENDING_WHITELIST:
            logger.debug(f"[filter] Blocked B-stock (stripped '{stripped}'): {symbol}")
            return False

    if not base or not base[0].isalpha():
        logger.debug(f"[filter] Non-alpha prefix: {symbol}")
        return False

    # Length sanity — real crypto pairs have base assets 2-15 chars
    # Also blocks the bare "USDT" entry that Binance sometimes returns
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
