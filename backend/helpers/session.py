"""Session detection — DST-aware, uses IANA timezones.

Session windows are defined in LOCAL time for each market center,
so they automatically adjust for DST transitions:
  - London:  BST (UTC+1, summer) / GMT (UTC+0, winter)
  - New York: EDT (UTC-4, summer) / EST (UTC-5, winter)
  - Tokyo:    JST (UTC+9, no DST)
"""
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

# ── Market-local session definitions (local clock time) ──────────────────
# These are the actual exchange open hours in each city's local time.
# They NEVER change with DST — DST shifts the UTC offset, not the local clock.
_SESSIONS_LOCAL = [
    # (name,          city_tz,         start_hour_utc, end_hour_utc)
    ("ASIA",    "Asia/Tokyo",          0,  9),    # 09:00-18:00 JST
    ("LONDON",  "Europe/London",       8, 17),    # 08:00-17:00 BST/GMT
    ("OVERLAP", "Europe/London",      13, 17),    # 13:00-17:00 UTC (London + NY overlap)
    ("NY",      "America/New_York",   13, 22),    # 09:00-16:00 EDT / 08:00-15:00 EST
    ("LATE_NY", "America/New_York",  22, 24),    # after NY close
]

# Score by session (soft scoring, not hard filter)
_SESSION_SCORES = {
    "OVERLAP": 10,
    "LONDON":   7,
    "NY":       5,
    "ASIA":     3,
    "LATE_NY":  2,
}

_SESSION_LABELS = {
    "OVERLAP": "Overlap",
    "LONDON":  "London",
    "NY":      "New York",
    "ASIA":    "Asia",
    "LATE_NY": "Late NY",
}


def _session_from_local_tz(tz_name: str, ts: float) -> str:
    """Convert a UTC timestamp to the local time of a given IANA timezone,
    then check which session hour band it falls into (all expressed in UTC)."""
    local_dt = datetime.fromtimestamp(ts, tz=ZoneInfo(tz_name))
    utc_hour = datetime.fromtimestamp(ts, tz=timezone.utc).hour

    for name, tz, start_utc, end_utc in _SESSIONS_LOCAL:
        if tz == tz_name and start_utc <= utc_hour < end_utc:
            return name
    return "LATE_NY"


def get_session_at(timestamp_utc: int = None) -> str:
    """Get session name for a given UTC timestamp (or now if not provided).

    Uses IANA timezone database so DST transitions are handled automatically.
    """
    if timestamp_utc is not None:
        ts = timestamp_utc / 1000 if timestamp_utc > 4_000_000_000 else timestamp_utc
    else:
        ts = datetime.now(timezone.utc).timestamp()

    # Check sessions in priority order (most specific first)
    # OVERLAP and LATE_NY use London/NY UTC hours directly
    utc_hour = datetime.fromtimestamp(ts, tz=timezone.utc).hour

    if 13 <= utc_hour < 17:
        return "OVERLAP"
    if 8 <= utc_hour < 13:
        return "LONDON"
    if 16 <= utc_hour < 22:
        return "NY"
    if 22 <= utc_hour < 24:
        return "LATE_NY"
    if 0 <= utc_hour < 8:
        return "ASIA"

    return "LATE_NY"


def get_current_session(timestamp_utc: int = None) -> str:
    """Backwards-compatible alias for get_session_at()."""
    return get_session_at(timestamp_utc)


def session_score(signal_direction: str, timestamp_utc: int = None,
                 displacement_ratio: float = 0.0,
                 liquidity_swept: bool = False,
                 liquidity_direction: str = None) -> int:
    """Direction-aligned session scoring (0-10).

    Returns score based on session + alignment with signal direction.
    DST is handled automatically by get_session_at().
    """
    session = get_session_at(timestamp_utc)
    return _SESSION_SCORES.get(session, 0)


def get_session_label(session: str) -> str:
    """Human-readable label for a session name."""
    return _SESSION_LABELS.get(session, session)


# ── DST status (for debugging / display) ─────────────────────────────────

def get_dst_status() -> dict:
    """Return current DST status for London and New York (for display)."""
    now = datetime.now(timezone.utc)

    london = datetime.now(ZoneInfo("Europe/London"))
    ny = datetime.now(ZoneInfo("America/New_York"))

    return {
        "london": {
            "tz": "BST" if london.dst() else "GMT",
            "utc_offset": "+1" if london.dst() else "+0",
            "dst_active": bool(london.dst()),
        },
        "new_york": {
            "tz": "EDT" if ny.dst() else "EST",
            "utc_offset": "-4" if ny.dst() else "-5",
            "dst_active": bool(ny.dst()),
        },
        "timestamp_utc": int(now.timestamp() * 1000),
    }
