#!/usr/bin/env python3
"""
Camera calibration (checkerboard) for Raspberry Pi vision pipeline.

Usage example:
  python3 src/working_standalone_scripts/calibrate_camera.py \
    --images "data/raw/calib_cam/*.png" \
    --rows 10 --cols 7 \
    --square-mm 25.0 \
    --lens-pos 2.3276 \
    --shutter-us 8000 \
    --gain 1.0 \
    --out calib/camera.yaml \
    --debug-dir data/processed/calib_debug \
    --undistort-example data/raw/calib_cam/chess_001.png

Notes:
- rows/cols are the number of INNER corners.
  (A board with 11x8 squares has 10x7 inner corners.)
- Calibration is valid ONLY for the same resolution + lens focus settings.
"""

from __future__ import annotations

import argparse
import glob
from pathlib import Path
from typing import Optional

import yaml
import numpy as np
import cv2


def ensure_dir(path: Optional[str | Path]):
    if not path:
        return
    Path(path).mkdir(parents=True, exist_ok=True)


def build_object_points(rows: int, cols: int, square_mm: float) -> np.ndarray:
    """
    Returns (N,3) array of checkerboard corner points in board coordinates.
    rows/cols are INNER corners.
    """
    objp = np.zeros((rows * cols, 3), np.float32)
    # OpenCV pattern_size is (cols, rows); here we build (x,y) with x along cols, y along rows
    objp[:, :2] = np.mgrid[0:cols, 0:rows].T.reshape(-1, 2)
    objp *= float(square_mm)
    return objp


