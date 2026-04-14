"""
Target State – confidence-aware enemy champion tracking with rich context.

Every enemy/ally champion is a TrackedTarget.  The object provides:

  POSITION CONFIDENCE
    - Three sources: minimap dot (highest), API zone inference (medium),
      kill event location (medium).
    - Confidence decays over time until next sighting.

  HP VALIDITY
    - Riot API gives real-time HP even in fog.  We always use it while
      the last poll was recent (< HP_VALIDITY_S).
    - Dead/stale HP is never surfaced to the gank scorer.

  IN-COMBAT DETECTION
    - Compare HP between consecutive polls.  A drop of >5% in 1s = being
      attacked in lane right now.  Confirms they are in their zone.

  BACK DETECTION
    - HP jumping from <60% to >90% between polls = they backed to fountain.
      Cooldown: don't recommend gank for 15s after back.

  FED / STARVED STATUS
    - kills/deaths synced from API scores each poll.
    - is_fed  = 3+ kills and kills > deaths
    - is_starved = 3+ deaths and kills < deaths

  ITEMS TRACKING
    - items_count compared each poll.  New item = "item_spiked" flag for
      2 seconds so build recommender can surface it.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import List, Optional, Tuple

import config


# ---------------------------------------------------------------------------
# Role → default zone (API-only inference)
# ---------------------------------------------------------------------------

_ROLE_DEFAULT_ZONE = {
    "TOP":     "top_lane",
    "MIDDLE":  "mid_lane",
    "BOTTOM":  "bot_lane",
    "UTILITY": "bot_lane",
    "JUNGLE":  "top_jungle",
}


# ---------------------------------------------------------------------------
# Enumerations
# ---------------------------------------------------------------------------

class Visibility(Enum):
    VISIBLE       = "visible"
    RECENTLY_SEEN = "recently_seen"
    STALE         = "stale"
    EXPIRED       = "expired"
    DEAD          = "dead"
    UNKNOWN       = "unknown"


# ---------------------------------------------------------------------------
# Core data model
# ---------------------------------------------------------------------------

@dataclass
class TrackedTarget:
    """
    One champion's tracked state.  Updated every API poll + minimap frame.
    """

    # ── Identity ─────────────────────────────────────────────────────────────
    champion:      str = ""
    summoner_name: str = ""
    team:          str = ""    # "ORDER" or "CHAOS"
    role:          str = ""    # "TOP" / "JUNGLE" / "MIDDLE" / "BOTTOM" / "UTILITY"

    # ── Live API stats (always real-time) ─────────────────────────────────────
    current_hp: float = 1000.0
    max_hp:     float = 1000.0
    level:      int   = 1
    kills:      int   = 0
    deaths:     int   = 0
    assists:    int   = 0
    cs:         int   = 0
    items:      list  = field(default_factory=list)
    items_count: int  = 0
    is_dead:    bool  = False

    # ── HP velocity (in-combat / back detection) ──────────────────────────────
    _prev_hp_pct:    float = 100.0
    hp_delta:        float = 0.0    # current_hp_pct - prev_hp_pct (negative = taking damage)
    in_combat:       bool  = False  # hp dropped >5% this poll
    back_detected:   bool  = False  # hp jumped >20% (recalled to fountain)
    item_spiked:     bool  = False  # bought new item this poll
    _back_timer:     float = 0.0    # game_time when back was detected

    # ── Position tracking ─────────────────────────────────────────────────────
    last_minimap_pos:      Optional[Tuple[float, float]] = None
    last_zone:             str   = "unknown"
    zone_confidence:       float = 0.0
    last_seen_game_time:   float = 0.0
    zone_inferred:         bool  = False   # True when zone came from role, not minimap

    # ── HP validity ───────────────────────────────────────────────────────────
    last_api_update_time:    float = 0.0
    last_valid_hp_value:     Optional[float] = None
    last_valid_hp_game_time: float = 0.0

    # ── Visibility & confidence ────────────────────────────────────────────────
    visibility:          Visibility = Visibility.UNKNOWN
    position_confidence: float      = 0.0

    # ── Derived properties ────────────────────────────────────────────────────

    @property
    def hp_percent(self) -> float:
        if self.max_hp <= 0:
            return 100.0
        return min(100.0, max(0.0, self.current_hp / self.max_hp * 100.0))

    @property
    def is_fed(self) -> bool:
        """3+ kills and more kills than deaths."""
        return self.kills >= 3 and self.kills > max(1, self.deaths)

    @property
    def is_starved(self) -> bool:
        """3+ deaths and fewer kills (behind in gold)."""
        return self.deaths >= 3 and self.kills < self.deaths

    @property
    def kda_str(self) -> str:
        return f"{self.kills}/{self.deaths}/{self.assists}"

    # ── HP validity ───────────────────────────────────────────────────────────

    def get_valid_hp_for_gank(self, current_game_time: float) -> Optional[float]:
        """
        Return HP % only when it can be trusted for gank decisions.

        Priority:
          1. Currently visible on minimap → live HP
          2. Fresh API data (< HP_VALIDITY_S) → current HP (Riot provides real-time fog HP)
          3. Recently stored reading → stored value
          4. None → HP unknown
        """
        if self.is_dead:
            return None
        hp = self.hp_percent
        if hp <= 0:
            return None
        if self.visibility == Visibility.VISIBLE:
            return hp
        api_age = current_game_time - self.last_api_update_time
        if self.last_api_update_time > 0 and api_age < config.HP_VALIDITY_S:
            return hp
        age_s = current_game_time - self.last_valid_hp_game_time
        if (self.last_valid_hp_value is not None
                and age_s < config.HP_VALIDITY_S
                and self.last_valid_hp_value > 0):
            return self.last_valid_hp_value
        return None

    # ── Gank validity gate ────────────────────────────────────────────────────

    def is_valid_gank_target(self, current_game_time: float) -> bool:
        if self.is_dead or self.visibility == Visibility.DEAD:
            return False
        if self.position_confidence < config.GANK_MIN_CONFIDENCE:
            return False
        if self.last_zone in ("unknown", "blue_base", "red_base"):
            return False
        hp = self.get_valid_hp_for_gank(current_game_time)
        if hp is None or hp <= config.GANK_BLOCK_TARGET_HP_PCT:
            return False
        return True

    # ── Position helpers ──────────────────────────────────────────────────────

    def age(self, current_game_time: float) -> float:
        if self.last_seen_game_time <= 0:
            return float("inf")
        return max(0.0, current_game_time - self.last_seen_game_time)

    # ── Update from API ───────────────────────────────────────────────────────

    def update_from_api(self, player_dict: dict, current_game_time: float) -> None:
        """
        Ingest fresh allPlayers data.  Called every API poll.

        Key improvements over v1:
          - Tracks kills/deaths/assists/cs from scores sub-dict
          - Tracks items count for item spike detection
          - Computes HP velocity (in_combat / back_detected)
          - Infers zone from role when minimap hasn't seen target
          - Records HP validity from every poll (Riot API = real-time fog HP)
        """
        from api.live_client import is_player_dead

        prev_hp = self.hp_percent
        old_items_count = self.items_count

        self.current_hp   = player_dict.get("currentHealth",  self.current_hp)
        self.max_hp       = max(1.0, player_dict.get("maxHealth", self.max_hp))
        self.level        = player_dict.get("level",           self.level)
        self.items        = player_dict.get("items",           self.items)
        self.items_count  = len(self.items)
        self.last_api_update_time = current_game_time

        # Scores (kills/deaths/assists/cs)
        sc = player_dict.get("scores", {})
        self.kills   = sc.get("kills",       self.kills)
        self.deaths  = sc.get("deaths",      self.deaths)
        self.assists = sc.get("assists",      self.assists)
        self.cs      = sc.get("creepScore",  self.cs)

        # Death / respawn detection
        new_dead = is_player_dead(player_dict) or self.current_hp <= 0
        if new_dead and not self.is_dead:
            self.is_dead    = True
            self.visibility = Visibility.DEAD
            self.position_confidence = 0.0
            self.in_combat  = False
            return
        elif not new_dead and self.is_dead:
            self.is_dead             = False
            self.visibility          = Visibility.UNKNOWN
            self.position_confidence = 0.0
            self.last_zone           = "unknown"
            self.zone_inferred       = False
            self._prev_hp_pct        = 100.0
            return

        # ── HP velocity ───────────────────────────────────────────────────────
        curr_hp = self.hp_percent
        self.hp_delta = curr_hp - prev_hp

        # In-combat: HP dropped >5% in one poll
        self.in_combat = self.hp_delta < -5.0

        # Back detection: HP jumped >20% from below 60% (fountain heal)
        if prev_hp < 60.0 and curr_hp > prev_hp + 20.0:
            self.back_detected = True
            self._back_timer   = current_game_time
        elif self.back_detected and (current_game_time - self._back_timer) > 15.0:
            self.back_detected = False   # expires after 15s

        # Item spike
        self.item_spiked = (self.items_count > old_items_count and old_items_count > 0)

        # Record valid HP
        if not self.is_dead and curr_hp > 0:
            self.last_valid_hp_value     = curr_hp
            self.last_valid_hp_game_time = current_game_time
        self._prev_hp_pct = curr_hp

        # ── Zone inference from role ───────────────────────────────────────────
        if self.last_zone == "unknown" and self.role:
            inferred = _ROLE_DEFAULT_ZONE.get(self.role)
            if inferred:
                self.last_zone           = inferred
                self.zone_inferred       = True
                if self.visibility == Visibility.UNKNOWN:
                    self.visibility          = Visibility.RECENTLY_SEEN
                    self.position_confidence = 0.65

    # ── Update from minimap ───────────────────────────────────────────────────

    def update_from_minimap(
        self,
        pos:       Tuple[float, float],
        zone:      str,
        zone_conf: float,
        game_time: float,
    ) -> None:
        """Minimap dot confirmed.  Sets visibility to VISIBLE, confidence = 1.0."""
        self.last_minimap_pos    = pos
        self.last_zone           = zone
        self.zone_confidence     = zone_conf
        self.last_seen_game_time = game_time
        self.visibility          = Visibility.VISIBLE
        self.position_confidence = 1.0
        self.zone_inferred       = False   # real data overrides inference

        if not self.is_dead and self.hp_percent > 0:
            self.last_valid_hp_value     = self.hp_percent
            self.last_valid_hp_game_time = game_time

    def mark_not_visible(self) -> None:
        if self.visibility == Visibility.VISIBLE:
            self.visibility = Visibility.RECENTLY_SEEN

    # ── Confidence decay ──────────────────────────────────────────────────────

    def update_confidence(self, current_game_time: float) -> None:
        if self.is_dead:
            self.visibility          = Visibility.DEAD
            self.position_confidence = 0.0
            return

        if self.visibility == Visibility.VISIBLE:
            self.position_confidence = 1.0
            return

        elapsed = self.age(current_game_time)

        # No minimap data — API-inferred confidence
        if self.last_seen_game_time <= 0:
            api_age = current_game_time - self.last_api_update_time
            if self.last_api_update_time > 0 and api_age < config.API_POLL_INTERVAL * 4:
                self.visibility          = Visibility.RECENTLY_SEEN
                self.position_confidence = 0.65
            elif self.last_api_update_time > 0:
                self.visibility          = Visibility.STALE
                self.position_confidence = 0.40
            else:
                self.visibility          = Visibility.UNKNOWN
                self.position_confidence = 0.0
            return

        # Minimap-based decay
        if elapsed <= config.TARGET_RECENTLY_SEEN_S:
            self.visibility          = Visibility.RECENTLY_SEEN
            self.position_confidence = 0.90
        elif elapsed <= config.TARGET_STALE_S:
            t   = elapsed - config.TARGET_RECENTLY_SEEN_S
            rng = config.TARGET_STALE_S - config.TARGET_RECENTLY_SEEN_S
            self.visibility          = Visibility.STALE
            self.position_confidence = max(0.35, 0.90 - (t / rng) * 0.55)
        elif elapsed <= config.TARGET_EXPIRED_S:
            t   = elapsed - config.TARGET_STALE_S
            rng = config.TARGET_EXPIRED_S - config.TARGET_STALE_S
            self.visibility          = Visibility.STALE
            self.position_confidence = max(0.08, 0.35 - (t / rng) * 0.27)
        else:
            self.visibility          = Visibility.EXPIRED
            self.position_confidence = 0.05

    # ── Human-readable position ───────────────────────────────────────────────

    def position_description(self, current_game_time: float) -> str:
        if self.is_dead:
            return "dead"
        if self.visibility == Visibility.UNKNOWN:
            return "unknown"
        age  = self.age(current_game_time)
        zone = self.last_zone.replace("_", " ")
        if self.visibility == Visibility.VISIBLE:
            return f"spotted {zone}"
        if self.zone_inferred:
            return f"{zone} (est.)"
        if age < config.TARGET_RECENTLY_SEEN_S:
            return f"{zone} ({int(age)}s)"
        if age < config.TARGET_STALE_S:
            return f"~{zone} ({int(age)}s)"
        return f"MIA {int(age)}s"
