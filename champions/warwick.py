"""
Warwick champion module.

Warwick is a sustain duelist and low-HP punisher.
Unique mechanics:
  - W (Eternal Hunger): trails the scent of targets below 50% HP
  - R (Infinite Duress): point-and-click suppression; reliable engage tool

Scoring biases:
  - Huge bonus to GANK when any enemy is below 50% HP (W gives movement speed)
  - INVADE bonus: WW is extremely strong in 1v1 early
  - GANK_TOP: top-laners are often bruisers that WW sustains through
  - FARM when all enemies are healthy → WW has limited kill pressure on full-HP targets
  - HOVER: WW W-scent makes "hover and wait" genuinely impactful

Build hints:
  - Sunfire Aegis → Warmog's → situational tank
  - Blade of the Ruined King for on-hit path
"""

from __future__ import annotations

from typing import Optional
from champions.base import ChampionBase

TANK_ITEMS   = {"sunfire aegis", "warmog's armor", "heartsteel", "titanic hydra"}
ONHIT_ITEMS  = {"blade of the ruined king", "kraken slayer", "wit's end"}


class WarwickModule(ChampionBase):
    champion_name = "warwick"

    def score_modifier(self, action: str, me, game_time: float) -> float:
        mins = game_time / 60.0

        # WW is insanely strong early – full invade potential
        if action in ("INVADE_TOP", "INVADE_BOT") and mins < 15:
            return +15.0

        # Low-HP scent mechanic: big gank bonus whenever a laner is low
        # (The HP check is done in the scorer, but WW is extra impactful)
        if action in ("GANK_TOP", "GANK_MID", "GANK_BOT"):
            return +10.0   # WW suppression is a hard CC lock-down

        # Hover is especially strong for WW (scent trails)
        if action == "HOVER_LANE":
            return +8.0

        # Late game WW needs to be in teamfights
        if mins > 20 and action in ("FARM_TOP_SIDE", "FARM_BOT_SIDE"):
            return -5.0

        return 0.0

    def build_hint(self, me, enemies: dict, game_time: float) -> Optional[str]:
        items_lower = {i.get("displayName", "").lower() for i in (me.items or [])}
        mins = game_time / 60.0

        has_tank   = bool(items_lower & TANK_ITEMS)
        has_onhit  = bool(items_lower & ONHIT_ITEMS)

        # First back hint
        if not has_tank and not has_onhit:
            if me.gold >= 1100:
                return "First back: Sunfire Aegis (tank) or BotRK (on-hit)"
            return None

        # Full build path
        if has_tank:
            if "warmog's armor" not in items_lower and me.gold >= 3000:
                return "Warmog's Armor next (sustain)"
            return None

        if has_onhit:
            if "kraken slayer" not in items_lower and me.gold >= 3100:
                return "Kraken Slayer (on-hit)"
            return None

        # Anti-heal reminder
        healer_champs = {"soraka", "yuumi", "aatrox", "dr. mundo", "sona"}
        enemy_champs  = {v.champion.lower() for v in enemies.values()}
        if healer_champs & enemy_champs:
            if "chempunk chainsword" not in items_lower and "mortal reminder" not in items_lower:
                if me.gold >= 2500:
                    return "Anti-heal: Chempunk or Mortal Reminder"

        return None
