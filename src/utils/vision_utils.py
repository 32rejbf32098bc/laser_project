# utils/vision_utils.py
from __future__ import annotations

from pathlib import Path
import numpy as np
import cv2


def crop_roi(img: np.ndarray, roi: dict | None):
    """
    roi dict format (suggested):
      {"x": 0, "y": 0, "w": 100, "h": 200}
    Returns (img_roi, (ox, oy)) where ox/oy are offsets.
    """
    if roi is None:
        return img, (0, 0)

    x = int(roi.get("x", 0))
    y = int(roi.get("y", 0))
    w = int(roi.get("w", img.shape[1] - x))
    h = int(roi.get("h", img.shape[0] - y))

    x2 = min(img.shape[1], x + w)
    y2 = min(img.shape[0], y + h)
    return img[y:y2, x:x2], (x, y)


def hsv_mask_red(bgr: np.ndarray,
                 red1_lower, red1_upper,
                 red2_lower, red2_upper) -> np.ndarray:
    """
    Red mask using two HSV ranges (wrap-around).
    Thresholds should be lists like [H,S,V].
    """
    hsv = cv2.cvtColor(bgr, cv2.COLOR_BGR2HSV)

    r1l = np.array(red1_lower, dtype=np.uint8)
    r1u = np.array(red1_upper, dtype=np.uint8)
    r2l = np.array(red2_lower, dtype=np.uint8)
    r2u = np.array(red2_upper, dtype=np.uint8)

    m1 = cv2.inRange(hsv, r1l, r1u)
    m2 = cv2.inRange(hsv, r2l, r2u)
    return cv2.bitwise_or(m1, m2)


def apply_morph(mask: np.ndarray, ksize: int = 0, iters: int = 1) -> np.ndarray:
    """Morph open/close to clean the mask."""
    if not ksize or ksize <= 0:
        return mask
    k = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (ksize, ksize))
    out = cv2.morphologyEx(mask, cv2.MORPH_OPEN, k, iterations=max(1, iters))
    out = cv2.morphologyEx(out, cv2.MORPH_CLOSE, k, iterations=max(1, iters))
    return out

# TODO: ADD the exposure calibration code here. This will be called from run_scan if --exposure_cal is set. It should save the results to out_dir.
def auto_exposure_calibrate(
                       capture_fn,
                       tmp_dir,
                       target_peak=210,
                       tolerance=10,
                       max_iters=8,
                       roi=None):
    """
    Returns updated CameraPlan with tuned shutter_us.
    capture_fn(out_path, cam_plan) must save a frame.
    """

# TODO: Change to gaussian fit for subpixel accuracy instead of mean/median of pixels above threshold.
def extract_centerline(mask: np.ndarray,
                       orientation: str = "vertical",
                       min_pixels: int = 5,
                       subpixel: bool = True) -> np.ndarray:
    """
    Returns Nx2 points as (y,x).
    orientation:
      - "vertical":  one x per row  (laser roughly vertical in image)
      - "horizontal": one y per col (laser roughly horizontal in image)
    """
    h, w = mask.shape[:2]
    pts = []

    if orientation == "vertical":
        for y in range(h):
            xs = np.where(mask[y] > 0)[0]
            if xs.size < min_pixels:
                continue
            x = float(xs.mean()) if subpixel else float(xs[xs.size // 2])
            pts.append((y, x))

    elif orientation == "horizontal":
        for x in range(w):
            ys = np.where(mask[:, x] > 0)[0]
            if ys.size < min_pixels:
                continue
            y = float(ys.mean()) if subpixel else float(ys[ys.size // 2])
            pts.append((y, x))
    else:
        raise ValueError("orientation must be 'vertical' or 'horizontal'")

    return np.array(pts, dtype=np.float32)

# TODO: This will go when we implement subpixel extraction via gaussian fit. For now it is useful for visualization.
def overlay_points(bgr: np.ndarray, pts_yx: np.ndarray, radius: int = 1) -> np.ndarray:
    out = bgr.copy()
    for y, x in pts_yx:
        cv2.circle(out, (int(round(x)), int(round(y))), radius, (0, 255, 0), -1)
    return out
