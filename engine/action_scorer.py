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
  CROSS_MAP

Hard rules (override scores):
  - My HP < GANK_BLOCK_MY_HP_PCT  → block all ganks
  - Objective imminent < 45s      → boost OBJECTIVE_PREP
  - RECALL triggers at recall thresholds

Gank guard rails (all must pass to recommend a gank):
  1. TrackedTarget.is_valid_gank_target() == True
  2. Target zone is in GANKABLE_ZONES[lane]
  3. My HP is above GANK_BLOCK_MY_HP_PCT
  4. Score beats GANK_MIN_FINAL_SCORE
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple

import config
from tracking.target_state import TrackedTarget, Visibility
from tracking.lane_classifier import GANKABLE_ZONES, is_extended, classify
from engine.objective_tracker import ObjectiveTracker


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

    def score_all(
        self,
        me,                        # MyState
        enemies: dict,             # champion → TrackedTarget
        jg_tracker,                # JunglerTracker
        obj_tracker: ObjectiveTracker,
        game_time: float,
    ) -> List[ActionResult]:
        """
        Score every candidate action.  Returns a list sorted best-first.
        Always returns at least one result (fallback: farm).
        """
        scores: List[ActionResult] = []
        enemy_jg_dead = jg_tracker.enemy.is_dead

        # ── When enemy jungler is dead → surface that as the top action ───────
        if enemy_jg_dead:
            scores.append(self._score_free_map(me, jg_tracker, obj_tracker, game_time))

        # ── Recall ──────────────────────────────────────────────────────────
        recall = self._score_recall(me, obj_tracker, game_time)
        if recall:
            scores.append(recall)

        # ── Objective prep ───────────────────────────────────────────────────
        obj = self._score_objective(me, obj_tracker, game_time)
        if obj:
            scores.append(obj)

        # ── Gank candidates ──────────────────────────────────────────────────
        if me.hp_percent >= config.GANK_BLOCK_MY_HP_PCT:
            for lane in ("top", "mid", "bot"):
                result = self._score_gank(lane, me, enemies, jg_tracker, obj_tracker, game_time)
                if result:
                    scores.append(result)

        # ── Farm sides ───────────────────────────────────────────────────────
        farm_bonus = 20 if enemy_jg_dead else 0   # free farm when JG is dead
        scores.append(self._score_farm("top", me, jg_tracker, obj_tracker, game_time, farm_bonus))
        scores.append(self._score_farm("bot", me, jg_tracker, obj_tracker, game_time, farm_bonus))

        # ── Invade ── skip entirely when enemy JG is dead (irrelevant) ────────
        if not enemy_jg_dead:
            for side in ("top", "bot"):
                inv = self._score_invade(side, me, jg_tracker, obj_tracker, game_time)
                if inv:
                    scores.append(inv)

        # ── Hover lane ───────────────────────────────────────────────────────
        if not enemy_jg_dead:
            hover = self._score_hover(me, enemies, jg_tracker, game_time)
            if hover:
                scores.append(hover)

        # ── Apply champion modifiers ─────────────────────────────────────────
        if self.champion_module:
            for r in scores:
                mod = self.champion_module.score_modifier(r.action, me, game_time)
                r.score = min(100.0, max(0.0, r.score + mod))

        # Sort descending by score
        scores.sort(key=lambda r: r.score, reverse=True)
        return scores if scores else [self._fallback_farm()]

    # ── Recall scorer ────────────────────────────────────────────────────────

    def _score_recall(
        self, me, obj_tracker: ObjectiveTracker, game_time: float
    ) -> Optional[ActionResult]:
        score = 0.0
        reason_parts = []

        # HP triggers
        if me.hp_percent <= config.RECALL_HP_CRITICAL:
            score = 95.0
            reason_parts.append(f"critical HP {int(me.hp_percent)}%")
        elif me.hp_percent <= config.RECALL_HP_LOW:
            score = 85.0
            reason_parts.append(f"low HP {int(me.hp_percent)}%")
        elif me.hp_percent <= config.RECALL_HP_SOFT:
            score += 50.0
            reason_parts.append(f"HP {int(me.hp_percent)}%")

        # Gold triggers
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

        reason = "  |  ".join(reason_parts) if reason_parts else "consider recalling"
        is_crit = score >= 80
        return ActionResult(
            action     = "RECALL",
            score      = score,
            label      = "RECALL NOW" if is_crit else "Consider recall",
            reason     = reason,
            level      = "critical" if is_crit else "warn",
            tts_text   = "recall now",
            confidence = 1.0,
        )

    # ── Objective scorer ──────────────────────────────────────────────────────

    def _score_objective(
        self, me, obj_tracker: ObjectiveTracker, game_time: float
    ) -> Optional[ActionResult]:
        warning = obj_tracker.get_warning(game_time)
        if not warning:
            return None

        msg, lvl = warning
        t_drag = obj_tracker.time_until_dragon(game_time)
        score  = 0.0

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

    # ── Gank scorer ───────────────────────────────────────────────────────────

    def _score_gank(
        self,
        lane: str,
        me,
        enemies: dict,
        jg_tracker,
        obj_tracker: ObjectiveTracker,
        game_time: float,
    ) -> Optional[ActionResult]:
        """
        Score ganking a specific lane.  Returns None if the gank is invalid
        (no target, bad position, low confidence, etc.).

        FIX over old version:
          - Requires is_valid_gank_target() == True (position confidence + HP validity)
          - Requires target zone to be in GANKABLE_ZONES[lane]
          - HP shown only when valid; never shows 0% or stale values
          - Does NOT default to "top" – each lane is independently evaluated
        """
        targets = [t for t in enemies.values() if _role_matches_lane(t.role, lane)]
        if not targets:
            return None

        # Use the most dangerous (lowest HP + best confidence) target
        best_target: Optional[TrackedTarget] = None
        for t in targets:
            if t.is_valid_gank_target(game_time):
                if best_target is None or t.hp_percent < best_target.hp_percent:
                    best_target = t

        if best_target is None:
            return None   # No valid gank target in this lane

        # Zone check: target must be in a position where a gank makes sense
        allowed_zones = GANKABLE_ZONES.get(lane, set())
        if best_target.last_zone not in allowed_zones:
            return None   # Target is in base or wrong area

        # Get valid HP for display + scoring
        hp = best_target.get_valid_hp_for_gank(game_time)
        if hp is None:
            return None   # No valid HP data – cannot score the gank

        # ── Score calculation ────────────────────────────────────────────────
        score = config.SCORE_GANK_BASE

        # HP factor — single most important gank signal
        if hp <= config.SCORE_ENEMY_LOW_HP_THRESH:
            score += config.SCORE_ENEMY_LOW_HP
        elif hp <= config.SCORE_ENEMY_MED_HP_THRESH:
            score += config.SCORE_ENEMY_MED_HP

        # Extended (past river line) — minimap-confirmed only
        is_extended_flag = False
        if best_target.last_minimap_pos and not best_target.zone_inferred:
            mx, my = best_target.last_minimap_pos
            enemy_team = best_target.team
            my_team    = "ORDER" if enemy_team == "CHAOS" else "CHAOS"
            if is_extended(mx, my, best_target.role, my_team):
                score += config.SCORE_EXTENDED
                is_extended_flag = True

        # Level advantage — real players always consider this
        level_diff = me.level - best_target.level
        if level_diff >= 2:
            score += 12   # significant level lead → easier kill
        elif level_diff >= 1:
            score += 6
        elif level_diff <= -2:
            score -= 10   # they outscale us at this level

        # Early game aggression window (levels 1-6 are volatile)
        mins = game_time / 60.0
        if mins < 8:
            score += 8   # early game ganks have highest kill potential

        # Enemy jungler away from this side → safe window
        safe = jg_tracker.safe_side()
        if safe == lane:
            score += config.SCORE_JG_FAR

        # Enemy jungler recently seen on this side → danger
        threat = jg_tracker.threat_side()
        if threat == lane:
            elapsed = jg_tracker.enemy.age(game_time)
            if elapsed < 15:
                score += config.SCORE_JG_NEARBY_PENALTY

        # Position confidence penalty — stale minimap data
        if best_target.visibility == Visibility.STALE:
            score += config.SCORE_NO_VISION_PENALTY
        # API-inferred zone (not minimap-confirmed) — small penalty
        if best_target.zone_inferred:
            score -= 8   # we're less certain of their exact position

        # Objective penalty: don't gank when objective is about to spawn
        if obj_tracker.is_objective_imminent(game_time, 35.0):
            score -= 30

        # Hard minimum
        if score < config.GANK_MIN_FINAL_SCORE:
            return None

        # ── Build output ─────────────────────────────────────────────────────
        hp_str    = f"{int(hp)}%"
        zone_str  = best_target.last_zone.replace("_", " ")
        inferred  = " (est.)" if best_target.zone_inferred else ""
        ext_str   = ", extended" if is_extended_flag else ""
        lvl_str   = f" Lv{best_target.level}" if best_target.level > 1 else ""
        reason    = f"{best_target.champion}{lvl_str} {hp_str} HP, {zone_str}{inferred}{ext_str}"

        is_critical = score >= 65
        lvl    = "critical" if is_critical else "warn"
        prefix = "GANK" if is_critical else "Gank"

        return ActionResult(
            action     = f"GANK_{lane.upper()}",
            score      = score,
            label      = f"{prefix} {lane}",
            reason     = reason,
            level      = lvl,
            tts_text   = f"gank {lane}",
            confidence = best_target.position_confidence,
        )

    # ── Farm scorer ──────────────────────────────────────────────────────────

    def _score_farm(
        self,
        side: str,   # "top" or "bot"
        me,
        jg_tracker,
        obj_tracker: ObjectiveTracker,
        game_time: float,
        bonus: float = 0,
    ) -> ActionResult:
        score = config.SCORE_FARM_BASE + bonus

        # Bonus: enemy jungler is confirmed on OTHER side
        safe = jg_tracker.safe_side()
        if safe == side:
            score += 20   # this side is confirmed safe

        # Penalty: enemy jungler recently seen on this side
        threat = jg_tracker.threat_side()
        if threat == side:
            score -= 15

        # Bonus: objective is on this side
        if side == "bot" and 0 < obj_tracker.time_until_dragon(game_time) <= 90:
            score += 10   # near dragon, farm bot side first

        # Penalty: objective very close → should be prepping, not just farming
        if obj_tracker.is_objective_imminent(game_time, 45.0):
            score -= 10

        zone_label = "top" if side == "top" else "bot"
        jg_info    = f"JG {jg_tracker.enemy.last_zone.replace('_', ' ')}" if jg_tracker.enemy.champion and not jg_tracker.enemy.is_dead else ""
        reason     = f"safe side{', ' + jg_info if jg_info else ''}"

        return ActionResult(
            action     = f"FARM_{side.upper()}_SIDE",
            score      = score,
            label      = f"Farm {zone_label} camps",
            reason     = reason,
            level      = "info",
            tts_text   = f"farm {zone_label} side",
            confidence = 0.8,
        )

    # ── Invade scorer ────────────────────────────────────────────────────────

    def _score_invade(
        self,
        side: str,
        me,
        jg_tracker,
        obj_tracker: ObjectiveTracker,
        game_time: float,
    ) -> Optional[ActionResult]:
        # Only suggest invade if enemy jungler is confirmed on the other side
        safe = jg_tracker.safe_side()
        if safe != side:
            return None

        # Don't invade late game when baron is close
        if obj_tracker.is_objective_imminent(game_time, 50.0):
            return None

        # Invades make most sense early-mid game
        mins = game_time / 60.0
        if mins > 20:
            return None

        score = 38.0
        if jg_tracker.enemy.age(game_time) < 15:
            score += 15  # high-confidence JG position

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

    # ── Hover scorer ─────────────────────────────────────────────────────────

    def _score_hover(
        self,
        me,
        enemies: dict,
        jg_tracker,
        game_time: float,
    ) -> Optional[ActionResult]:
        """
        Suggest hovering a lane when ally is in danger but gank isn't clean.
        Only fires when an ally lane has pressure signs.
        """
        # Simple heuristic: if enemy jungler MIA and one ally lane is losing
        # (we don't have ally HP here, so keep this minimal for now)
        elapsed = jg_tracker.enemy.age(game_time)
        if elapsed < config.JG_WARN_S:
            return None  # JG recently seen, no danger

        # JG is missing → suggest hovering a lane to prevent ganks
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

    # ── Free map (enemy JG dead) ─────────────────────────────────────────────

    def _score_free_map(
        self,
        me,
        jg_tracker,
        obj_tracker: ObjectiveTracker,
        game_time: float,
    ) -> ActionResult:
        """
        Called when the enemy jungler is confirmed dead.
        This is the highest-value window in the game — free invade, pressure,
        or objective control.
        """
        score = 78.0   # default high priority

        # Boost even further if an objective is alive/imminent
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

    # ── Fallback ─────────────────────────────────────────────────────────────

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
    """Check if an API role corresponds to the given lane name."""
    return {
        "top":  role == "TOP",
        "mid":  role == "MIDDLE",
        "bot":  role in ("BOTTOM", "UTILITY"),
    }.get(lane, False)


def _objective_name(obj_tracker: ObjectiveTracker, game_time: float) -> str:
    import config as cfg
    if game_time >= (cfg.BARON_FIRST_SPAWN - 1) * 60:
        if obj_tracker.time_until_baron(game_time) <= cfg.OBJECTIVE_WARN_S:
            return "baron"
    return "dragon"
