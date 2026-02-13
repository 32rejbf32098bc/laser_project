#!/usr/bin/env python3
"""
ridge.py  (pure NumPy + OpenCV derivatives)

Steger-style subpixel ridge (laser line) extraction.

What you get:
- Subpixel ridge points as (y, x) float32
- Orientation-independent (works for horizontal/vertical/curved stripes)
- No binarisation required (you can still use ROI + red-channel)

Notes:
- This uses Sobel derivatives on a Gaussian-blurred image (practical + fast).
- True “derivative-of-Gaussian” filtering is equivalent in spirit for our use.

Updates in this version:
- Uses denom = lam_n (eigenvalue) directly for subpixel offset (stable + correct for eigenvector normal)
- Adds an adaptive ridge threshold based on percentile of strength, so you don’t retune as much
- Keeps your existing ridge_thresh as a hard minimum (floor)
"""

from __future__ import annotations
import numpy as np
import cv2


def _eig2x2(a: np.ndarray, b: np.ndarray, c: np.ndarray):
    """
    Eigen decomposition for symmetric 2x2 matrices:
      [[a, b],
       [b, c]]

    Returns:
      l1, l2 : eigenvalues
      vx1, vy1 : eigenvector for l1 (corresponding to l_plus)
      vx2, vy2 : eigenvector for l2 (orthogonal)
    """
    # eigenvalues
    tr = a + c
    det_term = (a - c) * (a - c) + 4.0 * b * b
    s = np.sqrt(np.maximum(det_term, 0.0))
    l_plus = 0.5 * (tr + s)
    l_minus = 0.5 * (tr - s)

    # eigenvectors (for l_plus)
    # Solve (A - λI)v = 0. For symmetric 2x2, a stable formula:
    # If b != 0: v = [b, λ - a]
    vx_p = b
    vy_p = l_plus - a

    # If b is tiny, fall back to axis-aligned
    tiny = 1e-12
    mask = (np.abs(b) < tiny) & (np.abs(vy_p) < tiny)
    vx_p = np.where(mask, 1.0, vx_p)
    vy_p = np.where(mask, 0.0, vy_p)

    # Normalise
    nrm = np.sqrt(vx_p * vx_p + vy_p * vy_p)
    vx_p /= np.maximum(nrm, tiny)
    vy_p /= np.maximum(nrm, tiny)

    # eigenvector for l_minus is orthogonal
    vx_m = -vy_p
    vy_m = vx_p

    return l_plus, l_minus, vx_p, vy_p, vx_m, vy_m


