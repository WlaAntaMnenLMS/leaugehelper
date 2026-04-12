"""
Dynamic Item Build Recommender.

Produces a short build hint based on:
  - Champion name
  - Current items
  - Enemy team composition (AD-heavy, AP-heavy, healing, shields)
  - Gold available
  - Game stage (early / mid / late)

This module does NOT rely on any external data source (no memory reads,
no API calls beyond what GameState already has).  All recommendations are
heuristic and intended as a lightweight assist, not a full build calculator.

Recommendations focus on:
  1. Starter / first back rush
  2. Core mythic if not yet purchased
  3. Situational anti-heal / MR / armour when warranted
  4. "You have enough gold" nudges

Supports: generic jungler + champion-specific overrides for Kayn, Viego, WW.
Champion modules (champions/*.py) return their own build hints; this module
is used as a fallback for unlisted champions.
"""

from __future__ import annotations

from typing import Optional, List, Dict


# ---------------------------------------------------------------------------
# Per-champion starting / mythic defaults
# ---------------------------------------------------------------------------

MYTHIC_DEFAULTS: Dict[str, List[str]] = {
    # Assassin junglers
    "kayn":    ["Hubris", "Opportunity", "Edge of Night"],
    "khazix":  ["Duskblade of Draktharr", "Edge of Night"],
    "rengar":  ["Duskblade of Draktharr", "Edge of Night"],
    "talon":   ["Duskblade of Draktharr", "Youmuu's Ghostblade"],
    # Tank junglers
    "warwick": ["Sunfire Aegis", "Warmog's Armor"],
    "amumu":   ["Sunfire Aegis", "Thornmail"],
    "rammus":  ["Sunfire Aegis", "Thornmail"],
    "zac":     ["Sunfire Aegis", "Heartsteel"],
    # Fighter junglers
    "vi":      ["Trinity Force", "Black Cleaver"],
    "jarvaniv":["Trinity Force", "Black Cleaver"],
    "xin zhao":["Trinity Force", "Black Cleaver"],
    "viego":   ["Kraken Slayer", "Phantom Dancer"],
    # AP junglers
    "ekko":    ["Night Harvester", "Shadowflame"],
    "karthus": ["Luden's Tempest", "Shadowflame"],
    "lillia":  ["Night Harvester", "Rylai's Crystal Scepter"],
}

ANTIHEAL_ITEMS = ["Chempunk Chainsword", "Mortal Reminder"]
MR_ITEMS       = ["Force of Nature", "Spirit Visage", "Wit's End"]
ARMOR_ITEMS    = ["Frozen Heart", "Thornmail", "Randuin's Omen"]


class BuildRecommender:
    """
    Returns a short build hint for the overlay BUILD slot.

    Usage:
        rec = BuildRecommender()
        hint = rec.get_hint(me, enemies, game_time)
    """

    def get_hint(
        self,
        me,
        enemies: dict,
        game_time: float,
    ) -> Optional[str]:
        """Return a ≤40-char hint string or None."""
        champ  = (me.champion or "").lower()
        items_lower = {i.get("displayName", "").lower() for i in (me.items or [])}
        mins   = game_time / 60.0

        # ── First check: does enemy composition need immediate answers? ────────
        situational = self._situational_hint(enemies, items_lower, me.gold)
        if situational:
            return situational

        # ── Champion-specific core path ───────────────────────────────────────
        defaults = MYTHIC_DEFAULTS.get(champ, [])
        for item in defaults:
            if item.lower() not in items_lower:
                cost = _estimate_cost(item)
                if me.gold >= cost * 0.85:
                    return f"Buy {item}"
                elif me.gold >= 1300 and mins < 8:
                    return f"Saving for {item} (need {cost - int(me.gold)}g)"
                return None   # Can't afford yet

        return None   # Build is on track

    def _situational_hint(
        self,
        enemies: dict,
        items_lower: set,
        gold: float,
    ) -> Optional[str]:
        # Count enemy team composition
        healer_champs = {"soraka", "yuumi", "aatrox", "dr. mundo", "vladimir", "sona"}
        ap_champs     = {"syndra", "orianna", "lux", "zoe", "veigar", "viktor"}
        ad_champs     = {"zed", "talon", "caitlyn", "draven", "jinx", "vayne"}

        enemy_champs  = {v.champion.lower() for v in enemies.values()}
        n_ap    = len(ap_champs & enemy_champs)
        n_ad    = len(ad_champs & enemy_champs)
        healers = healer_champs & enemy_champs

        if healers:
            has_antiheal = any(i.lower() in items_lower for i in
                               ["chempunk chainsword", "mortal reminder", "executioner's calling"])
            if not has_antiheal and gold >= 800:
                return f"Anti-heal needed ({', '.join(healers)})"

        if n_ap >= 2:
            has_mr = any(i in items_lower for i in ["force of nature", "spirit visage", "wit's end", "banshee's veil"])
            if not has_mr and gold >= 2500:
                return "MR item (heavy AP)"

        if n_ad >= 2:
            has_arm = any(i in items_lower for i in ["frozen heart", "thornmail", "randuin's omen"])
            if not has_arm and gold >= 2500:
                return "Armour item (heavy AD)"

        return None


def _estimate_cost(item: str) -> int:
    """Very rough item cost estimate for nudge messages."""
    estimates = {
        "Hubris": 2600,
        "Opportunity": 2700,
        "Duskblade of Draktharr": 3100,
        "Sunfire Aegis": 2700,
        "Kraken Slayer": 3100,
        "Trinity Force": 3333,
        "Black Cleaver": 3000,
        "Night Harvester": 3000,
        "Luden's Tempest": 3000,
        "Warmog's Armor": 3000,
    }
    return estimates.get(item, 2800)
