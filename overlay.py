"""
Always-on-top overlay window built with tkinter.

Features
--------
• Dark HUD aesthetic with colour-coded severity levels.
• Draggable via the title bar.
• Messages expire after MSG_EXPIRE_SECS seconds (configurable).
• Runs on its own daemon thread so it never blocks the engine.
• Thread-safe: callers push messages via add_message() from any thread.

Usage
-----
    overlay = Overlay()
    overlay.start()
    overlay.add_message("Enemy JG last seen bot", "info")
    overlay.add_message("GANK TOP → enemy 30% HP", "critical")
    overlay.stop()
"""

import queue
import threading
import time
import tkinter as tk
from collections import deque
from typing import Tuple

import config


# ── Message dataclass ────────────────────────────────────────────────────────

class _Msg:
    __slots__ = ("text", "level", "born")

    def __init__(self, text: str, level: str):
        self.text  = text
        self.level = level
        self.born  = time.monotonic()

    def is_expired(self) -> bool:
        return time.monotonic() - self.born > config.MSG_EXPIRE_SECS


# ── Overlay class ────────────────────────────────────────────────────────────

class Overlay:
    def __init__(self):
        self._queue:    queue.Queue     = queue.Queue()
        self._messages: deque          = deque(maxlen=10)
        self._root:     tk.Tk | None   = None
        self._text:     tk.Text | None = None
        self._running:  bool           = False
        self._thread:   threading.Thread | None = None

        # Drag state
        self._drag_x = 0
        self._drag_y = 0

    # ── Public API ───────────────────────────────────────────────────────────

    def start(self):
        """Launch the overlay in a background daemon thread."""
        self._running = True
        self._thread  = threading.Thread(target=self._run, daemon=True, name="overlay")
        self._thread.start()

    def stop(self):
        """Gracefully close the overlay window."""
        self._running = False
        if self._root:
            try:
                self._root.quit()
            except Exception:
                pass

    def add_message(self, text: str, level: str = "info"):
        """
        Thread-safe.  level: "info" | "warn" | "critical" | "dim"
        """
        self._queue.put(_Msg(text, level))

    # ── Tkinter thread ───────────────────────────────────────────────────────

    def _run(self):
        self._root = tk.Tk()
        root = self._root

        # ── Window chrome ────────────────────────────────────────────────────
        root.title("")
        root.geometry(
            f"{config.OVERLAY_WIDTH}x{config.OVERLAY_HEIGHT}"
            f"+{config.OVERLAY_X}+{config.OVERLAY_Y}"
        )
        root.overrideredirect(True)           # no OS title bar
        root.attributes("-topmost", True)     # always on top
        root.attributes("-alpha", config.OVERLAY_ALPHA)
        root.configure(bg=config.COLORS["bg"])
        root.minsize(200, 80)

        # ── Title bar (draggable) ────────────────────────────────────────────
        header = tk.Frame(root, bg=config.COLORS["header_bg"], height=24)
        header.pack(fill=tk.X)
        header.pack_propagate(False)

        title = tk.Label(
            header,
            text=" ◈  LEAGUE ADVISOR",
            fg=config.COLORS["accent"],
            bg=config.COLORS["header_bg"],
            font=("Consolas", 9, "bold"),
            anchor="w",
        )
        title.pack(side=tk.LEFT, padx=6, pady=3)

        # Close button
        close_btn = tk.Label(
            header,
            text="✕",
            fg=config.COLORS["dim"],
            bg=config.COLORS["header_bg"],
            font=("Consolas", 10),
            cursor="hand2",
        )
        close_btn.pack(side=tk.RIGHT, padx=6)
        close_btn.bind("<Button-1>", lambda _: self.stop())

        # Bind drag to both header and title
        for widget in (header, title):
            widget.bind("<ButtonPress-1>",   self._on_drag_start)
            widget.bind("<B1-Motion>",        self._on_drag_move)

        # ── Thin border line ─────────────────────────────────────────────────
        tk.Frame(root, bg=config.COLORS["border"], height=1).pack(fill=tk.X)

        # ── Message text area ────────────────────────────────────────────────
        self._text = tk.Text(
            root,
            bg=config.COLORS["bg"],
            fg=config.COLORS["info"],
            font=config.OVERLAY_FONT,
            relief=tk.FLAT,
            state=tk.DISABLED,
            wrap=tk.WORD,
            padx=8,
            pady=6,
            cursor="arrow",
            selectbackground=config.COLORS["bg"],
            highlightthickness=0,
            borderwidth=0,
        )
        self._text.pack(fill=tk.BOTH, expand=True)

        # Colour tags
        self._text.tag_configure("info",     foreground=config.COLORS["info"])
        self._text.tag_configure("warn",     foreground=config.COLORS["warn"])
        self._text.tag_configure("critical", foreground=config.COLORS["critical"],
                                             font=("Consolas", 10, "bold"))
        self._text.tag_configure("dim",      foreground=config.COLORS["dim"])

        # ── Kick off periodic refresh ────────────────────────────────────────
        root.after(400, self._tick)
        root.mainloop()

    def _tick(self):
        """Called every 400 ms from the Tk main loop."""
        if not self._running:
            self._root.destroy()
            return

        # Drain incoming queue
        changed = False
        try:
            while True:
                msg = self._queue.get_nowait()
                self._messages.append(msg)
                changed = True
        except queue.Empty:
            pass

        # Also redraw periodically to expire old messages
        self._redraw()
        self._root.after(400, self._tick)

    def _redraw(self):
        """Rewrite the text widget from current message list."""
        # Purge expired messages
        now_mono = time.monotonic()
        live = [m for m in self._messages if not m.is_expired()]
        self._messages = deque(live, maxlen=10)

        self._text.configure(state=tk.NORMAL)
        self._text.delete("1.0", tk.END)

        for msg in self._messages:
            age = now_mono - msg.born
            # Dim messages that are close to expiry
            tag = "dim" if age > config.MSG_EXPIRE_SECS * 0.7 else msg.level
            self._text.insert(tk.END, msg.text + "\n", tag)

        self._text.configure(state=tk.DISABLED)
        self._text.see(tk.END)

    # ── Drag handlers ────────────────────────────────────────────────────────

    def _on_drag_start(self, event):
        self._drag_x = event.x_root - self._root.winfo_x()
        self._drag_y = event.y_root - self._root.winfo_y()

    def _on_drag_move(self, event):
        x = event.x_root - self._drag_x
        y = event.y_root - self._drag_y
        self._root.geometry(f"+{x}+{y}")
