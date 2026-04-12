"""
Text-to-speech wrapper using pyttsx3.

Features:
  - Global cooldown: will not speak within TTS_COOLDOWN_S of ANY message
  - Per-message cooldown: will not repeat the same phrase within
    TTS_SAME_MSG_COOLDOWN_S seconds
  - Non-blocking: TTS runs in a daemon thread so it never stalls the UI

Thread safety: all public methods use a lock.
"""

from __future__ import annotations

import threading
import time
from typing import Dict, Optional

import config


class TTS:
    """
    Thread-safe, non-blocking TTS with cooldowns.

    Usage:
        tts = TTS()
        tts.speak("gank bot")
    """

    def __init__(self):
        self._lock           = threading.Lock()
        self._last_spoken_at = 0.0
        self._last_text:  str = ""
        self._per_msg_ts: Dict[str, float] = {}   # text → last-spoken game_time
        self._engine      = None
        self._enabled     = config.TTS_ENABLED
        self._ready       = False

        if self._enabled:
            self._init_thread = threading.Thread(
                target=self._init_engine,
                daemon=True,
                name="TTS-init",
            )
            self._init_thread.start()

    def _init_engine(self) -> None:
        try:
            import pyttsx3
            engine = pyttsx3.init()
            engine.setProperty("rate",   config.TTS_RATE)
            engine.setProperty("volume", config.TTS_VOLUME)
            with self._lock:
                self._engine = engine
                self._ready  = True
        except Exception as e:
            print(f"[TTS] pyttsx3 init failed: {e}")
            with self._lock:
                self._enabled = False

    def speak(self, text: str) -> None:
        """
        Queue a phrase to be spoken, subject to cooldowns.
        Does nothing if TTS is disabled, engine not ready, or on cooldown.
        """
        if not self._enabled:
            return

        now = time.monotonic()
        with self._lock:
            if not self._ready or self._engine is None:
                return
            # Global cooldown
            if now - self._last_spoken_at < config.TTS_COOLDOWN_S:
                return
            # Per-message cooldown
            last = self._per_msg_ts.get(text, 0.0)
            if now - last < config.TTS_SAME_MSG_COOLDOWN_S:
                return

            self._last_spoken_at    = now
            self._per_msg_ts[text]  = now
            engine = self._engine

        # Speak in background thread so we never block
        t = threading.Thread(
            target=self._speak_now,
            args=(engine, text),
            daemon=True,
            name=f"TTS-say-{text[:12]}",
        )
        t.start()

    @staticmethod
    def _speak_now(engine, text: str) -> None:
        try:
            engine.say(text)
            engine.runAndWait()
        except Exception:
            pass

    def set_enabled(self, enabled: bool) -> None:
        with self._lock:
            self._enabled = enabled
