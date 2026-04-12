"""
Minimap screen-capture and enemy-dot detection.

Uses mss for fast low-overhead screen capture and OpenCV to isolate the
bright-red enemy champion dots that appear on the League minimap.

Coordinate system
-----------------
All positions returned are (x, y) in 0–100 scale relative to the captured
minimap region:
  (0, 0)     = top-left  corner of the minimap  (top lane end)
  (100, 100) = bottom-right corner               (bot lane end)
"""

import cv2
import numpy as np
import mss
from typing import List, Tuple, Optional

import config


class MinimapTracker:
    def __init__(self, region: Optional[dict] = None):
        """
        region: dict with keys x, y, width, height (screen coordinates).
                Defaults to config.MINIMAP_REGION.
        """
        self.region = region or config.MINIMAP_REGION
        self._sct = mss.mss()

    # ── Public API ──────────────────────────────────────────────────────────

    def capture(self) -> Optional[np.ndarray]:
        """Grab the minimap region.  Returns BGR ndarray or None on error."""
        try:
            mon = {
                "top":    self.region["y"],
                "left":   self.region["x"],
                "width":  self.region["width"],
                "height": self.region["height"],
            }
            raw = self._sct.grab(mon)
            bgr = cv2.cvtColor(np.array(raw), cv2.COLOR_BGRA2BGR)
            return bgr
        except Exception:
            return None

    def detect_enemy_dots(self, img: np.ndarray) -> List[Tuple[float, float]]:
        """
        Find red (enemy) dots in a minimap BGR image.
        Returns a list of (x, y) positions in 0–100 scale.
        """
        if img is None or img.size == 0:
            return []

        hsv = cv2.cvtColor(img, cv2.COLOR_BGR2HSV)

        lower1 = np.array(config.ENEMY_DOT_HSV["lower1"])
        upper1 = np.array(config.ENEMY_DOT_HSV["upper1"])
        lower2 = np.array(config.ENEMY_DOT_HSV["lower2"])
        upper2 = np.array(config.ENEMY_DOT_HSV["upper2"])

        mask = cv2.bitwise_or(
            cv2.inRange(hsv, lower1, upper1),
            cv2.inRange(hsv, lower2, upper2),
        )

        # Slight morphological opening removes single-pixel noise
        kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (2, 2))
        mask = cv2.morphologyEx(mask, cv2.MORPH_OPEN, kernel)

        contours, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)

        h, w = img.shape[:2]
        positions: List[Tuple[float, float]] = []

        for cnt in contours:
            area = cv2.contourArea(cnt)
            if not (config.DOT_AREA_MIN <= area <= config.DOT_AREA_MAX):
                continue
            M = cv2.moments(cnt)
            if M["m00"] <= 0:
                continue
            cx = M["m10"] / M["m00"]
            cy = M["m01"] / M["m00"]
            positions.append((cx / w * 100.0, cy / h * 100.0))

        return positions

    def get_snapshot(self) -> Tuple[Optional[np.ndarray], List[Tuple[float, float]]]:
        """Capture + detect in one call.  Returns (img, enemy_positions)."""
        img = self.capture()
        return img, self.detect_enemy_dots(img) if img is not None else []

    def save_debug_frame(self, img: np.ndarray, path: str = "debug_minimap.png"):
        """Save the captured frame with detected dots circled (for calibration)."""
        if img is None:
            return
        vis = img.copy()
        positions = self.detect_enemy_dots(img)
        h, w = img.shape[:2]
        for px, py in positions:
            cx = int(px / 100.0 * w)
            cy = int(py / 100.0 * h)
            cv2.circle(vis, (cx, cy), 6, (0, 255, 0), 2)
        cv2.imwrite(path, vis)

    def update_region(self, region: dict):
        """Hot-update the capture region without restarting."""
        self.region = region
