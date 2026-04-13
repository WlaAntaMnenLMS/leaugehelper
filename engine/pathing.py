"""
Jungle Pathing Advisor.

Recommends the next camp(s) to clear based on:
  - Which camps are available (cooldown-based estimation)
  - Current position (last known zone)
  - Objective approach direction
  - Champion-specific efficient clears

This module is intentionally simple and heuristic-based.
It does NOT read memory; camp timers are estimated from game time
and typical clear speeds.

Pathing logic:
  - Full clears: Blue → Gromp → Wolves → Raptors → Red → Krugs (standard)
  - Mirror clear or invade when JG confirmed away
  - Objective-oriented pathing when timer within 2 min
  - Skip to obj when <60s remaining

Output: a short ordered list of camps to visit next, e.g. ["Red", "Raptors", "Dragon"]
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import List, Optional, Tuple
import config


# ---------------------------------------------------------------------------
# Camp definitions
# ---------------------------------------------------------------------------

@dataclass
class Camp:
    name:        str
    zone:        str        # matches lane_classifier zone
    side:        str        # "bot" = blue side, "top" = red side
    spawn_time:  float      # first spawn (seconds)
    respawn:     float      # respawn timer (seconds)
    priority:    int        # 1 = high priority, 3 = low


# Standard camps with approximate spawn/respawn timers
CAMPS: List[Camp] = [
    Camp("Blue Buff",  "top_jungle",  "bot", 90,  300, 1),
    Camp("Red Buff",   "bot_jungle",  "bot", 90,  300, 1),
    Camp("Gromp",      "top_jungle",  "bot", 90,  120, 2),
    Camp("Wolves",     "top_jungle",  "bot", 90,  120, 2),
    Camp("Raptors",    "bot_jungle",  "bot", 90,  120, 2),
    Camp("Krugs",      "bot_jungle",  "bot", 90,  120, 2),
    Camp("Rift Scuttler", "top_river", "both", 210, 180, 1),
    Camp("Rift Scuttler", "bot_river", "both", 210, 180, 1),
]


# ---------------------------------------------------------------------------
# Pathing state
# ---------------------------------------------------------------------------

@dataclass
class PathingState:
    """Tracks estimated camp availability and current route."""
    # camp_name → estimated next available time
    _cleared_at:   dict = field(default_factory=dict)
    current_route: List[str] = field(default_factory=list)
    route_index:   int  = 0


# ---------------------------------------------------------------------------
# Pathing advisor
# ---------------------------------------------------------------------------

class PathingAdvisor:
    """
    Recommends which camps to visit next.

    Call get_next_camps() to get up to 3 camps in priority order.
    Call notify_cleared(camp_name, game_time) when a camp is known cleared.
    """

    def __init__(self, champion: str = ""):
        self.champion = champion.lower()
        self.state    = PathingState()

    def notify_cleared(self, camp_name: str, game_time: float) -> None:
        """Record that a camp was just cleared."""
        for c in CAMPS:
            if c.name == camp_name:
                self.state._cleared_at[camp_name] = game_time + c.respawn
                break

    def is_available(self, camp: Camp, game_time: float) -> bool:
        """Estimate whether a camp is available right now."""
        if camp.name not in self.state._cleared_at:
            return game_time >= camp.spawn_time
        return game_time >= self.state._cleared_at[camp.name]

    def get_next_camps(
        self,
        me_zone: str,
        obj_tracker,
        game_time: float,
        jg_safe_side: Optional[str] = None,
    ) -> List[str]:
        """
        Return a recommended sequence of up to 3 camps/objectives.

        Reasoning priority:
          1. Objective in < 60s → path to dragon/baron pit
          2. Objective in 60–120s → clear 1 camp then path to pit
          3. Safe invade available → suggest 1 enemy camp
          4. Normal efficient clear
        """
        result: List[str] = []

        t_drag  = obj_tracker.time_until_dragon(game_time)
        t_baron = obj_tracker.time_until_baron(game_time)
        t_her   = obj_tracker.time_until_herald(game_time)

        # ── Objective already alive in pit → take it now ─────────────────────
        # (t == 0 means spawned but not yet taken; 0 < t means about to spawn)
        if t_drag == 0.0:
            return ["Dragon pit – contest now"]
        if t_her is not None and t_her == 0.0:
            return ["Take Herald now"]
        if game_time >= (config.BARON_FIRST_SPAWN - 1) * 60 and t_baron == 0.0:
            return ["Baron pit – contest now"]

        # ── Objective about to spawn (within 30s) ─────────────────────────────
        next_obj = self._next_objective_name(obj_tracker, game_time)

        if 0 < t_drag <= 30 or 0 < t_baron <= 30 or (t_her is not None and 0 < t_her <= 30):
            return [f"Path to {next_obj}"]

        if 0 < t_drag <= 90 or 0 < t_baron <= 90 or (t_her is not None and 0 < t_her <= 90):
            # Clear one nearby camp then path
            camp = self._nearest_available_camp(me_zone, game_time)
            if camp:
                result.append(camp)
            result.append(f"→ {next_obj}")
            return result

        # ── Normal pathing ────────────────────────────────────────────────────
        available = [
            c for c in CAMPS
            if self.is_available(c, game_time) and c.name != "Rift Scuttler"
        ]

        # Prioritise buffs first if not yet cleared
        for c in available:
            if c.name in ("Blue Buff", "Red Buff") and c.priority == 1:
                result.append(c.name)

        # Fill with nearby priority 2 camps
        for c in sorted(available, key=lambda x: x.priority):
            if c.name not in result:
                result.append(c.name)
            if len(result) >= 3:
                break

        # Scuttler hint
        if game_time >= 210 and (t_drag > 90 or t_baron > 90):
            if t_her is None or (t_her is not None and t_her > 60):
                if len(result) < 3:
                    result.append("Scuttler")

        return result[:3] if result else ["Clear nearest camp"]

    # ── Helpers ───────────────────────────────────────────────────────────────

    def _nearest_available_camp(
        self, me_zone: str, game_time: float
    ) -> Optional[str]:
        """Return the name of the nearest available camp to me_zone."""
        # Simple heuristic: same zone or same side
        for c in CAMPS:
            if c.zone == me_zone and self.is_available(c, game_time):
                return c.name
        # Fallback: any available
        for c in CAMPS:
            if self.is_available(c, game_time) and c.name != "Rift Scuttler":
                return c.name
        return None

    def _next_objective_name(self, obj_tracker, game_time: float) -> str:
        """Return the name of the next objective to SPAWN (t > 0 only)."""
        t_drag  = obj_tracker.time_until_dragon(game_time)
        t_baron = obj_tracker.time_until_baron(game_time)
        t_her   = obj_tracker.time_until_herald(game_time)

        # Only include objectives that haven't spawned yet (t > 0)
        candidates = []
        if t_drag > 0:
            candidates.append(("Dragon", t_drag))
        if game_time >= (config.BARON_FIRST_SPAWN - 1) * 60 and t_baron > 0:
            candidates.append(("Baron", t_baron))
        if t_her is not None and t_her > 0:
            candidates.append(("Herald", t_her))

        if not candidates:
            return "Dragon"   # fallback
        return min(candidates, key=lambda x: x[1])[0]

    def pathing_label(
        self,
        me_zone: str,
        obj_tracker,
        game_time: float,
        jg_safe_side: Optional[str] = None,
    ) -> Tuple[str, str]:
        """
        Returns (label_text, level) for the overlay PATH slot.

        label_text is a short string like "Red → Gromp → Dragon"
        level is info / warn / critical
        """
        camps = self.get_next_camps(me_zone, obj_tracker, game_time, jg_safe_side)
        if not camps:
            return "Clear camps", "info"

        label = " → ".join(camps[:3])

        t_drag  = obj_tracker.time_until_dragon(game_time)
        t_baron = obj_tracker.time_until_baron(game_time)
        if t_drag <= 60 or t_baron <= 60:
            level = "critical"
        elif t_drag <= 120 or t_baron <= 120:
            level = "warn"
        else:
            level = "info"

        return label, level
