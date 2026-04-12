"""
Shared Game State – single source of truth for all engine threads.

All fields are protected by a single RLock.  The API thread writes to it;
the overlay, voice, and pathing threads read from it.

Design principles:
  - Never store stale data without marking it as such.
  - HP is always the live API value.  Gank-validity for HP is evaluated
    at decision time via TrackedTarget.get_valid_hp_for_gank().
  - The state object does NOT make decisions – it only stores facts.
"""

from __future__ import annotations

import threading
from dataclasses import dataclass, field
from typing import Dict, List, Optional

from tracking.target_state import TrackedTarget, Visibility
from tracking.jungler_tracker import JunglerTracker


@dataclass
class MyState:
    """State for the local (our) player."""
    summoner_name:  str   = ""
    champion:       str   = ""
    team:           str   = ""   # "ORDER" or "CHAOS"
    hp_percent:     float = 100.0
    mana_percent:   float = 100.0
    gold:           float = 0.0
    level:          int   = 1
    kills:          int   = 0
    deaths:         int   = 0
    assists:        int   = 0
    cs:             int   = 0
    items:          list  = field(default_factory=list)


@dataclass
class ObjectiveTimers:
    """Tracks last-kill timestamps for major objectives."""
    dragon_killed_at:  float = 0.0   # game-time in seconds
    baron_killed_at:   float = 0.0
    herald_killed_at:  float = 0.0
    dragon_count:      int   = 0     # how many dragons our team has taken
    enemy_dragon_count:int   = 0


class GameState:
    """
    Central shared state.  All public methods are thread-safe.

    Usage:
        state = GameState()
        # Writer (API thread):
        state.update_from_api_data(api_dict)
        # Reader (decision thread):
        with state.lock:
            hp = state.me.hp_percent
    """

    def __init__(self):
        self.lock = threading.RLock()

        # Core state objects
        self.me                 = MyState()
        self.jg_tracker         = JunglerTracker()
        self.enemies:  Dict[str, TrackedTarget] = {}  # champion_name → target
        self.allies:   Dict[str, TrackedTarget] = {}

        # Scalars
        self.game_time: float = 0.0
        self.game_mode: str   = "CLASSIC"

        # Objectives
        self.objectives = ObjectiveTimers()

        # Raw last snapshot for modules that need full access
        self._last_api_snapshot: Optional[dict] = None

    # ── Writer methods (called by API thread) ─────────────────────────────

    def update_from_api(self, data: dict) -> None:
        """
        Ingest a full allgamedata snapshot.
        Thread-safe – acquires lock internally.
        """
        from api import live_client as lc

        if not data:
            return

        with self.lock:
            self._last_api_snapshot = data

            # Game time
            gd = data.get("gameData", {})
            self.game_time = gd.get("gameTime", self.game_time)
            self.game_mode = gd.get("gameMode", self.game_mode)

            active = data.get("activePlayer", {})
            players: List[dict] = data.get("allPlayers", [])

            # ── My state ────────────────────────────────────────────────────
            self.me.summoner_name = active.get("summonerName", self.me.summoner_name)
            self.me.gold          = active.get("currentGold", self.me.gold)

            for p in players:
                if lc.get_summoner_name(p) == self.me.summoner_name:
                    self.me.champion    = lc.get_champion_name(p)
                    self.me.team        = lc.get_team(p)
                    self.me.level       = lc.get_level(p)
                    self.me.hp_percent  = lc.hp_percent(p)
                    scores              = lc.get_scores(p)
                    self.me.kills       = scores.get("kills",   0)
                    self.me.deaths      = scores.get("deaths",  0)
                    self.me.assists     = scores.get("assists", 0)
                    self.me.cs          = scores.get("creepScore", 0)
                    self.me.items       = lc.get_items(p)
                    # Mana (if available)
                    stats = p.get("championStats", {})
                    max_res = stats.get("resourceMax", 0)
                    if max_res > 0:
                        cur_res = stats.get("resourceValue", max_res)
                        self.me.mana_percent = (cur_res / max_res) * 100.0
                    break

            # ── Enemy + ally targets ────────────────────────────────────────
            my_team    = self.me.team
            enemy_team = "CHAOS" if my_team == "ORDER" else "ORDER"

            for p in players:
                champ   = lc.get_champion_name(p)
                team    = lc.get_team(p)
                summ    = lc.get_summoner_name(p)
                role    = lc.get_role(p)

                if summ == self.me.summoner_name:
                    continue  # skip ourselves

                target_dict = self.enemies if team == enemy_team else self.allies

                if champ not in target_dict:
                    target_dict[champ] = TrackedTarget(
                        champion      = champ,
                        summoner_name = summ,
                        team          = team,
                        role          = role,
                    )

                target_dict[champ].update_from_api(p, self.game_time)

            # ── Jungler identification ──────────────────────────────────────
            self.jg_tracker.identify(players, self.me.summoner_name)
            self.jg_tracker.update_from_api(players, self.game_time)

            # ── Objective events ────────────────────────────────────────────
            events: list = data.get("events", {}).get("Events", [])
            my_team_lower = my_team.lower()
            for ev in events:
                etype = ev.get("EventName", "")
                etime = ev.get("EventTime", 0.0)
                killer_team = ev.get("KillerName", "").lower()  # approximate

                if etype == "DragonKill":
                    self.objectives.dragon_killed_at = etime
                    if "order" in killer_team or my_team_lower == "order":
                        self.objectives.dragon_count += 0  # updated below
                    self.objectives.dragon_count += 1

                elif etype == "BaronKill":
                    self.objectives.baron_killed_at = etime

                elif etype == "HeraldKill":
                    self.objectives.herald_killed_at = etime

            # ── Target confidence decay ─────────────────────────────────────
            for tgt in list(self.enemies.values()) + list(self.allies.values()):
                tgt.update_confidence(self.game_time)

    def update_minimap(self, positions: list) -> None:
        """
        Called by the minimap thread with detected dot positions.
        Updates enemy target visibility and jungler tracker.
        """
        with self.lock:
            gt = self.game_time
            if positions:
                self.jg_tracker.update_from_minimap(positions, gt)
            else:
                self.jg_tracker.mark_all_not_visible()
                for tgt in self.enemies.values():
                    tgt.mark_not_visible()

    # ── Reader helpers (acquire lock before calling) ───────────────────────

    def get_enemy_by_role(self, role: str) -> Optional[TrackedTarget]:
        """Return the first enemy with the given API role, or None."""
        # role: "TOP" / "JUNGLE" / "MIDDLE" / "BOTTOM" / "UTILITY"
        with self.lock:
            for tgt in self.enemies.values():
                if tgt.role == role:
                    return tgt
        return None

    def get_enemies_by_lane(self, lane: str) -> List[TrackedTarget]:
        """
        Return enemy targets associated with a lane name.
        lane: "top" / "mid" / "bot"
        """
        role_map = {"top": ["TOP"], "mid": ["MIDDLE"], "bot": ["BOTTOM", "UTILITY"]}
        roles = role_map.get(lane, [])
        with self.lock:
            return [t for t in self.enemies.values() if t.role in roles]

    def snapshot_time(self) -> float:
        """Return current game time safely."""
        with self.lock:
            return self.game_time
