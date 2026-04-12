"""
Objective Timer Tracker.

Tracks dragon / baron / herald spawn windows and generates reminders.
The dragon timing logic from the old version was one of its few strengths;
this module keeps it and improves it with:
  - Countdown messages (e.g. "Dragon in 38s")
  - Recall-before-objective advice
  - Priority gating (don't suggest gank if Baron spawns in 30s)
"""

from __future__ import annotations

from typing import Optional, Tuple
import config


class ObjectiveTracker:

    def __init__(self):
        # Track spawned/killed timestamps
        self._dragon_killed_at:  float = 0.0
        self._baron_killed_at:   float = 0.0
        self._herald_killed_at:  float = 0.0

    # ── Ingestion ────────────────────────────────────────────────────────────

    def update(self, objectives) -> None:
        """Ingest ObjectiveTimers from GameState."""
        self._dragon_killed_at = objectives.dragon_killed_at
        self._baron_killed_at  = objectives.baron_killed_at
        self._herald_killed_at = objectives.herald_killed_at

    # ── Spawn time queries ───────────────────────────────────────────────────

    def next_dragon_spawn(self) -> float:
        """Returns game-time (seconds) when dragon next spawns."""
        if self._dragon_killed_at > 0:
            return self._dragon_killed_at + config.DRAGON_RESPAWN * 60
        return config.DRAGON_FIRST_SPAWN * 60

    def next_baron_spawn(self) -> float:
        if self._baron_killed_at > 0:
            return self._baron_killed_at + config.BARON_RESPAWN * 60
        return config.BARON_FIRST_SPAWN * 60

    def next_herald_window(self) -> Optional[float]:
        """Returns the game-time when Herald is available, or None if expired."""
        if self._herald_killed_at > 0:
            return None   # Herald was killed; Baron takes over at 20 min
        return config.HERALD_SPAWN * 60

    # ── Time-until helpers ───────────────────────────────────────────────────

    def time_until_dragon(self, game_time: float) -> float:
        return max(0.0, self.next_dragon_spawn() - game_time)

    def time_until_baron(self, game_time: float) -> float:
        if game_time < config.BARON_FIRST_SPAWN * 60:
            return config.BARON_FIRST_SPAWN * 60 - game_time
        return max(0.0, self.next_baron_spawn() - game_time)

    def time_until_herald(self, game_time: float) -> Optional[float]:
        w = self.next_herald_window()
        if w is None:
            return None
        if game_time > config.HERALD_DESPAWN * 60:
            return None
        return max(0.0, w - game_time)

    # ── Warning messages ─────────────────────────────────────────────────────

    def get_warning(self, game_time: float) -> Optional[Tuple[str, str]]:
        """
        Return (message, level) for the most pressing objective timer,
        or None if no objective is imminent.
        """
        warn = config.OBJECTIVE_WARN_S

        # Baron trumps everything in late game
        if game_time >= (config.BARON_FIRST_SPAWN - 1) * 60:
            t = self.time_until_baron(game_time)
            if 0 < t <= warn:
                secs = int(t)
                if secs <= 30:
                    return f"Baron in {secs}s  → PATH NOW", "critical"
                return f"Baron in {secs}s  → prepare", "warn"

        # Dragon
        t_drag = self.time_until_dragon(game_time)
        if 0 < t_drag <= warn:
            secs = int(t_drag)
            if secs <= 20:
                return f"Dragon in {secs}s  → CONTEST NOW", "critical"
            if secs <= 45:
                return f"Dragon in {secs}s  → path bot", "warn"
            return f"Dragon in {secs}s  → prepare", "info"

        # Rift Herald
        t_her = self.time_until_herald(game_time)
        if t_her is not None and 0 < t_her <= warn:
            despawn_left = config.HERALD_DESPAWN * 60 - game_time
            if despawn_left <= 45:
                return f"Herald despawns in {int(despawn_left)}s  → contest!", "critical"
            secs = int(t_her)
            if secs <= 45:
                return f"Herald in {secs}s  → path top river", "warn"

        return None

    def recall_before_objective(self, game_time: float) -> Optional[str]:
        """
        Returns a hint like "Recall now, dragon in 75s" when there's just
        enough time to recall and path to the objective.
        Recall window: objective in 60–100s.
        """
        # Dragon
        t = self.time_until_dragon(game_time)
        if 60 < t <= 105:
            return f"Recall now  →  Dragon in {int(t)}s"

        # Baron
        if game_time >= (config.BARON_FIRST_SPAWN - 1) * 60:
            t = self.time_until_baron(game_time)
            if 60 < t <= 105:
                return f"Recall now  →  Baron in {int(t)}s"

        return None

    def is_objective_imminent(self, game_time: float, window_s: float = 45.0) -> bool:
        """Return True if any major objective spawns within window_s seconds."""
        if self.time_until_dragon(game_time) <= window_s:
            return True
        if game_time >= (config.BARON_FIRST_SPAWN - 1) * 60:
            if self.time_until_baron(game_time) <= window_s:
                return True
        t_her = self.time_until_herald(game_time)
        if t_her is not None and t_her <= window_s:
            return True
        return False
