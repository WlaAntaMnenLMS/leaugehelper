"""
Minimap Region Calibration Tool.

Run this standalone script ONCE to find the pixel coordinates of your
League minimap on screen.  The result is saved to minimap_region.json
and picked up automatically by MinimapReader.

Usage:
    python -m ui.calibrate

How it works:
  1. Captures a full screenshot
  2. Shows it in a tkinter window at half scale
  3. User drags a rectangle over the minimap corner
  4. Tool saves the region: {"left": x, "top": y, "width": w, "height": h}
"""

from __future__ import annotations

import json
import os
import tkinter as tk
from tkinter import messagebox
from typing import Optional, Tuple

try:
    import mss
    import mss.tools
    from PIL import Image, ImageTk
except ImportError:
    print("pip install mss Pillow")
    raise


REGION_FILE = os.path.join(
    os.path.dirname(os.path.dirname(__file__)),
    "minimap_region.json",
)
DISPLAY_SCALE = 0.5   # Show screenshot at 50% to fit on screen


class CalibrateTool:
    def __init__(self):
        self._root        = tk.Tk()
        self._root.title("Minimap Calibration – drag a rectangle")
        self._canvas:   Optional[tk.Canvas] = None
        self._photo:    Optional[ImageTk.PhotoImage] = None
        self._rect_id:  Optional[int] = None
        self._x0 = self._y0 = self._x1 = self._y1 = 0
        self._scale = DISPLAY_SCALE
        self._screen_w = self._screen_h = 0

        self._capture_and_show()

    def _capture_and_show(self) -> None:
        with mss.mss() as sct:
            monitor = sct.monitors[1]   # primary monitor
            self._screen_w = monitor["width"]
            self._screen_h = monitor["height"]
            raw = sct.grab(monitor)
            img = Image.frombytes("RGB", raw.size, raw.bgra, "raw", "BGRX")

        w = int(img.width  * self._scale)
        h = int(img.height * self._scale)
        img_resized = img.resize((w, h), Image.LANCZOS)
        self._photo = ImageTk.PhotoImage(img_resized)

        self._canvas = tk.Canvas(self._root, width=w, height=h, cursor="crosshair")
        self._canvas.pack()

        self._canvas.create_image(0, 0, anchor="nw", image=self._photo)
        self._canvas.bind("<ButtonPress-1>",  self._on_press)
        self._canvas.bind("<B1-Motion>",      self._on_drag)
        self._canvas.bind("<ButtonRelease-1>", self._on_release)

        btn = tk.Button(
            self._root,
            text="Save region",
            command=self._save,
            state=tk.DISABLED,
        )
        btn.pack(pady=6)
        self._save_btn = btn

        self._root.mainloop()

    def _on_press(self, e: tk.Event) -> None:
        self._x0, self._y0 = e.x, e.y
        if self._rect_id:
            self._canvas.delete(self._rect_id)

    def _on_drag(self, e: tk.Event) -> None:
        if self._rect_id:
            self._canvas.delete(self._rect_id)
        self._rect_id = self._canvas.create_rectangle(
            self._x0, self._y0, e.x, e.y,
            outline="#FF4444", width=2,
        )

    def _on_release(self, e: tk.Event) -> None:
        self._x1, self._y1 = e.x, e.y
        self._save_btn.config(state=tk.NORMAL)

    def _save(self) -> None:
        # Convert display coords back to screen coords
        s = self._scale
        left   = int(min(self._x0, self._x1) / s)
        top    = int(min(self._y0, self._y1) / s)
        right  = int(max(self._x0, self._x1) / s)
        bottom = int(max(self._y0, self._y1) / s)
        width  = right  - left
        height = bottom - top

        if width < 50 or height < 50:
            messagebox.showerror("Too small", "Selection is too small.  Redraw.")
            return

        region = {"left": left, "top": top, "width": width, "height": height}
        with open(REGION_FILE, "w") as f:
            json.dump(region, f, indent=2)

        messagebox.showinfo(
            "Saved",
            f"Minimap region saved:\n{region}\n\nFile: {REGION_FILE}",
        )
        self._root.destroy()


def run_calibration() -> None:
    CalibrateTool()


if __name__ == "__main__":
    run_calibration()
