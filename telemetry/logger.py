"""
Structured logger + health counters for the League Advisor.

Usage:
    from telemetry.logger import get_logger, health

    log = get_logger(__name__)
    log.info("API poll took %.1fms", elapsed * 1000)
    health.increment("api_errors")
    health.increment("minimap_frames")

Health counter keys (by convention):
    api_errors          – HTTP failures / JSON parse errors
    api_polls           – successful API polls
    minimap_errors      – capture / OpenCV failures
    minimap_frames      – successful minimap captures
    stale_snapshots     – decision ticks where game_time hasn't advanced
    dropped_updates     – queue.Full when pushing to overlay
    decision_ticks      – total decision engine ticks
    tts_calls           – TTS speak() invocations
"""

from __future__ import annotations

import logging
import threading
from collections import defaultdict
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import Dict

# ── Log file setup ─────────────────────────────────────────────────────────────

_LOG_DIR  = Path("logs")
_LOG_DIR.mkdir(exist_ok=True)
_LOG_FILE = _LOG_DIR / "advisor.log"

_FORMATTER = logging.Formatter(
    "%(asctime)s [%(levelname)-8s] %(name)s: %(message)s",
    datefmt="%H:%M:%S",
)

# Module-level cache so get_logger() is idempotent
_loggers: Dict[str, logging.Logger] = {}
_logger_lock = threading.Lock()


def get_logger(name: str, console_level: int = logging.INFO) -> logging.Logger:
    """
    Return a logger that writes DEBUG+ to rotating file and INFO+ to console.
    Safe to call from multiple threads; returns the same logger for the same name.
    """
    with _logger_lock:
        if name in _loggers:
            return _loggers[name]

        log = logging.getLogger(name)
        log.setLevel(logging.DEBUG)

        # Rotating file handler (5 MB, 3 backups)
        fh = RotatingFileHandler(
            _LOG_FILE, maxBytes=5 * 1024 * 1024, backupCount=3, encoding="utf-8"
        )
        fh.setLevel(logging.DEBUG)
        fh.setFormatter(_FORMATTER)
        log.addHandler(fh)

        # Console handler
        ch = logging.StreamHandler()
        ch.setLevel(console_level)
        ch.setFormatter(_FORMATTER)
        log.addHandler(ch)

        # Prevent propagation to root logger (avoid duplicate output)
        log.propagate = False

        _loggers[name] = log
        return log


# ── Health counters ────────────────────────────────────────────────────────────

class _HealthCounters:
    """
    Thread-safe integer counters for operational metrics.
    Singleton – import `health` to use.
    """

    def __init__(self) -> None:
        self._lock   = threading.Lock()
        self._counts: Dict[str, int] = defaultdict(int)

    def increment(self, key: str, amount: int = 1) -> None:
        with self._lock:
            self._counts[key] += amount

    def get(self, key: str) -> int:
        with self._lock:
            return self._counts[key]

    def snapshot(self) -> Dict[str, int]:
        with self._lock:
            return dict(self._counts)

    def reset(self) -> None:
        with self._lock:
            self._counts.clear()

    def summary_line(self) -> str:
        s = self.snapshot()
        return (
            f"api={s.get('api_polls',0)}/{s.get('api_errors',0)}err "
            f"mm={s.get('minimap_frames',0)}/{s.get('minimap_errors',0)}err "
            f"stale={s.get('stale_snapshots',0)} "
            f"dropped={s.get('dropped_updates',0)} "
            f"ticks={s.get('decision_ticks',0)}"
        )


# Public singleton
health = _HealthCounters()
