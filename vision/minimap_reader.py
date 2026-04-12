"""
Minimap screen capture and enemy dot detection.

Uses mss for fast low-overhead ROI capture and OpenCV HSV masking to
find enemy champion dots (bright red) on the minimap.

All positions are returned in a normalised 0–100 coordinate space:
  (0,  0)   = top-left  of the captured minimap region
  (100,100) = bottom-right of the captured minimap region

With the standard League minimap orientation (blue bottom-left, red top-right):
  Blue base ≈ (5,  90) in normalised coords
  Red  base ≈ (90, 5)  in normalised coords
"""

import cv2
import numpy as np
import mss
from typing import List, Tuple, Optional

import config


class MinimapReader:
    """Thread-safe ROI screen capture + enemy dot detection."""

    def __init__(self, region: Optional[dict] = None):
        """
        region: {"x": int, "y": int, "width": int, "height": int}
                Defaults to config.MINIMAP_REGION.
        """
        self.region = region or config.MINIMAP_REGION
        self._sct   = mss.mss()

    # ── Public API ───────────────────────────────────────────────────────────

    def capture(self) -> Optional[np.ndarray]:
        """Grab the minimap region from the screen. Returns BGR ndarray or None."""
        try:
            mon = {
                "top":    self.region["y"],
                "left":   self.region["x"],
                "width":  self.region["width"],
                "height": self.region["height"],
            }
            raw = self._sct.grab(mon)
            return cv2.cvtColor(np.array(raw), cv2.COLOR_BGRA2BGR)
        except Exception:
            return None

    def detect_enemy_dots(self, img: np.ndarray) -> List[Tuple[float, float]]:
        """
        Find red enemy-champion dots in a BGR minimap image.
        Returns a list of (x, y) positions in 0–100 normalised coords.

        Only returns dots within a reasonable size range to filter both
        single-pixel noise and large structures like turret icons.
        """
        if img is None or img.size == 0:
            return []

        hsv = cv2.cvtColor(img, cv2.COLOR_BGR2HSV)

        lo1 = np.array(config.ENEMY_DOT_HSV["lower1"])
        hi1 = np.array(config.ENEMY_DOT_HSV["upper1"])
        lo2 = np.array(config.ENEMY_DOT_HSV["lower2"])
        hi2 = np.array(config.ENEMY_DOT_HSV["upper2"])

        mask = cv2.bitwise_or(
            cv2.inRange(hsv, lo1, hi1),
            cv2.inRange(hsv, lo2, hi2),
        )

        # Small morphological open removes 1-pixel noise without eroding real dots
        k    = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (2, 2))
        mask = cv2.morphologyEx(mask, cv2.MORPH_OPEN, k)

        contours, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)

        h, w = img.shape[:2]
        results: List[Tuple[float, float]] = []

        for cnt in contours:
            area = cv2.contourArea(cnt)
            if not (config.DOT_AREA_MIN <= area <= config.DOT_AREA_MAX):
                continue
            M = cv2.moments(cnt)
            if M["m00"] <= 0:
                continue
            cx = M["m10"] / M["m00"]
            cy = M["m01"] / M["m00"]
            results.append((cx / w * 100.0, cy / h * 100.0))

        return results

    def get_snapshot(self) -> Tuple[Optional[np.ndarray], List[Tuple[float, float]]]:
        """Capture + detect in one call. Returns (image, enemy_positions)."""
        img = self.capture()
        dots = self.detect_enemy_dots(img) if img is not None else []
        return img, dots

    def update_region(self, region: dict):
        """Hot-swap the capture region without restarting."""
        self.region = region

    def save_debug_frame(self, path: str = "debug_minimap.png"):
        """
        Capture the minimap and save it with detected dots circled in green.
        Useful for verifying dot detection during calibration.
        """
        img = self.capture()
        if img is None:
            return
        dots = self.detect_enemy_dots(img)
        vis  = img.copy()
        h, w = vis.shape[:2]
        for px, py in dots:
            cx = int(px / 100.0 * w)
            cy = int(py / 100.0 * h)
            cv2.circle(vis, (cx, cy), 7, (0, 255, 0), 2)
        cv2.imwrite(path, vis)
        return path
