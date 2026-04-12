"""
Decision Engine – threaded coordinator that ties everything together.

Two background daemon threads:
  1. API thread  – polls Riot Live Client every 1.5 s, updates GameState
  2. Decision thread – reads GameState every 0.8 s, runs ActionScorer,
                       updates the overlay slot queue, and fires TTS

The overlay thread lives in ui/overlay.py and reads from the slot queue.

Thread ownership:
  GameState ← written by API thread, read by decision thread (RLock)
  overlay queue ← written by decision thread, read by overlay thread
"""

from __future__ import annotations

import queue
import threading
import time
import traceback
from typing import Optional

import config
from engine.game_state import GameState
from engine.objective_tracker import ObjectiveTracker
from engine.action_scorer import ActionScorer
from engine.pathing import PathingAdvisor


class DecisionEngine:
    """
    Coordinates all background threads.

    Usage:
        engine = DecisionEngine()
        engine.start(overlay_queue)   # overlay_queue: queue.Queue
        ...
        engine.stop()
    """

    def __init__(self):
        self.state           = GameState()
        self.obj_tracker     = ObjectiveTracker()
        self.pathing_advisor = PathingAdvisor()
        self.scorer          = ActionScorer()

        # Slot queue: dict updates pushed to the overlay
        # Each item is a dict: {"slot": str, "label": str, "reason": str, "level": str}
        self._overlay_queue: Optional[queue.Queue] = None

        self._running = False
        self._api_thread:      Optional[threading.Thread] = None
        self._decision_thread: Optional[threading.Thread] = None

        # Champion module – set after identification
        self._champion_module = None

        # TTS (injected by main.py)
        self._tts = None

    def set_tts(self, tts) -> None:
        self._tts = tts

    # ── Start / stop ─────────────────────────────────────────────────────────

    def start(self, overlay_queue: queue.Queue) -> None:
        self._overlay_queue = overlay_queue
        self._running = True

        self._api_thread = threading.Thread(
            target=self._api_loop,
            name="API-thread",
            daemon=True,
        )
        self._decision_thread = threading.Thread(
            target=self._decision_loop,
            name="Decision-thread",
            daemon=True,
        )

        self._api_thread.start()
        self._decision_thread.start()

    def stop(self) -> None:
        self._running = False

    def is_game_active(self) -> bool:
        """Return True if we have received at least one valid API response."""
        with self.state.lock:
            return self.state.game_time > 0.0

    # ── API polling loop ──────────────────────────────────────────────────────

    def _api_loop(self) -> None:
        from api import live_client as lc

        while self._running:
            try:
                data = lc.get_all_game_data()
                if data:
                    self.state.update_from_api(data)
                    self.obj_tracker.update(self.state.objectives)
                    self._try_load_champion_module()
            except Exception:
                pass  # Game not running yet or API hiccup
            time.sleep(config.API_POLL_INTERVAL)

    # ── Decision loop ─────────────────────────────────────────────────────────

    def _decision_loop(self) -> None:
        while self._running:
            try:
                self._tick()
            except Exception:
                traceback.print_exc()
            time.sleep(config.DECISION_INTERVAL)

    def _tick(self) -> None:
        with self.state.lock:
            if self.state.game_time < 1.0:
                return   # No game data yet

            me         = self.state.me
            enemies    = dict(self.state.enemies)
            jg_tracker = self.state.jg_tracker
            game_time  = self.state.game_time

        # ── Score actions ─────────────────────────────────────────────────────
        self.scorer.champion_module = self._champion_module
        results = self.scorer.score_all(
            me, enemies, jg_tracker, self.obj_tracker, game_time
        )
        best = results[0] if results else None

        # ── JG slot ───────────────────────────────────────────────────────────
        jg_msg, jg_lvl = jg_tracker.status_message(game_time)
        self._push_slot("JG", jg_msg, "", jg_lvl)

        # ── Action slot ───────────────────────────────────────────────────────
        if best:
            self._push_slot("ACTION", best.label, best.reason, best.level)
            self._try_speak(best.tts_text, best.score)

        # ── Pathing slot ──────────────────────────────────────────────────────
        with self.state.lock:
            me_zone = self.state.jg_tracker.ally.last_zone or "unknown"
            jg_safe = jg_tracker.safe_side()

        path_label, path_lvl = self.pathing_advisor.pathing_label(
            me_zone, self.obj_tracker, game_time, jg_safe
        )
        self._push_slot("PATH", path_label, "", path_lvl)

        # ── Objective slot ────────────────────────────────────────────────────
        obj_warn = self.obj_tracker.get_warning(game_time)
        if obj_warn:
            self._push_slot("OBJ", obj_warn[0], "", obj_warn[1])
        else:
            # Show countdown even when not in warning window
            t_drag = self.obj_tracker.time_until_dragon(game_time)
            t_bar  = self.obj_tracker.time_until_baron(game_time)
            mins_d = int(t_drag // 60)
            secs_d = int(t_drag % 60)
            self._push_slot(
                "OBJ",
                f"Dragon {mins_d}:{secs_d:02d}",
                "",
                "dim",
            )

        # ── Build slot ────────────────────────────────────────────────────────
        if self._champion_module and hasattr(self._champion_module, "build_hint"):
            with self.state.lock:
                hint = self._champion_module.build_hint(me, enemies, game_time)
            if hint:
                self._push_slot("BUILD", hint, "", "info")

        # ── Recall hint ───────────────────────────────────────────────────────
        recall_hint = self.obj_tracker.recall_before_objective(game_time)
        if recall_hint and best and best.action != "RECALL":
            self._push_slot("OBJ", recall_hint, "", "warn")

    # ── Helpers ───────────────────────────────────────────────────────────────

    def _push_slot(
        self, slot: str, label: str, reason: str, level: str
    ) -> None:
        if self._overlay_queue is None:
            return
        try:
            self._overlay_queue.put_nowait(
                {"slot": slot, "label": label, "reason": reason, "level": level}
            )
        except queue.Full:
            pass

    def _try_speak(self, text: str, score: float) -> None:
        if self._tts is None:
            return
        if score >= config.TTS_MIN_SCORE:
            self._tts.speak(text)

    def _try_load_champion_module(self) -> None:
        """Load champion-specific module once we know who we're playing."""
        if self._champion_module is not None:
            return

        with self.state.lock:
            champ = self.state.me.champion.lower()

        if not champ:
            return

        module = None
        if champ == "kayn":
            from champions.kayn import KaynModule
            module = KaynModule()
        elif champ == "viego":
            from champions.viego import ViegoModule
            module = ViegoModule()
        elif champ == "warwick":
            from champions.warwick import WarwickModule
            module = WarwickModule()

        if module:
            self._champion_module = module
            self.pathing_advisor.champion = champ
