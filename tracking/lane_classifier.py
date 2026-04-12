"""
Lane / Zone Classifier for minimap coordinates.

Converts a normalised minimap position (x, y) in 0–100 space into a named
map zone with an associated confidence score.

Minimap coordinate conventions (IMPORTANT):
  The League minimap is captured from the HUD (bottom-right corner).
  In the captured image:

    (0,   0) = top-left  of image  ≈ TOP of the game map (near Red side top)
    (100, 0) = top-right of image  ≈ Red base
    (0, 100) = bottom-left         ≈ Blue base
    (100,100)= bottom-right        ≈ BOTTOM of game map (near Blue side bot)

  So in standard League orientation (blue = bottom-left):
    Blue base ≈ (5,  90)   Red base ≈ (90, 5)

Zone definitions (heuristic, verified against typical gameplay):
  blue_base    – Blue team spawn / fountain
  red_base     – Red team spawn / fountain
  top_lane     – The top-lane corridor (L-shape: left edge + top edge)
  mid_lane     – Mid-lane diagonal band
  bot_lane     – Bot-lane corridor (L-shape: bottom edge + right edge)
  top_river    – Baron-side river (upper centre)
  bot_river    – Dragon-side river (lower centre)
  top_jungle   – Blue-side jungle quadrant
  bot_jungle   – Red-side jungle quadrant
  unknown      – Ambiguous / unclassifiable

Confidence:
  0.9+   very confident (deep in the zone)
  0.7–0.9 confident
  0.5–0.7 moderate (near zone boundary)
  < 0.5  uncertain
"""

from __future__ import annotations
from typing import Tuple


# ---------------------------------------------------------------------------
# Public classifier
# ---------------------------------------------------------------------------

def classify(mx: float, my: float) -> Tuple[str, float]:
    """
    Classify a normalised minimap position into a zone.

    Parameters
    ----------
    mx, my : float  – 0-100 coordinates (x=horizontal, y=vertical,
                      origin at top-left of minimap image).

    Returns
    -------
    (zone_name, confidence) : Tuple[str, float]
    """

    # ── Base areas ───────────────────────────────────────────────────────────
    # Blue base: bottom-left corner of minimap image
    if mx < 13 and my > 87:
        return "blue_base", 0.95
    # Red base: top-right corner
    if mx > 87 and my < 13:
        return "red_base", 0.95

    # ── Top lane ─────────────────────────────────────────────────────────────
    # L-shape: left arm (low mx, not in blue base) + top arm (low my, not in red base)
    # Left arm: mx < 20, my in [10, 85]
    # Top arm:  my < 20, mx in [10, 85]
    on_left_arm = (mx < 20) and (10 < my < 85)
    on_top_arm  = (my < 20) and (10 < mx < 85)
    if on_left_arm or on_top_arm:
        # Deeper in the corridor → higher confidence
        deep_left = mx < 12 and 15 < my < 80
        deep_top  = my < 12 and 15 < mx < 80
        conf = 0.88 if (deep_left or deep_top) else 0.68
        return "top_lane", conf

    # ── Bot lane ─────────────────────────────────────────────────────────────
    # L-shape: bottom arm (high my, not in blue base) + right arm (high mx, not in red base)
    on_bottom_arm = (my > 80) and (15 < mx < 88)
    on_right_arm  = (mx > 80) and (15 < my < 88)
    if on_bottom_arm or on_right_arm:
        deep_bottom = my > 88 and 20 < mx < 82
        deep_right  = mx > 88 and 20 < my < 82
        conf = 0.88 if (deep_bottom or deep_right) else 0.68
        return "bot_lane", conf

    # ── Mid lane ─────────────────────────────────────────────────────────────
    # Diagonal band.  Blue base is at (~5, 90), Red base at (~90, 5).
    # The mid lane runs roughly along x + y ≈ 95.
    # We check distance from this diagonal within the central map area.
    if 15 < mx < 85 and 15 < my < 85:
        diag_dist = abs(mx + my - 95)
        if diag_dist < 11:
            # Confidence falls off with distance from the lane centreline
            conf = max(0.50, 0.88 - diag_dist * 0.035)
            return "mid_lane", conf

    # ── River zones ──────────────────────────────────────────────────────────
    # Top river (Baron side): sits in the upper-centre area
    # Approximate band: mx ∈ [30, 60], my ∈ [18, 45], and mx + my < 78
    if 28 < mx < 62 and 15 < my < 48 and (mx + my) < 80:
        return "top_river", 0.72

    # Bot river (Dragon side): lower-centre
    # mx ∈ [38, 72], my ∈ [52, 82], and mx + my > 112
    if 36 < mx < 74 and 50 < my < 84 and (mx + my) > 112:
        return "bot_river", 0.72

    # ── Jungle quadrants ─────────────────────────────────────────────────────
    # Top jungle: upper-left quadrant (blue side)
    if mx < 55 and my < 55:
        return "top_jungle", 0.70

    # Bot jungle: lower-right quadrant (red side)
    if mx > 45 and my > 45:
        return "bot_jungle", 0.70

    return "unknown", 0.30


# ---------------------------------------------------------------------------
# Zone metadata helpers
# ---------------------------------------------------------------------------

# Which API role corresponds to which lane zone?
ROLE_TO_LANE_ZONE = {
    "TOP":     "top_lane",
    "MIDDLE":  "mid_lane",
    "BOTTOM":  "bot_lane",
    "UTILITY": "bot_lane",  # support shares bot lane
    "JUNGLE":  None,         # jungle has no single lane zone
}

# Zones considered "gankable" per role (target must be in one of these)
GANKABLE_ZONES = {
    "top":  {"top_lane", "top_river"},
    "mid":  {"mid_lane", "top_river", "bot_river"},
    "bot":  {"bot_lane", "bot_river"},
}

# Is this zone on the "safe" side of the map (away from base)?
EXTENDED_ZONES = {
    # For a BLUE-team top laner, extended means top-right part of lane
    # For a RED-team top laner, extended means top-left part of lane
    # We approximate by checking the specific minimap quadrant.
    "top_lane":  True,   # always possible to be extended
    "mid_lane":  True,
    "bot_lane":  True,
    "top_river": True,   # in river = very extended / risky
    "bot_river": True,
}


def is_extended(mx: float, my: float, role: str, my_team: str) -> bool:
    """
    Heuristic: is the target extended (past the river / in enemy territory)?

    A target is "extended" if they are in the half of the map that belongs to
    the OPPOSING team.  This increases gank kill potential because:
      - They are farther from their tower safety
      - They take longer to reach their tower

    Approximation:
      - Top laner: extended if on the top-right portion (past river line)
      - Bot laner: extended if on the bot-left portion (past river line)
      - Mid: always somewhat extended in river zone
    """
    if role == "TOP":
        if my_team == "ORDER":
            # Our top laner is blue; enemy top laner is red.
            # Red top laner is "extended" when deep in the blue side (left + top)
            return mx < 30 and my < 45
        else:
            return mx > 70 and my > 55

    if role == "BOTTOM":
        if my_team == "ORDER":
            # Red bot laner extended = on the right + bottom (far from their base)
            return mx > 70 and my > 55
        else:
            return mx < 30 and my < 45

    if role in ("MIDDLE", "UTILITY"):
        # Extended when in river zone
        zone, _ = classify(mx, my)
        return zone in ("top_river", "bot_river")

    return False
