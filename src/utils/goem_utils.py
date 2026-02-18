# utils/geom_utils.py
from __future__ import annotations

import numpy as np
import cv2


def norm(v: np.ndarray) -> np.ndarray:
    v = np.asarray(v, dtype=float)
    n = np.linalg.norm(v)
    return v / n if n > 0 else v


def ray_plane_intersection(C: np.ndarray, r: np.ndarray, n: np.ndarray, d: float, eps: float = 1e-9):
    """
    Plane: n·X + d = 0
    Ray: X(t)=C+t r
    """
    denom = float(np.dot(n, r))
    if abs(denom) < eps:
        return None, None
    t = -(np.dot(n, C) + d) / denom
    X = C + t * r
    return t, X


def pixels_to_ray(u: float, v: float, K: np.ndarray) -> np.ndarray:
    """
    Convert pixel (u,v) to camera ray direction in camera coords.
    Assumes pinhole, no distortion, and camera at origin.
    """
    fx = K[0, 0]
    fy = K[1, 1]
    cx = K[0, 2]
    cy = K[1, 2]
    x = (u - cx) / fx
    y = (v - cy) / fy
    r = np.array([x, y, 1.0], dtype=float)
    return norm(r)


def triangulate_centerline_to_points(pts_yx: np.ndarray,
    K: np.ndarray,
    dist: np.ndarray,
    plane_n: np.ndarray,
    plane_d: float,
) -> np.ndarray:
    """
    pts_yx: (N,2) float32 (y,x)
    Returns: (N,3) 3D points in camera frame
    """

    if pts_yx.size == 0:
        return np.zeros((0, 3), np.float32)

    # Convert to (x,y)
    uv = pts_yx[:, ::-1].astype(np.float64)

    pts = uv.reshape(-1, 1, 2)
    xy_norm = cv2.undistortPoints(pts, K, dist).reshape(-1, 2)

    # Rays
    rays = np.hstack([xy_norm, np.ones((xy_norm.shape[0], 1))])

    nd = rays @ plane_n
    valid = np.abs(nd) > 1e-9

    rays = rays[valid]
    nd = nd[valid]

    s = -plane_d / nd
    X = rays * s[:, None]

    return X.astype(np.float32)

def points3d_quality_ok(xyz: np.ndarray,
                        min_pts: int = 200,
                        max_abs_mm: float = 5000.0,
                        z_range_mm: tuple[float, float] = (1.0, 2000.0)) -> tuple[bool, str]:
    if xyz is None or xyz.size == 0:
        return False, "empty_xyz"
    if xyz.shape[0] < min_pts:
        return False, f"too_few_xyz ({xyz.shape[0]} < {min_pts})"

    # finite check
    ok = np.isfinite(xyz).all(axis=1)
    xyz = xyz[ok]
    if xyz.shape[0] < min_pts:
        return False, "too_many_invalid_xyz"

    # bounds check
    if np.max(np.abs(xyz)) > max_abs_mm:
        return False, "xyz_out_of_bounds"

    zmin, zmax = z_range_mm
    z = xyz[:, 2]
    # for a camera-coordinate system, Z should generally be positive and within some range
    if np.quantile(z, 0.05) < zmin or np.quantile(z, 0.95) > zmax:
        return False, f"bad_z_range (z05={float(np.quantile(z,0.05)):.1f}, z95={float(np.quantile(z,0.95)):.1f})"

    return True, "ok"
