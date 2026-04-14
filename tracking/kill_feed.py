"""
Kill Feed Tracker – maintains recent kill history and surfaces
strategic context for the decision engine.

What this enables:
  - Know which enemies are fed (avoid or gank with care)
  - Know which allies just died (roam warning)
  - Know when a kill happened in a lane (reset opportunity)
  - Generate alert messages for the overlay

This module reads from EventProcessor and adds lane context.
"""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass
from typing import Deque, Dict, List, Optional, Tuple

from api.event_processor import GameEvent


@dataclass
class KillEntry:
    killer:    str
    victim:    str
    assists:   list
    game_time: float
    lane:      str = ""     # inferred from victim's role


class KillFeedTracker:
    """
    Tracks all kill events and generates human-readable alerts.

    Usage:
        kf = KillFeedTracker()
        alerts = kf.update(event_proc.new_events, enemies, allies, game_time)
    """

    HISTORY_SIZE = 20   # keep last 20 kills

    def __init__(self):
        self._history: Deque[KillEntry] = deque(maxlen=self.HISTORY_SIZE)
        self._pending_alerts: List[Tuple[str, str]] = []   # (message, level)
        self._my_team: str = ""

    def update(
        self,
        new_events: List[GameEvent],
        enemies:    dict,
        allies:     dict,
        game_time:  float,
        my_team:    str = "",
    ) -> List[Tuple[str, str]]:
        """
        Process new events.  Returns list of (alert_message, level) tuples.
        """
        if my_team:
            self._my_team = my_team

        alerts: List[Tuple[str, str]] = []

        enemy_summoners  = {t.summoner_name: t for t in enemies.values()}
        ally_summoners   = {t.summoner_name: t for t in allies.values()}
        enemy_champions  = {t.champion.lower(): t for t in enemies.values()}
        ally_champions   = {t.champion.lower(): t for t in allies.values()}

        def _find_target(name: str):
            t = enemy_summoners.get(name) or enemy_champions.get(name.lower())
            if t:
                return t, "enemy"
            t = ally_summoners.get(name) or ally_champions.get(name.lower())
            if t:
                return t, "ally"
            return None, ""

        for ev in new_events:
            if ev.event_type == "firstblood":
                victim = ev.data.get("victim", "")
                t, side = _find_target(victim)
                if side == "enemy":
                    alerts.append(("First blood – enemy down, push advantage!", "warn"))
                else:
                    alerts.append(("First blood TAKEN – play safe, tilt incoming", "critical"))

            elif ev.event_type == "kill":
                killer = ev.data["killer"]
                victim = ev.data["victim"]
                k_t, k_side = _find_target(killer)
                v_t, v_side = _find_target(victim)

                lane = ""
                if v_t:
                    lane = _role_to_lane(v_t.role)

                entry = KillEntry(killer, victim, ev.data.get("assists", []),
                                  ev.game_time, lane)
                self._history.append(entry)

                # Alert: ally killed → enemy will roam
                if v_side == "ally" and v_t:
                    lane_name = lane or v_t.role.lower()
                    alerts.append((
                        f"Ally {v_t.champion} died – {lane_name} open",
                        "warn",
                    ))

                # Alert: enemy fed (killed an ally for their 3rd+ kill)
                if k_side == "enemy" and k_t:
                    from api.event_processor import EventProcessor
                    # We rely on event_proc.is_fed(), surfaced by decision engine

            elif ev.event_type == "multikill":
                killer = ev.data["killer"]
                kind   = ev.data["kind"]   # "Double", "Triple", "Quadra", "Penta"
                k_t, k_side = _find_target(killer)
                if k_side == "enemy":
                    alerts.append((
                        f"ENEMY {killer} {kind} KILL – back off!",
                        "critical",
                    ))
                else:
                    alerts.append((
                        f"{killer} {kind} kill – extend the lead!",
                        "warn",
                    ))

            elif ev.event_type == "ace":
                winning = ev.data.get("winning_team", "")
                if winning == self._my_team:
                    alerts.append(("TEAM ACE – take Baron/Dragon NOW!", "critical"))
                else:
                    alerts.append(("Team got aced – play safe, disengage", "critical"))

            elif ev.event_type == "dragon":
                stolen   = ev.data.get("stolen", False)
                our_cnt  = ev.data.get("our_count", 0)
                their_cnt = ev.data.get("enemy_count", 0)
                if stolen:
                    alerts.append(("DRAGON STOLEN!", "critical"))
                elif their_cnt >= 3:
                    alerts.append((
                        f"Enemy at {their_cnt} drakes – STOP NEXT DRAGON",
                        "critical",
                    ))
                elif their_cnt == 2:
                    alerts.append((
                        f"Enemy 2 drakes – contest next dragon hard",
                        "warn",
                    ))

            elif ev.event_type == "baron":
                stolen = ev.data.get("stolen", False)
                team   = ev.data.get("team", "")
                if stolen:
                    alerts.append(("BARON STOLEN! Reset and defend.", "critical"))
                elif team != self._my_team:
                    alerts.append(("Enemy took Baron – disengage, turtle", "critical"))
                else:
                    alerts.append(("Baron secured – push side lanes!", "warn"))

            elif ev.event_type == "herald":
                team = ev.data.get("team", "")
                if team == self._my_team:
                    alerts.append(("Herald secured – use it to take turret", "info"))

        return alerts

    # ── Query helpers ─────────────────────────────────────────────────────────

    def ally_died_recently(self, lane: str, game_time: float, window: float = 60.0) -> bool:
        """Did an ally in this lane die in the last window seconds?"""
        for entry in reversed(self._history):
            if entry.lane == lane and (game_time - entry.game_time) < window:
                return True
        return False

    def recent_kill_in_lane(self, lane: str, game_time: float, window: float = 60.0) -> bool:
        """Was there any kill in this lane in the last window seconds?"""
        for entry in reversed(self._history):
            if entry.lane == lane and (game_time - entry.game_time) < window:
                return True
        return False


def _role_to_lane(role: str) -> str:
    return {"TOP": "top", "MIDDLE": "mid", "BOTTOM": "bot",
            "UTILITY": "bot", "JUNGLE": "jungle"}.get(role, "")
