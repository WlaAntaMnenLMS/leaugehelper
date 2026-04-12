"""
League Decision Advisor – entry point.

Usage
-----
    python main.py

The script waits for a League game to start (the Live Client API becomes
available), then runs the advisor loop until Ctrl-C or the game ends.

Architecture
------------
  main thread   – keeps Python alive and handles shutdown signals
  overlay thread – daemon thread running the tkinter window
  engine loop   – driven by the main thread via time.sleep polling
"""

import signal
import sys
import time

import api_client
import config
from chat_auto import ChatAuto
from decision_engine import DecisionEngine
from minimap_tracker import MinimapTracker
from overlay import Overlay


# ── Boot banner ──────────────────────────────────────────────────────────────

BANNER = r"""
  ╔══════════════════════════════════════╗
  ║      League Decision Advisor         ║
  ║  Enemy JG · Gank · Objectives · HUD  ║
  ╚══════════════════════════════════════╝
"""


def main():
    print(BANNER)

    # ── Wait for League to start ─────────────────────────────────────────────
    print("Waiting for a game to start (Live Client API)…")
    print("  → Start a League match, this will auto-connect.")
    print("  → Press Ctrl-C to quit.\n")

    while True:
        if api_client.is_game_running():
            break
        try:
            time.sleep(3)
        except KeyboardInterrupt:
            print("\nExiting.")
            sys.exit(0)

    print("Game detected!  Starting advisor…\n")

    # ── Initialise components ────────────────────────────────────────────────
    overlay  = Overlay()
    chat     = ChatAuto()
    minimap  = MinimapTracker()

    engine = DecisionEngine(
        overlay_cb=overlay.add_message,
        chat_cb=chat.send,
    )

    # ── Start overlay ────────────────────────────────────────────────────────
    overlay.start()
    time.sleep(0.6)   # let the window render

    overlay.add_message("◈ Advisor active", "info")
    overlay.add_message("Identifying enemy jungler…", "dim")
    if not chat.is_available:
        overlay.add_message("Auto-chat OFF (install pyautogui)", "dim")

    # ── Graceful shutdown on Ctrl-C / SIGTERM ────────────────────────────────
    def _shutdown(sig=None, frame=None):
        print("\nShutting down…")
        overlay.add_message("Advisor stopped.", "dim")
        time.sleep(0.8)
        overlay.stop()
        sys.exit(0)

    signal.signal(signal.SIGINT,  _shutdown)
    signal.signal(signal.SIGTERM, _shutdown)

    print("Advisor running.  Ctrl-C to stop.\n")

    # ── Main loop ────────────────────────────────────────────────────────────
    consecutive_failures = 0

    while True:
        try:
            engine.tick(minimap_tracker=minimap)
            consecutive_failures = 0

        except KeyboardInterrupt:
            _shutdown()

        except Exception as exc:
            consecutive_failures += 1
            print(f"[Engine error #{consecutive_failures}] {exc}")
            if consecutive_failures >= 10:
                overlay.add_message("API error – is the game still running?", "warn")
                consecutive_failures = 0

        time.sleep(config.POLL_INTERVAL)


if __name__ == "__main__":
    main()
