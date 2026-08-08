"""ALL tunable parameters in one place. Edit this to tune the scanner."""

# === SCANNER ===
SCAN_INTERVAL_SECONDS = 15        # Full D1 scan cycle (candidate filter + CRT/SMC pipeline)
SIGNAL_TTL_MINUTES = 15        # Unified: D2 and D3 share 15 min expiry (was 30)
MAX_SIGNALS = 200
BOOTSTRAP_CANDLES = 200
SCAN_CONCURRENCY = 20             # Max parallel CRT+SMC scans per cycle
D1_TTL_SECONDS = 120              # D1 signal expiry in state_store

# === LOGGING (Excel/CSV) ===
# Set to False to disable signal logging (production mode)
# Set to True to log every signal to CSV (development/backtest mode)
ENABLE_SIGNAL_LOGGING = False
LOG_FILE = "signal_log.csv"

# === CANDIDATE SELECTION ENGINE (formerly pre-filter) ===
# Adaptive ATR threshold: each coin's threshold = 60% of its own 50-period rolling ATR baseline.
# No fixed % per TF. Self-tuning per coin.
ADAPTIVE_ATR_LOOKBACK = 50
ADAPTIVE_ATR_MIN_MULTIPLIER = 0.60
ADAPTIVE_ATR_BASELINE_MIN_PCT = 0.03   # Floor: 0.03% ATR
ADAPTIVE_ATR_BASELINE_MAX_PCT = 5.0    # Ceiling: 5% ATR
ADAPTIVE_ATR_MIN_ABSOLUTE = 0.00001   # Absolute floor for dust coins

# Fixed-threshold pre-filter (used by pre_filter.py alongside adaptive selector)
MIN_ATR_PERCENT = 0.15                 # Min ATR% to pass pre-filter
MIN_PRICE_CHANGE_4H_PCT = 0.15        # Min 4H price change %
MIN_24H_VOLUME_USDT = 5_000_000       # Min 24h volume in USDT

# === CRT ===
CRT_SCORE_MAX = 25              # cap applied to both natural and synthetic CRT scores
MIN_RANGE_MULTIPLIER = 0.5
MIN_PRICE_CHANGE_PCT = 0.05            # Min price movement to be "active"
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

# === SCORING ARCHITECTURE (institutional 4-layer model) ===
# Total max = 90 points across 4 independent components.
# No single component can carry a signal to SNIPER alone.
# CRT (timing)        0-25   — is the entry timed right?
# SMC (structure)     0-20   — is smart money structure there?
# Flow (conviction)   0-25   — is real money flowing RIGHT NOW?
# Momentum (ignition) 0-20   — is price about to explode?
# Base max (CRT+SMC) = 45/90. Flow + Momentum required for SNIPER.

# === TIERS (fixed — no sensitivity modes) ===
# Same thresholds for D1 and D2 scoring.
TIER_SNIPER_SCORE = 85       # SNIPER >= 85: highest probability (was 70)
TIER_OPPORTUNITY_SCORE = 65  # OPPORTUNITY >= 65: strong setups (was 55)
TIER_WATCH_SCORE = 40        # WATCH >= 40: partial confirmation
IGNORE_MIN_SCORE = 60        # Below this: IGNORE regardless of tier (was 50)
MIN_RR = 1.5

# === 100-POINT SCORING WEIGHTS ===
# D1 (HTF: 1H/4H/1D): CRT(25) + SMC(25) + Flow(15) + Momentum(15) + Timing(10) + R/R(10) + Confluence(5) = 100
SMC_SCORE_MAX = 25           # D1 SMC cap (was 30)
# D2 (LTF: 15M): Entry Precision(25) + LTF Structure(20) + Flow(15) + Nascent Move(15) + HTF Context(10) + Momentum(10) + Timing(5) + Confluence(5) = 100
D2_FLOW_SCORE_MAX = 15       # D2 flow cap (was 30)

# === TIMING SCORES ===
TIMING_KILLZONE_MAX = 4      # London open + NY open
TIMING_SESSION_MAX = 3       # High vol / normal / low
TIMING_DAYS_MAX = 3          # Days-to-expiry factor

# === RISK/REWARD SCORES ===
RR_SCORE_MAX = 6             # 3:1+=6, 2.5:1=5, 2:1=3, 1.5:1=1, <1.5:1=0 (FATAL FLAW)
SL_QUALITY_MAX = 4           # Beyond OB+FVG=4, beyond OB=3, beyond swing=2, arbitrary=0

# === CONFLUENCE BONUS ===
CONFLUENCE_MAX = 5           # 1 pt per satisfied factor, max 5

# === D2 HTF CONTEXT BONUS ===
HTF_CONTEXT_SAME = 5         # Same direction as D1
HTF_CONTEXT_NEUTRAL = 2      # D1 neutral (range-bound)
HTF_CONTEXT_OPPOSING = -5    # Opposing direction
HTF_CONTEXT_NO_DATA = 3      # No D1 data available
HTF_CONTEXT_MAX = 10         # Capped at +10
HTF_CONTEXT_MIN = -5         # Floor at -5

