"""
Unit tests for tracking/target_state.py – TrackedTarget state machine.
"""
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pytest
from tracking.target_state import TrackedTarget, Visibility
import config


def _fresh(hp_pct=100.0, role="MIDDLE", dead=False) -> TrackedTarget:
    t = TrackedTarget(
        champion="Syndra",
        summoner_name="testplayer",
        team="CHAOS",
        role=role,
        current_hp=1000.0 * hp_pct / 100.0,
        max_hp=1000.0,
        level=6,
        is_dead=dead,
    )
    return t


class TestHPPercent:
    def test_full_hp(self):
        t = _fresh(100.0)
        assert t.hp_percent == pytest.approx(100.0)

    def test_half_hp(self):
        t = _fresh(50.0)
        assert t.hp_percent == pytest.approx(50.0)

    def test_no_div_by_zero(self):
        t = TrackedTarget(max_hp=0.0)
        assert t.hp_percent == 100.0


class TestHPValidity:
    def test_visible_returns_hp(self):
        t = _fresh(40.0)
        t.visibility = Visibility.VISIBLE
        assert t.get_valid_hp_for_gank(100.0) == pytest.approx(40.0)

    def test_fresh_api_returns_hp(self):
        t = _fresh(55.0)
        t.last_api_update_time = 100.0
        result = t.get_valid_hp_for_gank(103.0)   # 3s < HP_VALIDITY_S (7)
        assert result == pytest.approx(55.0)

    def test_stale_api_returns_none(self):
        t = _fresh(55.0)
        t.last_api_update_time = 0.0   # never updated
        t.last_valid_hp_game_time = 0.0
        result = t.get_valid_hp_for_gank(200.0)
        assert result is None

    def test_dead_returns_none(self):
        t = _fresh(dead=True)
        assert t.get_valid_hp_for_gank(100.0) is None


class TestFedStarved:
    def test_fed_three_kills_more_kills(self):
        t = _fresh()
        t.kills = 4; t.deaths = 1
        assert t.is_fed is True

    def test_not_fed_too_few_kills(self):
        t = _fresh()
        t.kills = 2; t.deaths = 0
        assert t.is_fed is False

    def test_starved_three_deaths(self):
        t = _fresh()
        t.kills = 0; t.deaths = 4
        assert t.is_starved is True

    def test_not_starved_when_equal(self):
        t = _fresh()
        t.kills = 3; t.deaths = 3
        assert t.is_starved is False


class TestConfidenceDecay:
    def test_recently_seen_high_conf(self):
        t = _fresh()
        t.last_seen_game_time = 100.0
        t.visibility          = Visibility.RECENTLY_SEEN
        t.update_confidence(100.0 + config.TARGET_RECENTLY_SEEN_S - 1)
        assert t.position_confidence >= 0.85

    def test_expired_low_conf(self):
        t = _fresh()
        t.last_seen_game_time = 0.0
        t.update_confidence(config.TARGET_EXPIRED_S + 10)
        # Without a minimap sighting, confidence is based on API data age
        # which is also 0; so we expect either expired or unknown
        assert t.position_confidence <= 0.20

    def test_dead_target_zero_conf(self):
        t = _fresh(dead=True)
        t.update_confidence(500.0)
        assert t.position_confidence == 0.0
        assert t.visibility == Visibility.DEAD


class TestMinimap:
    def test_update_from_minimap_sets_visible(self):
        t = _fresh()
        t.update_from_minimap((40.0, 50.0), "mid_lane", 0.85, 60.0)
        assert t.visibility          == Visibility.VISIBLE
        assert t.position_confidence == 1.0
        assert t.last_zone           == "mid_lane"
        assert t.zone_inferred       is False

    def test_mark_not_visible(self):
        t = _fresh()
        t.visibility = Visibility.VISIBLE
        t.mark_not_visible()
        assert t.visibility == Visibility.RECENTLY_SEEN


class TestBackDetection:
    def test_back_detected_on_hp_jump(self):
        """HP jumping from 40% to 95% should set back_detected."""
        t = _fresh(40.0)
        t._prev_hp_pct = 40.0
        # Simulate an API update where HP jumps to 95%
        player_dict = {
            "currentHealth": 950.0,
            "maxHealth":     1000.0,
            "level":         6,
            "items":         [],
            "scores":        {"kills": 0, "deaths": 0, "assists": 0, "creepScore": 0},
        }
        # Patch is_player_dead to return False
        import api.live_client as lc
        orig = lc.is_player_dead
        lc.is_player_dead = lambda p: False
        try:
            t.update_from_api(player_dict, 200.0)
        finally:
            lc.is_player_dead = orig
        assert t.back_detected is True
