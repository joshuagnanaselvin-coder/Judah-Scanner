"""Session detection — direction-aligned scoring, NOT a hard filter."""
from datetime import datetime, timezone

# Session hours (UTC)
_SESSIONS = [
    ("ASIA",      0, 9),
    ("LONDON",    8, 17),
    ("OVERLAP",   13, 17),
    ("NY",        13, 22),
    ("LATE_NY",   21, 24),
]

def get_session_at(timestamp_utc: int = None) -> str:
    """Get session name for a given UTC timestamp (or now if not provided)."""
    if timestamp_utc is not None:
        hour = datetime.fromtimestamp(timestamp_utc / 1000, tz=timezone.utc).hour
    else:
        hour = datetime.now(timezone.utc).hour
    for name, start, end in _SESSIONS:
        if start <= hour < end:
            return name
    return "LATE_NY"

def session_score(signal_direction: str, timestamp_utc: int = None,
                 displacement_ratio: float = 0.0,
                 liquidity_swept: bool = False,
                 liquidity_direction: str = None) -> int:
    """
    Direction-aligned session scoring.
    Returns 0-10 based on whether the session supports the signal direction.

    Args:
        signal_direction: "BULLISH" or "BEARISH"
        timestamp_utc: ms timestamp of the signal (or None for now)
        displacement_ratio: displacement body ratio (for Asian edge case bonus)
        liquidity_swept: whether liquidity was swept in signal's favor
        liquidity_direction: "BULLISH" (sellside swept) or "BEARISH" (buyside swept)
    """
    session = get_session_at(timestamp_utc)

    # Base scores by session (same for both directions — all sessions are liquid)
    base_scores = {
        "OVERLAP": 10,
        "LONDON":  9,
        "NY":      8,
        "ASIA":    4,
        "LATE_NY": 2,
    }
    score = base_scores.get(session, 0)

    # Asian session edge cases
    if session == "ASIA":
        # High volume confirmation: displacement >= 3x body → +2 bonus
        if displacement_ratio >= 3.0:
            score = min(score + 2, 10)
        # Liquidity sweep alignment: +1 if swept in signal's favor
        if liquidity_swept and liquidity_direction == signal_direction:
            score = min(score + 1, 10)

    return score

def get_session_label(session: str) -> str:
    return {
        "OVERLAP": "Overlap", "LONDON": "London", "NY": "New York",
        "ASIA": "Asia", "LATE_NY": "Late NY",
    }.get(session, session)
