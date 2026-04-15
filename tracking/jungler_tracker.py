"""
Enemy Jungler Tracker – maintains a dedicated TrackedTarget for the enemy
jungler and provides intelligence about their likely position / threat level.

Identification:
  Primary method: find the enemy player with Smite as a summoner spell.
  Fallback: find the enemy with role == "JUNGLE" if Smite check fails.

Position logic:
  - Minimap dots in jungle zones update position directly.
  - Confidence decays via TrackedTarget.update_confidence().
  - Chat message is generated only when there is high-confidence new info
    AND the side has changed since last message.

Output messages:
  "Kayn bot  (8s)"            – recently seen, short
  "Kayn missing 24s"          – stale, warning
  "Kayn MIA 38s – PLAY SAFE"  – danger
"""

from __future__ import annotations

import math
from typing import List, Optional, Tuple

import config
from tracking.target_state import TrackedTarget, Visibility
from tracking.lane_classifier import classify
from tracking.jungler_belief import JunglerBelief


class JunglerTracker:
    """Tracks the enemy jungler's identity, position, and threat level."""

    def __init__(self):
        self.enemy: TrackedTarget = TrackedTarget()
        self.ally:  TrackedTarget = TrackedTarget()
        self._identified: bool = False
        self._my_team:    str  = ""

        # Probabilistic zone belief (wraps the deterministic TrackedTarget)
        self.belief: JunglerBelief = JunglerBelief()

        # Chat de-dup: only fire a chat message when the side changes
        self._last_chat_side:      str   = ""
        self._last_chat_game_time: float = 0.0

    # ── Identification ───────────────────────────────────────────────────────

    def identify(self, player_list: list, my_summoner: str) -> bool:
        """
        Scan the player list for Smite holders.  Sets self.enemy and self.ally.
        Returns True once identification is complete.

        Identification is 100% API-based (champion name + Smite spell).
        The system does NOT try to identify champions visually from the minimap —
        minimap detection only provides position dots, not champion icons.
        """
        if self._identified and self.enemy.champion:
            return True
        if not player_list or not my_summoner:
            return False

        from api.live_client import has_smite, get_team, get_role

        def _name_matches(p: dict) -> bool:
            """Match player against my summoner name, handling Riot ID format."""
            return (
                p.get("summonerName", "") == my_summoner
                or p.get("riotIdGameName", "") == my_summoner
                or p.get("summonerName", "").split("#")[0] == my_summoner.split("#")[0]
            )

        # Find my team
        for p in player_list:
            if _name_matches(p):
                self._my_team = get_team(p)
                break
        if not self._my_team:
            return False

        enemy_team = "CHAOS" if self._my_team == "ORDER" else "ORDER"

        for p in player_list:
            team = get_team(p)
            if not has_smite(p):
                continue
            champ    = p.get("championName", "")
            summoner = p.get("summonerName",  "")
            role     = get_role(p)
            if team == self._my_team:
                self.ally.champion      = champ
                self.ally.summoner_name = summoner
                self.ally.team          = team
                self.ally.role          = role
            else:
                self.enemy.champion      = champ
                self.enemy.summoner_name = summoner
                self.enemy.team          = team
                self.enemy.role          = "JUNGLE"

        # Fallback: if Smite detection missed, look for JUNGLE role
        if not self.enemy.champion:
            for p in player_list:
                if get_team(p) == enemy_team and get_role(p) == "JUNGLE":
                    self.enemy.champion      = p.get("championName", "")
                    self.enemy.summoner_name = p.get("summonerName",  "")
                    self.enemy.team          = enemy_team
                    self.enemy.role          = "JUNGLE"
                    break

        if self.enemy.champion:
            self._identified = True
        return self._identified

    # ── Updates ─────────────────────────────────────────────────────────────

    def update_from_api(self, player_list: list, game_time: float) -> None:
        """Refresh HP and level for both junglers from the API player list."""
        for jg in (self.enemy, self.ally):
            if not jg.champion:
                continue
            for p in player_list:
                if p.get("championName") == jg.champion and p.get("team") == jg.team:
                    jg.update_from_api(p, game_time)
                    break

        # If JG confirmed dead → reset belief to base zones
        if self.enemy.is_dead:
            self.belief.reset_to_base()
        else:
            self.belief.tick(game_time)

        # Decay confidence on both
        self.enemy.update_confidence(game_time)
        self.ally.update_confidence(game_time)

    def update_from_minimap(
        self,
        positions: list,
        game_time: float,
    ) -> None:
        """
        Receive detected minimap dot positions and update the enemy JG location.

        Improvements over v1:
          1. Filter high-confidence lane-zone dots (they're almost certainly laners).
          2. Apply velocity check: reject positions requiring unrealistic movement.
          3. Pick closest-to-last-known from remaining candidates.
          4. Update both TrackedTarget AND JunglerBelief.
        """
        if not self.enemy.champion:
            return

        candidates = _filter_non_laner_positions(positions)

        # Velocity filter: remove positions that require superhuman movement
        if self.enemy.last_minimap_pos and self.enemy.last_seen_game_time > 0:
            elapsed = max(0.1, game_time - self.enemy.last_seen_game_time)
            candidates = _filter_by_velocity(
                candidates, self.enemy.last_minimap_pos, elapsed
            )

        if not candidates:
            self.enemy.mark_not_visible()
            self.belief.tick(game_time)
            return

        # Choose best candidate
        if len(candidates) == 1 or self.enemy.last_minimap_pos is None:
            pos = candidates[0]
        else:
            pos = _closest(candidates, self.enemy.last_minimap_pos)

        zone, zone_conf = classify(pos[0], pos[1])
        self.enemy.update_from_minimap(pos, zone, zone_conf, game_time)

        # Update probabilistic belief with this sighting
        self.belief.update_from_sighting(zone, zone_conf, game_time)

    def mark_all_not_visible(self) -> None:
        """Called when no minimap dots are detected at all."""
        self.enemy.mark_not_visible()

    # ── Status messages ──────────────────────────────────────────────────────

    def status_message(self, game_time: float) -> Tuple[str, str]:
        """
        Returns (message, level) where level is "info"|"warn"|"critical"|"dim".
        """
        e = self.enemy
        if not e.champion:
            return "Identifying junglers…", "dim"
        if e.is_dead:
            # Dead jungler = free map — this is actionable, show it prominently
            return f"{e.champion} DEAD – FREE MAP!", "warn"

        e.update_confidence(game_time)
        name    = e.champion
        elapsed = e.age(game_time)

        if e.visibility == Visibility.VISIBLE:
            zone = e.last_zone.replace("_", " ")
            return f"{name} spotted {zone}", "info"

        if elapsed < config.JG_AWARE_S:
            zone = e.last_zone.replace("_", " ")
            return f"{name} {zone}  ({int(elapsed)}s)", "info"

        if elapsed < config.JG_WARN_S:
            zone = e.last_zone.replace("_", " ")
            return f"{name} MIA {int(elapsed)}s  [{zone}]", "warn"

        if elapsed < config.JG_DANGER_S:
            return f"{name} MIA {int(elapsed)}s  → PLAY SAFE", "critical"

        return f"{name} MIA {int(elapsed)}s  → WARD UP", "critical"

    def safe_side(self) -> Optional[str]:
        """
        Return "top" or "bot" indicating which side is SAFER to operate on,
        based on where the enemy jungler was last seen.
        Returns None if unknown or if JG is in mid/river (ambiguous).
        """
        zone = self.enemy.last_zone
        if zone in ("top_lane", "top_jungle"):
            return "bot"   # JG is top → bot side is safer
        if zone in ("bot_lane", "bot_jungle"):
            return "top"   # JG is bot → top side is safer
        # River zones: JG is between halves, neither side is clearly safe
        return None

    def threat_side(self) -> Optional[str]:
        """
        The lane the enemy jungler is most directly threatening.
        Returns "top", "mid", or "bot", or None if unknown.
        """
        zone = self.enemy.last_zone
        # JG confirmed in a lane → that lane is directly threatened
        if zone == "mid_lane":
            return "mid"
        # River zones threaten the adjacent lane
        if zone == "top_river":
            return "top"
        if zone == "bot_river":
            return "bot"
        # Pure jungle quadrants: inverse of safe_side
        s = self.safe_side()
        if s == "top":
            return "bot"
        if s == "bot":
            return "top"
        return None

    def chat_message(self, game_time: float) -> Optional[str]:
        """
        Return a short team-chat message if there's new actionable info.
        Returns None if nothing useful to say or cooldown not elapsed.
        """
        if not self.enemy.champion:
            return None
        if self.enemy.is_dead:
            return None

        elapsed = self.enemy.age(game_time)
        if elapsed > config.JG_DANGER_S:
            # Long MIA
            if game_time - self._last_chat_game_time > config.TTS_SAME_MSG_COOLDOWN_S:
                self._last_chat_game_time = game_time
                return "jg mia"

        zone = self.enemy.last_zone
        side = None
        if zone in ("top_lane", "top_river", "top_jungle"):
            side = "top"
        elif zone in ("bot_lane", "bot_river", "bot_jungle"):
            side = "bot"
        elif zone == "mid_lane":
            side = "mid"

        if side and side != self._last_chat_side and elapsed < config.JG_AWARE_S:
            cooldown_ok = (game_time - self._last_chat_game_time) >= config.TTS_SAME_MSG_COOLDOWN_S
            if cooldown_ok:
                self._last_chat_side       = side
                self._last_chat_game_time  = game_time
                return f"{self.enemy.champion} {side}"

        return None


