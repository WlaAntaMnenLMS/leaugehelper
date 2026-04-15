"""
Probabilistic Enemy Jungler Zone Belief.

Replaces the deterministic "last seen zone" with a probability distribution
over all map zones. The distribution:

  - Starts uniform (no information).
  - Collapses toward the observed zone on a minimap sighting.
  - Diffuses outward over time (JG could have walked anywhere).
  - Boosts adjacent zones when a kill event occurs near a lane.
  - Resets toward base zones when the JG is confirmed dead/recalled.

Zones (10):
  top_jungle, bot_jungle, top_river, bot_river, mid_lane,
  top_lane, bot_lane, blue_base, red_base, unknown

Output per tick:
  most_likely_zone  str
  confidence        float 0–1 (max prob)
  entropy           float 0–1 (0 = certain, 1 = maximum uncertainty)
  zone_probs        Dict[str, float]
  safe_side()       "top" | "bot" | None
  threat_lane()     "top" | "mid" | "bot" | None
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Dict, List, Optional

import config

# ---------------------------------------------------------------------------
# Zone graph
# ---------------------------------------------------------------------------

# Reachable-in-~15s adjacency (walking distances, not teleport)
_ADJACENCY: Dict[str, List[str]] = {
    "blue_base":  ["top_jungle", "bot_jungle", "top_lane", "bot_lane"],
    "red_base":   ["top_jungle", "bot_jungle", "top_lane", "bot_lane"],
    "top_jungle": ["blue_base", "red_base", "top_lane", "top_river", "mid_lane"],
    "bot_jungle": ["blue_base", "red_base", "bot_lane", "bot_river", "mid_lane"],
    "top_lane":   ["blue_base", "red_base", "top_jungle", "top_river"],
    "bot_lane":   ["blue_base", "red_base", "bot_jungle", "bot_river"],
    "mid_lane":   ["top_jungle", "bot_jungle", "top_river", "bot_river"],
    "top_river":  ["top_jungle", "top_lane", "mid_lane"],
    "bot_river":  ["bot_jungle", "bot_lane", "mid_lane"],
    "unknown":    [],
}

_ALL_ZONES = list(_ADJACENCY.keys())
_N         = len(_ALL_ZONES)
_UNIFORM   = {z: 1.0 / _N for z in _ALL_ZONES}


# ---------------------------------------------------------------------------
# Belief state
# ---------------------------------------------------------------------------

class JunglerBelief:
    """
    Bayesian-ish probability distribution over JG zones.

    Thread note: not internally locked; the caller (JunglerTracker /
    DecisionEngine) must protect concurrent access.
    """

    def __init__(self) -> None:
        self._probs: Dict[str, float]   = dict(_UNIFORM)
        self._last_game_time: float     = 0.0

    # ── Public updaters ───────────────────────────────────────────────────────

    def update_from_sighting(
        self,
        zone:      str,
        zone_conf: float,
        game_time: float,
    ) -> None:
        """
        Minimap dot confirmed in *zone* with classifier confidence *zone_conf*.
        Collapses the distribution toward that zone.
        """
        self._advance_time(game_time)

        # Likelihood ratio update: observed zone gets weight zone_conf,
        # all others share the remaining mass uniformly.
        noise = max(1e-6, (1.0 - zone_conf) / max(1, _N - 1))
        for z in _ALL_ZONES:
            if z == zone:
                self._probs[z] = self._probs.get(z, 0.0) * zone_conf
            else:
                self._probs[z] = self._probs.get(z, 0.0) * noise

        self._normalize()
        self._last_game_time = game_time

    def update_from_kill_event(
        self,
        kill_zone: str,
        game_time: float,
    ) -> None:
        """
        A kill happened in/near *kill_zone* → JG was probably close by.
        Boost the probability mass of that zone and its neighbours.
        """
        self._advance_time(game_time)

        # Boost the kill zone + adjacent zones
        boost_zones = set(_ADJACENCY.get(kill_zone, [])) | {kill_zone}
        for z in boost_zones:
            if z in self._probs:
                self._probs[z] *= 1.8

        self._normalize()
        self._last_game_time = game_time

    def tick(self, game_time: float) -> None:
        """Advance time without a new sighting — just diffuse the distribution."""
        self._advance_time(game_time)
        self._last_game_time = game_time

    def reset_to_uniform(self) -> None:
        """No information — uniform distribution."""
        self._probs = dict(_UNIFORM)

    def reset_to_base(self) -> None:
        """
        Called when JG is confirmed dead or recalled.
        Concentrates probability on base zones; small residual elsewhere.
        """
        base_prob = 0.70
        other     = (1.0 - base_prob) / max(1, _N - 2)
        self._probs = {z: other for z in _ALL_ZONES}
        self._probs["blue_base"] = base_prob / 2
        self._probs["red_base"]  = base_prob / 2
        self._normalize()

    # ── Queries ───────────────────────────────────────────────────────────────

    @property
    def most_likely_zone(self) -> str:
        return max(self._probs, key=lambda z: self._probs[z])

    @property
    def confidence(self) -> float:
        """Probability of the most likely zone (0–1)."""
        return max(self._probs.values())

    @property
    def entropy(self) -> float:
        """
        Normalised Shannon entropy: 0 = perfectly certain, 1 = uniform.
        Use this to gate aggressive suggestions.
        """
        raw = -sum(
            p * math.log(p + 1e-12)
            for p in self._probs.values()
        )
        return raw / math.log(_N) if _N > 1 else 0.0

    @property
    def zone_probs(self) -> Dict[str, float]:
        return dict(self._probs)

    def safe_side(self) -> Optional[str]:
        """
        Returns "top" or "bot" indicating the SAFER farming side, based on
        cumulative threat probability on each half.
        Returns None if uncertainty is too high to call.
        """
        top_threat = (
            self._probs.get("top_jungle", 0) * 1.0
            + self._probs.get("top_river",  0) * 1.2
            + self._probs.get("top_lane",   0) * 1.0
        )
        bot_threat = (
            self._probs.get("bot_jungle", 0) * 1.0
            + self._probs.get("bot_river",  0) * 1.2
            + self._probs.get("bot_lane",   0) * 1.0
        )

        min_conf = config.JG_BELIEF_MIN_THREAT_CONF
        diff     = top_threat - bot_threat

        if diff > min_conf:
            return "bot"   # top is more dangerous → bot is safer
        if diff < -min_conf:
            return "top"   # bot is more dangerous → top is safer
        return None        # too uncertain

    def threat_lane(self) -> Optional[str]:
        """
        Returns the lane most directly threatened by the JG right now.
        Returns None if too uncertain.
        """
        top = (
            self._probs.get("top_jungle", 0) * 0.7
            + self._probs.get("top_river",  0) * 1.0
            + self._probs.get("top_lane",   0) * 1.0
        )
        mid = self._probs.get("mid_lane", 0) * 1.0
        bot = (
            self._probs.get("bot_jungle", 0) * 0.7
            + self._probs.get("bot_river",  0) * 1.0
            + self._probs.get("bot_lane",   0) * 1.0
        )

        best = max(top, mid, bot)
        if best < config.JG_BELIEF_MIN_THREAT_CONF:
            return None

        if best == top:
            return "top"
        if best == mid:
            return "mid"
        return "bot"

    def prob_zone(self, zone: str) -> float:
        """Probability mass on a specific zone."""
        return self._probs.get(zone, 0.0)

    def label(self) -> str:
        """Short human-readable label for the overlay JG belief row."""
        conf = self.confidence
        zone = self.most_likely_zone.replace("_", " ")
        pct  = int(conf * 100)

        if conf >= 0.60:
            return f"likely {zone} ({pct}%)"
        if conf >= 0.35:
            return f"~{zone}? ({pct}%)"
        return f"unknown – {pct}% spread"

    # ── Internal helpers ──────────────────────────────────────────────────────

    def _advance_time(self, game_time: float) -> None:
        """Diffuse probability mass based on time elapsed since last update."""
        elapsed = max(0.0, game_time - self._last_game_time)
        if elapsed < 0.5:
            return

        rate  = config.JG_BELIEF_DIFFUSE_PER_S
        steps = min(int(elapsed), 45)   # cap to prevent huge jumps in diffusion

        for _ in range(steps):
            delta = {z: 0.0 for z in _ALL_ZONES}
            for z, p in self._probs.items():
                neighbours = _ADJACENCY.get(z, [])
                if not neighbours:
                    continue
                outflow = p * rate
                share   = outflow / len(neighbours)
                delta[z] -= outflow
                for n in neighbours:
                    delta[n] = delta.get(n, 0.0) + share

            for z in _ALL_ZONES:
                self._probs[z] = max(0.0, self._probs.get(z, 0.0) + delta.get(z, 0.0))

        self._normalize()

    def _normalize(self) -> None:
        total = sum(self._probs.values())
        if total <= 1e-9:
            self._probs = dict(_UNIFORM)
        else:
            self._probs = {z: v / total for z, v in self._probs.items()}
