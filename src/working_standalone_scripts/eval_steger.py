#!/usr/bin/env python3
from __future__ import annotations
import json
from pathlib import Path
import numpy as np
import cv2

from src.utils.processing.ridge import steger_laser_centerline_from_bgr
from utils.io_utils import print_progress

def nearest_errors(gt_xy: np.ndarray, det_yx: np.ndarray) -> np.ndarray:
    """For each GT point (x,y), return distance to nearest detected (y,x)."""
    if det_yx.size == 0 or gt_xy.size == 0:
        return np.array([], dtype=np.float32)

    det_xy = np.stack([det_yx[:, 1], det_yx[:, 0]], axis=1).astype(np.float32)

    # brute force is fine for ~400 GT points and a few thousand detections
    errs = []
    for gx, gy in gt_xy:
        d2 = (det_xy[:, 0] - gx) ** 2 + (det_xy[:, 1] - gy) ** 2
        errs.append(np.sqrt(float(d2.min())))
    return np.array(errs, dtype=np.float32)

def main():
    dataset = Path("synth_test_clean")   # change me
    gt_path = dataset / "ground_truth.json"
    with open(gt_path, "r", encoding="utf-8") as f:
        gt = json.load(f)["items"]

    all_errs = []

    # Use the parameters you found work well
    sigma = 2.0
    thresh = 6.0
    tmax = 1.0

    total = len(gt)

    for i, item in enumerate(gt):
        print_progress(i + 1, total)

        img_path = dataset / item["image"]
        img = cv2.imread(str(img_path), cv2.IMREAD_COLOR)
        if img is None:
            continue

        pts_yx, strength = steger_laser_centerline_from_bgr(
            img, sigma=sigma, ridge_thresh=thresh, t_max=tmax
        )

        gt_xy = np.array(item["centerline_xy"], dtype=np.float32)
        errs = nearest_errors(gt_xy, pts_yx)
        if errs.size:
            all_errs.append(errs)

    if not all_errs:
        print("No errors computed (no detections or no GT).")
        return

    all_errs = np.concatenate(all_errs)
    print(f"N errors: {all_errs.size}")
    print(f"Mean   : {all_errs.mean():.3f} px")
    print(f"Median : {np.median(all_errs):.3f} px")
    print(f"P90    : {np.percentile(all_errs, 90):.3f} px")
    print(f"P95    : {np.percentile(all_errs, 95):.3f} px")
    print(f"Max    : {all_errs.max():.3f} px")

if __name__ == "__main__":
    main()