# ---------------------------------------------------------------------------
# Private helpers
# ---------------------------------------------------------------------------

def _filter_non_laner_positions(
    positions: list,
) -> list:
    """
    Remove dots that almost certainly belong to laners rather than the jungler.

    Two-stage filter:
      1. Geometric: exclude base corners and extreme border pixels.
      2. Zone-classifier: exclude dots whose zone is a pure lane corridor
         with confidence ≥ JG_LANER_FILTER_CONF (they're almost certainly
         laners, not the jungler).

    The zone check is intentionally lenient — we only exclude HIGH-confidence
    lane dots so we still catch the JG when they're actively ganking a lane.
    """
    filtered = []
    for pos in positions:
        x, y = pos
        # Stage 1: geometric exclusions
        if (x < 10 and y > 90) or (x > 90 and y < 10):
            continue  # deep base corners
        if x < 6 or x > 94 or y < 6 or y > 94:
            continue  # extreme border / outside minimap

        # Stage 2: zone-classifier exclusion
        zone, conf = classify(x, y)
        if conf >= config.JG_LANER_FILTER_CONF and zone in (
            "top_lane", "mid_lane", "bot_lane"
        ):
            continue  # almost certainly a laner, not the JG

        filtered.append(pos)
    return filtered


def _filter_by_velocity(
    candidates: list,
    last_pos:   Tuple[float, float],
    elapsed_s:  float,
) -> list:
    """
    Reject candidate positions that would require the JG to move faster than
    JG_MAX_MOVE_PCT_PER_S (minimap %-units per second).

    League champion base move speed ≈ 400 units/s on a ~14 000-unit map →
    ~2.9 minimap-% per second.  We add a 1.7× buffer for dashes / blinks /
    teleport, giving a cap of ~5%/s (configurable via JG_MAX_MOVE_PCT_PER_S).
    """
    max_dist = config.JG_MAX_MOVE_PCT_PER_S * elapsed_s
    rx, ry = last_pos
    return [
        p for p in candidates
        if math.sqrt((p[0] - rx) ** 2 + (p[1] - ry) ** 2) <= max_dist
    ]


def _closest(
    candidates: list,
    ref: Tuple[float, float],
) -> Tuple[float, float]:
    """Return the candidate closest (Euclidean) to a reference position."""
    rx, ry = ref
    return min(candidates, key=lambda p: (p[0] - rx) ** 2 + (p[1] - ry) ** 2)
