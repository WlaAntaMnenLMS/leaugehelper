"""
Shared Game State – single source of truth for all engine threads.

All fields are protected by a single RLock.  The API thread writes to it;
the overlay, voice, and pathing threads read from it.

Design principles:
  - Never store stale data without marking it as such.
  - HP is always the live API value.  Gank-validity for HP is evaluated
    at decision time via TrackedTarget.get_valid_hp_for_gank().
  - The state object does NOT make decisions – it only stores facts.
  - Events are processed exactly once via EventProcessor (stateful, dedup).
  - Alerts from KillFeedTracker are enqueued here for the decision engine.
"""

from __future__ import annotations

import threading
from collections import deque
from dataclasses import dataclass, field
from typing import Deque, Dict, List, Optional, Tuple

from tracking.target_state import TrackedTarget, Visibility
from tracking.jungler_tracker import JunglerTracker
from api.event_processor import EventProcessor
from tracking.kill_feed import KillFeedTracker


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
    dragon_killed_at:   float = 0.0   # game-time in seconds
    baron_killed_at:    float = 0.0
    herald_killed_at:   float = 0.0
    dragon_count:       int   = 0     # how many dragons OUR team has taken
    enemy_dragon_count: int   = 0     # how many dragons ENEMY team has taken


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

        # Event processing (stateful – processes each event exactly once)
        self.event_proc = EventProcessor()
        self.kill_feed  = KillFeedTracker()

        # Alert queue: (message, level) tuples for the overlay ALERT slot
        # Capacity-capped deque – oldest alert drops if we fill up
        self.alerts: Deque[Tuple[str, str]] = deque(maxlen=8)

        # player_map for event attribution: summonerName → team
        self._player_map: Dict[str, str] = {}

        # Scalars
        self.game_time: float = 0.0
        self.game_mode: str   = "CLASSIC"

        # Objectives (synced from event_proc each poll)
        self.objectives = ObjectiveTimers()

        # Raw last snapshot for modules that need full access
        self._last_api_snapshot: Optional[dict] = None

    # ── Writer methods (called by API thread) ─────────────────────────────

    def update_from_api(self, data: dict) -> None:
        """
        Ingest a full allgamedata snapshot.
        Thread-safe – acquires lock internally.

        Order of operations:
          1. Update game time and mode.
          2. Update my own stats.
          3. Upsert all enemy/ally TrackedTargets.
          4. Identify junglers.
          5. Build player_map and process events (ONCE each, via EventProcessor).
          6. Feed new events to KillFeedTracker → generate alerts.
          7. Sync objective timers from EventProcessor's authoritative data.
          8. Decay position confidence for all tracked targets.
        """
        from api import live_client as lc

        if not data:
            return

        with self.lock:
            self._last_api_snapshot = data

            # ── 1. Game time / mode ──────────────────────────────────────────
            gd = data.get("gameData", {})
            self.game_time = gd.get("gameTime", self.game_time)
            self.game_mode = gd.get("gameMode", self.game_mode)

            active  = data.get("activePlayer", {})
            players: List[dict] = data.get("allPlayers", [])

            # ── 2. My state ──────────────────────────────────────────────────
            self.me.summoner_name = active.get("summonerName", self.me.summoner_name)
            self.me.gold          = active.get("currentGold", self.me.gold)

            for p in players:
                if lc.get_summoner_name(p) == self.me.summoner_name:
                    self.me.champion    = lc.get_champion_name(p)
                    self.me.team        = lc.get_team(p)
                    self.me.level       = lc.get_level(p)
                    self.me.hp_percent  = lc.hp_percent(p)
                    scores              = lc.get_scores(p)
                    self.me.kills       = scores.get("kills",       0)
                    self.me.deaths      = scores.get("deaths",      0)
                    self.me.assists     = scores.get("assists",      0)
                    self.me.cs          = scores.get("creepScore",   0)
                    self.me.items       = lc.get_items(p)
                    # Mana / energy (if available)
                    stats   = p.get("championStats", {})
                    max_res = stats.get("resourceMax", 0)
                    if max_res > 0:
                        cur_res = stats.get("resourceValue", max_res)
                        self.me.mana_percent = (cur_res / max_res) * 100.0
                    break

            # ── 3. Enemy + ally TrackedTargets ───────────────────────────────
            my_team    = self.me.team
            enemy_team = "CHAOS" if my_team == "ORDER" else "ORDER"

            for p in players:
                champ = lc.get_champion_name(p)
                team  = lc.get_team(p)
                summ  = lc.get_summoner_name(p)
                role  = lc.get_role(p)

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

            # ── 4. Jungler identification ────────────────────────────────────
            self.jg_tracker.identify(players, self.me.summoner_name)
            self.jg_tracker.update_from_api(players, self.game_time)

            # ── 5. Event processing ──────────────────────────────────────────
            # Build player_map (summonerName → team) for kill attribution
            self._player_map = {
                lc.get_summoner_name(p): lc.get_team(p)
                for p in players
                if lc.get_summoner_name(p)
            }

            events_list = data.get("events", {}).get("Events", [])
            new_events  = self.event_proc.process(
                events_list, my_team, self._player_map
            )

            # ── 6. Kill-feed alerts ──────────────────────────────────────────
            new_alerts = self.kill_feed.update(
                new_events, self.enemies, self.allies, self.game_time, my_team
            )
            for msg, lvl in new_alerts:
                self.alerts.append((msg, lvl))

            # ── 7. Sync objective timers from EventProcessor ─────────────────
            # event_proc is the authoritative source (processes each event once)
            self.objectives.dragon_killed_at  = self.event_proc.dragon_killed_at
            self.objectives.baron_killed_at   = self.event_proc.baron_taken_at
            self.objectives.herald_killed_at  = self.event_proc.herald_killed_at
            self.objectives.dragon_count      = len(self.event_proc.our_drakes)
            self.objectives.enemy_dragon_count = len(self.event_proc.enemy_drakes)

            # ── 8. Confidence decay ──────────────────────────────────────────
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

    def pop_alerts(self) -> List[Tuple[str, str]]:
        """
        Drain and return all pending alerts.
        Thread-safe.  Returns list of (message, level).
        """
        with self.lock:
            items = list(self.alerts)
            self.alerts.clear()
        return items

    # ── Reader helpers ─────────────────────────────────────────────────────

    def get_enemy_by_role(self, role: str) -> Optional[TrackedTarget]:
        """Return the first enemy with the given API role, or None."""
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
