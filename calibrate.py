"""
Minimap Calibration Tool

Run this WHILE in a League game (or in practice tool) to find the correct
minimap screen region for your resolution / UI scale.

What it does
------------
1. Shows you a live preview of the captured region so you can see if you've
   got the right area.
2. Runs the enemy-dot detection and circles any found dots in green.
3. Lets you adjust the region interactively.
4. Saves the correct values back to config.py automatically.

Usage
-----
    python calibrate.py

Controls (in the preview window)
---------------------------------
  Arrow keys  – nudge the capture region 5 px at a time
  +  /  -     – grow / shrink the width & height
  s           – save current region to config.py and exit
  q           – quit without saving
"""

import sys
import time
import re

import cv2
import numpy as np

from minimap_tracker import MinimapTracker
import config


def _draw_info(img: np.ndarray, region: dict, dot_count: int) -> np.ndarray:
    """Overlay the current region info and dot count onto the preview image."""
    vis = img.copy()
    h, w = vis.shape[:2]

    text_lines = [
        f"x={region['x']}  y={region['y']}",
        f"w={region['width']}  h={region['height']}",
        f"Dots detected: {dot_count}",
        "Arrows=move  +/-=resize  s=save  q=quit",
    ]
    y_pos = 14
    for line in text_lines:
        cv2.putText(vis, line, (4, y_pos), cv2.FONT_HERSHEY_SIMPLEX,
                    0.38, (0, 0, 0), 2, cv2.LINE_AA)
        cv2.putText(vis, line, (4, y_pos), cv2.FONT_HERSHEY_SIMPLEX,
                    0.38, (50, 255, 50), 1, cv2.LINE_AA)
        y_pos += 16
    return vis


def _save_region(region: dict):
    """Write the new MINIMAP_REGION into config.py using a regex replace."""
    with open("config.py", "r") as f:
        content = f.read()

    new_block = (
        f'MINIMAP_REGION = {{\n'
        f'    "x":      {region["x"]},\n'
        f'    "y":      {region["y"]},\n'
        f'    "width":  {region["width"]},\n'
        f'    "height": {region["height"]},\n'
        f'}}'
    )

    # Replace the existing MINIMAP_REGION block
    pattern = r'MINIMAP_REGION\s*=\s*\{[^}]+\}'
    updated = re.sub(pattern, new_block, content, flags=re.DOTALL)

    with open("config.py", "w") as f:
        f.write(updated)

    print(f"\n[calibrate] Saved to config.py: {region}")


def main():
    region = dict(config.MINIMAP_REGION)
    tracker = MinimapTracker(region)

    print("=" * 54)
    print("  League Advisor – Minimap Calibration")
    print("=" * 54)
    print(f"Starting region: {region}")
    print("A preview window will open.")
    print("Adjust with arrow keys / +/- until the minimap fits,")
    print("then press  s  to save  or  q  to quit.\n")

    cv2.namedWindow("Minimap Calibration", cv2.WINDOW_NORMAL)
    cv2.resizeWindow("Minimap Calibration", 400, 340)

    while True:
        tracker.update_region(region)
        img = tracker.capture()

        if img is None:
            blank = np.zeros((200, 300, 3), dtype=np.uint8)
            cv2.putText(blank, "No screen capture – check mss",
                        (10, 100), cv2.FONT_HERSHEY_SIMPLEX, 0.5,
                        (0, 80, 255), 1)
            cv2.imshow("Minimap Calibration", blank)
        else:
            dots = tracker.detect_enemy_dots(img)
            vis  = img.copy()
            h, w = vis.shape[:2]
            for px, py in dots:
                cx = int(px / 100.0 * w)
                cy = int(py / 100.0 * h)
                cv2.circle(vis, (cx, cy), 7, (0, 255, 0), 2)

            vis = _draw_info(vis, region, len(dots))
            cv2.imshow("Minimap Calibration", vis)

        key = cv2.waitKey(100) & 0xFF

        if key == ord('q'):
            print("Quit without saving.")
            break
        elif key == ord('s'):
            _save_region(region)
            print("Saved.  You can close the window.")
            break

        # Arrow keys (platform-specific codes)
        elif key in (81, ord('a')):   # left  → move left
            region["x"] -= 5
        elif key in (83, ord('d')):   # right → move right
            region["x"] += 5
        elif key in (82, ord('w')):   # up    → move up
            region["y"] -= 5
        elif key in (84, ord('s')):   # down  → move down (s captured above)
            region["y"] += 5
        elif key == ord('+') or key == 43:
            region["width"]  += 5
            region["height"] += 5
        elif key == ord('-') or key == 45:
            region["width"]  = max(50, region["width"]  - 5)
            region["height"] = max(50, region["height"] - 5)

    cv2.destroyAllWindows()


if __name__ == "__main__":
    main()
