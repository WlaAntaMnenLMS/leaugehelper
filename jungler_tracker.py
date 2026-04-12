"""
Enemy Jungler Intelligence – the PRIMARY feature.

Responsibilities
----------------
1. Identify who the enemy jungler is (player with Smite on the enemy team).
2. Track their last-seen position on the minimap.
3. Calculate time-since-seen.
4. Translate minimap coords → human map side (top / mid / bot / jungle).
5. Produce a short status string for the overlay.
6. Decide whether a chat ping message is appropriate.

Map-side logic
--------------
League's minimap (bottom-right corner) maps to the actual map like this:

  Minimap (0,0) = top-left = the **top lane / blue-side base** area
  Minimap (100,100) = bottom-right = **bot lane / red-side base** area

  The top lane runs roughly along the left edge (low X).
  The bot lane runs roughly along the bottom edge (high Y).
  The river cuts diagonally from top-right to bottom-left.

  We divide the minimap into zones:
    y < 30              → "top"
    y > 70              → "bot"
    30 ≤ y ≤ 70, x < 40 → "top jungle"
    30 ≤ y ≤ 70, x > 60 → "bot jungle"
    otherwise           → "mid"
"""

import time
from dataclasses import dataclass, field
from typing import List, Optional, Tuple

import config
import api_client


@dataclass
class JunglerState:
    champion: str = ""
    summoner_name: str = ""

    # Tracking
    last_seen_game_time: float = 0.0        # game-clock seconds when last spotted
    last_seen_side: str = "unknown"          # "top", "bot", "mid", "top jungle", "bot jungle"
    last_seen_pos: Tuple[float, float] = (0.0, 0.0)  # minimap 0-100
    is_visible: bool = False

    # Stats from API
    hp_percent: float = 100.0
    level: int = 1


