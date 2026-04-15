# =============================================================================
#  League Advisor – Central Configuration
#  All tunable thresholds, timings, and display settings live here.
#
#  PROFILES
#  --------
#  Set ACTIVE_PROFILE to one of: "safe" | "balanced" | "aggressive"
#  Profile values override the defaults below at module-import time.
#  Individual constants can still be overridden after import.
# =============================================================================

ACTIVE_PROFILE = "balanced"   # safe | balanced | aggressive

# ── Riot Live Client API ──────────────────────────────────────────────────────
API_BASE    = "https://127.0.0.1:2999"
API_TIMEOUT = 2.0   # seconds per HTTP request

# ── Poll intervals (seconds) ──────────────────────────────────────────────────
POLL_API_INTERVAL      = 1.0   # game state / HP / gold
POLL_MINIMAP_INTERVAL  = 0.5   # screen capture + dot detection
OVERLAY_REFRESH_MS     = 300   # tkinter label refresh

# Aliases used internally by engine modules
API_POLL_INTERVAL  = POLL_API_INTERVAL
DECISION_INTERVAL  = 0.4   # how often the decision engine ticks

# How long (seconds) before the overlay shows "API offline"
API_STALE_WARN_S   = 8.0    # show warning after 8s no fresh data
API_STALE_CRIT_S   = 15.0   # show critical after 15s (stale recs unreliable)

# TTS minimum action score before voice fires
TTS_MIN_SCORE = 50

# ── Minimap capture region ────────────────────────────────────────────────────
# Pixel coordinates on screen (top-left corner + size).
# Run  python ui/calibrate.py  to calibrate for your resolution.
# Default: 1920×1080, default UI scale.
MINIMAP_REGION = {"x": 1630, "y": 868, "width": 290, "height": 210}

# Minimap coordinate system after normalisation:
#   (0, 0)     = top-left  of captured region  ≈ red base / top-right of game map
#   (100, 100) = bottom-right of captured region ≈ blue base / bottom-left of game map
#
# With standard League orientation (blue bottom-left, red top-right):
#   Blue base ≈ minimap (5, 90)   Red base ≈ minimap (90, 5)

# ── Enemy dot detection (HSV) ─────────────────────────────────────────────────
ENEMY_DOT_HSV = {
    "lower1": [0,   140, 140],
    "upper1": [12,  255, 255],
    "lower2": [163, 140, 140],
    "upper2": [180, 255, 255],
}
DOT_AREA_MIN = 5
DOT_AREA_MAX = 280

# JG position tracking — max believable movement per second (0–100 minimap %)
# League champion move speed ≈ 400 units/s; map ≈ 14 000 units → ~2.9%/s
# Add 1.5× buffer for dashes/blinks/teleport
JG_MAX_MOVE_PCT_PER_S = 5.0

# JG dot filter: min zone-classifier confidence to treat a dot as a laner
# Dots classified as top/mid/bot lane with confidence ≥ this threshold are
# excluded from JG candidate set (they're almost certainly laners)
JG_LANER_FILTER_CONF  = 0.70

# ── Target state / confidence ─────────────────────────────────────────────────
TARGET_RECENTLY_SEEN_S  = 12   # full high-conf window
TARGET_STALE_S          = 28   # degraded confidence
TARGET_EXPIRED_S        = 45   # very low confidence, no gank recs
HP_VALIDITY_S           = 7    # HP reading expires this many seconds after losing sight

# Minimum confidence required to fire a gank recommendation
GANK_MIN_CONFIDENCE     = 0.55   # lowered from 0.60; role inference gives 0.65
# Minimum confidence to even mention target position in overlay
POSITION_MIN_CONFIDENCE = 0.35

# ── Jungler probabilistic belief ──────────────────────────────────────────────
# Fraction of probability mass that diffuses to adjacent zones per second
JG_BELIEF_DIFFUSE_PER_S = 0.04
# Minimum belief confidence to assert a threat/safe lane from belief
JG_BELIEF_MIN_THREAT_CONF = 0.25

# ── Jungler tracking (deterministic fallback thresholds) ─────────────────────
JG_AWARE_S   = 18   # "last seen Xs ago" info tier
JG_WARN_S    = 32   # "missing Xs" warning tier
JG_DANGER_S  = 48   # "PLAY SAFE" critical tier

