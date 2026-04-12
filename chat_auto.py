"""
Human-like auto-chat for League of Legends.

How it works
------------
1. The caller passes a message string to  send().
2. A short random pause fires (0.15–0.70 s) to simulate reaction time.
3. pyautogui presses Enter to open the chat box.
4. Each character is typed with a random inter-key delay (0.04–0.11 s).
5. Enter is pressed again to send the message.

Safety
------
• A minimum interval between messages (config.CHAT_MIN_INTERVAL seconds)
  is enforced so the advisor never floods chat.
• The game window must be focused for this to work; if the user alt-tabs the
  keystrokes land wherever focus is.  The engine stops sending during the
  loading screen (game_time < 30 s).
• CHAT_ENABLED in config.py lets the user turn this feature off entirely.
"""

import random
import threading
import time
from typing import Optional

import config

try:
    import pyautogui
    _PYAUTOGUI_OK = True
    # Disable pyautogui's built-in 0.1 s pause between actions
    pyautogui.PAUSE = 0.0
except ImportError:
    _PYAUTOGUI_OK = False


class ChatAuto:
    def __init__(self):
        self._lock          = threading.Lock()
        self._last_sent_at  = 0.0   # wall-clock time of last sent message
        self._enabled       = config.CHAT_ENABLED and _PYAUTOGUI_OK

        if config.CHAT_ENABLED and not _PYAUTOGUI_OK:
            print("[ChatAuto] pyautogui not installed – auto-chat disabled.")

    # ── Public API ───────────────────────────────────────────────────────────

    def send(self, message: str):
        """
        Queue a chat message to be sent asynchronously.
        Ignored if disabled or the cooldown has not elapsed.
        """
        if not self._enabled:
            return
        with self._lock:
            now = time.monotonic()
            if now - self._last_sent_at < config.CHAT_MIN_INTERVAL:
                return
            # Mark immediately to prevent a second call overtaking this one
            self._last_sent_at = now

        thread = threading.Thread(
            target=self._send_impl,
            args=(message,),
            daemon=True,
            name="chat-send",
        )
        thread.start()

    # ── Implementation ───────────────────────────────────────────────────────

    def _send_impl(self, message: str):
        try:
            min_d, max_d = config.CHAT_HUMAN_DELAY
            time.sleep(random.uniform(min_d, max_d))

            # Open chat
            pyautogui.press("enter")
            time.sleep(random.uniform(0.08, 0.18))

            # Type each character with human-like timing
            min_t, max_t = config.CHAT_TYPING_SPEED
            for ch in message:
                pyautogui.typewrite(ch, interval=random.uniform(min_t, max_t))

            time.sleep(random.uniform(0.07, 0.15))

            # Send
            pyautogui.press("enter")

        except Exception as exc:
            # Never crash the engine because of a chat failure
            print(f"[ChatAuto] Error sending '{message}': {exc}")

    # ── Diagnostics ──────────────────────────────────────────────────────────

    @property
    def is_available(self) -> bool:
        return self._enabled

    def set_enabled(self, value: bool):
        self._enabled = value and _PYAUTOGUI_OK
