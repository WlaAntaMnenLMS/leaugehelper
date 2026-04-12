"""
Jungle Pathing Advisor – where to go next + when to recall.

Two public methods
------------------
recall_check(hp, gold, game_time)
    → (message, level) if you should back, else None
    Highest priority output – always shown if triggered.

suggest(game_time, my_level, enemy_jg_side, hp)
    → (message, level)
    Champion-specific next-action advice.

Champion coverage
-----------------
Kayn   – stack-farming mentality, form-timing reminders, wall-path tips
Viego  – possession/skirmish priority, W sustain awareness, fight-seeking
Generic – safe-side farming, standard rotation cues

Recall priority tiers
---------------------
  1. HP ≤ CRITICAL                     → "RECALL NOW – critical HP"       critical
  2. HP ≤ LOW                           → "RECALL NOW – low HP"             critical
  3. gold ≥ FORCE (big item done)       → "RECALL NOW – {gold}g (item)"     critical
  4. HP ≤ SOFT  +  gold ≥ any tier      → "RECALL NOW – {gold}g (item)"     warn
  5. HP ≤ MEDIUM + gold ≥ dirk/phage    → "Consider recall – {gold}g"       dim
"""

from typing import Optional, Tuple

import config


# ── Helpers ─────────────────────────────────────────────────────────────────

def _opposite_side(side: str) -> str:
    """Return the safe farming side given where the enemy JG was last seen."""
    return {
        "top":        "bot",
        "top jungle": "bot",
        "bot":        "top",
        "bot jungle": "top",
        "mid":        "top or bot",
    }.get(side, "opposite side")


def _gold_label(gold: float) -> str:
    """Pick the most descriptive item label for a given gold amount."""
    for threshold, label in config.RECALL_GOLD_TIERS:
        if gold >= threshold:
            return label
    return "pots + ward"


# ── Main class ───────────────────────────────────────────────────────────────

