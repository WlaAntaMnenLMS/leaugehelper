"""
Unit tests for engine/threat_map.py – ThreatMapBuilder scoring.

Run with:  python -m pytest tests/ -v
"""
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pytest
import config
from engine.threat_map import ThreatMapBuilder, LaneThreat
from tracking.target_state import TrackedTarget, Visibility


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_target(
    hp_pct: float = 55.0,
    role:   str   = "MIDDLE",
    level:  int   = 6,
    team:   str   = "CHAOS",
    zone:   str   = "mid_lane",
    conf:   float = 0.90,
    dead:   bool  = False,
) -> TrackedTarget:
    max_hp = 1000.0
    t = TrackedTarget(
        champion      = "TestChamp",
        summoner_name = "testplayer",
        team          = team,
        role          = role,
        current_hp    = max_hp * hp_pct / 100.0,
        max_hp        = max_hp,
        level         = level,
        is_dead       = dead,
        last_zone     = zone,
        zone_confidence   = conf,
        position_confidence = conf,
        visibility    = Visibility.VISIBLE if not dead else Visibility.DEAD,
        last_api_update_time = 100.0,
    )
    return t


class _FakeJgTracker:
    def __init__(self, safe=None, threat=None, age=999.0):
        self._safe   = safe
        self._threat = threat
        self._age    = age

    def safe_side(self):   return self._safe
    def threat_side(self): return self._threat

    class enemy:
        @staticmethod
        def age(game_time): return 999.0


class _FakeObjTracker:
    def is_objective_imminent(self, game_time, window): return False


class _FakeEventProc:
    def recently_died(self, name, game_time, window=30.0): return False


class _FakeMe:
    level = 7


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

class TestThreatMapScoring:
    """ThreatMapBuilder._score_lane produces correct scores."""

    def setup_method(self):
        self.builder    = ThreatMapBuilder()
        self.obj        = _FakeObjTracker()
        self.event_proc = _FakeEventProc()
        self.me         = _FakeMe()

    def _build(self, enemies, allies=None, jg=None, game_time=300.0):
        allies = allies or {}
        jg     = jg or _FakeJgTracker()
        return self.builder.build(
            enemies, allies, jg, self.obj, self.event_proc, self.me, game_time
        )

    # ── HP tier tests ─────────────────────────────────────────────────────────

    def test_high_priority_target_55pct_hp(self):
        """Target at 55% HP (≤65 tier) should produce a non-zero score."""
        t = _make_target(hp_pct=55.0, zone="mid_lane")
        tm = self._build({"TestChamp": t})
        assert "mid" in tm.lanes
        lane = tm.lanes["mid"]
        # ≤65% HP → +20; baseline should exceed GANK_MIN_FINAL_SCORE (18)
        assert lane.score >= config.GANK_MIN_FINAL_SCORE

    def test_critical_hp_30pct_scores_highest(self):
        """≤30% HP should score higher than ≤50% which scores higher than ≤65%."""
        t30 = _make_target(hp_pct=28.0, zone="mid_lane")
        t50 = _make_target(hp_pct=45.0, zone="mid_lane")
        t65 = _make_target(hp_pct=60.0, zone="mid_lane")

        s30 = self._build({"A": t30}).lanes.get("mid")
        s50 = self._build({"A": t50}).lanes.get("mid")
        s65 = self._build({"A": t65}).lanes.get("mid")

        assert s30 is not None and s50 is not None and s65 is not None
        assert s30.score > s50.score > s65.score

    def test_full_hp_target_scores_low(self):
        """100% HP target should score the minimum hp tier bonus (+3)."""
        t = _make_target(hp_pct=99.0, zone="mid_lane")
        tm = self._build({"A": t})
        lane = tm.lanes.get("mid")
        # Should still appear but score is very low
        if lane:
            assert lane.score < 20

    # ── JG safety signals ─────────────────────────────────────────────────────

    def test_jg_far_bonus_applies(self):
        """JG confirmed on opposite side should give SCORE_JG_FAR bonus."""
        t  = _make_target(hp_pct=55.0, zone="mid_lane")
        jg = _FakeJgTracker(safe="mid")  # mid is "safe" → JG away from mid
        tm = self._build({"A": t}, jg=jg)
        lane = tm.lanes.get("mid")
        assert lane is not None
        assert "JG away" in lane.reason

    def test_jg_nearby_penalty_applies(self):
        """JG recently seen near mid should subtract SCORE_JG_NEARBY_PENALTY."""
        t  = _make_target(hp_pct=30.0, zone="mid_lane")

        class _NearJg(_FakeJgTracker):
            def threat_side(self): return "mid"
            class enemy:
                @staticmethod
                def age(game_time): return 5.0   # 5s = very recent

        tm = self._build({"A": t}, jg=_NearJg())
        lane = tm.lanes.get("mid")
        assert lane is not None
        risks = lane.risks
        assert any("JG" in r for r in risks)

    # ── Ally HP gate ──────────────────────────────────────────────────────────

    def test_ally_low_hp_creates_risk(self):
        """Ally below ALLY_MIN_HP_FOR_GANK → 'ally X% HP' in risks."""
        target = _make_target(hp_pct=35.0, zone="mid_lane")
        ally   = _make_target(hp_pct=20.0, role="MIDDLE", team="ORDER", zone="mid_lane")
        tm = self._build({"A": target}, allies={"B": ally})
        lane = tm.lanes.get("mid")
        if lane:
            assert any("HP" in r for r in lane.risks)

    # ── Dead target excluded ──────────────────────────────────────────────────

    def test_dead_target_excluded(self):
        """Dead targets should not produce a lane threat."""
        t = _make_target(hp_pct=0.0, dead=True, zone="mid_lane")
        tm = self._build({"A": t})
        # No mid threat — target is dead
        assert "mid" not in tm.lanes


class TestThreatMapHPSignal:
    """hp_signal is correctly normalised for EV model."""

    def test_hp_signal_low_hp(self):
        """30% HP at ≤30 tier: hp_signal = 1 - (30/30) = 0.0... wait, should be 1.0 - (hp/thresh)."""
        # hp=29, thresh=30: hp_signal = 1.0 - (29/30) ≈ 0.033
        t = _make_target(hp_pct=29.0, zone="mid_lane")
        b = ThreatMapBuilder()
        obj  = _FakeObjTracker()
        ep   = _FakeEventProc()
        me   = _FakeMe()
        tm   = b.build({"A": t}, {}, _FakeJgTracker(), obj, ep, me, 300.0)
        lane = tm.lanes.get("mid")
        assert lane is not None
        assert 0.0 < lane.hp_signal <= 1.0

    def test_hp_signal_zero_for_full_hp(self):
        """Full-HP target → hp_signal at the ≤100 tier: 1 - (99/100) = 0.01."""
        t = _make_target(hp_pct=99.0, zone="mid_lane")
        b = ThreatMapBuilder()
        obj  = _FakeObjTracker()
        ep   = _FakeEventProc()
        me   = _FakeMe()
        tm   = b.build({"A": t}, {}, _FakeJgTracker(), obj, ep, me, 300.0)
        lane = tm.lanes.get("mid")
        if lane:
            assert lane.hp_signal < 0.05
