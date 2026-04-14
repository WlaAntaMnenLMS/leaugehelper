"""
Event Processor – parses the Riot Live Client Events list into
structured, deduplicated events that drive real-time state updates.

The processor is fully stateful: it tracks the highest seen EventID
so each event fires exactly once regardless of how often we poll.

Emitted event types:
  "kill"      – champion killed (killer, victim, assists, position)
  "dragon"    – dragon taken (team, dragon_type, our_stack, enemy_stack)
  "baron"     – baron taken (team, game_time)
  "herald"    – herald taken (team, game_time)
  "multikill" – double/triple/quadra/penta (killer, kind)
  "ace"       – team aced (winning_team, game_time)
  "firstblood"– first blood (killer, victim)
  "turret"    – turret destroyed
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple


@dataclass
class GameEvent:
    event_id:   int
    event_type: str        # "kill", "dragon", "baron", etc.
    game_time:  float
    data:       dict = field(default_factory=dict)


class EventProcessor:
    """
    Stateful event parser.  Call process(events_list) each API poll.
    Keeps cumulative kill/death/item counts for all champions seen.
    """

    def __init__(self):
        self._last_event_id:  int   = -1
        self._first_blood_fired: bool = False

        # Per-champion cumulative stats (keyed by summonerName or championName)
        self.kill_counts:      Dict[str, int]   = {}
        self.death_counts:     Dict[str, int]   = {}
        self.assist_counts:    Dict[str, int]   = {}
        self.last_death_time:  Dict[str, float] = {}

        # Dragon tracking (team → list of dragon types taken)
        self.our_drakes:    List[str] = []
        self.enemy_drakes:  List[str] = []
        self._my_team: str = ""      # set by game_state on first API parse

        # Objective kill timestamps (game-time seconds of last kill)
        self.dragon_killed_at:  float = 0.0
        self.herald_killed_at:  float = 0.0

        # Baron buff
        self.baron_taken_at:   float = 0.0
        self.baron_taken_team: str   = ""

        # Processed events list for this tick
        self.new_events: List[GameEvent] = []

    # ── Main entry point ─────────────────────────────────────────────────────

    def process(
        self,
        events_list: list,
        my_team: str = "",
        player_map: Optional[Dict[str, str]] = None,   # summonerName → team
    ) -> List[GameEvent]:
        """
        Process the events array from allgamedata.
        Returns ONLY new events since last call (each fires exactly once).

        player_map: optional {summonerName: team} to attribute kills to teams.
        """
        self.new_events = []
        if my_team:
            self._my_team = my_team

        for ev in events_list:
            eid   = ev.get("EventID", -1)
            ename = ev.get("EventName", "")
            etime = float(ev.get("EventTime", 0.0))

            if eid <= self._last_event_id:
                continue

            self._last_event_id = max(self._last_event_id, eid)
            parsed = self._parse_event(eid, ename, etime, ev, player_map or {})
            if parsed:
                self.new_events.append(parsed)

        return self.new_events

    def _parse_event(
        self,
        eid: int,
        ename: str,
        etime: float,
        raw: dict,
        player_map: Dict[str, str],
    ) -> Optional[GameEvent]:

        if ename == "FirstBlood":
            recipient = raw.get("Recipient", "")
            self._first_blood_fired = True
            return GameEvent(eid, "firstblood", etime,
                             {"victim": recipient})

        if ename == "ChampionKill":
            killer  = raw.get("KillerName", "")
            victim  = raw.get("VictimName", "")
            assists = raw.get("Assisters", [])

            # Update cumulative stats
            if killer:
                self.kill_counts[killer]  = self.kill_counts.get(killer, 0) + 1
            if victim:
                self.death_counts[victim] = self.death_counts.get(victim, 0) + 1
                self.last_death_time[victim] = etime
            for a in assists:
                self.assist_counts[a] = self.assist_counts.get(a, 0) + 1

            return GameEvent(eid, "kill", etime,
                             {"killer": killer, "victim": victim, "assists": assists})

        if ename in ("DoubleKill", "TripleKill", "QuadraKill", "PentaKill"):
            killer = raw.get("KillerName", "")
            return GameEvent(eid, "multikill", etime,
                             {"killer": killer, "kind": ename.replace("Kill", "")})

        if ename == "DragonKill":
            killer      = raw.get("KillerName", "")
            dragon_type = raw.get("DragonType", "Unknown")
            is_steal    = raw.get("Stolen", False)
            killer_team = player_map.get(killer, "")
            self.dragon_killed_at = etime
            if killer_team == self._my_team:
                self.our_drakes.append(dragon_type)
            elif killer_team:
                self.enemy_drakes.append(dragon_type)
            return GameEvent(eid, "dragon", etime, {
                "team":        killer_team,
                "dragon_type": dragon_type,
                "stolen":      is_steal,
                "our_count":   len(self.our_drakes),
                "enemy_count": len(self.enemy_drakes),
            })

        if ename == "BaronKill":
            killer      = raw.get("KillerName", "")
            is_steal    = raw.get("Stolen", False)
            killer_team = player_map.get(killer, "")
            self.baron_taken_at   = etime
            self.baron_taken_team = killer_team
            return GameEvent(eid, "baron", etime, {
                "team":   killer_team,
                "stolen": is_steal,
            })

        if ename in ("HeraldKill", "RiftHeraldKill"):
            killer      = raw.get("KillerName", "")
            killer_team = player_map.get(killer, "")
            self.herald_killed_at = etime
            return GameEvent(eid, "herald", etime, {"team": killer_team})

        if ename == "Ace":
            return GameEvent(eid, "ace", etime,
                             {"winning_team": raw.get("AcingTeam", "")})

        if ename in ("TurretKilled", "BuildingKill"):
            return GameEvent(eid, "turret", etime,
                             {"killer": raw.get("KillerName", "")})

        return None

    # ── Query helpers ────────────────────────────────────────────────────────

    def kills(self, name: str) -> int:
        return self.kill_counts.get(name, 0)

    def deaths(self, name: str) -> int:
        return self.death_counts.get(name, 0)

    def is_fed(self, name: str, min_kills: int = 3) -> bool:
        """Fed = 3+ kills AND more kills than deaths."""
        k = self.kills(name)
        d = self.deaths(name)
        return k >= min_kills and k > d

    def is_starved(self, name: str) -> bool:
        """Starved = 3+ deaths with fewer kills (behind in gold)."""
        d = self.deaths(name)
        k = self.kills(name)
        return d >= 3 and k < d

    def recently_died(self, name: str, game_time: float, window: float = 45.0) -> bool:
        last = self.last_death_time.get(name, -999.0)
        return (game_time - last) <= window

    def baron_buff_active(self, game_time: float) -> Tuple[bool, str]:
        """Returns (is_active, team). Baron buff lasts 3 minutes."""
        if self.baron_taken_at <= 0:
            return False, ""
        elapsed = game_time - self.baron_taken_at
        return elapsed < 180.0, self.baron_taken_team

    def enemy_soul_imminent(self, my_team: str) -> bool:
        """Return True if enemy is at 3 drakes (soul on next)."""
        return len(self.enemy_drakes) >= 3

    def our_soul_imminent(self, my_team: str) -> bool:
        return len(self.our_drakes) >= 3

    def drake_display(self) -> Tuple[str, str]:
        """Returns (our_stack_label, enemy_stack_label) e.g. ('2 Drake', '3 Drake⚠')"""
        ours   = len(self.our_drakes)
        theirs = len(self.enemy_drakes)
        our_s  = f"{ours} Drake{'s' if ours != 1 else ''}"
        their_s = f"{theirs} Drake{'s' if theirs != 1 else ''}"
        if theirs >= 3:
            their_s += " ⚠SOUL"
        return our_s, their_s
