"""
Augment Recommender – Arena / Mayhem / event mode support.

In Arena mode, augments are offered at levels 3, 6, 9, 12, … (approximately).
This module recommends which augment tier/type to prioritise when an augment
selection is due.

Because we cannot read the augment choices from memory, this module:
  - Detects that the game is in a non-CLASSIC mode (from GameState.game_mode)
  - Advises WHAT TYPE of augment to look for at each selection stage
  - Gives champion-specific augment priority hints

Augment archetypes (simplified):
  Prismatic (gold):  chase if it synergises with your champion
  Gold:              prefer utility/survivability in early rounds
  Silver:            filler; prioritise damage scaling

Champion-specific augment priorities are kept intentionally brief since
we can't enumerate all possible augments.  The hint is meant to remind
the player WHAT to look for, not to read their screen.
"""

from __future__ import annotations

from typing import Optional


# ---------------------------------------------------------------------------
# Per-champion augment focus by round
# ---------------------------------------------------------------------------

# champion → (early_focus, mid_focus, late_focus)
CHAMPION_AUGMENT_FOCUS = {
    "kayn": (
        "Ability Haste / extra dashes",
        "Lethality stacking or burst damage",
        "Omnivamp / healing if behind; execute if ahead",
    ),
    "viego": (
        "Attack Speed + On-Hit amplifiers",
        "Reset-enablers (execute, chain kill damage)",
        "Survivability so you survive to possess",
    ),
    "warwick": (
        "Grievous Wounds for opponents / Omnivamp for you",
        "Health stacking + tenacity",
        "Max HP percent damage to shred tanks",
    ),
}

DEFAULT_AUGMENT_FOCUS = (
    "Damage or utility — match your playstyle",
    "Fill a gap (tankiness, damage, or mobility)",
    "Win condition amplifier",
)


class AugmentRecommender:
    """
    Recommends augment priorities for non-CLASSIC game modes.

    Call get_hint(game_mode, champion, level) to get a short hint.
    """

    def get_hint(
        self,
        game_mode: str,
        champion: str,
        level: int,
        game_time: float,
    ) -> Optional[str]:
        """
        Return a short hint string for the overlay, or None if classic mode.
        """
        if game_mode.upper() == "CLASSIC":
            return None

        champ = champion.lower()
        early, mid, late = CHAMPION_AUGMENT_FOCUS.get(champ, DEFAULT_AUGMENT_FOCUS)

        # Augment stage by level approximation
        if level < 6:
            stage = "Early"
            focus = early
        elif level < 11:
            stage = "Mid"
            focus = mid
        else:
            stage = "Late"
            focus = late

        return f"[Augment {stage}] {focus}"

    def mode_label(self, game_mode: str) -> str:
        """Return a display label for the current game mode."""
        mode_names = {
            "ARAM":      "ARAM",
            "ARENA":     "Arena",
            "URF":       "URF",
            "ONEFORALL": "One For All",
            "CLASSIC":   "SR",
        }
        return mode_names.get(game_mode.upper(), game_mode)
