"""
Decision Engine – the brain that combines all data sources into actions.

Every poll cycle it:
  1. Fetches a fresh allgamedata snapshot from the Live Client API.
  2. Identifies / updates junglers.
  3. Updates minimap positions (if a MinimapTracker is supplied).
  4. Evaluates gank opportunities.
  5. Checks objective timers.
  6. Emits overlay messages and (optionally) chat messages.

The engine owns no threads itself – the caller drives it via  engine.tick().
"""

import time
from typing import Callable, List, Optional, Tuple

import api_client
import config
from jungler_tracker import JunglerTracker
from gank_advisor import GankAdvisor, LaneState

# Role string → lane label mapping from the Live Client API
_ROLE_TO_LANE = {
    "TOP":     "top",
    "MIDDLE":  "mid",
    "BOTTOM":  "bot",
    "UTILITY": "bot",   # support shares lane with ADC
    "JUNGLE":  None,    # exclude from lane states
    "":        None,
}


class DecisionEngine:
    def __init__(
        self,
        overlay_cb: Optional[Callable[[str, str], None]] = None,
        chat_cb:    Optional[Callable[[str], None]] = None,
    ):
        """
        overlay_cb(message: str, level: str)  – called with each new line.
            level: "info" | "warn" | "critical" | "dim"
        chat_cb(message: str)                 – called when a chat ping fires.
        """
        self.overlay_cb = overlay_cb
        self.chat_cb    = chat_cb

        self.jg_tracker = JunglerTracker()
        self.gank_advisor = GankAdvisor()

        # State
        self.game_time:    float = 0.0
        self.my_summoner:  str   = ""
        self.my_champion:  str   = ""
        self.my_level:     int   = 1
        self.my_hp:        float = 100.0
        self.my_team:      str   = ""

        # Dragon / baron kill tracking  (game_time of last kill)
        self._dragon_killed_at: float = 0.0
        self._baron_killed_at:  float = 0.0
        self._herald_killed_at: float = 0.0

        # Chat de-duplication
        self._last_chat_time:    float = 0.0
        self._last_chat_side:    str   = ""

        # Overlay de-duplication: only emit a message if it changed
        self._last_jg_msg:   str = ""
        self._last_gank_msg: str = ""
        self._last_obj_msg:  str = ""

    # ── Main tick ───────────────────────────────────────────────────────────

    def tick(self, minimap_tracker=None) -> List[Tuple[str, str]]:
        """
        Run one decision cycle.  Returns a list of (message, level) tuples
        that were newly emitted (also forwarded to overlay_cb if set).
        """
        data = api_client.get_all_game_data()
        if not data:
            return []

        self._ingest(data)
        if minimap_tracker:
            self._update_minimap(minimap_tracker)

        output: List[Tuple[str, str]] = []
        self._emit_jungler(output)
        self._emit_gank(data, output)
        self._emit_objectives(data, output)
        self._maybe_chat()

        # Forward to overlay callback
        if self.overlay_cb:
            for msg, lvl in output:
                self.overlay_cb(msg, lvl)

        return output

    # ── Data ingestion ──────────────────────────────────────────────────────

    def _ingest(self, data: dict):
        stats = data.get("gameData", {})
        self.game_time = stats.get("gameTime", 0.0)

        active = data.get("activePlayer", {})
        self.my_summoner = active.get("summonerName", self.my_summoner)

        player_list = data.get("allPlayers", [])

        # Identify junglers once
        if self.my_summoner:
            self.jg_tracker.identify(player_list, self.my_summoner)

        # Refresh enemy jungler stats
        self.jg_tracker.update_stats(player_list)

        # Refresh my own stats
        for p in player_list:
            if p.get("summonerName") == self.my_summoner:
                self.my_champion = p.get("championName", self.my_champion)
                self.my_level    = p.get("level", self.my_level)
                self.my_hp       = api_client.hp_percent(p)
                self.my_team     = p.get("team", self.my_team)
                self.gank_advisor.champion = self.my_champion
                break

        # Parse objective events
        for event in data.get("events", {}).get("Events", []):
            etype = event.get("EventName", "")
            etime = event.get("EventTime", 0.0)
            if etype == "DragonKill":
                self._dragon_killed_at = etime
            elif etype == "BaronKill":
                self._baron_killed_at = etime
            elif etype == "HeraldKill":
                self._herald_killed_at = etime

    def _update_minimap(self, minimap_tracker):
        _, enemy_positions = minimap_tracker.get_snapshot()
        if enemy_positions:
            self.jg_tracker.update_from_minimap(enemy_positions, self.game_time)
        else:
            self.jg_tracker.mark_not_visible()

    # ── Emitters ────────────────────────────────────────────────────────────

    def _emit_jungler(self, output: List[Tuple[str, str]]):
        msg, lvl = self.jg_tracker.status_message(self.game_time)
        if msg and msg != self._last_jg_msg:
            output.append((f"{self._ts()} {msg}", lvl))
            self._last_jg_msg = msg

    def _emit_gank(self, data: dict, output: List[Tuple[str, str]]):
        lanes = self._build_lanes(data.get("allPlayers", []))
        if not lanes:
            return

        rec = self.gank_advisor.evaluate(lanes, self.my_level)
        if rec is None:
            reason = self.gank_advisor.no_gank_reason(lanes, self.my_hp)
            # Only emit the no-gank reason on change to avoid spam
            if reason != self._last_gank_msg:
                output.append((f"{self._ts()} {reason}", "dim"))
                self._last_gank_msg = reason
        else:
            lvl = "critical" if rec.startswith("GANK") else "warn"
            if rec != self._last_gank_msg:
                output.append((f"{self._ts()} {rec}", lvl))
                self._last_gank_msg = rec

    def _emit_objectives(self, data: dict, output: List[Tuple[str, str]]):
        t = self.game_time
        warn = config.OBJECTIVE_WARN_SECS

        msg = self._objective_warning(t, warn)
        if msg and msg != self._last_obj_msg:
            output.append((f"{self._ts()} {msg}", "warn"))
            self._last_obj_msg = msg

    # ── Chat ────────────────────────────────────────────────────────────────

    def _maybe_chat(self):
        if not (config.CHAT_ENABLED and self.chat_cb):
            return
        now = self.game_time
        if now - self._last_chat_time < config.CHAT_MIN_INTERVAL:
            return

        chat_msg = self.jg_tracker.chat_message(self.game_time)
        if not chat_msg:
            return

        # Avoid repeating the same side
        new_side = self.jg_tracker.enemy.last_seen_side
        if new_side == self._last_chat_side and new_side not in ("unknown",):
            return

        self.chat_cb(chat_msg)
        self._last_chat_time = now
        self._last_chat_side = new_side

    # ── Helpers ─────────────────────────────────────────────────────────────

    def _ts(self) -> str:
        m = int(self.game_time // 60)
        s = int(self.game_time % 60)
        return f"[{m}:{s:02d}]"

    def _build_lanes(self, player_list: list) -> List[LaneState]:
        """Build LaneState objects from the current player list."""
        if not self.my_team:
            return []

        enemy_team = "CHAOS" if self.my_team == "ORDER" else "ORDER"

        enemies: dict = {}  # lane -> hp%
        allies:  dict = {}

        for p in player_list:
            team     = p.get("team", "")
            position = p.get("position", "")
            lane     = _ROLE_TO_LANE.get(position)
            if not lane:
                continue
            hp = api_client.hp_percent(p)
            if team == enemy_team:
                # Keep lowest HP per lane (most actionable)
                if lane not in enemies or hp < enemies[lane]:
                    enemies[lane] = hp
            elif team == self.my_team:
                if lane not in allies or hp < allies[lane]:
                    allies[lane] = hp

        states = []
        jg = self.jg_tracker.enemy
        elapsed = self.jg_tracker.time_since_seen(self.game_time)

        for lane in ("top", "mid", "bot"):
            if lane not in enemies:
                continue
            enemy_hp = enemies[lane]
            ally_hp  = allies.get(lane, 100.0)

            # Extended heuristic: enemy HP < 70 and not just recalled
            extended = enemy_hp < 70

            # Jungler nearby if last seen this side within threshold
            jg_nearby = (
                jg.last_seen_side in (lane, f"{lane} jungle")
                and elapsed < config.GANK_JG_NEARBY_SECONDS
            )

            states.append(LaneState(
                lane=lane,
                enemy_hp=enemy_hp,
                ally_hp=ally_hp,
                extended=extended,
                jungler_nearby=jg_nearby,
            ))

        return states

    def _objective_warning(self, t: float, warn: float) -> Optional[str]:
        """Return an objective timer warning string, or None."""
        # Dragon
        if self._dragon_killed_at > 0:
            next_dragon = self._dragon_killed_at + config.DRAGON_RESPAWN * 60
        else:
            next_dragon = config.DRAGON_FIRST_SPAWN * 60
        if 0 < next_dragon - t <= warn:
            secs = int(next_dragon - t)
            return f"Dragon in {secs}s  →  path bot"

        # Baron
        if t >= config.BARON_FIRST_SPAWN * 60:
            if self._baron_killed_at > 0:
                next_baron = self._baron_killed_at + config.BARON_RESPAWN * 60
            else:
                next_baron = config.BARON_FIRST_SPAWN * 60
            if 0 < next_baron - t <= warn:
                secs = int(next_baron - t)
                return f"Baron in {secs}s  →  prepare"

        # Rift Herald
        if t < config.RIFT_HERALD_DESPAWN * 60 and t >= config.RIFT_HERALD_SPAWN * 60:
            if self._herald_killed_at == 0:
                # Herald is alive
                time_left = config.RIFT_HERALD_DESPAWN * 60 - t
                if 0 < time_left <= warn:
                    return f"Herald despawns in {int(time_left)}s  →  contest now"

        return None