class PathingAdvisor:
    def __init__(self):
        self.champion: str = ""

    def update_champion(self, champion: str):
        self.champion = champion

    # ── Recall logic ────────────────────────────────────────────────────────

    def recall_check(
        self,
        hp: float,
        gold: float,
        game_time: float,
    ) -> Optional[Tuple[str, str]]:
        """
        Return (message, level) if a recall is advisable, otherwise None.
        Checks are ordered from most to least urgent.
        """
        # 1. Critical HP – back immediately
        if hp <= config.RECALL_HP_CRITICAL:
            return ("RECALL NOW  →  critical HP", "critical")

        # 2. Low HP – back after this camp
        if hp <= config.RECALL_HP_LOW:
            return ("RECALL NOW  →  low HP", "critical")

        # 3. Huge gold – full item ready, always worth backing
        if gold >= config.RECALL_GOLD_FORCE:
            label = _gold_label(gold)
            return (f"RECALL NOW  →  {int(gold)}g  ({label})", "critical")

        # 4. Decent HP but good gold → back now
        if hp <= config.RECALL_HP_SOFT:
            for threshold, label in config.RECALL_GOLD_TIERS:
                if gold >= threshold:
                    return (f"RECALL NOW  →  {int(gold)}g  ({label})", "warn")

        # 5. Soft suggestion – medium HP, mid-tier gold
        if hp <= config.RECALL_HP_MEDIUM:
            for threshold, label in config.RECALL_GOLD_TIERS[2:4]:   # 1300 / 800 tier
                if gold >= threshold:
                    return (f"Consider recall  →  {int(gold)}g  ({label})", "dim")

        return None

    # ── Pathing suggestions ──────────────────────────────────────────────────

    def suggest(
        self,
        game_time: float,
        my_level: int,
        enemy_jg_side: str,
        hp: float,
        enemy_comp: Optional[list] = None,
    ) -> Tuple[str, str]:
        """Return (message, level) for the next pathing action."""
        champ = self.champion

        if champ == "Kayn":
            return self._kayn(game_time, my_level, enemy_jg_side, hp, enemy_comp)
        if champ == "Viego":
            return self._viego(game_time, my_level, enemy_jg_side, hp)
        return self._generic(game_time, enemy_jg_side)

    # ── Kayn ─────────────────────────────────────────────────────────────────

    def _kayn(
        self,
        game_time: float,
        level: int,
        enemy_jg_side: str,
        hp: float,
        enemy_comp: Optional[list],
    ) -> Tuple[str, str]:
        mins = game_time / 60.0
        safe = _opposite_side(enemy_jg_side)
        known_side = enemy_jg_side not in ("unknown", "")

        # ── Form hint (shown once early) ─────────────────────────────────────
        if mins < 1.5 and enemy_comp:
            ranged = {"Caitlyn", "Jinx", "Jhin", "Ezreal", "Lux", "Syndra",
                      "Jayce", "Kennen", "Ziggs", "Xerath", "Vel'Koz", "Zoe"}
            n_ranged = sum(1 for c in enemy_comp if c in ranged)
            form_hint = "→ go Shadow Assassin (ranged)" if n_ranged >= 2 else "→ go Rhaast (tanks/melee)"
            return (f"Kayn: start buff  {form_hint}", "dim")

        # ── Clear phase ──────────────────────────────────────────────────────
        if mins < 2.5:
            return ("Kayn: Clear buff → Gromp/Wolves → second buff (lvl 3)", "info")

        if mins < 4.5:
            return ("Kayn: Finish clear → gank any lane for first stacks", "info")

        # ── Stack-farming phase ──────────────────────────────────────────────
        if mins < 7:
            if known_side:
                return (
                    f"Kayn: JG {enemy_jg_side} → farm {safe} + gank {safe} for stacks",
                    "info",
                )
            return ("Kayn: Gank every lane → each hit = form stacks", "info")

        if mins < 9:
            if known_side:
                return (
                    f"Kayn: JG {enemy_jg_side} → invade {safe} or gank {safe}",
                    "warn",
                )
            return ("Kayn: Gank repeatedly → form unlocks ~10 min", "warn")

        # ── Form completion phase ─────────────────────────────────────────────
        if mins < 11:
            return ("Kayn: Form completing  →  force fights to finish fast", "warn")

        if mins < 13:
            return ("Kayn: Form ready?  →  dive lanes + skirmish for picks", "warn")

        # ── Power spike phase ────────────────────────────────────────────────
        if mins < 18:
            return ("Kayn: Full power  →  wall-path into backline, force objectives", "info")

        return ("Kayn: Scale into teamfights  →  flank with W through walls", "info")

    # ── Viego ────────────────────────────────────────────────────────────────

    def _viego(
        self,
        game_time: float,
        level: int,
        enemy_jg_side: str,
        hp: float,
    ) -> Tuple[str, str]:
        mins = game_time / 60.0
        safe = _opposite_side(enemy_jg_side)
        known_side = enemy_jg_side not in ("unknown", "")

        # ── Early clear ──────────────────────────────────────────────────────
        if mins < 2.5:
            return ("Viego: Full clear → W heals through damage, stay healthy", "info")

        if mins < 4.5:
            if hp < 55:
                return ("Viego: W passive heals → finish clear, don't recall yet", "info")
            return ("Viego: Good HP → look for 2v2 or early skirmish", "info")

        # ── Skirmish phase ───────────────────────────────────────────────────
        if mins < 7:
            if known_side:
                return (
                    f"Viego: JG {enemy_jg_side} → farm {safe} then force fight {safe}",
                    "info",
                )
            if hp > 60:
                return ("Viego: Find a fight  →  possession snowballs every kill", "info")
            return ("Viego: Clear camps  →  W sustains you back to full", "info")

        if mins < 10:
            if known_side:
                return (
                    f"Viego: JG {enemy_jg_side} → {safe} camps + look for pick",
                    "info",
                )
            return ("Viego: Force skirmishes near river  →  possess to chain kills", "warn")

        # ── Objective phase ──────────────────────────────────────────────────
        if mins < 14:
            return (
                "Viego: Fight near dragon/herald  →  possession extends teamfights",
                "warn",
            )

        if mins < 18:
            return ("Viego: Pick off isolated enemies  →  1 possession = fight won", "warn")

        return ("Viego: Stay in fights  →  W sustain + possession = never die", "info")

    # ── Generic ──────────────────────────────────────────────────────────────

    def _generic(self, game_time: float, enemy_jg_side: str) -> Tuple[str, str]:
        mins = game_time / 60.0
        safe = _opposite_side(enemy_jg_side)
        known_side = enemy_jg_side not in ("unknown", "")

        if mins < 2.5:
            return ("Clear starting buff  →  path to level 3", "info")

        if mins < 4.5:
            return ("Finish early clear  →  gank or continue farming", "info")

        if mins < 7:
            if known_side:
                return (f"JG {enemy_jg_side} side  →  farm {safe} camps safely", "info")
            return ("Full clear  →  gank on path", "info")

        if mins < 12:
            if known_side:
                return (f"Mirror JG  →  contest {safe} side camps", "info")
            return ("Clear nearest camps  →  mirror enemy JG rotation", "info")

        if mins < 18:
            return ("Farm efficiently  →  prepare Baron/Herald vision", "info")

        return ("Late game  →  group for objectives, avoid solo camps", "info")
