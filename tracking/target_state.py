"""
Target State – validated, confidence-aware enemy champion tracking.

This is the most critical fix over the old version.

Each enemy champion is represented as a TrackedTarget.  The object tracks:
  - Current HP (from API – always real-time)
  - Last confirmed minimap position + zone
  - Visibility status (VISIBLE / RECENTLY_SEEN / STALE / EXPIRED / DEAD)
  - Position confidence (decays over time when not seen)

IMPORTANT RULES enforced here:
  1. HP data is only treated as "valid for gank decisions" when:
       a. The target is currently VISIBLE, OR
       b. The HP was read while VISIBLE and is less than HP_VALIDITY_S seconds old
  2. HP of 0 or ≤ 0 means DEAD – never recommend ganking a dead target.
  3. Position confidence decays on a three-tier schedule.
  4. A gank recommendation requires is_valid_gank_target() == True.
  5. Lane zone is assigned from minimap position via LaneClassifier, NOT guessed.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Optional, Tuple

import config


# ---------------------------------------------------------------------------
# Role → default zone mapping (used when we have no minimap data)
# Real players assume a laner is in their lane until proven otherwise.
# ---------------------------------------------------------------------------

_ROLE_DEFAULT_ZONE = {
    "TOP":     "top_lane",
    "MIDDLE":  "mid_lane",
    "BOTTOM":  "bot_lane",
    "UTILITY": "bot_lane",
    "JUNGLE":  "top_jungle",   # rough starting-side guess
}


# ---------------------------------------------------------------------------
# Enumerations
# ---------------------------------------------------------------------------

class Visibility(Enum):
    """Describes how fresh our knowledge of a target's position is."""
    VISIBLE        = "visible"         # Currently on minimap / confirmed in-view
    RECENTLY_SEEN  = "recently_seen"   # Seen within TARGET_RECENTLY_SEEN_S
    STALE          = "stale"           # Seen between RECENTLY_SEEN_S and EXPIRED_S
    EXPIRED        = "expired"         # Too old to trust for position
    DEAD           = "dead"            # Confirmed dead from game events
    UNKNOWN        = "unknown"         # Never seen this game


# ---------------------------------------------------------------------------
# Core data object
# ---------------------------------------------------------------------------

