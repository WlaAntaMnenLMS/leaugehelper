"""
League Advisor – entry point.

Starts four logical threads:
  1. API thread        – polls Riot Live Client API every 1.5s
  2. Minimap thread    – captures and analyses minimap every 0.5s
  3. Decision thread   – scores all actions every 0.8s, writes to overlay queue
  4. Overlay (main)    – tkinter UI, polls queue every 300ms (must run on main thread)

The engine / minimap threads are daemon threads so they die with the process
when the user closes the overlay window.

Usage:
    python main.py [--calibrate]

    --calibrate   Run the minimap calibration tool instead of starting the advisor.
"""

from __future__ import annotations

import argparse
import queue
import sys
import threading
import time

import config
from engine.decision_engine import DecisionEngine
from voice.tts import TTS
from ui.overlay import Overlay


def main() -> None:
    parser = argparse.ArgumentParser(description="League Advisor")
    parser.add_argument("--calibrate", action="store_true",
                        help="Run minimap calibration tool")
    parser.add_argument("--no-voice", action="store_true",
                        help="Disable TTS voice output")
    args = parser.parse_args()

    if args.calibrate:
        from ui.calibrate import run_calibration
        run_calibration()
        return

    if args.no_voice:
        config.TTS_ENABLED = False

    print("[LeagueAdvisor] Starting…  (waiting for a game to be active)")

    # ── Shared queue: decision → overlay ─────────────────────────────────────
    overlay_queue: queue.Queue = queue.Queue(maxsize=50)

    # ── TTS ───────────────────────────────────────────────────────────────────
    tts = TTS()

    # ── Decision engine (starts API + decision threads internally) ────────────
    engine = DecisionEngine()
    engine.set_tts(tts)
    engine.start(overlay_queue)

    # ── Minimap thread ────────────────────────────────────────────────────────
    minimap_thread = threading.Thread(
        target=_minimap_loop,
        args=(engine,),
        name="Minimap-thread",
        daemon=True,
    )
    minimap_thread.start()

    # ── Wait for game to become active before showing overlay ─────────────────
    _wait_for_game(engine)
    print("[LeagueAdvisor] Game detected!  Opening overlay…")

    # ── Overlay (blocks main thread) ──────────────────────────────────────────
    overlay = Overlay(overlay_queue)
    overlay.run()

    # Cleanup
    engine.stop()
    print("[LeagueAdvisor] Overlay closed.  Exiting.")


# ---------------------------------------------------------------------------
# Minimap polling loop
# ---------------------------------------------------------------------------

def _minimap_loop(engine: DecisionEngine) -> None:
    """Capture minimap, detect dots, push into GameState."""
    from vision.minimap_reader import MinimapReader

    reader = MinimapReader()
    while True:
        try:
            _, dots = reader.get_snapshot()
            engine.state.update_minimap(dots)
        except Exception:
            pass
        time.sleep(config.POLL_MINIMAP_INTERVAL)


# ---------------------------------------------------------------------------
# Wait helper
# ---------------------------------------------------------------------------

def _wait_for_game(engine: DecisionEngine, timeout: float = 600) -> None:
    """Block until the game API responds or timeout (default 10 min)."""
    start = time.monotonic()
    dots = 0
    while not engine.is_game_active():
        elapsed = time.monotonic() - start
        if elapsed > timeout:
            print("\n[LeagueAdvisor] Timed out waiting for game.  Exiting.")
            sys.exit(1)
        dots = (dots + 1) % 4
        print(f"\r[LeagueAdvisor] Waiting for game{'.' * dots}   ", end="", flush=True)
        time.sleep(2)
    print()


if __name__ == "__main__":
    main()
