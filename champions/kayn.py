"""
Kayn champion module.

Kayn has two forms: Shadow Assassin (SA) and Rhaast (Red).
The form transforms the optimal playstyle dramatically.

Form detection:
  - We cannot read game memory, so we estimate form from:
    a) Item builds (Hubris → SA, Sundered Sky → Rhaast)
    b) Game time heuristics (form typically decided by 12–15 min)
  - If no form detected, display a hint to collect stacks early.

Scoring biases:
  SA (Shadow Assassin):
    - Prefers ganking squishy targets (ADC/mid mage)
    - Prefers roaming and cross-map plays
    - Penalises staying in jungle too long (falls off if passive)
  Rhaast (Red Kayn):
    - Prefers tanky enemies and team-fight setups
    - Values bot-lane ganks (more teamfight presence)
    - Less reward on invades (better sustained fighting)
  Pre-form:
    - Big bonus to GANK_MID and GANK_BOT to collect blue stacks
    - Small bonus to INVADE (orb collection)
"""

from __future__ import annotations

from typing import Optional
from champions.base import ChampionBase


# Items that signal SA
SA_ITEMS   = {"hubris", "opportunity", "edge of night", "serylda's grudge"}
# Items that signal Rhaast
RHAAST_ITEMS = {"sundered sky", "black cleaver", "sterak's gage", "heartsteel"}


class KaynModule(ChampionBase):
    champion_name = "kayn"

    def __init__(self):
        self._form: str = "pre"   # "pre", "sa", "rhaast"
        self._form_detected_at: float = 0.0

    # ── Form detection ────────────────────────────────────────────────────────

    def _detect_form(self, me) -> str:
        items_lower = {i.get("displayName", "").lower() for i in (me.items or [])}
        if items_lower & SA_ITEMS:
            return "sa"
        if items_lower & RHAAST_ITEMS:
            return "rhaast"
        return "pre"

    # ── Score modifier ────────────────────────────────────────────────────────

    def score_modifier(self, action: str, me, game_time: float) -> float:
        form = self._detect_form(me)
        mins = game_time / 60.0

        # ── Pre-form: prioritise gank and invade for orb collection ───────────
        if form == "pre":
            if action in ("GANK_MID", "GANK_BOT"):
                return +12.0   # blue ranged targets give SA orbs
            if action in ("GANK_TOP",):
                return +8.0    # melee gives Rhaast orbs
            if action in ("INVADE_TOP", "INVADE_BOT"):
                return +6.0
            if action in ("FARM_TOP_SIDE", "FARM_BOT_SIDE") and mins < 10:
                return -8.0    # don't just farm early — get orbs
            return 0.0

        # ── SA: burst-oriented, roam, punish squishies ────────────────────────
        if form == "sa":
            if action in ("GANK_MID", "GANK_BOT"):
                return +15.0   # squishier targets, better for SA
            if action == "GANK_TOP":
                return -5.0    # tanks resist SA
            if action in ("INVADE_TOP", "INVADE_BOT"):
                return +10.0   # SA excels at skirmishes
            if action == "CROSS_MAP":
                return +8.0
            if action in ("FARM_TOP_SIDE", "FARM_BOT_SIDE") and mins > 12:
                return -10.0   # SA falls off if passive
            return 0.0

        # ── Rhaast: sustained fighting, teamfight bot ─────────────────────────
        if form == "rhaast":
            if action == "GANK_BOT":
                return +12.0   # ADC + teamfight
            if action == "GANK_TOP":
                return +8.0    # tolerates tanks
            if action in ("INVADE_TOP", "INVADE_BOT"):
                return -8.0    # risky for sustained style
            if action in ("FARM_TOP_SIDE", "FARM_BOT_SIDE"):
                return +5.0    # Rhaast needs items
            return 0.0

        return 0.0

    # ── Build hint ────────────────────────────────────────────────────────────

    def build_hint(self, me, enemies: dict, game_time: float) -> Optional[str]:
        form = self._detect_form(me)
        mins = game_time / 60.0

        if form == "pre":
            if mins < 12:
                return "Collect orbs: gank bot/mid for SA, top for Rhaast"
            return "Pick a form – gank to collect remaining orbs"

        if form == "sa":
            items_lower = {i.get("displayName", "").lower() for i in (me.items or [])}
            if "hubris" not in items_lower and me.gold >= 2600:
                return "Rush Hubris (SA)"
            if "edge of night" not in items_lower and me.gold >= 2800:
                return "Edge of Night next (SA)"
            return None

        if form == "rhaast":
            items_lower = {i.get("displayName", "").lower() for i in (me.items or [])}
            if "sundered sky" not in items_lower and me.gold >= 3000:
                return "Rush Sundered Sky (Rhaast)"
            if "black cleaver" not in items_lower and me.gold >= 3000:
                return "Black Cleaver next (Rhaast)"
            return None

        return None