def steger_ridge_points(
    img_gray: np.ndarray,
    sigma: float = 1.2,
    ridge_thresh: float = 8.0,
    t_max: float = 0.5,
    border: int = 2,
    adaptive_percentile: float = 95.0,
    adaptive_frac: float = 0.30,
    intensity_percentile: float = 90,   # NEW
):
    """
    Extract subpixel ridge points using a Steger-style method.

    Args:
      img_gray: uint8/float 2D image (grayscale).
      sigma: Gaussian blur sigma (match ~half stripe width in pixels).
      ridge_thresh: hard minimum ridge strength (floor).
      t_max: maximum subpixel shift magnitude along the normal (pixels).
      border: ignore this many pixels around edges.
      adaptive_percentile: percentile (0-100) of ridge strength used for adaptive thresholding.
      adaptive_frac: fraction of that percentile to use as the adaptive threshold.

    Returns:
      pts_yx: (N,2) float32 array of (y, x) points
      strength: (N,) float32 ridge strength (positive for bright ridges)
    """
    if img_gray.ndim != 2:
        raise ValueError("img_gray must be 2D")

    I = img_gray.astype(np.float32)

    # 1) Smooth
    k = int(np.ceil(sigma * 6)) | 1
    Is = cv2.GaussianBlur(I, (k, k), sigmaX=sigma, sigmaY=sigma, borderType=cv2.BORDER_REPLICATE)

    # NEW: intensity gate – only keep very bright pixels (laser should be among brightest)
    I_thr = float(np.percentile(Is, intensity_percentile))
    intensity_mask = Is >= I_thr

    # 2) First derivatives
    Ix = cv2.Sobel(Is, cv2.CV_32F, 1, 0, ksize=3)
    Iy = cv2.Sobel(Is, cv2.CV_32F, 0, 1, ksize=3)

    # 3) Second derivatives (Hessian)
    Ixx = cv2.Sobel(Is, cv2.CV_32F, 2, 0, ksize=3)
    Ixy = cv2.Sobel(Is, cv2.CV_32F, 1, 1, ksize=3)
    Iyy = cv2.Sobel(Is, cv2.CV_32F, 0, 2, ksize=3)

    # 4) Eigen stuff
    l1, l2, v1x, v1y, v2x, v2y = _eig2x2(Ixx, Ixy, Iyy)

    # Direction ACROSS the ridge = direction of maximum curvature magnitude
    use1 = np.abs(l1) >= np.abs(l2)
    lam_n = np.where(use1, l1, l2)           # curvature across ridge
    nx = np.where(use1, v1x, v2x)
    ny = np.where(use1, v1y, v2y)

    # Ridge condition for BRIGHT ridge on dark background:
    # lam_n should be strongly NEGATIVE (concave down across ridge)
    strength_map = -lam_n
    strength_map = np.maximum(strength_map, 0.0)  # clamp: bright ridges -> positive, everything else -> 0

    # Adaptive threshold: scale to image content so you don't retune ridge_thresh constantly
    # Keep ridge_thresh as a hard minimum floor.
    pval = float(np.percentile(strength_map, adaptive_percentile))
    adaptive_thr = adaptive_frac * pval
    thr = max(float(ridge_thresh), float(adaptive_thr))

    ridge_mask = (lam_n < 0.0) & (strength_map >= thr) & intensity_mask

    # 5) Subpixel offset t along normal:
    # t = -(grad·n) / (n^T H n)
    # For eigenvector normal n, n^T H n == lam_n (since ||n||=1)
    grad_dot_n = Ix * nx + Iy * ny

    denom = lam_n  # updated: use eigenvalue directly (stable + correct)
    denom_ok = np.abs(denom) > 1e-9

    t = np.zeros_like(Is, dtype=np.float32)
    t[denom_ok] = -grad_dot_n[denom_ok] / denom[denom_ok]

    ridge_mask &= denom_ok & (np.abs(t) <= t_max)

    # Border exclude
    h, w = Is.shape
    ridge_mask[:border, :] = False
    ridge_mask[-border:, :] = False
    ridge_mask[:, :border] = False
    ridge_mask[:, -border:] = False

    # 6) Optional NMS along normal (cheap 2-sample check)
    ys, xs = np.where(ridge_mask)
    if ys.size == 0:
        return np.zeros((0, 2), np.float32), np.zeros((0,), np.float32)

    x1 = np.clip((xs + np.round(nx[ys, xs]).astype(int)), 0, w - 1)
    y1 = np.clip((ys + np.round(ny[ys, xs]).astype(int)), 0, h - 1)
    x2 = np.clip((xs - np.round(nx[ys, xs]).astype(int)), 0, w - 1)
    y2 = np.clip((ys - np.round(ny[ys, xs]).astype(int)), 0, h - 1)

    I0 = Is[ys, xs]
    Ipos = Is[y1, x1]
    Ineg = Is[y2, x2]
    nms = (I0 >= Ipos) & (I0 >= Ineg)

    ys = ys[nms]
    xs = xs[nms]

    # Subpixel point: (x,y) + t*n
    tt = t[ys, xs]
    x_sub = xs.astype(np.float32) + tt * nx[ys, xs]
    y_sub = ys.astype(np.float32) + tt * ny[ys, xs]

    pts_yx = np.stack([y_sub, x_sub], axis=1).astype(np.float32)
    strength = strength_map[ys, xs].astype(np.float32)  # updated: positive ridge strength
    return pts_yx, strength


# ----------------------------
# Convenience wrapper for your laser images
# ----------------------------
def steger_laser_centerline_from_bgr(
    img_bgr: np.ndarray,
    sigma: float = 1.2,
    ridge_thresh: float = 8.0,
    t_max: float = 0.5,
    adaptive_percentile: float = 95.0,
    adaptive_frac: float = 0.30,
):
    """
    Practical wrapper:
    - Use RED channel as grayscale (good for red laser)
    - Return (y,x) points
    """
    red = img_bgr[:, :, 2]  # OpenCV BGR -> red channel
    pts_yx, strength = steger_ridge_points(
        red,
        sigma=sigma,
        ridge_thresh=ridge_thresh,
        t_max=t_max,
        adaptive_percentile=adaptive_percentile,
        adaptive_frac=adaptive_frac,
    )
    return pts_yx, strength


# ----------------------------
# Quick test (single image)
# ----------------------------
if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--in", dest="inp", required=True)
    ap.add_argument("--out", default="steger_overlay.jpg")
    ap.add_argument("--sigma", type=float, default=1.2)
    ap.add_argument("--thresh", type=float, default=8.0)
    ap.add_argument("--tmax", type=float, default=1.0)
    ap.add_argument("--pctl", type=float, default=95.0, help="adaptive percentile")
    ap.add_argument("--frac", type=float, default=0.30, help="fraction of percentile for adaptive threshold")
    args = ap.parse_args()

    img = cv2.imread(args.inp, cv2.IMREAD_COLOR)
    if img is None:
        raise SystemExit(f"Could not read: {args.inp}")

    pts, strength = steger_laser_centerline_from_bgr(
        img,
        sigma=args.sigma,
        ridge_thresh=args.thresh,
        t_max=args.tmax,
        adaptive_percentile=args.pctl,
        adaptive_frac=args.frac,
    )

    overlay = img.copy()
    for y, x in pts:
        cv2.circle(overlay, (int(round(x)), int(round(y))), 1, (0, 255, 0), -1)

    cv2.imwrite(args.out, overlay)
    print(f"pts: {pts.shape[0]}, mean strength: {float(np.mean(strength)) if strength.size else 0:.2f}")
    print(f"wrote: {args.out}")
