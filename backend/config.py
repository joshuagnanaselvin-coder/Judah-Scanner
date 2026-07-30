"""ALL tunable parameters in one place. Edit this to tune the scanner."""

# === SCANNER ===
SCAN_INTERVAL_SECONDS = 5
SIGNAL_TTL_MINUTES = 30
MAX_SIGNALS = 200
BOOTSTRAP_CANDLES = 200

# === LOGGING (Excel/CSV) ===
# Set to False to disable signal logging (production mode)
# Set to True to log every signal to CSV (development/backtest mode)
ENABLE_SIGNAL_LOGGING = False
LOG_FILE = "signal_log.csv"

# === PRE-FILTER ===
MIN_24H_VOLUME_USDT = 500_000
MIN_ATR_PERCENT = 0.05
MIN_ATR_ABSOLUTE = 0.0001
MIN_PRICE_CHANGE_4H_PCT = 0.05
MIN_RANGE_MULTIPLIER = 1.5


# === CRT ===
RANGE_LOOKBACK = 20
DISPLACEMENT_BODY_RATIO = 1.5
EXTREME_DISPLACEMENT_RATIO = 3.0
AVG_BODY_PERIOD = 14
OTE_LOW = 50
OTE_HIGH = 62

CRT_SCORE_DISPLACEMENT = 15
CRT_SCORE_RETRACEMENT = 15
CRT_SCORE_SESSION = 10
CRT_SCORE_RANGE_BREAK = 10

# === SMC ===
SWING_LOOKBACK = 3
VSP_BODY_RATIO_MIN = 0.15
OB_PROXIMITY_PERCENT = 1.5
OB_TOUCH_PENALTY = [0, 3, 7, 10]
MSB_LOOKBACK = 10
FVG_LOOKBACK = 20
FVG_PROXIMITY_PERCENT = 2.0
LIQUIDITY_SWEEP_PERCENT = 0.5

SMC_SCORE_SWING = 5
SMC_SCORE_VSP = 10
SMC_SCORE_OB = 10
SMC_SCORE_MSB = 10
SMC_SCORE_FVG = 10
SMC_SCORE_FVG_PROXIMITY_BONUS = 5
SMC_SCORE_LIQUIDITY = 5

# === SCORING ARCHITECTURE (hedge fund model) ===
# Total max = 90 points across 4 independent components.
# No single component can carry a signal to SNIPER alone.
# CRT (timing)        0-25   — is the entry timed right?
# SMC (structure)     0-20   — is smart money structure there?
# Flow (conviction)   0-25   — is real money flowing RIGHT NOW?
# Momentum (ignition) 0-20   — is price about to explode?
# Base max (CRT+SMC) = 45/90. Flow + Momentum required for SNIPER.

# === TIERS (matches README and original spec) ===
TIER_SNIPER_SCORE = 70       # SNIPER >= 70: highest probability
TIER_OPPORTUNITY_SCORE = 55  # OPPORTUNITY >= 55: strong setups
TIER_WATCH_SCORE = 40        # WATCH >= 40: partial confirmation
MIN_RR = 1.5

# === RISK ===
SL_BUFFER_PERCENT = 0.15
TP_RR_MULTIPLIER = 2.0
TP_MAX_RR = 4.0

# === INSTITUTIONAL SL (hedge fund methodology) ===
# Relevance gate — only use a swing if it's within this % of entry
SL_RELEVANCE_PCT = 3.0
# Maximum allowed structural SL distance; beyond this, fall back to ATR
SL_MAX_STRUCTURAL_DISTANCE_PCT = 4.0
# Fallback: ATR multiplier when no nearby swing exists
SL_ATR_FALLBACK_MULT = 1.5
# Skip swing points that have already been "swallowed" (price traded
# through them and closed back on the other side). Stale structure.
SL_SKIP_SWEPT = True
# Lookback cap on how many candles we search for valid swing
SWING_SL_LOOKBACK = 8

# === FRESHNESS ===
FRESH_MAX_AGE_MIN = 3
FRESH_MAX_DISTANCE_PCT = 1.0
WARM_MAX_AGE_MIN = 8
WARM_MAX_DISTANCE_PCT = 2.0
COOLING_MAX_AGE_MIN = 15
COOLING_MAX_DISTANCE_PCT = 3.0

FRESH_SCORE_FACTOR = 1.0
WARM_SCORE_FACTOR = 0.95
COOLING_SCORE_FACTOR = 0.85
STALE_SCORE_FACTOR = 0.75

# === TIMEFRAMES ===
TIMEFRAMES = ["1h", "4h", "1d"]

# === BINANCE ===
BINANCE_REST_BASE = "https://api.binance.com/api/v3"
BINANCE_WS_BASE = "wss://stream.binance.com:9443/stream?streams="
WS_RECONNECT_DELAY_SEC = 5
WS_MAX_STREAMS_PER_CONN = 793

# === SERVER ===
HOST = "0.0.0.0"
PORT = 8000
