#!/usr/bin/env python3
"""
calib_laser_plane.py

Calibrate a laser plane relative to a calibrated camera.

Method:
  For each image:
    1) detect checkerboard corners
    2) solvePnP -> board pose (R,t) in camera frame
    3) extract laser ridge points (Steger) -> pixel coords (u,v)
    4) undistort points -> normalized rays
    5) intersect rays with checkerboard plane -> 3D points in camera frame
  Aggregate all 3D points and fit plane: n^T X + d = 0

Outputs:
  YAML containing:
    laser_plane:
      n: [nx, ny, nz]
      d: float
    units: "mm"

Dependencies:
  pip install numpy opencv-python pyyaml
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass
from pathlib import Path
from typing import Tuple, List, Optional

import cv2
import numpy as np
import yaml

# Your ridge extractor (expects BGR)
from src.utils.processing.ridge import steger_ridge_points


# -----------------------------
# Data classes
# -----------------------------
@dataclass
class CameraIntrinsics:
    K: np.ndarray        # (3,3)
    dist: np.ndarray     # (N,)


@dataclass
class ChessboardSpec:
    cols: int            # inner corners per row
    rows: int            # inner corners per col
    square_mm: float     # millimetres


# -----------------------------
# YAML helpers
# -----------------------------
def load_camera_yaml(path: Path) -> CameraIntrinsics:
    path = Path(path)
    with open(path, "r", encoding="utf-8") as f:
        data = yaml.safe_load(f) or {}

    # Accept multiple layouts:
    # 1) {camera: {K: ..., dist: ...}}  (your calibrate_camera.py output)
    # 2) {K: ..., dist: ...}
    # 3) {camera_matrix: {data: [...]}, dist_coeff: {data: [...]}} (OpenCV-ish)
    if "camera" in data and isinstance(data["camera"], dict):
        cam = data["camera"]
        if "K" in cam:
            K = np.array(cam["K"], dtype=np.float64).reshape(3, 3)
        else:
            raise KeyError("camera.K not found in camera YAML")
        dist = np.array(cam.get("dist", []), dtype=np.float64).ravel()
    elif "camera_matrix" in data:
        K = np.array(data["camera_matrix"]["data"], dtype=np.float64).reshape(3, 3)
        dist = np.array(data.get("dist_coeff", {}).get("data", []), dtype=np.float64).ravel()
    elif "K" in data:
        K = np.array(data["K"], dtype=np.float64).reshape(3, 3)
        dist = np.array(data.get("dist", []), dtype=np.float64).ravel()
    else:
        raise KeyError("Could not find camera intrinsics (camera.K or K or camera_matrix) in YAML")

    if dist.size == 0:
        # allow no distortion, but normally you want it
        dist = np.zeros((5,), dtype=np.float64)

    return CameraIntrinsics(K=K, dist=dist)


def write_laser_plane_yaml(out_path: Path, base_yaml_path: Optional[Path], n: np.ndarray, d: float) -> None:
    plane = {
        "laser_plane": {"n": [float(n[0]), float(n[1]), float(n[2])], "d": float(d)},
        "units": "mm",
    }

    if base_yaml_path is not None and Path(base_yaml_path).exists():
        with open(base_yaml_path, "r", encoding="utf-8") as f:
            data = yaml.safe_load(f) or {}
        data.update(plane)
        with open(out_path, "w", encoding="utf-8") as f:
            yaml.safe_dump(data, f, sort_keys=False)
    else:
        with open(out_path, "w", encoding="utf-8") as f:
            yaml.safe_dump(plane, f, sort_keys=False)


# -----------------------------
# Chessboard helpers
# -----------------------------
def chessboard_object_points(spec: ChessboardSpec) -> np.ndarray:
    """(N,3) object points in board frame, Z=0 plane, units=mm."""
    objp = np.zeros((spec.rows * spec.cols, 3), np.float32)
    grid = np.mgrid[0:spec.cols, 0:spec.rows].T.reshape(-1, 2)
    objp[:, 0:2] = grid * float(spec.square_mm)
    return objp


def find_chessboard(gray: np.ndarray, spec: ChessboardSpec) -> Optional[np.ndarray]:
    """Return refined corners (N,1,2) or None."""
    pattern_size = (spec.cols, spec.rows)

    # Prefer SB if available (more robust)
    if hasattr(cv2, "findChessboardCornersSB"):
        flags = cv2.CALIB_CB_NORMALIZE_IMAGE | cv2.CALIB_CB_EXHAUSTIVE
        ok, corners = cv2.findChessboardCornersSB(gray, pattern_size, flags)
        if not ok:
            return None
        return corners.astype(np.float32)

    flags = cv2.CALIB_CB_ADAPTIVE_THRESH | cv2.CALIB_CB_NORMALIZE_IMAGE
    ok, corners = cv2.findChessboardCorners(gray, pattern_size, flags)
    if not ok:
        return None

    crit = (cv2.TERM_CRITERIA_EPS + cv2.TERM_CRITERIA_MAX_ITER, 40, 0.001)
    corners = cv2.cornerSubPix(gray, corners, (11, 11), (-1, -1), crit)
    return corners


def solve_board_pose(corners: np.ndarray, objp: np.ndarray, cam: CameraIntrinsics) -> Tuple[np.ndarray, np.ndarray]:
    """PnP pose of board in camera frame: R (3,3), t (3,) (units mm)."""
    imgp = corners.reshape(-1, 2).astype(np.float64)
    objp64 = objp.astype(np.float64)

    ok, rvec, tvec = cv2.solvePnP(objp64, imgp, cam.K, cam.dist, flags=cv2.SOLVEPNP_ITERATIVE)
    if not ok:
        raise RuntimeError("solvePnP failed")
    R, _ = cv2.Rodrigues(rvec)
    t = tvec.reshape(3)
    return R, t


# -----------------------------
# Laser ridge extraction (channel-robust)
# -----------------------------
def pick_best_channel(img_bgr: np.ndarray, roi):
    b = img_bgr[..., 0].astype(np.int16)
    g = img_bgr[..., 1].astype(np.int16)
    r = img_bgr[..., 2].astype(np.int16)
    redish = r - np.maximum(g, b)
    redish = np.clip(redish, 0, 255).astype(np.uint8)
    return redish


def filter_points_on_board(X_cam: np.ndarray, R: np.ndarray, t: np.ndarray, spec: ChessboardSpec,
                           margin_mm: float = 5.0) -> np.ndarray:
    """
    Keep only intersections that lie on/near the physical checkerboard area.
    Board frame: origin at first inner corner, x across cols, y across rows, z ~ 0.
    """
    if X_cam.shape[0] == 0:
        return X_cam

    # camera -> board: Xb = R^T (X - t)
    Xb = (R.T @ (X_cam - t.reshape(1, 3)).T).T

    w = (spec.cols - 1) * spec.square_mm
    h = (spec.rows - 1) * spec.square_mm

    x, y, z = Xb[:, 0], Xb[:, 1], Xb[:, 2]
    m = float(margin_mm)

    keep = (x >= -m) & (x <= w + m) & (y >= -m) & (y <= h + m) & (np.abs(z) <= 2.0*m)
    return X_cam[keep]

def steger_laser_ridge_points_any_channel(
    img_bgr: np.ndarray,
    roi: Optional[Tuple[int, int, int, int]],
    sigma: float,
    ridge_thresh: float,
    t_max: float,
) -> Tuple[np.ndarray, np.ndarray]:
    """
    Returns:
      pts_yx: (N,2) float32 (y,x)
      strength: (N,) float32
    """
    chan = pick_best_channel(img_bgr, roi)
    # If ROI, run Steger only inside ROI for speed and fewer false positives
    if roi is not None:
        x0, y0, x1, y1 = roi
        sub = chan[y0:y1, x0:x1]
        pts_yx, strength = steger_ridge_points(sub, sigma=sigma, ridge_thresh=ridge_thresh, t_max=t_max)
        if pts_yx.size == 0:
            return pts_yx, strength
        pts_yx[:, 0] += y0
        pts_yx[:, 1] += x0
        return pts_yx, strength
    else:
        return steger_ridge_points(chan, sigma=sigma, ridge_thresh=ridge_thresh, t_max=t_max)


# -----------------------------
# Geometry
# -----------------------------
def undistort_points(uv: np.ndarray, cam: CameraIntrinsics) -> np.ndarray:
    """uv: (N,2) pixel points -> normalized (N,2)."""
    if uv.shape[0] == 0:
        return np.zeros((0, 2), np.float64)
    pts = uv.reshape(-1, 1, 2).astype(np.float64)
    und = cv2.undistortPoints(pts, cam.K, cam.dist, P=None)
    return und.reshape(-1, 2)


def intersect_rays_with_board_plane(xy_norm: np.ndarray, R: np.ndarray, t: np.ndarray) -> np.ndarray:
    """
    Ray dir in camera: d=[x,y,1]
    Board plane in camera:
      normal n = R[:,2]
      point p0 = t
    Intersect X = s d, s = (n·p0)/(n·d)
    Units: mm
    """
    if xy_norm.shape[0] == 0:
        return np.zeros((0, 3), np.float64)

    d = np.concatenate([xy_norm, np.ones((xy_norm.shape[0], 1), np.float64)], axis=1)
    n = R[:, 2].reshape(3)
    p0 = t.reshape(3)

    nd = d @ n
    eps = 1e-9
    valid = np.abs(nd) > eps
    d = d[valid]
    nd = nd[valid]

    s = (n @ p0) / nd
    X = d * s[:, None]
    return X


def fit_plane_svd(X: np.ndarray) -> Tuple[np.ndarray, float]:
    """Fit plane n^T X + d = 0 to points X (N,3)."""
    if X.shape[0] < 200:
        raise ValueError("Not enough points to fit plane (need >=200)")

    c = X.mean(axis=0)
    Y = X - c
    _, _, Vt = np.linalg.svd(Y, full_matrices=False)
    n = Vt[-1]
    n = n / (np.linalg.norm(n) + 1e-12)
    d = -float(n @ c)

    # Make normal direction consistent (optional)
    if n[2] < 0:
        n = -n
        d = -d
    return n, d


def plane_residuals_mm(X: np.ndarray, n: np.ndarray, d: float) -> np.ndarray:
    """Signed distance to plane (mm)."""
    return (X @ n) + d


# -----------------------------
# Main
# -----------------------------
def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--cam-yaml", required=True, help="Camera YAML (from calibrate_camera.py)")
    ap.add_argument("--images", required=True, help="Folder of laser-plane calibration images")
    ap.add_argument("--out-yaml", required=True, help="Output YAML to write (laser_plane + units)")
    ap.add_argument("--update-yaml", default=None, help="If set, load this YAML and update it (write merged)")

    ap.add_argument("--cols", type=int, required=True, help="Checkerboard inner corners per row (cols)")
    ap.add_argument("--rows", type=int, required=True, help="Checkerboard inner corners per col (rows)")
    ap.add_argument("--square-mm", type=float, required=True, help="Checkerboard square size in mm")

    ap.add_argument("--sigma", type=float, default=2.0)
    ap.add_argument("--ridge-thresh", type=float, default=6.0)
    ap.add_argument("--tmax", type=float, default=1.0)

    ap.add_argument("--roi", type=str, default=None, help="ROI as x0,y0,x1,y1 (optional)")
    ap.add_argument("--max-pts-per-img", type=int, default=5000)
    ap.add_argument("--min-pts-per-img", type=int, default=200)

    ap.add_argument("--debug", action="store_true", help="Write debug overlays")
    ap.add_argument("--debug-dir", default=None, help="Directory for debug overlays (required if --debug)")
    ap.add_argument("--robust", action="store_true", help="Robust refit (inlier plane fit)")
    ap.add_argument("--inlier-mm", type=float, default=1.0, help="Inlier threshold for robust refit (mm)")

    args = ap.parse_args()

    cam = load_camera_yaml(Path(args.cam_yaml))
    spec = ChessboardSpec(cols=args.cols, rows=args.rows, square_mm=args.square_mm)
    objp = chessboard_object_points(spec)

    img_dir = Path(args.images)
    paths = sorted([p for p in img_dir.glob("*") if p.suffix.lower() in (".jpg", ".jpeg", ".png", ".bmp", ".tif", ".tiff")])
    if not paths:
        raise SystemExit(f"No images found in {img_dir}")

    roi: Optional[Tuple[int, int, int, int]] = None
    if args.roi:
        parts = [int(s.strip()) for s in args.roi.split(",")]
        if len(parts) != 4:
            raise SystemExit("--roi must be x0,y0,x1,y1")
        roi = (parts[0], parts[1], parts[2], parts[3])

    if args.debug:
        if not args.debug_dir:
            raise SystemExit("--debug-dir is required when using --debug")
        Path(args.debug_dir).mkdir(parents=True, exist_ok=True)

    all_X: List[np.ndarray] = []
    used = 0

    for p in paths:
        img = cv2.imread(str(p), cv2.IMREAD_COLOR)
        if img is None:
            continue

        gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
        corners = find_chessboard(gray, spec)
        if corners is None:
            continue

        R, t = solve_board_pose(corners, objp, cam)

        pts_yx, strength = steger_laser_ridge_points_any_channel(
            img, roi=roi, sigma=args.sigma, ridge_thresh=args.ridge_thresh, t_max=args.tmax
        )
        if pts_yx.size == 0:
            continue

        # downsample ridge points per image for speed / balance
        if pts_yx.shape[0] > args.max_pts_per_img:
            idx = np.random.choice(pts_yx.shape[0], size=args.max_pts_per_img, replace=False)
            pts_yx = pts_yx[idx]

        if pts_yx.shape[0] < args.min_pts_per_img:
            continue

        uv = pts_yx[:, ::-1].astype(np.float64)  # (y,x)->(x,y)
        xy = undistort_points(uv, cam)
        X = intersect_rays_with_board_plane(xy, R, t)
        X = filter_points_on_board(X, R, t, spec, margin_mm=5.0)

        if X.shape[0] < args.min_pts_per_img:
            continue

        all_X.append(X)
        used += 1

        if args.debug:
            overlay = img.copy()
            step = max(1, uv.shape[0] // 1200)
            for (u, v) in uv[::step]:
                cv2.circle(overlay, (int(u), int(v)), 1, (0, 255, 0), -1)
            outp = Path(args.debug_dir) / (p.stem + "_ridge.jpg")
            cv2.imwrite(str(outp), overlay)

    if used < 5:
        raise SystemExit(f"Only used {used} images. Need more valid poses / better stripe extraction.")

    Xcat = np.concatenate(all_X, axis=0)

    # initial fit
    n, d = fit_plane_svd(Xcat)
    r = np.abs(plane_residuals_mm(Xcat, n, d))

    if args.robust:
        inliers = r <= float(args.inlier_mm)
        Xin = Xcat[inliers]
        if Xin.shape[0] > 500:
            n, d = fit_plane_svd(Xin)
            r = np.abs(plane_residuals_mm(Xcat, n, d))

    # stats
    r_sorted = np.sort(r)
    def pct(a, q): return float(a[int(np.clip(q * (a.size - 1), 0, a.size - 1))])

    print(f"Used images      : {used}/{len(paths)}")
    print(f"Total 3D points  : {Xcat.shape[0]:,} (mm)")
    print(f"Laser plane (cam): n={n.tolist()}, d={d:.6f} mm")
    print(f"Residual |dist|  : mean={float(r.mean()):.4f} mm  "
          f"median={float(np.median(r)):.4f} mm  "
          f"P95={pct(r_sorted, 0.95):.4f} mm  "
          f"max={float(r.max()):.4f} mm")

    out_yaml = Path(args.out_yaml)
    base_yaml = Path(args.update_yaml) if args.update_yaml else None
    write_laser_plane_yaml(out_yaml, base_yaml, n, d)
    print(f"Wrote: {out_yaml}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