# === DECAY RATES (per signal type, per revalidation cycle) ===
DECAY_TYPE_A = 0.94          # Type A (HTF Structure): decay 0.94x per 5 min
DECAY_TYPE_B = 0.90          # Type B (LTF Momentum): decay 0.90x per 2 min
DECAY_TYPE_C = 0.98          # Type C (Full Confluence): decay 0.98x per 5 min
DECAY_TYPE_D = 1.0           # Type D (Early Warning): no decay
DECAY_TYPE_E = 1.0           # Type E (Conflict): no decay

# === POSITION SIZING ===
POSITION_BASE_PCT = 1.0      # Base: 1% of account per trade
POSITION_HARD_CAP_PCT = 3.0  # Hard cap per single trade
POSITION_DIRECTION_CAP_PCT = 5.0  # Hard cap per direction
TYPE_POSITION_MULT = {"A": 0.75, "B": 0.35, "C": 1.0, "D": 0.0, "E": 0.0}
TYPE_STOP_MULT = {"A": 1.5, "B": 1.0, "C": 1.5, "D": 1.5, "E": 1.5}

# === MARKET EVOLUTION STATE MULTIPLIERS ===
STATE_POSITION_MULT = {
    "Dormant": 0.5,
    "Consolidation": 0.75,
    "Compression": 0.75,
    "Coiling": 0.85,
    "Awakening": 0.85,
    "Expansion": 1.0,
    "Institutional Entry": 1.0,
    "Acceleration": 0.0,      # NO NEW ENTRIES — take profits only
    "Transition": 0.5,
    "Distribution": 0.35,
    "Reversal": 0.5,
    "Capitulation": 0.25,     # contrarian only
    "Trap": 0.0,              # no new positions
    "Sweep": 0.0,             # no new positions
    "Accumulation": 0.85,
    "Markup": 1.0,
}

# === REGIME ENGINE ===
REGIME_ATR_PERIOD = 20
REGIME_TREND_SLOPE_PERIOD = 20
REGIME_VP_WIDTH_PERCENT = 15.0
REGIME_MIN_BARS = 20

# === CORRELATION FILTER ===
CORRELATION_MAX_SAME_DIRECTION = 4  # Hard stop at 4
CORRELATION_REDUCE_AT = 3           # Reduce new position by 50% at 3
CORRELATION_REDUCE_FACTOR = 0.5
CORRELATION_MAJOR_PAIRS = {"BTCUSDT", "ETHUSDT", "BNBUSDT"}  # Correlated majors

# === KILLZONE SESSIONS ===
KILLZONE_LONDON_START = 8.0    # 08:00 UTC
KILLZONE_LONDON_END = 11.0     # 11:00 UTC
KILLZONE_NY_START = 13.5       # 13:30 UTC
KILLZONE_NY_END = 16.5         # 16:30 UTC
KILLZONE_OVERLAP_START = 13.5
KILLZONE_OVERLAP_END = 16.5
KILLZONE_LONDON_CLOSE_START = 10.5
KILLZONE_LONDON_CLOSE_END = 12.0

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

# === DIMENSION 2 (LTF Scanner) ===
D2_TIMEFRAME = "15M"
D2_SIGNAL_TTL_MINUTES = 15
D2_SCAN_INTERVAL_SECONDS = 5

# === TYPE B MINIMUMS ===
TYPE_B_MIN_D2_SCORE = 72           # minimum total D2 score (was 65)
TYPE_B_ENTRY_PRECISION_GATE = 16   # minimum Entry Precision sub-score out of 20 (was 18)

# === D2 MINIMUM THRESHOLDS ===
D2_MIN_ENTRY_PRECISION = 12        # min sub-score out of 20 (was 15)
D2_MIN_FLOW = 8                    # min sub-score (was 5)
D2_MIN_MOMENTUM = 8                # min sub-score (was 0)

# === DECAY RATES (per signal type, per 5-min interval) ===
DECAY_TYPE_A = 0.94                # Type A (2h TTL): decay 0.94x per 5 min
DECAY_TYPE_C = 0.98                # Type C (4h TTL): decay 0.98x per 5 min

# === ACCELERATION STATE ===
ACCELERATION_NEW_ENTRY_MULT = 0.0  # 0x for new entries — take profits only
ACCELERATION_HOLD_MULT = 1.0       # Full size for existing positions in acceleration


# === TIMEFRAMES ===
TIMEFRAMES_HTF = ["1H", "4H", "1D"]  # Dimension 1
TIMEFRAMES_LTF = ["15M"]  # Dimension 2
ALL_TIMEFRAMES = ["15M", "1H", "4H", "1D"]  # All including D2
# Backward-compat alias (D1 scanner still uses this name)
TIMEFRAMES = TIMEFRAMES_HTF

# === BINANCE ===
BINANCE_REST_BASE = "https://api.binance.com/api/v3"
BINANCE_WS_BASE = "wss://stream.binance.com:9443/stream?streams="
WS_RECONNECT_DELAY_SEC = 5
WS_MAX_STREAMS_PER_CONN = 793

# Internal TF → Binance REST/WS interval (Binance uses lowercase)
BINANCE_INTERVAL_MAP = {
    "1H": "1h", "4H": "4h", "1D": "1d",
    "15M": "15m", "30M": "30m", "1W": "1w", "1M": "1M",
}

# === SERVER ===
HOST = "0.0.0.0"
PORT = 8000
