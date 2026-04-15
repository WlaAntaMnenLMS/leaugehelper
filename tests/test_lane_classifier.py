"""
Unit tests for tracking/lane_classifier.py – zone classification correctness.
"""
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pytest
from tracking.lane_classifier import classify, is_extended


class TestClassify:
    """classify() returns correct zone for representative coordinates."""

    # ── Bases ────────────────────────────────────────────────────────────────
    def test_blue_base(self):
        zone, conf = classify(5.0, 93.0)
        assert zone == "blue_base"
        assert conf >= 0.90

    def test_red_base(self):
        zone, conf = classify(92.0, 5.0)
        assert zone == "red_base"
        assert conf >= 0.90

    # ── Lanes ─────────────────────────────────────────────────────────────────
    def test_top_lane_left_arm(self):
        zone, conf = classify(10.0, 50.0)   # deep left arm
        assert zone == "top_lane"
        assert conf >= 0.65

    def test_top_lane_top_arm(self):
        zone, conf = classify(50.0, 10.0)   # deep top arm
        assert zone == "top_lane"
        assert conf >= 0.65

    def test_bot_lane_bottom_arm(self):
        zone, conf = classify(50.0, 90.0)   # deep bottom arm
        assert zone == "bot_lane"
        assert conf >= 0.65

    def test_bot_lane_right_arm(self):
        zone, conf = classify(90.0, 50.0)   # deep right arm
        assert zone == "bot_lane"
        assert conf >= 0.65

    def test_mid_lane_centre(self):
        zone, conf = classify(47.5, 47.5)   # x+y ≈ 95, centre diagonal
        assert zone == "mid_lane"
        assert conf >= 0.50

    # ── River ─────────────────────────────────────────────────────────────────
    def test_top_river(self):
        zone, _ = classify(45.0, 30.0)
        assert zone == "top_river"

    def test_bot_river(self):
        zone, _ = classify(55.0, 65.0)
        assert zone == "bot_river"

    # ── Jungle quadrants ─────────────────────────────────────────────────────
    def test_top_jungle(self):
        # (25, 50): not a lane, not a river (x < 28), falls in top_jungle quadrant
        zone, _ = classify(25.0, 50.0)
        assert zone == "top_jungle"

    def test_bot_jungle(self):
        # (75, 75): not a lane, not bot_river (x > 74), falls in bot_jungle quadrant
        zone, _ = classify(75.0, 75.0)
        assert zone == "bot_jungle"

    # ── Confidence is always in [0, 1] ──────────────────────────────────────
    @pytest.mark.parametrize("mx,my", [
        (0, 0), (50, 50), (100, 100), (5, 90), (90, 5),
        (30, 70), (70, 30), (15, 15), (85, 85),
    ])
    def test_confidence_in_range(self, mx, my):
        _, conf = classify(mx, my)
        assert 0.0 <= conf <= 1.0


class TestIsExtended:
    """is_extended() correctly identifies when a laner is past the river."""

    def test_red_top_laner_extended_deep_blue_side(self):
        # Enemy top laner deep on blue side (left-top area) = extended vs ORDER
        assert is_extended(20.0, 35.0, "TOP", "ORDER") is True

    def test_red_top_laner_not_extended_near_base(self):
        # Near red base = not extended
        assert is_extended(80.0, 20.0, "TOP", "ORDER") is False

    def test_bot_extended_far_right(self):
        assert is_extended(75.0, 60.0, "BOTTOM", "ORDER") is True

    def test_mid_in_river_is_extended(self):
        # classify(45, 30) = top_river
        assert is_extended(45.0, 30.0, "MIDDLE", "ORDER") is True

    def test_mid_in_jungle_not_extended(self):
        # classify(25, 50) = top_jungle (not a river zone) → not extended
        assert is_extended(25.0, 50.0, "MIDDLE", "ORDER") is False
