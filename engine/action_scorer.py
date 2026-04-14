"""
Action Scorer – scores every possible jungle action and returns the best one.

This is the brain of the decision engine.  Every candidate action gets an
explicit numeric score.  The winner is surfaced to the overlay.

Candidate actions:
  GANK_TOP / GANK_MID / GANK_BOT
  FARM_TOP_SIDE / FARM_BOT_SIDE
  INVADE_TOP / INVADE_BOT
  RECALL
  OBJECTIVE_PREP
  HOVER_LANE
  FREE_MAP   (enemy JG dead)

Hard rules (override scores):
  - My HP < GANK_BLOCK_MY_HP_PCT  → block all ganks
  - Objective imminent < 45s      → boost OBJECTIVE_PREP
  - RECALL triggers at recall thresholds

Gank scoring uses ThreatMapBuilder for composite intelligence:
  HP, in-combat, fed/starved, level diff, extended, JG safe/threat,
  ally HP, recently died, back detected, objective imminent.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple

import config
from tracking.target_state import TrackedTarget, Visibility
from tracking.lane_classifier import GANKABLE_ZONES, is_extended, classify
from engine.objective_tracker import ObjectiveTracker
from engine.threat_map import ThreatMapBuilder, LaneThreat


# ---------------------------------------------------------------------------
# Result types
# ---------------------------------------------------------------------------

@dataclass
class ActionResult:
    """A scored action with a human-readable explanation."""
    action:      str    # e.g. "GANK_MID"
    score:       float  # 0–100
    label:       str    # short display label, e.g. "Gank mid"
    reason:      str    # explanation, e.g. "Azir 38% HP, extended"
    level:       str    # overlay colour level: info / warn / critical
    tts_text:    str    # spoken text, short
    confidence:  float  # 0–1 confidence in this recommendation


# ---------------------------------------------------------------------------
# Main scorer class
# ---------------------------------------------------------------------------

class ActionScorer:
    """
    Scores all candidate actions given the current game state.

    Call score_all() to receive a ranked list of ActionResult objects.
    The first element is the recommended action.
    """

    def __init__(self, champion_module=None):
        """
        champion_module: optional champion-specific module (Kayn/Viego/Warwick)
                         that can apply modifiers to scores.
        """
        self.champion_module = champion_module
        self._threat_builder = ThreatMapBuilder()

    def score_all(
        self,
        me,                          # MyState
        enemies:    dict,            # champion → TrackedTarget
        allies:     dict,            # champion → TrackedTarget (ally laners)
        jg_tracker,                  # JunglerTracker
        obj_tracker: ObjectiveTracker,
        event_proc,                  # EventProcessor (for fed/starved queries)
        game_time:  float,
    ) -> List[ActionResult]:
        """
        Score every candidate action.  Returns a list sorted best-first.
        Always returns at least one result (fallback: farm).
        """
        scores: List[ActionResult] = []
        enemy_jg_dead = jg_tracker.enemy.is_dead

        # ── Enemy JG dead → free map is the top priority ─────────────────────
        if enemy_jg_dead:
            scores.append(self._score_free_map(me, jg_tracker, obj_tracker, game_time))

        # ── Recall ────────────────────────────────────────────────────────────
        recall = self._score_recall(me, obj_tracker, game_time)
        if recall:
            scores.append(recall)

        # ── Objective prep ─────────────────────────────────────────────────────
        obj = self._score_objective(me, obj_tracker, game_time)
        if obj:
            scores.append(obj)

        # ── Gank candidates (via ThreatMap) ────────────────────────────────────
        if me.hp_percent >= config.GANK_BLOCK_MY_HP_PCT:
            threat_map = self._threat_builder.build(
                enemies, allies, jg_tracker, obj_tracker, event_proc, me, game_time
            )
            for lane_threat in threat_map.ranked():
                if lane_threat.score >= config.GANK_MIN_FINAL_SCORE:
                    # Extra zone safety check: target must be in a gankable zone
                    tgt = lane_threat.target
                    if tgt and tgt.last_zone in GANKABLE_ZONES.get(lane_threat.lane, set()):
                        result = self._lane_threat_to_action(lane_threat, game_time)
                        if result:
                            scores.append(result)

        # ── Farm sides ─────────────────────────────────────────────────────────
        farm_bonus = 20 if enemy_jg_dead else 0
        scores.append(self._score_farm("top", me, jg_tracker, obj_tracker, game_time, farm_bonus))
        scores.append(self._score_farm("bot", me, jg_tracker, obj_tracker, game_time, farm_bonus))

        # ── Invade – only when enemy JG is alive ───────────────────────────────
        if not enemy_jg_dead:
            for side in ("top", "bot"):
                inv = self._score_invade(side, me, jg_tracker, obj_tracker, game_time)
                if inv:
                    scores.append(inv)

        # ── Hover lane ─────────────────────────────────────────────────────────
        if not enemy_jg_dead:
            hover = self._score_hover(me, enemies, jg_tracker, game_time)
            if hover:
                scores.append(hover)

        # ── Champion modifiers ─────────────────────────────────────────────────
        if self.champion_module:
            for r in scores:
                mod = self.champion_module.score_modifier(r.action, me, game_time)
                r.score = min(100.0, max(0.0, r.score + mod))

        scores.sort(key=lambda r: r.score, reverse=True)
        return scores if scores else [self._fallback_farm()]

    # ── ThreatMap → ActionResult conversion ──────────────────────────────────

    def _lane_threat_to_action(
        self,
        lane_threat: LaneThreat,
        game_time: float,
    ) -> Optional[ActionResult]:
        """Convert a LaneThreat (from ThreatMap) into an ActionResult for the overlay."""
        target = lane_threat.target
        if target is None:
            return None

        hp = target.get_valid_hp_for_gank(game_time)
        if hp is None:
            return None

        lane  = lane_threat.lane
        # Add gank base score so ganks fairly compete with farm/invade
        score = lane_threat.score + config.SCORE_GANK_BASE
        reason = lane_threat.reason

        # Downgrade if ally can't follow up (still show but lower urgency)
        if not lane_threat.ally_ok:
            level = "warn"
        else:
            level = "critical" if score >= 65 else "warn"

        prefix = "GANK" if level == "critical" else "Gank"

        return ActionResult(
            action     = f"GANK_{lane.upper()}",
            score      = score,
            label      = f"{prefix} {lane}",
            reason     = reason,
            level      = level,
            tts_text   = f"gank {lane}",
            confidence = target.position_confidence,
        )

    # ── Recall scorer ──────────────────────────────────────────────────────────

    def _score_recall(
        self, me, obj_tracker: ObjectiveTracker, game_time: float
    ) -> Optional[ActionResult]:
        score = 0.0
        reason_parts = []

        if me.hp_percent <= config.RECALL_HP_CRITICAL:
            score = 95.0
            reason_parts.append(f"critical HP {int(me.hp_percent)}%")
        elif me.hp_percent <= config.RECALL_HP_LOW:
            score = 85.0
            reason_parts.append(f"low HP {int(me.hp_percent)}%")
        elif me.hp_percent <= config.RECALL_HP_SOFT:
            score += 50.0
            reason_parts.append(f"HP {int(me.hp_percent)}%")

        gold_label = None
        for threshold, label in config.RECALL_GOLD_TIERS:
            if me.gold >= threshold:
                gold_label = label
                break
        if gold_label:
            if me.gold >= config.RECALL_GOLD_FORCE:
                score = max(score, 90.0)
            elif me.hp_percent <= config.RECALL_HP_SOFT:
                score = max(score, 75.0)
            elif me.hp_percent <= config.RECALL_HP_MEDIUM:
                score = max(score, 55.0)
            reason_parts.append(f"{int(me.gold)}g ({gold_label})")

        # Penalty: objective imminent → don't recall now
        if obj_tracker.is_objective_imminent(game_time, 50.0) and score < 85:
            score -= 25

        if score < 30:
            return None

        reason   = "  |  ".join(reason_parts) if reason_parts else "consider recalling"
        is_crit  = score >= 80
        return ActionResult(
            action     = "RECALL",
            score      = score,
            label      = "RECALL NOW" if is_crit else "Consider recall",
            reason     = reason,
            level      = "critical" if is_crit else "warn",
            tts_text   = "recall now",
            confidence = 1.0,
        )

    # ── Objective scorer ───────────────────────────────────────────────────────

    def _score_objective(
        self, me, obj_tracker: ObjectiveTracker, game_time: float
    ) -> Optional[ActionResult]:
        warning = obj_tracker.get_warning(game_time)
        if not warning:
            return None

        msg, lvl = warning
        t_drag   = obj_tracker.time_until_dragon(game_time)
        score    = 0.0

        if t_drag <= 20:
            score = 92.0
        elif t_drag <= 45:
            score = 70.0
        elif t_drag <= 75:
            score = 50.0
        else:
            score = 35.0

        return ActionResult(
            action     = "OBJECTIVE_PREP",
            score      = score,
            label      = msg,
            reason     = "objective window",
            level      = lvl,
            tts_text   = f"path to {_objective_name(obj_tracker, game_time)}",
            confidence = 0.95,
        )

    # ── Farm scorer ────────────────────────────────────────────────────────────

    def _score_farm(
        self,
        side: str,   # "top" or "bot"
        me,
        jg_tracker,
        obj_tracker: ObjectiveTracker,
        game_time:   float,
        bonus:       float = 0,
    ) -> ActionResult:
        score = config.SCORE_FARM_BASE + bonus

        safe   = jg_tracker.safe_side()
        threat = jg_tracker.threat_side()

        if safe == side:
            score += 20   # this side is confirmed safe
        if threat == side:
            score -= 15   # enemy JG recently seen this side

        if side == "bot" and 0 < obj_tracker.time_until_dragon(game_time) <= 90:
            score += 10   # near dragon, farm bot first

        if obj_tracker.is_objective_imminent(game_time, 45.0):
            score -= 10

        jg_info   = (f"JG {jg_tracker.enemy.last_zone.replace('_', ' ')}"
                     if jg_tracker.enemy.champion and not jg_tracker.enemy.is_dead
                     else "")
        reason    = f"safe side{', ' + jg_info if jg_info else ''}"

        return ActionResult(
            action     = f"FARM_{side.upper()}_SIDE",
            score      = score,
            label      = f"Farm {side} camps",
            reason     = reason,
            level      = "info",
            tts_text   = f"farm {side} side",
            confidence = 0.8,
        )

    # ── Invade scorer ──────────────────────────────────────────────────────────

    def _score_invade(
        self,
        side: str,
        me,
        jg_tracker,
        obj_tracker: ObjectiveTracker,
        game_time:   float,
    ) -> Optional[ActionResult]:
        safe = jg_tracker.safe_side()
        if safe != side:
            return None

        if obj_tracker.is_objective_imminent(game_time, 50.0):
            return None

        mins = game_time / 60.0
        if mins > 20:
            return None

        score = 38.0
        if jg_tracker.enemy.age(game_time) < 15:
            score += 15

        reason = f"enemy JG confirmed {jg_tracker.threat_side() or '?'} side"
        return ActionResult(
            action     = f"INVADE_{side.upper()}",
            score      = score,
            label      = f"Invade {side} jungle",
            reason     = reason,
            level      = "info",
            tts_text   = f"invade {side} side",
            confidence = jg_tracker.enemy.position_confidence,
        )

    # ── Hover scorer ────────────────────────────────────────────────────────────

    def _score_hover(
        self,
        me,
        enemies: dict,
        jg_tracker,
        game_time: float,
    ) -> Optional[ActionResult]:
        elapsed = jg_tracker.enemy.age(game_time)
        if elapsed < config.JG_WARN_S:
            return None

        threat_side = jg_tracker.threat_side()
        if not threat_side:
            return None

        return ActionResult(
            action     = "HOVER_LANE",
            score      = 32.0,
            label      = f"Hover {threat_side} lane",
            reason     = f"JG MIA {int(elapsed)}s → countergank ready",
            level      = "warn",
            tts_text   = f"hover {threat_side}",
            confidence = 0.6,
        )

    # ── Free map (enemy JG dead) ───────────────────────────────────────────────

    def _score_free_map(
        self,
        me,
        jg_tracker,
        obj_tracker: ObjectiveTracker,
        game_time:   float,
    ) -> ActionResult:
        score = 78.0

        t_drag = obj_tracker.time_until_dragon(game_time)
        t_her  = obj_tracker.time_until_herald(game_time)

        if t_drag == 0:
            reason = f"{jg_tracker.enemy.champion} dead → TAKE DRAGON"
            score  = 92.0
        elif t_her is not None and t_her == 0:
            reason = f"{jg_tracker.enemy.champion} dead → TAKE HERALD"
            score  = 90.0
        elif 0 < t_drag <= 60:
            reason = f"{jg_tracker.enemy.champion} dead → path dragon ({int(t_drag)}s)"
            score  = 85.0
        else:
            reason = f"{jg_tracker.enemy.champion} dead → invade/pressure freely"

        return ActionResult(
            action     = "FREE_MAP",
            score      = score,
            label      = "FREE MAP – JG dead",
            reason     = reason,
            level      = "critical",
            tts_text   = "enemy jungler is dead, free map",
            confidence = 1.0,
        )

    # ── Fallback ──────────────────────────────────────────────────────────────

    @staticmethod
    def _fallback_farm() -> ActionResult:
        return ActionResult(
            action     = "FARM_GENERIC",
            score      = 30.0,
            label      = "Clear camps",
            reason     = "no priority action",
            level      = "dim",
            tts_text   = "clear camps",
            confidence = 0.5,
        )


# ---------------------------------------------------------------------------
# Private helpers
# ---------------------------------------------------------------------------

def _role_matches_lane(role: str, lane: str) -> bool:
    return {
        "top": role == "TOP",
        "mid": role == "MIDDLE",
        "bot": role in ("BOTTOM", "UTILITY"),
    }.get(lane, False)


def _objective_name(obj_tracker: ObjectiveTracker, game_time: float) -> str:
    import config as cfg
    if game_time >= (cfg.BARON_FIRST_SPAWN - 1) * 60:
        if obj_tracker.time_until_baron(game_time) <= cfg.OBJECTIVE_WARN_S:
            return "baron"
    return "dragon"