# ── ThreatMap HP scoring thresholds ──────────────────────────────────────────
# These drive the primary gank scoring signal.
# Format: (hp_threshold_pct, score_bonus)
# Evaluated top-down; first match wins.
THREAT_HP_TIERS = [
    (30,  55),   # ≤30% HP – kill shot
    (50,  35),   # ≤50% HP – high priority
    (65,  20),   # ≤65% HP – worth pathing
    (100,  3),   # >65% HP – low priority (needs other signals)
]

# ── Action scoring weights ────────────────────────────────────────────────────
SCORE_GANK_BASE           = 30    # added on top of ThreatMap score when creating ActionResult
SCORE_EXTENDED            = 20    # enemy past river (minimap confirmed)
SCORE_RIVER_ZONE          = 15    # enemy in river zone (zone-only check)
SCORE_JG_FAR              = 14    # enemy jungler confirmed opposite side
SCORE_JG_NEARBY_PENALTY   = -35   # enemy jungler recently seen near lane
SCORE_ALLY_LOW_HP_PENALTY = -20   # ally too low to follow up
SCORE_FARM_BASE           = 35    # default farm action score
SCORE_IN_COMBAT           = 12    # enemy taking damage this poll
SCORE_FED_BONUS           = 18    # fed target (high-value kill)
SCORE_STARVED_BONUS       =  8    # starved target (easier kill)
SCORE_LEVEL_ADV_2         = 12    # 2+ level advantage
SCORE_LEVEL_ADV_1         =  6    # 1 level advantage
SCORE_LEVEL_DIS_2         = -10   # 2+ level disadvantage
SCORE_RECENTLY_DIED       = -25   # target just respawned
SCORE_BACK_DETECTED       = -20   # target just recalled (full HP)
SCORE_OBJ_IMMINENT        = -20   # objective spawning soon

# ── Gank viability hard gates ─────────────────────────────────────────────────
GANK_BLOCK_MY_HP_PCT   = 25    # I'm too low to gank
GANK_BLOCK_TARGET_HP_PCT = 5   # Target is essentially dead already
GANK_MIN_FINAL_SCORE   = 18    # ThreatMap score must beat this (was 38 – too strict)
GANK_JG_NEARBY_SECONDS = 15    # Enemy JG seen within this many seconds → danger
ALLY_MIN_HP_FOR_GANK   = 40    # ally needs ≥ this HP% to follow up a gank

# ── EV (expected value) model weights ────────────────────────────────────────
# Used by the sigmoid probability model in ActionScorer.
# p = sigmoid(w0 + w_hp*hp_signal + w_level*level_diff + ...)
EV_W0            = -1.5   # bias (intercept)
EV_W_HP          =  4.0   # weight for HP signal (0–1 normalised)
EV_W_LEVEL       =  0.6   # weight for level difference
EV_W_EXTENDED    =  0.8   # weight for extended boolean
EV_W_JG_AWAY     =  0.5   # weight for JG safe-side bonus
EV_W_INCOMBAT    =  0.7   # weight for in-combat flag
EV_W_FED         =  0.5   # weight for fed flag
EV_REWARD_KILL   = 80     # reward EV for a successful gank
EV_RISK_COST     = 20     # risk cost for a failed gank

# ── Recall logic ─────────────────────────────────────────────────────────────
RECALL_HP_CRITICAL  = 22    # RECALL NOW immediately
RECALL_HP_LOW       = 32    # RECALL NOW after this camp
RECALL_HP_SOFT      = 48    # Recall if also good gold
RECALL_HP_MEDIUM    = 62    # Soft suggestion only

# (min_gold, item_label) – first match wins
RECALL_GOLD_TIERS = [
    (3300, "full item ready"),
    (1600, "Brutalizer / component set"),
    (1300, "Dirk / Phage"),
    (800,  "Long Sword ×2 + pots"),
    (550,  "component + pots"),
]
RECALL_GOLD_FORCE = 3300   # always recall regardless of HP

