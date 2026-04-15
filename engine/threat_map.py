"""
Threat Map – per-lane composite gank-opportunity score.

Every lane gets a score 0–100 each decision tick.
Higher score = better reason to gank that lane right now.

Signal priority (matches user mental model):
  1. Enemy HP low              ← most important; drives base score
  2. Enemy extended past river ← multiplies opportunity
  3. Enemy just died/respawned ← hard negative (wrong time)
  4. Level advantage           ← meaningful but secondary
  5. Objective imminent        ← timing gate
  6. Enemy JG last known pos   ← safety signal

Additional signals:
  + Enemy in-combat      (confirms they're in lane, taking damage now)
  + Enemy is fed         (high-value kill)
  + Enemy is starved     (easier kill)
  - Ally HP too low      (can't follow up)
  - Enemy just backed    (full HP = bad time)
  - JG confirmed near    (counter-gank risk)
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

import config
from tracking.target_state import TrackedTarget, Visibility
from tracking.lane_classifier import classify, is_extended


# ---------------------------------------------------------------------------
# Output types
# ---------------------------------------------------------------------------

@dataclass
class LaneThreat:
    lane:        str
    score:       float          # 0-100
    reason:      str            # short explanation shown in overlay
    risks:       List[str]      # 1-3 risk factors
    target:      Optional[TrackedTarget] = None
    ally_ok:     bool           = True
    hp_signal:   float          = 0.0   # normalised 0–1 (for EV model)
    level_diff:  int            = 0     # me.level - target.level


@dataclass
class ThreatMap:
    lanes: Dict[str, LaneThreat] = field(default_factory=dict)

    def best_lane(self) -> Optional[LaneThreat]:
        if not self.lanes:
            return None
        return max(self.lanes.values(), key=lambda l: l.score)

    def ranked(self) -> List[LaneThreat]:
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
        enemies:    dict,
        allies:     dict,
        jg_tracker,
        obj_tracker,
        event_proc,
        me,
        game_time:  float,
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
        lane:       str,
        enemies:    dict,
        allies:     dict,
        jg_tracker,
        obj_tracker,
        event_proc,
        me,
        game_time:  float,
    ) -> Optional[LaneThreat]:

        # ── 1. Find the enemy laner ──────────────────────────────────────────
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

        # ── 2. Validate HP data ──────────────────────────────────────────────
        hp = target.get_valid_hp_for_gank(game_time)
        if hp is None:
            return None

        # ── 3. Find allied laner ─────────────────────────────────────────────
        ally: Optional[TrackedTarget] = None
        for a in allies.values():
            if a.role in roles and not a.is_dead:
                ally = a
                break
        ally_hp = ally.hp_percent if ally else 100.0
        ally_ok = ally_hp >= config.ALLY_MIN_HP_FOR_GANK

        # ── 4. Score calculation ─────────────────────────────────────────────
        score   = 0.0
        reasons = []
        risks   = []

        # ── 4a. HP — primary signal (configurable tiers) ─────────────────────
        hp_signal = 0.0
        for thresh, bonus in config.THREAT_HP_TIERS:
            if hp <= thresh:
                score    += bonus
                hp_signal = 1.0 - (hp / thresh)   # normalised 0–1
                reasons.append(f"{int(hp)}% HP")
                break

        # ── 4b. In-combat — confirms they're actively taking damage now ───────
        if getattr(target, "in_combat", False):
            score += config.SCORE_IN_COMBAT
            reasons.append("in combat")

        # ── 4c. Fed / starved — use TrackedTarget.is_fed (from API scores) ───
        if target.is_fed:
            score += config.SCORE_FED_BONUS
            reasons.append("FED")
        elif target.is_starved:
            score += config.SCORE_STARVED_BONUS
            reasons.append("behind")

        # ── 4d. Level advantage ───────────────────────────────────────────────
        level_diff = getattr(me, "level", 1) - target.level
        if level_diff >= 2:
            score += config.SCORE_LEVEL_ADV_2
            reasons.append(f"Lv+{level_diff}")
        elif level_diff >= 1:
            score += config.SCORE_LEVEL_ADV_1
        elif level_diff <= -2:
            score += config.SCORE_LEVEL_DIS_2
            risks.append(f"Lv-{abs(level_diff)} disadvantage")

        # ── 4e. Extended — enemy far from their tower ─────────────────────────
        # Priority 1: minimap-confirmed position → use is_extended()
        # Priority 2: zone-only → river zone bonus
        extended_flag = False
        if target.last_minimap_pos and not target.zone_inferred:
            mx, my = target.last_minimap_pos
            my_team_for_check = "ORDER" if target.team == "CHAOS" else "CHAOS"
            if is_extended(mx, my, target.role, my_team_for_check):
                score += config.SCORE_EXTENDED
                reasons.append("extended")
                extended_flag = True
        if not extended_flag and target.last_zone in ("top_river", "bot_river"):
            score += config.SCORE_RIVER_ZONE
            reasons.append("in river")
            extended_flag = True

        # ── 4f. JG threat / safety ────────────────────────────────────────────
        safe   = jg_tracker.safe_side()
        threat = jg_tracker.threat_side()

        if safe == lane:
            score += config.SCORE_JG_FAR
            reasons.append("JG away")
        elif threat == lane:
            age = jg_tracker.enemy.age(game_time)
            if age < config.GANK_JG_NEARBY_SECONDS:
                score += config.SCORE_JG_NEARBY_PENALTY
                risks.append("JG nearby!")

        # ── 4g. Ally can't follow up ──────────────────────────────────────────
        if not ally_ok:
            score += config.SCORE_ALLY_LOW_HP_PENALTY
            risks.append(f"ally {int(ally_hp)}% HP")

        # ── 4h. Target recently respawned ─────────────────────────────────────
        victim_name = target.summoner_name or target.champion
        if event_proc and event_proc.recently_died(victim_name, game_time, window=30.0):
            score += config.SCORE_RECENTLY_DIED
            risks.append("just respawned")

        # ── 4i. Target just backed (HP jumped) ───────────────────────────────
        if getattr(target, "back_detected", False):
            score += config.SCORE_BACK_DETECTED
            risks.append("just backed")

        # ── 4j. Objective imminent ────────────────────────────────────────────
        if obj_tracker.is_objective_imminent(game_time, 35.0):
            score += config.SCORE_OBJ_IMMINENT
            risks.append("obj soon")

        # ── 5. Build reason string ────────────────────────────────────────────
        reason_str = ", ".join(reasons) if reasons else "low priority"
        return LaneThreat(
            lane       = lane,
            score      = max(0.0, score),
            reason     = f"{target.champion} – {reason_str}",
            risks      = risks,
            target     = target,
            ally_ok    = ally_ok,
            hp_signal  = hp_signal,
            level_diff = level_diff,
        )