def parse_awb_gains(s: Optional[str]):
    if not s:
        return None
    try:
        r, b = [float(x.strip()) for x in s.split(",")]
        return [r, b]
    except Exception:
        return s  # store raw if parsing fails


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument(
        "--images",
        required=True,
        help='Glob for images, e.g. "data/raw/calib_cam/*.png"',
    )
    ap.add_argument(
        "--rows",
        type=int,
        required=True,
        help="Number of INNER corners along board height (rows)",
    )
    ap.add_argument(
        "--cols",
        type=int,
        required=True,
        help="Number of INNER corners along board width (cols)",
    )
    ap.add_argument("--square-mm", type=float, required=True, help="Checkerboard square size in mm")
    ap.add_argument("--out", default="calib/camera.yaml", help="Output YAML path for intrinsics")
    ap.add_argument("--debug-dir", default=None, help="If set, saves images with corners drawn (and MISS frames)")
    ap.add_argument(
        "--undistort-example",
        default=None,
        help="Optional path to one input image to undistort & save",
    )
    ap.add_argument("--max-images", type=int, default=0, help="If >0, only use first N images after sorting")
    ap.add_argument("--visualize-scale", type=float, default=1.0, help="Scale debug images (e.g. 0.5 to save space)")

    # Record capture settings (for traceability)
    ap.add_argument("--lens-pos", type=float, default=None, help="Manual lens position used (recorded in YAML)")
    ap.add_argument("--shutter-us", type=int, default=None, help="Shutter used during capture (recorded in YAML)")
    ap.add_argument("--gain", type=float, default=None, help="Analogue gain used during capture (recorded in YAML)")
    ap.add_argument("--awb-gains", type=str, default=None, help='AWB gains "R,B" recorded in YAML')

    args = ap.parse_args()

    paths = sorted(glob.glob(args.images))
    if not paths:
        raise SystemExit(f"No images found for glob: {args.images}")

    if args.max_images and args.max_images > 0:
        paths = paths[: args.max_images]

    ensure_dir(Path(args.out).parent)
    ensure_dir(args.debug_dir)

    pattern_size = (args.cols, args.rows)  # OpenCV expects (cols, rows)
    objp = build_object_points(args.rows, args.cols, args.square_mm)

    # Storage for calibration correspondences
    objpoints = []
    imgpoints = []
    img_size = None

    # Corner refinement criteria (for classic detector)
    criteria = (cv2.TERM_CRITERIA_EPS + cv2.TERM_CRITERIA_MAX_ITER, 50, 1e-6)

    good = 0
    for i, p in enumerate(paths):
        img = cv2.imread(p, cv2.IMREAD_COLOR)
        if img is None:
            print(f"[WARN] Could not read: {p}")
            continue

        gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
        img_size = gray.shape[::-1]  # (w,h)

        corners2 = None

        # Prefer robust SB detector if available
        if hasattr(cv2, "findChessboardCornersSB"):
            sb_flags = cv2.CALIB_CB_NORMALIZE_IMAGE | cv2.CALIB_CB_EXHAUSTIVE
            found, corners = cv2.findChessboardCornersSB(gray, pattern_size, sb_flags)
            if found:
                corners2 = corners.astype(np.float32)
        else:
            flags = cv2.CALIB_CB_ADAPTIVE_THRESH | cv2.CALIB_CB_NORMALIZE_IMAGE
            found, corners = cv2.findChessboardCorners(gray, pattern_size, flags)
            if found:
                corners2 = cv2.cornerSubPix(gray, corners, (11, 11), (-1, -1), criteria)

        if corners2 is None:
            print(f"[MISS] {p}")
            if args.debug_dir:
                dbg = img.copy()
                if args.visualize_scale != 1.0:
                    dbg = cv2.resize(
                        dbg,
                        None,
                        fx=args.visualize_scale,
                        fy=args.visualize_scale,
                        interpolation=cv2.INTER_AREA,
                    )
                out_name = Path(args.debug_dir) / f"MISS_{i:03d}.png"
                cv2.imwrite(str(out_name), dbg)
            continue

        objpoints.append(objp)
        imgpoints.append(corners2)
        good += 1
        print(f"[OK] {p}")

        # optional debug draw
        if args.debug_dir:
            dbg = img.copy()
            cv2.drawChessboardCorners(dbg, pattern_size, corners2, True)
            if args.visualize_scale != 1.0:
                dbg = cv2.resize(
                    dbg,
                    None,
                    fx=args.visualize_scale,
                    fy=args.visualize_scale,
                    interpolation=cv2.INTER_AREA,
                )
            out_name = Path(args.debug_dir) / f"corners_{good:03d}.png"
            cv2.imwrite(str(out_name), dbg)

    if good < 10:
        raise SystemExit(
            f"Only {good} usable images. Take ~15–30 images with varied angles/distances."
        )

    if img_size is None:
        raise RuntimeError("No valid calibration images found. img_size is None.")

    # Calibrate
    ret, K, dist, rvecs, tvecs = cv2.calibrateCamera(objpoints, imgpoints, img_size, None, None)

    # Compute mean reprojection error (more interpretable)
    total_err2 = 0.0
    total_points = 0
    for op, ip, rv, tv in zip(objpoints, imgpoints, rvecs, tvecs):
        proj, _ = cv2.projectPoints(op, rv, tv, K, dist)
        err = cv2.norm(ip, proj, cv2.NORM_L2)
        total_err2 += float(err * err)
        total_points += int(len(op))
    mean_reproj_px = float(np.sqrt(total_err2 / max(1, total_points)))

    # Build output YAML
    out = {
        "camera": {
            "image_size": {"w": int(img_size[0]), "h": int(img_size[1])},
            "K": K.tolist(),
            "dist": dist.reshape(-1).tolist(),
            "fx": float(K[0, 0]),
            "fy": float(K[1, 1]),
            "cx": float(K[0, 2]),
            "cy": float(K[1, 2]),
        },
        "checkerboard": {
            "rows_inner": int(args.rows),
            "cols_inner": int(args.cols),
            "square_mm": float(args.square_mm),
        },
        "quality": {
            "rms_calibrateCamera_px": float(ret),
            "mean_reproj_error_px": mean_reproj_px,
            "used_images": int(good),
            "total_images": int(len(paths)),
        },
    }

    capture_settings = {}
    if args.lens_pos is not None:
        capture_settings["lens_pos"] = float(args.lens_pos)
    if args.shutter_us is not None:
        capture_settings["shutter_us"] = int(args.shutter_us)
    if args.gain is not None:
        capture_settings["gain"] = float(args.gain)
    awb = parse_awb_gains(args.awb_gains)
    if awb is not None:
        capture_settings["awb_gains_rb"] = awb
    if capture_settings:
        out["capture_settings"] = capture_settings

    with open(args.out, "w", encoding="utf-8") as f:
        yaml.safe_dump(out, f, sort_keys=False)

    print("\n=== Calibration complete ===")
    print(f"Used images : {good}/{len(paths)}")
    print(f"Image size  : {img_size[0]} x {img_size[1]}")
    print(f"RMS (OpenCV): {ret:.4f} px")
    print(f"Mean reproj : {mean_reproj_px:.4f} px")
    print(f"Saved       : {args.out}")

    # Optional: undistort one example image to verify visually
    if args.undistort_example:
        ex = cv2.imread(args.undistort_example, cv2.IMREAD_COLOR)
        if ex is None:
            print(f"[WARN] Could not read undistort example: {args.undistort_example}")
            return

        h, w = ex.shape[:2]
        newK, _roi = cv2.getOptimalNewCameraMatrix(K, dist, (w, h), 1.0, (w, h))
        und = cv2.undistort(ex, K, dist, None, newK)

        und_path = str(
            Path(args.undistort_example).with_name(Path(args.undistort_example).stem + "_undistorted.png")
        )
        cv2.imwrite(und_path, und)
        print(f"Undistorted example saved: {und_path}")


if __name__ == "__main__":
    main()
