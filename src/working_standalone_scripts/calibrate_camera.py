#!/usr/bin/env python3
"""
Camera calibration (checkerboard) for Raspberry Pi vision pipeline.

Usage example:
  python3 src/calibrate_camera.py \
    --images "data/raw/calib/*.png" \
    --rows 10 --cols 7 \
    --square-mm 25.0 \
    --out calib/camera.yaml \
    --debug-dir data/processed/calib_debug \
    --undistort-example data/raw/calib/example.png

Notes:
- rows/cols are the number of INNER corners.
  (A 11x8 squares board has 10x7 inner corners.)
- Calibration is valid ONLY for the same resolution + lens focus settings.
"""

import argparse
import glob
from pathlib import Path
import yaml
import numpy as np
import cv2


def ensure_dir(path: str | None):
    if not path:
        return
    Path(path).mkdir(parents=True, exist_ok=True)


def build_object_points(rows: int, cols: int, square_mm: float) -> np.ndarray:
    """
    Returns (N,3) array of checkerboard corner points in board coordinates.
    rows/cols are INNER corners.
    """
    objp = np.zeros((rows * cols, 3), np.float32)
    objp[:, :2] = np.mgrid[0:cols, 0:rows].T.reshape(-1, 2)  # (x,y)
    objp *= float(square_mm)
    return objp


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--images", required=True,
                    help='Glob for images, e.g. "data/raw/calib/*.png"')
    ap.add_argument("--rows", type=int, required=True,
                    help="Number of INNER corners along board height")
    ap.add_argument("--cols", type=int, required=True,
                    help="Number of INNER corners along board width")
    ap.add_argument("--square-mm", type=float, required=True,
                    help="Checkerboard square size in mm")
    ap.add_argument("--out", default="calib/camera.yaml",
                    help="Output YAML path for intrinsics")
    ap.add_argument("--debug-dir", default=None,
                    help="If set, saves images with detected corners drawn")
    ap.add_argument("--undistort-example", default=None,
                    help="Optional path to one input image to undistort & save")
    ap.add_argument("--max-images", type=int, default=0,
                    help="If >0, only use first N images after sorting")
    ap.add_argument("--visualize-scale", type=float, default=1.0,
                    help="Scale debug images (e.g. 0.5 to save space)")
    args = ap.parse_args()

    paths = sorted(glob.glob(args.images))
    if not paths:
        raise SystemExit(f"No images found for glob: {args.images}")

    if args.max_images and args.max_images > 0:
        paths = paths[:args.max_images]

    ensure_dir(str(Path(args.out).parent))
    ensure_dir(args.debug_dir)

    pattern_size = (args.cols, args.rows)  # OpenCV uses (cols, rows)
    objp = build_object_points(args.rows, args.cols, args.square_mm)

    # Storage for calibration correspondences
    objpoints = []
    imgpoints = []
    img_size = None

    # Corner refinement criteria
    criteria = (cv2.TERM_CRITERIA_EPS + cv2.TERM_CRITERIA_MAX_ITER, 50, 1e-6)

    good = 0
    for i, p in enumerate(paths):
        img = cv2.imread(p, cv2.IMREAD_COLOR)
        if img is None:
            print(f"[WARN] Could not read: {p}")
            continue

        gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
        img_size = gray.shape[::-1]  # (w,h)

        # find chessboard corners
        flags = (cv2.CALIB_CB_ADAPTIVE_THRESH |
                 cv2.CALIB_CB_NORMALIZE_IMAGE)
        found, corners = cv2.findChessboardCorners(gray, pattern_size, flags)

        if not found:
            print(f"[MISS] {p}")
            continue

        # subpixel refinement
        corners2 = cv2.cornerSubPix(gray, corners, (11, 11), (-1, -1), criteria)

        objpoints.append(objp)
        imgpoints.append(corners2)
        good += 1
        print(f"[OK] {p}")

        # optional debug draw
        if args.debug_dir:
            dbg = img.copy()
            cv2.drawChessboardCorners(dbg, pattern_size, corners2, found)
            if args.visualize_scale != 1.0:
                dbg = cv2.resize(
                    dbg, None, fx=args.visualize_scale, fy=args.visualize_scale,
                    interpolation=cv2.INTER_AREA
                )
            out_name = Path(args.debug_dir) / f"corners_{good:03d}.png"
            cv2.imwrite(str(out_name), dbg)

    if good < 10:
        raise SystemExit(
            f"Only {good} usable images. Take ~15–30 images with varied angles/distances."
        )

    # Calibrate
    # Returns RMS reprojection error (ret), camera matrix K, dist coeffs, rvecs, tvecs
    ret, K, dist, rvecs, tvecs = cv2.calibrateCamera(
        objpoints, imgpoints, img_size, None, None
    )

    # Compute mean reprojection error (more interpretable)
    total_err = 0.0
    total_points = 0
    for op, ip, rv, tv in zip(objpoints, imgpoints, rvecs, tvecs):
        proj, _ = cv2.projectPoints(op, rv, tv, K, dist)
        err = cv2.norm(ip, proj, cv2.NORM_L2)
        total_err += err * err
        total_points += len(op)
    mean_reproj_px = float(np.sqrt(total_err / total_points))

    if img_size is None:
        raise RuntimeError("No valid calibration images found. img_size is None.")

    out = {
        "image_size": {"w": int(img_size[0]), "h": int(img_size[1])},
        "checkerboard": {
            "rows_inner": int(args.rows),
            "cols_inner": int(args.cols),
            "square_mm": float(args.square_mm),
        },
        "rms_calibrateCamera": float(ret),
        "mean_reproj_error_px": mean_reproj_px,
        "K": K.tolist(),
        "dist": dist.reshape(-1).tolist(),  # typically k1,k2,p1,p2,k3 (may include more)
        # Convenience copies:
        "fx": float(K[0, 0]),
        "fy": float(K[1, 1]),
        "cx": float(K[0, 2]),
        "cy": float(K[1, 2]),
    }

    with open(args.out, "w") as f:
        yaml.safe_dump(out, f, sort_keys=False)

    print("\n=== Calibration complete ===")
    print(f"Used images: {good}/{len(paths)}")
    print(f"Image size : {img_size[0]} x {img_size[1]}")
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
        newK, roi = cv2.getOptimalNewCameraMatrix(K, dist, (w, h), 1.0, (w, h))
        und = cv2.undistort(ex, K, dist, None, newK)

        und_path = str(Path(args.undistort_example).with_name(
            Path(args.undistort_example).stem + "_undistorted.png"
        ))
        cv2.imwrite(und_path, und)
        print(f"Undistorted example saved: {und_path}")


if __name__ == "__main__":
    main()