class JunglerTracker:
    def __init__(self):
        self.enemy: JunglerState = JunglerState()
        self.ally: JunglerState = JunglerState()
        self._identified: bool = False
        self._my_team: str = ""   # "ORDER" or "CHAOS"

    # ── Identification ──────────────────────────────────────────────────────

    def identify(self, player_list: list, my_summoner: str) -> bool:
        """
        Scan the player list for Smite holders to find both junglers.
        Marks _identified = True once the enemy jungler is found.
        Returns True when identification is complete.
        """
        if self._identified:
            return True
        if not player_list or not my_summoner:
            return False

        # Find my team first
        for p in player_list:
            if p.get("summonerName") == my_summoner:
                self._my_team = p.get("team", "")
                break
        if not self._my_team:
            return False

        for p in player_list:
            if not api_client.has_smite(p):
                continue
            team = p.get("team", "")
            champ = p.get("championName", "Unknown")
            summoner = p.get("summonerName", "")
            if team == self._my_team:
                self.ally.champion = champ
                self.ally.summoner_name = summoner
            else:
                self.enemy.champion = champ
                self.enemy.summoner_name = summoner

        if self.enemy.champion:
            self._identified = True
        return self._identified

    # ── Update from API ─────────────────────────────────────────────────────

    def update_stats(self, player_list: list):
        """Refresh enemy jungler HP and level from the API player list."""
        if not self.enemy.champion:
            return
        for p in player_list:
            if p.get("championName") == self.enemy.champion:
                self.enemy.hp_percent = api_client.hp_percent(p)
                self.enemy.level = api_client.level_of(p)
                break

    # ── Update from minimap ─────────────────────────────────────────────────

    def update_from_minimap(self, enemy_positions: List[Tuple[float, float]], game_time: float):
        """
        Receive a list of detected enemy dot positions (0-100 scale) from the
        minimap tracker and attempt to update the jungler's location.

        Strategy: we exclude positions that clearly belong to laners
        (top-left corner = top laner, bottom-right = bot lane, etc.) and
        treat what's left as possible jungler positions.  When only one
        position is left it's assigned directly; when multiple remain we
        pick the one closest to the last known position.
        """
        if not enemy_positions:
            self.enemy.is_visible = False
            return

        candidates = _filter_jungle_positions(enemy_positions)

        if not candidates:
            # All dots look like laners – treat jungler as not visible
            self.enemy.is_visible = False
            return

        # Pick best candidate
        if len(candidates) == 1 or self.enemy.last_seen_game_time == 0:
            pos = candidates[0]
        else:
            pos = _closest(candidates, self.enemy.last_seen_pos)

        self.enemy.last_seen_game_time = game_time
        self.enemy.last_seen_pos = pos
        self.enemy.last_seen_side = _pos_to_side(pos)
        self.enemy.is_visible = True

    def mark_not_visible(self):
        self.enemy.is_visible = False

    # ── Status output ───────────────────────────────────────────────────────

    def time_since_seen(self, game_time: float) -> float:
        if self.enemy.last_seen_game_time <= 0:
            return 9999.0
        return max(0.0, game_time - self.enemy.last_seen_game_time)

    def status_message(self, game_time: float) -> Tuple[str, str]:
        """
        Returns (message, level) where level is "info" | "warn" | "critical".
        """
        name = self.enemy.champion or "Enemy JG"
        elapsed = self.time_since_seen(game_time)

        if not self.enemy.champion:
            return "Identifying junglers...", "dim"

        if self.enemy.is_visible:
            side = self.enemy.last_seen_side
            return f"{name} spotted {side}", "info"

        if elapsed < config.JUNGLER_AWARE_SECONDS:
            side = self.enemy.last_seen_side
            return f"{name} last seen {side}  ({int(elapsed)}s ago)", "info"

        if elapsed < config.JUNGLER_WARN_SECONDS:
            side = self.enemy.last_seen_side
            return f"{name} missing {int(elapsed)}s  [{side}]  →  stay aware", "warn"

        if elapsed < config.JUNGLER_DANGER_SECONDS:
            return f"{name} MISSING {int(elapsed)}s  →  PLAY SAFE", "critical"

        return f"{name} MISSING {int(elapsed)}s  →  ward & play safe", "critical"

    def chat_message(self, game_time: float) -> Optional[str]:
        """
        Return a short human-readable chat string when there is new actionable
        info, otherwise None.  The decision engine enforces cooldowns.
        """
        if not self.enemy.champion:
            return None
        name = self.enemy.champion
        elapsed = self.time_since_seen(game_time)
        side = self.enemy.last_seen_side

        if elapsed > config.JUNGLER_DANGER_SECONDS:
            return "jg mia"
        if side in ("top", "top jungle") and elapsed < config.JUNGLER_AWARE_SECONDS:
            return f"{name} top"
        if side in ("bot", "bot jungle") and elapsed < config.JUNGLER_AWARE_SECONDS:
            return f"{name} bot"
        if side == "mid" and elapsed < config.JUNGLER_AWARE_SECONDS:
            return f"{name} mid"
        return None


# ── Private helpers ─────────────────────────────────────────────────────────

def _pos_to_side(pos: Tuple[float, float]) -> str:
    """Map a minimap (x, y) in 0-100 to a human side label."""
    x, y = pos
    if y < config.ZONE_TOP_Y:
        return "top"
    if y > config.ZONE_BOT_Y:
        return "bot"
    if x < config.ZONE_LEFT_X:
        return "top jungle"
    if x > config.ZONE_RIGHT_X:
        return "bot jungle"
    return "mid"


def _filter_jungle_positions(
    positions: List[Tuple[float, float]],
) -> List[Tuple[float, float]]:
    """
    Remove positions that look like they belong to laners rather than the
    jungler.  Laners tend to cluster along the lane corridors:
      - Top lane:  x < 15  or  y < 15
      - Bot lane:  x > 85  or  y > 85
      - Mid lane:  close to the diagonal

    This is a heuristic filter – imperfect but cuts false positives.
    """
    filtered = []
    for pos in positions:
        x, y = pos
        # Extreme corners = almost certainly a laner
        if x < 10 and y < 10:
            continue
        if x > 90 and y > 90:
            continue
        # Base areas
        if x < 8 or y < 8:
            continue
        if x > 92 or y > 92:
            continue
        filtered.append(pos)
    return filtered


def _closest(
    candidates: List[Tuple[float, float]],
    reference: Tuple[float, float],
) -> Tuple[float, float]:
    """Return the candidate closest (Euclidean) to the reference position."""
    rx, ry = reference
    return min(candidates, key=lambda p: (p[0] - rx) ** 2 + (p[1] - ry) ** 2)
