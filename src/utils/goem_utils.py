# utils/geom_utils.py
from __future__ import annotations

import numpy as np


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
                                     laser_plane_n: np.ndarray,
                                     laser_plane_d: float):
    """
    Placeholder: convert a set of laser pixels into 3D points by intersecting
    camera rays with the laser plane.

    pts_yx is (y,x) from your extractor.
    Returns Nx3 points in camera frame.
    """
    C = np.zeros(3)
    out = []
    for (y, x) in pts_yx:
        r = pixels_to_ray(x, y, K)  # NOTE: u=x, v=y
        t, X = ray_plane_intersection(C, r, laser_plane_n, laser_plane_d)
        if t is None or X is None or t <= 0:
            out.append([np.nan, np.nan, np.nan])
        else:
            out.append(X.tolist())
    return np.array(out, dtype=float)
