"""
Camp Timer Engine – tracks own and inferred enemy camp respawn windows.

Own camp timers are set by the user calling `mark_cleared(camp, game_time)`.
Enemy inferred timers are estimated from observed events (kill near camp,
JG spotted in a quadrant) and carry lower confidence.

Outputs per tick:
  get_best_farm_plan(horizon_s=90)  → list of (camp, available_at, confidence)
  get_invade_windows(horizon_s=90)  → list of (camp, likely_respawn_window)

Camp names match config.CAMP_RESPAWN keys:
  blue, red, gromp, wolves, raptors, krugs, scuttle

Quadrant groupings (used for pathing plan):
  top_side:  blue, gromp, wolves         (blue side top quadrant)
  bot_side:  red, raptors, krugs         (blue side bot quadrant)
  scuttle:   scuttle                     (river crab, single camp)

Enemy camp inference:
  When we see the enemy JG near a zone, we estimate they cleared the adjacent
  camp(s).  Confidence starts at 0.5 and decays over time.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

import config

# ---------------------------------------------------------------------------
# Camp metadata
# ---------------------------------------------------------------------------

# Which zone(s) each camp is located in (for JG-sighting inference)
_CAMP_ZONES: Dict[str, List[str]] = {
    "blue":    ["top_jungle"],
    "red":     ["bot_jungle"],
    "gromp":   ["top_jungle"],
    "wolves":  ["top_jungle"],
    "raptors": ["bot_jungle"],
    "krugs":   ["bot_jungle"],
    "scuttle": ["top_river", "bot_river"],
}

# Natural clear order for each quadrant (for pathing plan)
_TOP_ROUTE = ["blue", "gromp", "wolves"]
_BOT_ROUTE = ["red", "raptors", "krugs"]


# ---------------------------------------------------------------------------
# Data types
# ---------------------------------------------------------------------------

@dataclass
class CampTimer:
    """Tracks respawn state for a single camp."""
    name:         str
    respawn_s:    int          # seconds until next available (from config)
    cleared_at:   float = -1.0  # game_time when last cleared; -1 = unknown
    confidence:   float = 1.0   # 1.0 = own camp, <1.0 = inferred enemy camp

    @property
    def available_at(self) -> float:
        """Game time at which this camp respawns (-1 if never cleared)."""
        if self.cleared_at < 0:
            return -1.0
        return self.cleared_at + self.respawn_s

    def is_available(self, game_time: float) -> bool:
        """True if the camp is up right now."""
        if self.cleared_at < 0:
            return True   # never tracked → assume available
        return game_time >= self.available_at

    def time_until_available(self, game_time: float) -> float:
        """Seconds until the camp respawns (0 if already up)."""
        if self.available_at < 0:
            return 0.0
        return max(0.0, self.available_at - game_time)

    def decayed_confidence(self, game_time: float) -> float:
        """
        For inferred (enemy) camps: confidence decays as the respawn window
        widens.  Own camps (confidence=1.0) don't decay.
        """
        if self.confidence >= 1.0:
            return 1.0
        elapsed = max(0.0, game_time - self.cleared_at)
        # Decay: halve confidence every 60 s beyond the respawn time
        overshoot = max(0.0, elapsed - self.respawn_s)
        decay     = math.exp(-overshoot / 60.0)
        return self.confidence * decay


@dataclass
class FarmPlanItem:
    camp:         str
    available_at: float   # -1 = now
    time_until:   float   # seconds from now (0 = available)
    confidence:   float   # 0–1


@dataclass
class InvadeWindow:
    camp:       str
    respawn_at: float   # expected enemy respawn
    confidence: float
    side:       str     # "top" or "bot"


# ---------------------------------------------------------------------------
# Engine
# ---------------------------------------------------------------------------

class CampTimerEngine:
    """
    Tracks respawn timers for own and inferred enemy camps.

    Own camps:
      Call mark_cleared(camp, game_time) whenever the user clears a camp
      (can be triggered by a minimap event, voice command, or key-bind).

    Enemy camps (inferred):
      Call infer_enemy_clear(zone, game_time) whenever the enemy JG is
      spotted in a zone — we infer they probably just cleared it.
    """

    def __init__(self) -> None:
        self._own:   Dict[str, CampTimer] = {}
        self._enemy: Dict[str, CampTimer] = {}
        self._init_camps()

    # ── Initialisation ─────────────────────────────────────────────────────────

    def _init_camps(self) -> None:
        for name, respawn in config.CAMP_RESPAWN.items():
            self._own[name]   = CampTimer(name=name, respawn_s=respawn)
            self._enemy[name] = CampTimer(
                name=name, respawn_s=respawn, confidence=0.0
            )

    # ── Own camp updates ───────────────────────────────────────────────────────

    def mark_cleared(self, camp: str, game_time: float) -> None:
        """Record that we just cleared this camp."""
        if camp in self._own:
            self._own[camp].cleared_at  = game_time
            self._own[camp].confidence  = 1.0

    def mark_all_cleared(self, camps: List[str], game_time: float) -> None:
        for c in camps:
            self.mark_cleared(c, game_time)

    # ── Enemy camp inference ───────────────────────────────────────────────────

    def infer_enemy_clear(self, zone: str, game_time: float) -> None:
        """
        Enemy JG spotted in *zone* → they likely just cleared adjacent camps.
        Sets enemy camp cleared_at with confidence 0.55 (inferred).
        Only updates if the new inference is fresher than any existing one.
        """
        camps = _CAMP_ZONES.get(zone, [])
        # Broaden to adjacent camps for jungle zones
        if zone == "top_jungle":
            camps = ["blue", "gromp", "wolves"]
        elif zone == "bot_jungle":
            camps = ["red", "raptors", "krugs"]
        elif zone in ("top_river", "bot_river"):
            camps = ["scuttle"]

        for name in camps:
            if name not in self._enemy:
                continue
            existing = self._enemy[name]
            # Only update if this is new information (more recent sighting)
            if existing.cleared_at < game_time:
                self._enemy[name].cleared_at = game_time
                self._enemy[name].confidence = 0.55

    # ── Queries ────────────────────────────────────────────────────────────────

    def get_best_farm_plan(
        self,
        game_time:  float,
        horizon_s:  float = 90.0,
    ) -> List[FarmPlanItem]:
        """
        Return own camps available within the next `horizon_s` seconds,
        sorted by soonest-first.  Camps never tracked (cleared_at==-1) are
        assumed available.
        """
        plan: List[FarmPlanItem] = []
        for name, timer in self._own.items():
            t = timer.time_until_available(game_time)
            if t <= horizon_s:
                plan.append(FarmPlanItem(
                    camp         = name,
                    available_at = timer.available_at,
                    time_until   = t,
                    confidence   = timer.decayed_confidence(game_time),
                ))
        plan.sort(key=lambda i: i.time_until)
        return plan

    def get_invade_windows(
        self,
        game_time:  float,
        horizon_s:  float = 90.0,
    ) -> List[InvadeWindow]:
        """
        Return inferred enemy camp respawn windows within the next `horizon_s`
        seconds where confidence is above the display threshold.
        Sorted by confidence descending.
        """
        windows: List[InvadeWindow] = []
        for name, timer in self._enemy.items():
            if timer.cleared_at < 0:
                continue
            conf = timer.decayed_confidence(game_time)
            if conf < config.CAMP_INFERRED_CONF_SHOW:
                continue
            t = timer.time_until_available(game_time)
            if t > horizon_s:
                continue
            side = "top" if name in ("blue", "gromp", "wolves") else "bot"
            windows.append(InvadeWindow(
                camp       = name,
                respawn_at = timer.available_at,
                confidence = conf,
                side       = side,
            ))
        windows.sort(key=lambda w: w.confidence, reverse=True)
        return windows

    def next_camp_top(self, game_time: float) -> Optional[FarmPlanItem]:
        """First available camp on the top-side route."""
        plan = self.get_best_farm_plan(game_time)
        for item in plan:
            if item.camp in _TOP_ROUTE:
                return item
        return None

    def next_camp_bot(self, game_time: float) -> Optional[FarmPlanItem]:
        """First available camp on the bot-side route."""
        plan = self.get_best_farm_plan(game_time)
        for item in plan:
            if item.camp in _BOT_ROUTE:
                return item
        return None

    def scuttle_status(self, game_time: float) -> Optional[FarmPlanItem]:
        timer = self._own.get("scuttle")
        if not timer:
            return None
        t = timer.time_until_available(game_time)
        return FarmPlanItem(
            camp         = "scuttle",
            available_at = timer.available_at,
            time_until   = t,
            confidence   = timer.decayed_confidence(game_time),
        )

    def summary_line(self, game_time: float) -> str:
        """Short human-readable summary for overlay BUILD/PATH slot."""
        plan = self.get_best_farm_plan(game_time, horizon_s=60.0)
        if not plan:
            return "camps: all on CD"
        ready   = [i.camp for i in plan if i.time_until == 0]
        coming  = [(i.camp, int(i.time_until)) for i in plan if i.time_until > 0]

        parts = []
        if ready:
            parts.append("up: " + ", ".join(ready))
        if coming:
            next_c, t = coming[0]
            parts.append(f"{next_c} in {t}s")
        return "  ".join(parts) if parts else "all cleared"
