"""Data structures used across the scanner."""
from dataclasses import dataclass
from typing import Optional
from enum import Enum

class Direction(str, Enum):
    BULLISH = "BULLISH"
    BEARISH = "BEARISH"

class Tier(str, Enum):
    SNIPER = "SNIPER"
    ACTIVE = "ACTIVE"
    WATCH = "WATCH"
    REJECTED = "REJECTED"

class FreshnessState(str, Enum):
    FRESH = "FRESH"
    WARM = "WARM"
    COOLING = "COOLING"
    STALE = "STALE"
    EXPIRED = "EXPIRED"

@dataclass
class Candle:
    time: int
    open: float
    high: float
    low: float
    close: float
    volume: float
    close_time: int
    is_closed: bool = False
