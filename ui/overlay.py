"""
Fixed-slot always-on-top overlay using tkinter.

Layout (5 fixed rows, always visible):
  ┌─────────────────────────────────────────┐
  │ ≡  League Advisor         [×]           │  ← draggable header
  ├─────────────────────────────────────────┤
  │ JG   Kayn bot (8s)                      │
  │ ACT  Gank mid  –  Syndra 38% HP         │
  │ PATH Red → Gromp → Dragon               │
  │ OBJ  Dragon in 45s → path bot           │
  │ BLD  Rush Hubris (SA)                   │
  └─────────────────────────────────────────┘

Slots update whenever a new dict is pushed to the queue:
  {"slot": "JG", "label": "...", "reason": "...", "level": "info"}

Levels map to colours:
  critical → red / bright
  warn     → orange
  info     → white
  dim      → grey

Thread safety:
  The overlay runs in the MAIN thread (tkinter requirement on Windows/macOS).
  Decision-engine pushes updates through a queue; the overlay polls it every
  300ms using Tk.after().

Topmost strategy:
  We call wm_attributes("-topmost", True) every 300ms to handle games that
  steal the topmost flag.  This is the most reliable approach without injecting
  into the window stack.
"""

from __future__ import annotations

import queue
import tkinter as tk
from typing import Dict, Optional

import config

# Slot display names (ordered top-to-bottom)
SLOT_ORDER  = ["JG", "ACTION", "PATH", "OBJ", "BUILD"]
SLOT_LABELS = {
    "JG":     "JG",
    "ACTION": "ACT",
    "PATH":   "PTH",
    "OBJ":    "OBJ",
    "BUILD":  "BLD",
}

LEVEL_COLORS = config.OVERLAY_COLORS   # {"critical": ..., "warn": ..., ...}

REFRESH_MS   = 300   # How often to poll the queue and reassert topmost


class Overlay:
    """
    Fixed-slot tkinter overlay.

    Usage (must be called from the main thread):
        overlay = Overlay(update_queue)
        overlay.run()   # blocks; call overlay.close() to exit
    """

    def __init__(self, update_queue: queue.Queue):
        self._queue = update_queue
        self._root:  Optional[tk.Tk]  = None
        self._slots: Dict[str, dict]  = {}   # slot → current content
        self._labels: Dict[str, tk.Label] = {}

        # Drag state
        self._drag_x = 0
        self._drag_y = 0

    def run(self) -> None:
        """Build window and start the event loop (blocking)."""
        self._root = root = tk.Tk()
        root.title("League Advisor")
        root.overrideredirect(True)          # No OS title bar
        root.wm_attributes("-topmost", True)
        root.wm_attributes("-alpha",   config.OVERLAY_ALPHA)
        root.configure(bg=config.OVERLAY_BG)
        root.geometry(f"+{config.OVERLAY_X}+{config.OVERLAY_Y}")

        self._build_ui(root)

        # Start polling
        root.after(REFRESH_MS, self._tick)
        root.mainloop()

    def close(self) -> None:
        if self._root:
            self._root.quit()

    # ── UI construction ───────────────────────────────────────────────────────

    def _build_ui(self, root: tk.Tk) -> None:
        font_header   = (config.OVERLAY_FONT, 9, "bold")
        font_slot_key = (config.OVERLAY_FONT, 9, "bold")
        font_slot_val = (config.OVERLAY_FONT, config.OVERLAY_FONT_SIZE)

        # ── Header row ───────────────────────────────────────────────────────
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

        # Drag bindings
        header.bind("<ButtonPress-1>",   self._on_drag_start)
        header.bind("<B1-Motion>",       self._on_drag_motion)

        # ── Separator ────────────────────────────────────────────────────────
        sep = tk.Frame(root, bg="#333333", height=1)
        sep.pack(fill=tk.X)

        # ── Slot rows ────────────────────────────────────────────────────────
        slots_frame = tk.Frame(root, bg=config.OVERLAY_BG, padx=6, pady=4)
        slots_frame.pack(fill=tk.BOTH, expand=True)

        for slot in SLOT_ORDER:
            row = tk.Frame(slots_frame, bg=config.OVERLAY_BG)
            row.pack(fill=tk.X, pady=1)

            key_lbl = tk.Label(
                row,
                text=SLOT_LABELS.get(slot, slot),
                bg=config.OVERLAY_BG,
                fg="#666666",
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

    # ── Periodic tick ─────────────────────────────────────────────────────────

    def _tick(self) -> None:
        if self._root is None:
            return

        # Reassert topmost every tick
        self._root.wm_attributes("-topmost", True)
        self._root.lift()

        # Drain the queue
        try:
            while True:
                update = self._queue.get_nowait()
                self._apply_update(update)
        except queue.Empty:
            pass

        self._root.after(REFRESH_MS, self._tick)

    def _apply_update(self, update: dict) -> None:
        slot   = update.get("slot", "")
        label  = update.get("label", "")
        reason = update.get("reason", "")
        level  = update.get("level", "info")

        lbl_widget = self._labels.get(slot)
        if lbl_widget is None:
            return

        text  = f"{label}  –  {reason}" if reason else label
        color = LEVEL_COLORS.get(level, LEVEL_COLORS.get("info", "#FFFFFF"))
        lbl_widget.config(text=text, fg=color)

    # ── Drag ─────────────────────────────────────────────────────────────────

    def _on_drag_start(self, event: tk.Event) -> None:
        self._drag_x = event.x_root - self._root.winfo_x()
        self._drag_y = event.y_root - self._root.winfo_y()

    def _on_drag_motion(self, event: tk.Event) -> None:
        x = event.x_root - self._drag_x
        y = event.y_root - self._drag_y
        self._root.geometry(f"+{x}+{y}")