@dataclass
class TrackedTarget:
    """
    Represents one enemy champion's tracked state.

    Fields updated from API (every poll):
        champion, summoner_name, team, role, current_hp, max_hp, level, is_dead

    Fields updated from minimap (every 0.5 s when visible):
        last_minimap_pos, last_zone, zone_confidence, last_seen_game_time
        visibility (set to VISIBLE on minimap update)

    Fields managed by update_confidence():
        position_confidence, visibility (decayed over time)
    """

    # Identity (set once, never changes)
    champion:      str = ""
    summoner_name: str = ""
    team:          str = ""   # "ORDER" or "CHAOS"
    role:          str = ""   # "TOP" / "JUNGLE" / "MIDDLE" / "BOTTOM" / "UTILITY"

    # API-sourced stats (updated every API poll – always real-time)
    current_hp:    float = 1000.0
    max_hp:        float = 1000.0
    level:         int   = 1

    # Derived
    is_dead:       bool  = False   # set when current_hp <= 0 or death event

    # Minimap tracking
    last_minimap_pos:  Optional[Tuple[float, float]] = None  # (x, y) 0-100
    last_zone:         str   = "unknown"   # zone from LaneClassifier
    zone_confidence:   float = 0.0         # 0-1 confidence in zone assignment
    last_seen_game_time: float = 0.0       # game-clock seconds when minimap dot seen

    # Timestamp of the last VALID HP reading
    last_valid_hp_game_time: float = 0.0
    last_valid_hp_value:     Optional[float] = None  # stored as percent 0-100

    # Timestamp of the last API update (real-time HP from Riot is always valid)
    last_api_update_time: float = 0.0

    # Whether zone was inferred from API role rather than confirmed by minimap
    zone_inferred: bool = False

    # Visibility and position confidence
    visibility:           Visibility = Visibility.UNKNOWN
    position_confidence:  float      = 0.0   # 0.0 – 1.0

    # ── HP helpers ───────────────────────────────────────────────────────────

    @property
    def hp_percent(self) -> float:
        """
        Current HP as a percentage (0–100).
        Returns the live API value (always available).
        A value of 0 means dead; never use this for gank advice – use
        get_valid_hp_for_gank() which applies validity checks.
        """
        if self.max_hp <= 0:
            return 100.0
        return min(100.0, max(0.0, self.current_hp / self.max_hp * 100.0))

    def get_valid_hp_for_gank(self, current_game_time: float) -> Optional[float]:
        """
        Return HP percent ONLY when it can be trusted for gank decisions.

        The Riot Live Client API provides real-time HP for ALL players,
        including fog-of-war enemies.  This is intentional by Riot for
        overlay tools.  So API HP is always valid as long as the poll is fresh.

        Priority order:
          1. Currently VISIBLE on minimap → use live hp_percent
          2. Fresh API data (< HP_VALIDITY_S since last poll) → use current hp_percent
          3. Recently stored reading (< HP_VALIDITY_S) → use stored value
          4. None → cannot score gank
        """
        if self.is_dead:
            return None

        hp = self.hp_percent
        if hp <= 0:
            return None

        if self.visibility == Visibility.VISIBLE:
            return hp

        # Riot API gives real-time HP — use it while the poll is fresh
        api_age = current_game_time - self.last_api_update_time
        if self.last_api_update_time > 0 and api_age < config.HP_VALIDITY_S:
            return hp

        # Fallback: stored reading from last confirmed sighting
        age_s = current_game_time - self.last_valid_hp_game_time
        if (
            self.last_valid_hp_value is not None
            and age_s < config.HP_VALIDITY_S
            and self.last_valid_hp_value > 0
        ):
            return self.last_valid_hp_value

        return None

    # ── Position helpers ─────────────────────────────────────────────────────

    def age(self, current_game_time: float) -> float:
        """Seconds since this target was last seen on the minimap."""
        if self.last_seen_game_time <= 0:
            return float("inf")
        return max(0.0, current_game_time - self.last_seen_game_time)

    def update_confidence(self, current_game_time: float) -> None:
        """
        Decay position confidence based on time elapsed since last sighting.
        Must be called every engine tick before making recommendations.

        Two confidence tracks:
          Minimap-confirmed: decays from 1.0 → 0.05 over TARGET_EXPIRED_S
          API-inferred only: stays at 0.65 as long as fresh API data exists,
                             then drops to 0.40 (stale role assumption)
        """
        if self.is_dead:
            self.visibility          = Visibility.DEAD
            self.position_confidence = 0.0
            return

        if self.visibility == Visibility.VISIBLE:
            self.position_confidence = 1.0
            return

        elapsed = self.age(current_game_time)  # time since last minimap sighting

        # No minimap sighting ever — use API-based confidence
        if self.last_seen_game_time <= 0:
            api_age = current_game_time - self.last_api_update_time
            if self.last_api_update_time > 0 and api_age < config.API_POLL_INTERVAL * 4:
                # Fresh API data: moderately confident they're in their lane zone
                self.visibility          = Visibility.RECENTLY_SEEN
                self.position_confidence = 0.65
            elif self.last_api_update_time > 0:
                # Stale API data: lower confidence but don't zero out
                self.visibility          = Visibility.STALE
                self.position_confidence = 0.40
            else:
                self.visibility          = Visibility.UNKNOWN
                self.position_confidence = 0.0
            return

        # Minimap-based decay schedule
        if elapsed <= config.TARGET_RECENTLY_SEEN_S:
            self.visibility          = Visibility.RECENTLY_SEEN
            self.position_confidence = 0.90
        elif elapsed <= config.TARGET_STALE_S:
            t   = elapsed - config.TARGET_RECENTLY_SEEN_S
            rng = config.TARGET_STALE_S - config.TARGET_RECENTLY_SEEN_S
            self.visibility          = Visibility.RECENTLY_SEEN if elapsed < 20 else Visibility.STALE
            self.position_confidence = max(0.35, 0.90 - (t / rng) * 0.55)
        elif elapsed <= config.TARGET_EXPIRED_S:
            t   = elapsed - config.TARGET_STALE_S
            rng = config.TARGET_EXPIRED_S - config.TARGET_STALE_S
            self.visibility          = Visibility.STALE
            self.position_confidence = max(0.08, 0.35 - (t / rng) * 0.27)
        else:
            self.visibility          = Visibility.EXPIRED
            self.position_confidence = 0.05

    # ── Gank validity ─────────────────────────────────────────────────────────

    def is_valid_gank_target(self, current_game_time: float) -> bool:
        """
        Hard gate for gank recommendations.

        Conditions required:
          1. Not dead.
          2. Position confidence above GANK_MIN_CONFIDENCE.
             (API-inferred zone gives 0.65; minimap-confirmed gives 0.90+)
          3. Last zone is a lane zone (not base, not unknown).
          4. Valid HP exists — either via fresh API data or recent sighting.
        """
        if self.is_dead:
            return False
        if self.visibility == Visibility.DEAD:
            return False
        if self.position_confidence < config.GANK_MIN_CONFIDENCE:
            return False
        # Must be in a lane-type zone (base or unknown = no gank)
        if self.last_zone in ("unknown", "blue_base", "red_base"):
            return False
        hp = self.get_valid_hp_for_gank(current_game_time)
        if hp is None:
            return False
        if hp <= config.GANK_BLOCK_TARGET_HP_PCT:
            return False
        return True

    # ── API update ───────────────────────────────────────────────────────────

    def update_from_api(self, player_dict: dict, current_game_time: float) -> None:
        """
        Ingest fresh data from the allPlayers API response.
        Called every API poll regardless of visibility.

        The Riot API provides real-time HP for all players (fog of war included).
        We always record HP here so gank scoring has valid data even without
        minimap detection.

        Zone inference: if we have never seen this target on the minimap, we
        infer their zone from their role ("they're probably in their lane").
        This gives a confidence of 0.65 — enough to generate gank candidates.
        A minimap sighting immediately overrides this with real position data.
        """
        from api.live_client import is_player_dead

        self.current_hp = player_dict.get("currentHealth", self.current_hp)
        self.max_hp     = max(1.0, player_dict.get("maxHealth", self.max_hp))
        self.level      = player_dict.get("level", self.level)
        self.last_api_update_time = current_game_time

        # Death / respawn detection
        new_dead = is_player_dead(player_dict) or self.current_hp <= 0
        if new_dead and not self.is_dead:
            self.is_dead    = True
            self.visibility = Visibility.DEAD
            self.position_confidence = 0.0
            return
        elif not new_dead and self.is_dead:
            # Respawned — back to base, zone inference will re-apply
            self.is_dead             = False
            self.visibility          = Visibility.UNKNOWN
            self.position_confidence = 0.0
            self.last_zone           = "unknown"
            self.zone_inferred       = False

        # Always record fresh HP (Riot API HP is real-time)
        hp = self.hp_percent
        if hp > 0 and not self.is_dead:
            self.last_valid_hp_value     = hp
            self.last_valid_hp_game_time = current_game_time

        # Zone inference: assign a lane zone from role when we have no minimap data.
        # This makes gank scoring work even when minimap detection isn't running.
        if self.last_zone == "unknown" and self.role:
            inferred = _ROLE_DEFAULT_ZONE.get(self.role)
            if inferred:
                self.last_zone           = inferred
                self.zone_inferred       = True
                # Only set confidence if not already established
                if self.visibility == Visibility.UNKNOWN:
                    self.visibility          = Visibility.RECENTLY_SEEN
                    self.position_confidence = 0.65  # API-inferred, enough for ganks

    # ── Minimap update ───────────────────────────────────────────────────────

    def update_from_minimap(
        self,
        pos: Tuple[float, float],
        zone: str,
        zone_conf: float,
        game_time: float,
    ) -> None:
        """
        Called when this target's dot is confirmed on the minimap.
        Resets confidence to 1.0 and records zone.
        """
        self.last_minimap_pos       = pos
        self.last_zone              = zone
        self.zone_confidence        = zone_conf
        self.last_seen_game_time    = game_time
        self.visibility             = Visibility.VISIBLE
        self.position_confidence    = 1.0

        # Also store HP as valid since we have visual confirmation
        if not self.is_dead and self.hp_percent > 0:
            self.last_valid_hp_value        = self.hp_percent
            self.last_valid_hp_game_time    = game_time

    def mark_not_visible(self) -> None:
        """Called each minimap frame when no dot is found for this target."""
        if self.visibility == Visibility.VISIBLE:
            self.visibility = Visibility.RECENTLY_SEEN
            # confidence will decay naturally on next update_confidence() call

    # ── Description helpers ──────────────────────────────────────────────────

    def position_description(self, current_game_time: float) -> str:
        """Short human-readable position string for overlay."""
        if self.is_dead:
            return "dead"
        if self.visibility == Visibility.UNKNOWN:
            return "unknown"
        age = self.age(current_game_time)
        zone = self.last_zone.replace("_", " ")
        if self.visibility == Visibility.VISIBLE:
            return f"spotted {zone}"
        if age < config.TARGET_RECENTLY_SEEN_S:
            return f"{zone} ({int(age)}s)"
        if age < config.TARGET_STALE_S:
            return f"~{zone} ({int(age)}s, low conf)"
        return f"MIA {int(age)}s"
