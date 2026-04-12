"""
Viego champion module.

Viego's unique mechanic: if he kills or assists in killing an enemy,
he briefly possesses them.  This creates:
  - Reset chains: kill → possess → recast → kill → possess → …
  - High skirmish value when multiple low-HP targets exist
  - Need to be near dying enemies to capitalise on possessions

Scoring biases:
  - GANK lanes where more than one enemy is low HP → big bonus
    (better reset potential)
  - GANK_MID: mid-laners tend to have lower HP thresholds → prefer
  - FARM when all targets are healthy → normal
  - INVADE: Viego is strong 1v1 pre-6 → small bonus early
  - When enemy jungler is dead → invade/cross_map big bonus
    (free possession window)

Build hints:
  - Early: Kraken Slayer path for carry / Goredrinker for survivability
  - Situational anti-heal if enemy has healing
"""

from __future__ import annotations

from typing import Optional
from champions.base import ChampionBase

KRAKEN_ITEMS   = {"kraken slayer", "phantom dancer", "infinity edge"}
GORE_ITEMS     = {"goredrinker", "sterak's gage"}


class ViegoModule(ChampionBase):
    champion_name = "viego"

    def score_modifier(self, action: str, me, game_time: float) -> float:
        mins = game_time / 60.0

        # ── Early game: invade to snowball ────────────────────────────────────
        if mins < 10:
            if action in ("INVADE_TOP", "INVADE_BOT"):
                return +10.0

        # ── Ganks: Viego thrives in extended fights + resets ──────────────────
        # Prefer mid ganks (squishier → possession is more impactful)
        if action == "GANK_MID":
            return +10.0
        if action == "GANK_BOT":
            return +8.0    # ADC is squishy → possession strong
        if action == "GANK_TOP":
            return +3.0    # Tanks resist; possession from tank is less powerful

        # ── Cross-map: Viego resets let him hard carry skirmishes ─────────────
        if action == "CROSS_MAP":
            return +6.0

        # ── Late game: Viego needs to be near fights, not farming ─────────────
        if mins > 20:
            if action in ("FARM_TOP_SIDE", "FARM_BOT_SIDE"):
                return -8.0

        return 0.0

    def build_hint(self, me, enemies: dict, game_time: float) -> Optional[str]:
        items_lower = {i.get("displayName", "").lower() for i in (me.items or [])}
        mins = game_time / 60.0

        # Check if enemy has heavy healing
        healer_champs = {"soraka", "yuumi", "aatrox", "dr. mundo", "vladimir"}
        enemy_champs  = {v.champion.lower() for v in enemies.values()}
        has_healers   = bool(healer_champs & enemy_champs)

        if not items_lower & KRAKEN_ITEMS and not items_lower & GORE_ITEMS:
            if me.gold >= 3100:
                return "Kraken Slayer (carry) or Goredrinker (survive)"

        if has_healers and "mortal reminder" not in items_lower and "chempunk chainsword" not in items_lower:
            if me.gold >= 2500:
                return "Anti-heal: Mortal Reminder or Chempunk Chainsword"

        if mins > 15 and "phantom dancer" not in items_lower and items_lower & KRAKEN_ITEMS:
            if me.gold >= 2600:
                return "Phantom Dancer (Kraken path)"

        return None
