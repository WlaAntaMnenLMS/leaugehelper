"""
Gank Advisor – decides whether and where to gank.

Scoring
-------
Each lane gets a score (0–100).  The score rises when:
  • Enemy HP is low
  • Enemy is extended (past river toward ally tower – heuristic: low HP + no
    recall recently = probably pushed)
  • Our champion has good gank potential (champion DB bonus)

The score falls when:
  • The enemy jungler was recently seen near that lane
  • Ally HP is too low to follow up

A recommendation is only surfaced when score ≥ GANK_MIN_SCORE.
"""

from dataclasses import dataclass, field
from typing import Optional, List

import config


@dataclass
class LaneState:
    lane: str                        # "top" | "mid" | "bot"
    enemy_hp: float = 100.0          # 0-100 %
    ally_hp:  float = 100.0          # 0-100 %
    extended: bool  = False          # enemy past river line
    jungler_nearby: bool = False     # enemy jungler recently seen this side

    def score(self, champion_bonus: float = 0.0) -> float:
        s = 0.0

        # ── Enemy HP factor ────────────────────────────────────────────────
        if self.enemy_hp <= config.GANK_ENEMY_LOW_HP:
            s += 45
        elif self.enemy_hp <= config.GANK_ENEMY_MED_HP:
            s += 25
        elif self.enemy_hp < 75:
            s += 10

        # ── Extension factor ───────────────────────────────────────────────
        if self.extended:
            s += 30

        # ── Champion modifier ──────────────────────────────────────────────
        s += champion_bonus

        # ── Risk factors ───────────────────────────────────────────────────
        if self.jungler_nearby:
            s -= 50          # enemy jungler nearby = potential counter-gank

        if self.ally_hp < 25:
            s -= 20          # ally too low to trade after gank

        return max(0.0, min(100.0, s))

    def recommendation(self, champion_bonus: float = 0.0) -> Optional[str]:
        s = self.score(champion_bonus)
        if s < config.GANK_MIN_SCORE:
            return None
        lane_up = self.lane.upper()
        if self.jungler_nearby:
            return None  # safety: never recommend into known counter-gank
        if s >= 65:
            return f"GANK {lane_up}  →  {lane_up} enemy {int(self.enemy_hp)}% HP  ✓"
        return f"Consider gank {lane_up}  →  {int(self.enemy_hp)}% HP"


class GankAdvisor:
    def __init__(self, champion: str = ""):
        self.champion = champion

    def _champion_bonus(self, level: int) -> float:
        data = config.CHAMPION_GANK_BONUS.get(self.champion)
        if not data:
            return 0.0
        return data["post6"] if level >= 6 else data["pre6"]

    def evaluate(self, lanes: List[LaneState], my_level: int = 1) -> Optional[str]:
        """
        Evaluate all lane states and return the single best recommendation,
        or None if no gank is advisable.
        """
        bonus = self._champion_bonus(my_level)
        best_score = 0.0
        best_rec: Optional[str] = None

        for lane in lanes:
            s = lane.score(bonus)
            if s > best_score:
                rec = lane.recommendation(bonus)
                if rec:
                    best_score = s
                    best_rec = rec

        return best_rec

    def no_gank_reason(self, lanes: List[LaneState], my_hp: float) -> str:
        """Return a short string explaining why no gank is recommended."""
        if my_hp < 30:
            return "You're low HP  →  recall first"
        all_full = all(l.enemy_hp > 75 for l in lanes)
        if all_full:
            return "All enemies healthy  →  clear camps"
        nearby = [l.lane for l in lanes if l.jungler_nearby]
        if nearby:
            return f"Enemy JG near {'/'.join(nearby)}  →  farm safely"
        return "Farm camps  →  no clean gank available"
