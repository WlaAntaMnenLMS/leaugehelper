# ─────────────────────────────────────────────
#  League Decision Advisor – Configuration
# ─────────────────────────────────────────────

# Riot Live Client API
API_BASE = "https://127.0.0.1:2999"
API_TIMEOUT = 2.0  # seconds per request

# How often the engine polls data (seconds)
POLL_INTERVAL = 2.0
MINIMAP_POLL_INTERVAL = 0.5

# ── Minimap capture region ──────────────────
# Adjust to match your screen resolution.
# Run  python calibrate.py  to auto-detect or set manually.
# Default: 1920×1080, default UI scale, bottom-right minimap.
MINIMAP_REGION = {
    "x": 1630,
    "y": 868,
    "width": 290,
    "height": 210,
}

# ── Enemy dot detection (HSV colour ranges) ─
# League enemy dots are bright red
ENEMY_DOT_HSV = {
    "lower1": [0,   150, 150],
    "upper1": [10,  255, 255],
    "lower2": [165, 150, 150],
    "upper2": [180, 255, 255],
}
# Dot area filter (px²) — ignores noise and icons that are too large
DOT_AREA_MIN = 6
DOT_AREA_MAX = 250

# ── Minimap position → map-side mapping ─────
# Minimap coordinate system: (0,0) = top-left = top lane corner
#                             (100,100) = bottom-right = bot lane corner
# Zones (in % of minimap width/height):
ZONE_TOP_Y    = 30   # y < this → "top"
ZONE_BOT_Y    = 70   # y > this → "bot"
ZONE_LEFT_X   = 30   # x < this → left side
ZONE_RIGHT_X  = 70   # x > this → right side

# ── Jungler tracking thresholds ─────────────
JUNGLER_AWARE_SECONDS   = 20   # "last seen Xs ago" info
JUNGLER_WARN_SECONDS    = 30   # "be aware" warning
JUNGLER_DANGER_SECONDS  = 45   # "PLAY SAFE" critical alert

# ── Gank decision thresholds ────────────────
GANK_MIN_SCORE          = 45   # minimum score to recommend a gank
GANK_ENEMY_LOW_HP       = 40   # HP% considered "low" for gank opportunity
GANK_ENEMY_MED_HP       = 60   # HP% considered "medium"
GANK_JG_NEARBY_SECONDS  = 15   # jungler seen < Xs ago → don't gank that side

# ── Champion database ────────────────────────
# Bonus gank score added for champions that synergise well
CHAMPION_GANK_BONUS = {
    "Warwick":  {"pre6": 0,  "post6": 25},   # ult is a lock-on engage
    "Kayn":     {"pre6": 5,  "post6": 15},
    "Hecarim":  {"pre6": 10, "post6": 10},   # speed makes all ganks better
    "Vi":       {"pre6": 10, "post6": 20},
    "Zac":      {"pre6": 15, "post6": 20},
    "Amumu":    {"pre6": 5,  "post6": 20},
    "Jarvan":   {"pre6": 15, "post6": 15},
    "Nocturne": {"pre6": 0,  "post6": 30},   # ult dramatically improves ganks
    "Shaco":    {"pre6": 20, "post6": 20},
    "Master Yi": {"pre6": 0, "post6": 5},    # Yi doesn't gank well
    "Karthus":  {"pre6": 0,  "post6": 0},
}

# ── Objective timers (minutes) ───────────────
DRAGON_FIRST_SPAWN   = 5.0
DRAGON_RESPAWN       = 5.0
BARON_FIRST_SPAWN    = 20.0
BARON_RESPAWN        = 6.0
RIFT_HERALD_SPAWN    = 8.0
RIFT_HERALD_DESPAWN  = 19.5   # disappears at 19:30
OBJECTIVE_WARN_SECS  = 60     # warn this many seconds before spawn

# ── Auto-chat ────────────────────────────────
CHAT_ENABLED        = True
CHAT_MIN_INTERVAL   = 20   # seconds between auto-messages
CHAT_HUMAN_DELAY    = (0.15, 0.70)   # (min, max) seconds before opening chat
CHAT_TYPING_SPEED   = (0.04, 0.11)   # (min, max) seconds per character

# Message templates – filled in at runtime
CHAT_TEMPLATES = {
    "jg_bot":      ["{jg} bot", "jg bot", "enemy jg bot side"],
    "jg_top":      ["{jg} top", "jg top", "enemy jg top side"],
    "jg_mid":      ["{jg} mid", "jg mid"],
    "jg_missing":  ["jg mia", "careful jg missing", "watch out jg mia"],
    "gank_coming": ["coming {lane}", "gank {lane}"],
}

# ── Overlay appearance ───────────────────────
OVERLAY_X       = 20
OVERLAY_Y       = 20
OVERLAY_WIDTH   = 390
OVERLAY_HEIGHT  = 230
OVERLAY_ALPHA   = 0.88

COLORS = {
    "bg":        "#0c0c0f",
    "header_bg": "#12122a",
    "border":    "#0055cc",
    "info":      "#b8ffb8",
    "warn":      "#ffd966",
    "critical":  "#ff4d4d",
    "dim":       "#4a4a6a",
    "accent":    "#00ccff",
}
OVERLAY_FONT = ("Consolas", 10)
MSG_EXPIRE_SECS = 25   # messages fade after this many seconds