# ── Objective timers (minutes) ────────────────────────────────────────────────
DRAGON_FIRST_SPAWN   = 5.0
DRAGON_RESPAWN       = 5.0
BARON_FIRST_SPAWN    = 20.0
BARON_RESPAWN        = 6.0
HERALD_SPAWN         = 8.0
HERALD_DESPAWN       = 19.5
OBJECTIVE_WARN_S     = 75    # warn this many seconds before objective spawns

# ── Camp timer engine ─────────────────────────────────────────────────────────
CAMP_RESPAWN = {
    "blue":    300,   # Blue Sentinel
    "red":     300,   # Red Brambleback
    "gromp":   120,
    "wolves":  120,
    "raptors": 120,
    "krugs":   120,
    "scuttle": 150,
}
# Confidence after which inferred enemy camp availability is shown
CAMP_INFERRED_CONF_SHOW = 0.50

# ── Overlay appearance ────────────────────────────────────────────────────────
OVERLAY_X      = 20
OVERLAY_Y      = 20
OVERLAY_W      = 380    # slightly wider to fit prob text
OVERLAY_H      = 222    # 8 rows × ~26px + header (includes SYS health row)
OVERLAY_ALPHA  = 0.90
# Aliases for overlay.py
OVERLAY_WIDTH  = OVERLAY_W
OVERLAY_HEIGHT = OVERLAY_H

COLORS = {
    "bg":        "#0b0b0e",
    "header_bg": "#111128",
    "header_fg": "#8899cc",
    "border":    "#1a3a6a",
    "info":      "#aaffaa",
    "warn":      "#ffd060",
    "critical":  "#ff4444",
    "dim":       "#55556a",
    "accent":    "#00ccff",
    "build":     "#cc99ff",
    "offline":   "#ff6622",
}
# Aliases for overlay.py
OVERLAY_COLORS     = COLORS
OVERLAY_BG         = COLORS["bg"]
OVERLAY_HEADER_BG  = COLORS["header_bg"]
OVERLAY_HEADER_FG  = COLORS["header_fg"]
OVERLAY_FONT      = "Consolas"
OVERLAY_FONT_SIZE = 10
OVERLAY_FONT_BOLD = ("Consolas", 10, "bold")
MSG_EXPIRE_S = 30

# ── Voice (TTS) ───────────────────────────────────────────────────────────────
TTS_ENABLED          = True
TTS_RATE             = 170       # words per minute
TTS_VOLUME           = 0.9
TTS_COOLDOWN_S       = 8        # minimum gap between any TTS callouts
TTS_SAME_MSG_COOLDOWN_S = 25    # re-speak same message after this long

# ── Logging ───────────────────────────────────────────────────────────────────
LOG_LEVEL  = "INFO"    # DEBUG / INFO / WARNING / ERROR
LOG_FILE   = "advisor.log"
DEBUG_MODE = False     # prints extra detail to console

# =============================================================================
#  PROFILE OVERRIDES
#  Applied last so they override the defaults above.
# =============================================================================

_PROFILES = {
    "safe": {
        "GANK_MIN_FINAL_SCORE":   25,
        "GANK_BLOCK_MY_HP_PCT":   35,
        "GANK_MIN_CONFIDENCE":    0.65,
        "ALLY_MIN_HP_FOR_GANK":   50,
        "SCORE_GANK_BASE":        20,
        "TTS_MIN_SCORE":          65,
        "JG_BELIEF_MIN_THREAT_CONF": 0.40,
    },
    "balanced": {
        # defaults above are balanced
    },
    "aggressive": {
        "GANK_MIN_FINAL_SCORE":   12,
        "GANK_BLOCK_MY_HP_PCT":   18,
        "GANK_MIN_CONFIDENCE":    0.45,
        "ALLY_MIN_HP_FOR_GANK":   28,
        "SCORE_GANK_BASE":        38,
        "TTS_MIN_SCORE":          40,
        "JG_BELIEF_MIN_THREAT_CONF": 0.18,
        "SCORE_JG_NEARBY_PENALTY": -20,   # less scared of counter-gank
    },
}

# Apply active profile
import sys as _sys
_mod = _sys.modules[__name__]
for _k, _v in _PROFILES.get(ACTIVE_PROFILE, {}).items():
    setattr(_mod, _k, _v)
# Clean up – _k/_v may not exist if the active profile is empty (balanced)
del _sys, _mod, _PROFILES
try:
    del _k, _v
except NameError:
    pass
