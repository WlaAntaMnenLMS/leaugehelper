# =============================================================================
#  League Advisor – Central Configuration
#  All tunable thresholds, timings, and display settings live here.
# =============================================================================

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

# ── Target state / confidence ─────────────────────────────────────────────────
# How long (seconds) before a sighted target's position confidence decays
TARGET_RECENTLY_SEEN_S  = 12   # full high-conf window
TARGET_STALE_S          = 28   # degraded confidence
TARGET_EXPIRED_S        = 45   # very low confidence, no gank recs
HP_VALIDITY_S           = 7    # HP reading expires this many seconds after losing sight

# Minimum confidence required to fire a gank recommendation
GANK_MIN_CONFIDENCE     = 0.60
# Minimum confidence to even mention target position in overlay
POSITION_MIN_CONFIDENCE = 0.35

# ── Jungler tracking ──────────────────────────────────────────────────────────
JG_AWARE_S   = 18   # "last seen Xs ago" info tier
JG_WARN_S    = 32   # "missing Xs" warning tier
JG_DANGER_S  = 48   # "PLAY SAFE" critical tier

# ── Action scoring weights ────────────────────────────────────────────────────
# Adjusting these changes how aggressive vs. passive the advisor is.
SCORE_GANK_BASE           = 30    # base score for any gank candidate
SCORE_ENEMY_LOW_HP        = 40    # enemy HP ≤ 35 %
SCORE_ENEMY_MED_HP        = 22    # enemy HP ≤ 55 %
SCORE_ENEMY_LOW_HP_THRESH = 35
SCORE_ENEMY_MED_HP_THRESH = 55
SCORE_EXTENDED            = 25    # enemy is on our side of the map (past river)
SCORE_JG_FAR              = 18    # enemy jungler was last seen the opposite side
SCORE_JG_NEARBY_PENALTY   = -45   # enemy jungler recently seen this side
SCORE_ALLY_LOW_HP_PENALTY = -15   # ally too low to follow up
SCORE_ALLY_LOW_HP_THRESH  = 25
SCORE_NO_VISION_PENALTY   = -20   # stale target, low confidence
SCORE_FARM_BASE           = 35    # default farm action score
SCORE_RECALL_HP_LOW       = 80    # HP below this makes recall score very high
SCORE_RECALL_GOLD_HIGH    = 65    # gold above threshold boosts recall score
SCORE_OBJECTIVE_CLOSE     = 50    # objective spawning within 60s

# ── Gank viability hard gates ─────────────────────────────────────────────────
# A gank is completely blocked (score capped at 0) if any gate triggers.
GANK_BLOCK_MY_HP_PCT      = 25    # I'm too low to gank
GANK_BLOCK_TARGET_HP_PCT  = 5     # Target is essentially dead already
GANK_MIN_FINAL_SCORE      = 38    # Score must beat this to surface the rec
GANK_JG_NEARBY_SECONDS    = 15    # Enemy JG seen within this many seconds → danger

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

# ── Overlay appearance ────────────────────────────────────────────────────────
OVERLAY_X      = 20
OVERLAY_Y      = 20
OVERLAY_W      = 360
OVERLAY_H      = 168    # 5 rows × ~26px + header
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
