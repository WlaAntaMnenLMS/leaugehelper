"""
Fixed-slot always-on-top overlay using tkinter.

Layout (6 fixed rows, always visible):
  ┌─────────────────────────────────────────┐
  │ ≡  League Advisor              [×]      │  ← draggable header
  ├─────────────────────────────────────────┤
  │ JG   Kayn bot (8s)                      │
  │ ACT  GANK mid  –  Syndra 38% HP         │
  │ PTH  Red → Gromp → Dragon               │
  │ OBJ  Dragon 1:45  2 Drakes/1 Drake      │
  │ BLD  Rush Hubris (SA)                   │
  │ ALT  First blood – enemy down!          │  ← auto-expires after 8s
  └─────────────────────────────────────────┘

Slots update whenever a new dict is pushed to the queue:
  {"slot": "JG", "label": "...", "reason": "...", "level": "info"}

Levels map to colours:
  critical → red / bright
  warn     → orange
  info     → white / green
  dim      → grey

Thread safety:
  The overlay runs in the MAIN thread (tkinter requirement on Windows/macOS).
  Decision-engine pushes updates through a queue; the overlay polls it every
  300ms using Tk.after().

Topmost strategy:
  wm_attributes("-topmost", True) is reasserted every tick so games that
  steal the topmost flag don't permanently bury the overlay.
"""

from __future__ import annotations

import queue
import time
import tkinter as tk
from typing import Dict, Optional

import config

# Slot display names (ordered top-to-bottom)
SLOT_ORDER  = ["JG", "ACTION", "PATH", "OBJ", "BUILD", "ALERT"]
SLOT_LABELS = {
    "JG":     "JG",
    "ACTION": "ACT",
    "PATH":   "PTH",
    "OBJ":    "OBJ",
    "BUILD":  "BLD",
    "ALERT":  "ALT",
}

LEVEL_COLORS  = config.OVERLAY_COLORS   # {"critical": ..., "warn": ..., ...}
REFRESH_MS    = 300    # How often to poll the queue and reassert topmost
ALERT_SHOW_S  = 8.0   # Seconds before the ALERT row auto-clears


