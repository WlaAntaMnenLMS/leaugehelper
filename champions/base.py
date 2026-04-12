"""
Base champion module interface.

Every champion module must subclass ChampionBase and implement
the three methods below.  The decision engine calls these at
decision time to blend champion-specific knowledge into the
generic scores.
"""

from __future__ import annotations

from typing import List, Optional, Tuple


class ChampionBase:
    """
    Abstract base for per-champion logic modules.

    Methods
    -------
    score_modifier(action, me, game_time) → float
        Return a delta (can be negative) to add to an ActionResult's score.
        Called for EVERY action in the scored list; use it to bias toward or
        away from specific actions for this champion.

    build_hint(me, enemies, game_time) → Optional[str]
        Return a short string (≤ 40 chars) to show in the BUILD overlay slot,
        or None if no hint is relevant right now.

    champion_name → str
        Canonical lowercase name used for loading.
    """

    champion_name: str = "unknown"

    def score_modifier(
        self,
        action: str,
        me,
        game_time: float,
    ) -> float:
        """
        Return a score delta for the given action name.
        action is one of: GANK_TOP, GANK_MID, GANK_BOT, FARM_TOP_SIDE,
        FARM_BOT_SIDE, INVADE_TOP, INVADE_BOT, RECALL, OBJECTIVE_PREP,
        HOVER_LANE, FARM_GENERIC.
        """
        return 0.0

    def build_hint(
        self,
        me,
        enemies: dict,
        game_time: float,
    ) -> Optional[str]:
        """Return short build hint string or None."""
        return None
