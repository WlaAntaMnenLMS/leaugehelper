"""
Unit tests for tracking/jungler_tracker.py – filter functions and tracker logic.
"""
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import math
import pytest
import config
from tracking.jungler_tracker import (
    JunglerTracker,
    _filter_non_laner_positions,
    _filter_by_velocity,
    _closest,
)


class TestFilterNonLanerPositions:
    """_filter_non_laner_positions removes base corners, borders, and high-conf lane dots."""

    def test_removes_blue_base_corner(self):
        # Blue base: low x, high y
        positions = [(5.0, 95.0)]
        result = _filter_non_laner_positions(positions)
        assert result == []

    def test_removes_red_base_corner(self):
        # Red base: high x, low y
        positions = [(95.0, 5.0)]
        result = _filter_non_laner_positions(positions)
        assert result == []

    def test_removes_extreme_border(self):
        positions = [(3.0, 50.0)]   # x < 6
        result = _filter_non_laner_positions(positions)
        assert result == []

    def test_jungle_position_passes_through(self):
        # (30, 30) is top_jungle; conf ~0.70, below JG_LANER_FILTER_CONF (0.70)
        # The classifier returns 0.70, which equals threshold → filter keeps it
        positions = [(30.0, 30.0)]
        result = _filter_non_laner_positions(positions)
        # Zone is top_jungle (not a lane) so it passes regardless of conf
        assert len(result) == 1

    def test_deep_top_lane_high_conf_excluded(self):
        # (10, 50) → top_lane with conf 0.88 (deep left arm)
        positions = [(10.0, 50.0)]
        result = _filter_non_laner_positions(positions)
        # conf 0.88 >= JG_LANER_FILTER_CONF (0.70) and zone is top_lane → excluded
        assert len(result) == 0

    def test_mid_lane_centre_excluded(self):
        # (47.5, 47.5) → mid_lane
        positions = [(47.5, 47.5)]
        result = _filter_non_laner_positions(positions)
        # High-conf mid → excluded
        assert len(result) == 0

    def test_river_position_passes(self):
        # (45, 30) → top_river; conf 0.72, zone is not a lane
        positions = [(45.0, 30.0)]
        result = _filter_non_laner_positions(positions)
        assert len(result) == 1


class TestFilterByVelocity:
    """_filter_by_velocity rejects candidates requiring superhuman movement."""

    def test_close_candidate_passes(self):
        candidates = [(50.0, 50.0)]
        last_pos   = (49.0, 49.0)
        elapsed    = 1.0
        result = _filter_by_velocity(candidates, last_pos, elapsed)
        assert result == candidates

    def test_far_candidate_rejected(self):
        candidates = [(80.0, 80.0)]
        last_pos   = (30.0, 30.0)
        elapsed    = 1.0
        # Distance ≈ 70.7 minimap %, max allowed = 5.0%/s * 1s = 5.0 → rejected
        result = _filter_by_velocity(candidates, last_pos, elapsed)
        assert result == []

    def test_boundary_candidate(self):
        # Exactly at the max distance
        max_dist   = config.JG_MAX_MOVE_PCT_PER_S * 2.0
        candidates = [(30.0 + max_dist, 30.0)]
        last_pos   = (30.0, 30.0)
        result = _filter_by_velocity(candidates, last_pos, 2.0)
        assert len(result) == 1

    def test_multiple_candidates_filtered(self):
        candidates = [(31.0, 30.0), (60.0, 60.0)]   # near, far
        last_pos   = (30.0, 30.0)
        result = _filter_by_velocity(candidates, last_pos, 1.0)
        assert len(result) == 1
        assert result[0] == (31.0, 30.0)


class TestClosest:
    def test_picks_closest(self):
        candidates = [(10.0, 10.0), (50.0, 50.0), (80.0, 80.0)]
        ref        = (12.0, 12.0)
        result     = _closest(candidates, ref)
        assert result == (10.0, 10.0)

    def test_single_candidate(self):
        result = _closest([(40.0, 40.0)], (0.0, 0.0))
        assert result == (40.0, 40.0)


class TestJunglerTrackerInit:
    def test_initial_state(self):
        jt = JunglerTracker()
        assert jt.enemy.champion == ""
        assert jt.ally.champion  == ""
        assert jt._identified    is False
        assert jt.belief is not None

    def test_status_message_before_identification(self):
        jt = JunglerTracker()
        msg, lvl = jt.status_message(60.0)
        assert "Identifying" in msg
        assert lvl == "dim"

    def test_safe_side_unknown(self):
        jt = JunglerTracker()
        assert jt.safe_side() is None

    def test_threat_side_unknown(self):
        jt = JunglerTracker()
        assert jt.threat_side() is None