class Overlay:
    """
    Fixed-slot tkinter overlay.

    Usage (must be called from the main thread):
        overlay = Overlay(update_queue)
        overlay.run()   # blocks; call overlay.close() to exit
    """

    def __init__(self, update_queue: queue.Queue):
        self._queue  = update_queue
        self._root:   Optional[tk.Tk]         = None
        self._slots:  Dict[str, dict]          = {}
        self._labels: Dict[str, tk.Label]      = {}

        # ALERT auto-expiry: wall-clock time when the slot should clear
        self._alert_expire_at: float = 0.0

        # Drag state
        self._drag_x = 0
        self._drag_y = 0

    def run(self) -> None:
        """Build window and start the event loop (blocking)."""
        self._root = root = tk.Tk()
        root.title("League Advisor")
        root.overrideredirect(True)           # No OS title bar
        root.wm_attributes("-topmost", True)
        root.wm_attributes("-alpha",   config.OVERLAY_ALPHA)
        root.configure(bg=config.OVERLAY_BG)
        root.geometry(f"+{config.OVERLAY_X}+{config.OVERLAY_Y}")

        self._build_ui(root)

        root.after(REFRESH_MS, self._tick)
        root.mainloop()

    def close(self) -> None:
        if self._root:
            self._root.quit()

    # ── UI construction ────────────────────────────────────────────────────────

    def _build_ui(self, root: tk.Tk) -> None:
        font_header   = (config.OVERLAY_FONT, 9, "bold")
        font_slot_key = (config.OVERLAY_FONT, 9, "bold")
        font_slot_val = (config.OVERLAY_FONT, config.OVERLAY_FONT_SIZE)

        # ── Header ────────────────────────────────────────────────────────────
        header = tk.Frame(root, bg=config.OVERLAY_HEADER_BG, cursor="fleur")
        header.pack(fill=tk.X)

        tk.Label(
            header, text="≡ League Advisor",
            bg=config.OVERLAY_HEADER_BG,
            fg=config.OVERLAY_HEADER_FG,
            font=font_header,
            padx=6, pady=3,
        ).pack(side=tk.LEFT)

        close_btn = tk.Label(
            header, text="×",
            bg=config.OVERLAY_HEADER_BG,
            fg="#888888",
            font=(config.OVERLAY_FONT, 11, "bold"),
            padx=6, cursor="hand2",
        )
        close_btn.pack(side=tk.RIGHT)
        close_btn.bind("<Button-1>", lambda _: self.close())

        header.bind("<ButtonPress-1>", self._on_drag_start)
        header.bind("<B1-Motion>",     self._on_drag_motion)

        # ── Separator ─────────────────────────────────────────────────────────
        tk.Frame(root, bg="#333333", height=1).pack(fill=tk.X)

        # ── Slot rows ─────────────────────────────────────────────────────────
        slots_frame = tk.Frame(root, bg=config.OVERLAY_BG, padx=6, pady=4)
        slots_frame.pack(fill=tk.BOTH, expand=True)

        for slot in SLOT_ORDER:
            row = tk.Frame(slots_frame, bg=config.OVERLAY_BG)
            row.pack(fill=tk.X, pady=1)

            # Use a subtler colour for the ALERT key label to distinguish it
            key_fg = "#cc8833" if slot == "ALERT" else "#666666"

            key_lbl = tk.Label(
                row,
                text=SLOT_LABELS.get(slot, slot),
                bg=config.OVERLAY_BG,
                fg=key_fg,
                font=font_slot_key,
                width=4,
                anchor="w",
            )
            key_lbl.pack(side=tk.LEFT)

            val_lbl = tk.Label(
                row,
                text="—",
                bg=config.OVERLAY_BG,
                fg=LEVEL_COLORS.get("dim", "#888888"),
                font=font_slot_val,
                anchor="w",
                wraplength=config.OVERLAY_WIDTH - 60,
            )
            val_lbl.pack(side=tk.LEFT, fill=tk.X, expand=True)

            self._labels[slot] = val_lbl

    # ── Periodic tick ──────────────────────────────────────────────────────────

    def _tick(self) -> None:
        if self._root is None:
            return

        # Reassert topmost every tick (handles games that steal focus)
        self._root.wm_attributes("-topmost", True)
        self._root.lift()

        # Drain the update queue
        try:
            while True:
                update = self._queue.get_nowait()
                self._apply_update(update)
        except queue.Empty:
            pass

        # Auto-expire the ALERT slot
        if self._alert_expire_at > 0 and time.time() >= self._alert_expire_at:
            lbl = self._labels.get("ALERT")
            if lbl:
                lbl.config(text="", fg=LEVEL_COLORS.get("dim", "#888888"))
            self._alert_expire_at = 0.0

        self._root.after(REFRESH_MS, self._tick)

    def _apply_update(self, update: dict) -> None:
        slot   = update.get("slot",   "")
        label  = update.get("label",  "")
        reason = update.get("reason", "")
        level  = update.get("level",  "info")

        lbl_widget = self._labels.get(slot)
        if lbl_widget is None:
            return

        text  = f"{label}  –  {reason}" if reason else label
        color = LEVEL_COLORS.get(level, LEVEL_COLORS.get("info", "#FFFFFF"))

        # Handle ALERT auto-expiry
        if slot == "ALERT":
            if label:
                # New alert received – start expiry timer
                self._alert_expire_at = time.time() + ALERT_SHOW_S
                lbl_widget.config(text=text, fg=color)
            # Ignore empty pushes (don't clear early; let timer handle it)
            return

        lbl_widget.config(text=text, fg=color)

    # ── Drag ──────────────────────────────────────────────────────────────────

    def _on_drag_start(self, event: tk.Event) -> None:
        self._drag_x = event.x_root - self._root.winfo_x()
        self._drag_y = event.y_root - self._root.winfo_y()

    def _on_drag_motion(self, event: tk.Event) -> None:
        x = event.x_root - self._drag_x
        y = event.y_root - self._drag_y
        self._root.geometry(f"+{x}+{y}")
