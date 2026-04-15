"""
Unit tests for tracking/jungler_belief.py – JunglerBelief probability model.
"""
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import math
import pytest
from tracking.jungler_belief import JunglerBelief, _ALL_ZONES, _N


class TestJunglerBelief:
    """JunglerBelief starts uniform and updates correctly."""

    def test_initial_uniform(self):
        b = JunglerBelief()
        probs = b.zone_probs
        expected = 1.0 / _N
        for z, p in probs.items():
            assert abs(p - expected) < 1e-9, f"Zone {z}: expected {expected}, got {p}"

    def test_probs_sum_to_one(self):
        b = JunglerBelief()
        assert abs(sum(b.zone_probs.values()) - 1.0) < 1e-9

    def test_sighting_collapses_toward_zone(self):
        b = JunglerBelief()
        b.update_from_sighting("bot_jungle", 0.85, 60.0)
        assert b.zone_probs["bot_jungle"] > 1.0 / _N
        assert b.most_likely_zone == "bot_jungle"

    def test_sighting_probs_still_sum_to_one(self):
        b = JunglerBelief()
        b.update_from_sighting("top_jungle", 0.80, 30.0)
        assert abs(sum(b.zone_probs.values()) - 1.0) < 1e-9

    def test_reset_to_base_concentrates_on_base(self):
        b = JunglerBelief()
        b.update_from_sighting("bot_lane", 0.90, 100.0)
        b.reset_to_base()
        probs = b.zone_probs
        base_total = probs["blue_base"] + probs["red_base"]
        assert base_total >= 0.60, f"Base zones should dominate; got {base_total:.2f}"
        assert abs(sum(probs.values()) - 1.0) < 1e-9

    def test_reset_to_uniform(self):
        b = JunglerBelief()
        b.update_from_sighting("mid_lane", 0.95, 50.0)
        b.reset_to_uniform()
        expected = 1.0 / _N
        for z, p in b.zone_probs.items():
            assert abs(p - expected) < 1e-9

    def test_entropy_is_max_when_uniform(self):
        b = JunglerBelief()
        assert abs(b.entropy - 1.0) < 0.01

    def test_entropy_drops_after_sighting(self):
        b = JunglerBelief()
        b.update_from_sighting("top_jungle", 0.90, 60.0)
        assert b.entropy < 1.0

    def test_confidence_increases_after_sighting(self):
        b = JunglerBelief()
        initial_conf = b.confidence
        b.update_from_sighting("top_jungle", 0.85, 60.0)
        assert b.confidence > initial_conf

    def test_diffusion_over_time(self):
        b = JunglerBelief()
        b.update_from_sighting("top_jungle", 0.90, 0.0)
        conf_t0 = b.confidence
        b.tick(60.0)   # 60 seconds later
        conf_t60 = b.confidence
        # Confidence should decay (diffuse) over time
        assert conf_t60 < conf_t0

    def test_kill_event_boosts_kill_zone(self):
        b = JunglerBelief()
        p_before = b.zone_probs.get("top_lane", 0)
        b.update_from_kill_event("top_lane", 120.0)
        p_after  = b.zone_probs.get("top_lane", 0)
        assert p_after > p_before

    def test_safe_side_returns_none_when_uniform(self):
        """When distribution is uniform, neither side is clearly safer."""
        b = JunglerBelief()
        # With a very low threshold we might get a result, but with default
        # balanced config (JG_BELIEF_MIN_THREAT_CONF=0.25) uniform returns None
        import config
        if config.JG_BELIEF_MIN_THREAT_CONF > 0.05:
            assert b.safe_side() is None

    def test_safe_side_returns_bot_when_jg_is_top(self):
        """JG confirmed in top_jungle → bot side is safer."""
        b = JunglerBelief()
        # Strongly concentrate on top jungle
        for _ in range(5):
            b.update_from_sighting("top_jungle", 0.95, 60.0)
        result = b.safe_side()
        assert result == "bot", f"Expected 'bot', got {result!r}"

    def test_threat_lane_returns_top_when_jg_in_top_river(self):
        """JG in top_river → top lane threatened."""
        b = JunglerBelief()
        for _ in range(5):
            b.update_from_sighting("top_river", 0.90, 60.0)
        result = b.threat_lane()
        assert result == "top", f"Expected 'top', got {result!r}"

    def test_label_format(self):
        """label() returns a human-readable string."""
        b = JunglerBelief()
        for _ in range(4):
            b.update_from_sighting("bot_jungle", 0.88, 60.0)
        lbl = b.label()
        assert "bot" in lbl and "%" in lbl

    def test_multiple_sightings_increase_confidence(self):
        """Repeated sightings in the same zone should raise confidence monotonically."""
        b    = JunglerBelief()
        game_time = 10.0
        confs = []
        for i in range(5):
            b.update_from_sighting("top_jungle", 0.80, game_time)
            confs.append(b.confidence)
            game_time += 1.0   # tiny time steps so diffusion doesn't dominate
        # Confidence should be generally increasing (may plateau)
        assert confs[-1] >= confs[0]
