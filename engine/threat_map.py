"""
Threat Map – per-lane composite gank-opportunity score.

Every lane gets a score 0–100 each decision tick.
Higher score = better reason to gank that lane right now.

Signals factored in:
  + Enemy HP low              (most important)
  + Enemy is fed              (high-value kill)
  + Enemy is starved/weak     (easier kill)
  + Enemy in-combat           (confirmed in lane, currently fighting)
  + Enemy extended past river (farther from tower)
  + Level advantage           (we can kill them)
  + JG confirmed away         (no counter-gank threat)
  - Ally HP too low           (can't follow up)
  - Enemy just died/respawned (probably still at base)
  - Enemy just backed         (full HP = bad time)
  - JG confirmed near lane    (counter-gank risk)
  - Objective imminent        (don't waste time)
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, Optional, Tuple

import config
from tracking.target_state import TrackedTarget, Visibility


# ---------------------------------------------------------------------------
# Output types
# ---------------------------------------------------------------------------

@dataclass
class LaneThreat:
    lane:        str
    score:       float   # 0-100
    reason:      str     # short explanation
    target:      Optional[TrackedTarget] = None
    ally_ok:     bool    = True   # ally has enough HP to follow up


@dataclass
class ThreatMap:
    lanes: Dict[str, LaneThreat] = field(default_factory=dict)

    def best_lane(self) -> Optional[LaneThreat]:
        if not self.lanes:
            return None
        return max(self.lanes.values(), key=lambda l: l.score)

    def ranked(self):
        return sorted(self.lanes.values(), key=lambda l: l.score, reverse=True)


# ---------------------------------------------------------------------------
# Scorer
# ---------------------------------------------------------------------------

class ThreatMapBuilder:
    """
    Builds a ThreatMap from the current game state.

    Usage:
        builder = ThreatMapBuilder()
        threat_map = builder.build(enemies, allies, jg_tracker,
                                   obj_tracker, event_proc, me, game_time)
    """

    def build(
        self,
        enemies:     dict,
        allies:      dict,
        jg_tracker,
        obj_tracker,
        event_proc,
        me,
        game_time: float,
    ) -> ThreatMap:
        tm = ThreatMap()

        for lane in ("top", "mid", "bot"):
            result = self._score_lane(
                lane, enemies, allies, jg_tracker,
                obj_tracker, event_proc, me, game_time,
            )
            if result is not None:
                tm.lanes[lane] = result

        return tm

    def _score_lane(
        self,
        lane:        str,
        enemies:     dict,
        allies:      dict,
        jg_tracker,
        obj_tracker,
        event_proc,
        me,
        game_time:   float,
    ) -> Optional[LaneThreat]:

        # ── Find the enemy laner ─────────────────────────────────────────────
        role_map = {
            "top": ("TOP",),
            "mid": ("MIDDLE",),
            "bot": ("BOTTOM", "UTILITY"),
        }
        roles = role_map.get(lane, ())
        target: Optional[TrackedTarget] = None
        for t in enemies.values():
            if t.role in roles and not t.is_dead:
                if target is None or t.hp_percent < target.hp_percent:
                    target = t

        if target is None:
            return None

        # ── Find the ally laner ──────────────────────────────────────────────
        ally: Optional[TrackedTarget] = None
        for a in allies.values():
            if a.role in roles and not a.is_dead:
                ally = a
                break
        ally_hp = ally.hp_percent if ally else 100.0
        ally_ok = ally_hp >= config.ALLY_MIN_HP_FOR_GANK

        # ── Score calculation ────────────────────────────────────────────────
        score   = 0.0
        reasons = []

        hp = target.get_valid_hp_for_gank(game_time)
        if hp is None:
            return None

        # HP — primary signal
        if hp <= 25:
            score += 55
            reasons.append(f"{int(hp)}% HP")
        elif hp <= 40:
            score += 40
            reasons.append(f"{int(hp)}% HP")
        elif hp <= 55:
            score += 22
            reasons.append(f"{int(hp)}% HP")
        else:
            score += 5   # still track but low priority

        # In-combat — enemy is actively fighting (HP dropped this poll)
        if getattr(target, "in_combat", False):
            score += 12
            reasons.append("in combat")

        # Fed/starved — use TrackedTarget's own scores (from API, reliable)
        if target.is_fed:
            score += 18
            reasons.append("FED")
        elif target.is_starved:
            score += 8
            reasons.append("behind")

        # Level advantage
        level_diff = me.level - target.level
        if level_diff >= 2:
            score += 12
            reasons.append(f"Lv+{level_diff}")
        elif level_diff >= 1:
            score += 6
        elif level_diff <= -2:
            score -= 10   # they outscale us

        # Extended past river (minimap confirmed)
        if not target.zone_inferred and target.last_zone in (
            "top_river", "bot_river",
        ):
            score += 15
            reasons.append("extended")

        # JG safe side bonus
        safe = jg_tracker.safe_side()
        if safe == lane:
            score += 14
            reasons.append("JG away")

        # JG threat — recently seen near this side
        threat = jg_tracker.threat_side()
        if threat == lane:
            age = jg_tracker.enemy.age(game_time)
            if age < config.GANK_JG_NEARBY_SECONDS:
                score -= 35
                reasons.append("JG nearby!")

        # Ally can't follow up — gank is weaker
        if not ally_ok:
            score -= 20
            reasons.append(f"ally {int(ally_hp)}% HP")

        # Target just respawned — probably still in base
        # Use summoner_name because EventProcessor keys deaths by VictimName (summoner/game name)
        victim_name = target.summoner_name or target.champion
        if event_proc and event_proc.recently_died(victim_name, game_time, window=30.0):
            score -= 25

        # Target just backed (HP jumped from low to high)
        if getattr(target, "back_detected", False):
            score -= 20

        # Objective imminent — don't waste time ganking
        if obj_tracker.is_objective_imminent(game_time, 35.0):
            score -= 20

        reason_str = ", ".join(reasons) if reasons else "low priority"
        return LaneThreat(
            lane    = lane,
            score   = max(0.0, score),
            reason  = f"{target.champion} – {reason_str}",
            target  = target,
            ally_ok = ally_ok,
        )
