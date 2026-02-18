from __future__ import annotations
import numpy as np


def _pca_axes_from_pts_xy(pts_xy: np.ndarray):
    """
    pts_xy: (N,2) float32 (x,y)
    Returns:
      mu: (2,)
      u:  (2,) dominant axis (along stripe)
      v:  (2,) orthogonal axis (across stripe)
      s:  (N,) coordinate along u
      t:  (N,) coordinate along v
    """
    mu = pts_xy.mean(axis=0)
    X = pts_xy - mu
    C = (X.T @ X) / max(1, X.shape[0] - 1)
    evals, evecs = np.linalg.eigh(C)      # ascending
    u = evecs[:, 1]                       # dominant (along)
    v = evecs[:, 0]                       # across
    s = X @ u
    t = X @ v
    return mu, u, v, s, t


def centerline_from_ridge_points(
    pts_yx: np.ndarray,
    strength: np.ndarray,
    bin_step_px: float = 1.0,
    smooth_win: int = 31,
    max_gap_px: float = 80.0,
    min_bins: int = 50,
) -> np.ndarray:
    """
    Build a continuous-ish centerline from unordered ridge points, without assuming
    any fixed orientation (no "one per image column").

    Method:
      - PCA to define a local (s,t) frame: s along the stripe, t across it
      - Bin along s, keep strongest point per bin (tie-break: smallest |t|)
      - Interpolate small gaps, optionally avoid bridging big gaps
      - Smooth t(s)
      - Map back to (y,x)

    Returns:
      center_yx: (M,2) float32 ordered points (y,x)
    """
    if pts_yx is None or pts_yx.size == 0:
        return np.zeros((0, 2), np.float32)

    # Convert to (x,y)
    pts_xy = np.stack([pts_yx[:, 1], pts_yx[:, 0]], axis=1).astype(np.float32)
    mu, u, v, s, t = _pca_axes_from_pts_xy(pts_xy)

    s_min, s_max = float(s.min()), float(s.max())
    span = s_max - s_min
    if span < 10.0:
        return np.zeros((0, 2), np.float32)

    step = float(bin_step_px)
    nbins = int(np.floor(span / step)) + 1
    bin_idx = np.clip(((s - s_min) / step).astype(np.int32), 0, nbins - 1)

    # Best per bin
    best_s = np.full(nbins, np.nan, dtype=np.float32)
    best_t = np.full(nbins, np.nan, dtype=np.float32)
    best_strength = np.full(nbins, -np.inf, dtype=np.float32)

    has_strength = (strength is not None) and (strength.size == pts_yx.shape[0])
    for i in range(pts_xy.shape[0]):
        b = int(bin_idx[i])
        si = float(s[i])
        ti = float(t[i])
        st = float(strength[i]) if has_strength else 0.0

        # Prefer higher strength; tie-breaker prefer smaller |t|
        if (st > best_strength[b]) or (st == best_strength[b] and (np.isnan(best_t[b]) or abs(ti) < abs(best_t[b]))):
            best_strength[b] = st
            best_s[b] = si
            best_t[b] = ti

    valid = np.isfinite(best_s) & np.isfinite(best_t)
    if int(valid.sum()) < min_bins:
        return np.zeros((0, 2), np.float32)

    s_bins = (s_min + step * np.arange(nbins, dtype=np.float32))
    t_bins = best_t.copy()

    # Interpolate missing bins, but avoid bridging huge gaps
    valid_idx = np.where(valid)[0]
    t_interp = np.interp(s_bins, s_bins[valid], t_bins[valid]).astype(np.float32)

    if max_gap_px is not None and max_gap_px > 0:
        max_gap_bins = int(np.ceil(max_gap_px / step))
        gaps = np.diff(valid_idx)
        for k in np.where(gaps > max_gap_bins)[0]:
            a = valid_idx[k]
            b = valid_idx[k + 1]
            t_interp[a:b+1] = np.nan

    # Smooth t(s) only where finite
    if smooth_win and smooth_win >= 3:
        if smooth_win % 2 == 0:
            smooth_win += 1
        kernel = np.ones(smooth_win, dtype=np.float32) / float(smooth_win)

        finite = np.isfinite(t_interp)
        if finite.sum() > 10:
            # Fill NaNs for smoothing, then re-mask
            t_fill = t_interp.copy()
            t_fill[~finite] = np.interp(s_bins[~finite], s_bins[finite], t_interp[finite]).astype(np.float32)
            t_smooth = np.convolve(t_fill, kernel, mode="same").astype(np.float32)
            t_interp = np.where(finite, t_smooth, np.nan).astype(np.float32)

    keep = np.isfinite(t_interp)
    s_keep = s_bins[keep]
    t_keep = t_interp[keep]

    # Map back to XY, then to YX
    center_xy = mu + np.outer(s_keep, u) + np.outer(t_keep, v)
    center_yx = np.stack([center_xy[:, 1], center_xy[:, 0]], axis=1).astype(np.float32)
    return center_yx


def centerline_quality_ok(center_yx: np.ndarray,
                          min_len_pts: int = 400,
                          max_jump_px: float = 8.0,
                          min_span_px: float = 0.0) -> tuple[bool, str]:
    if center_yx is None or center_yx.size == 0:
        return False, "empty_centerline"
    if center_yx.shape[0] < min_len_pts:
        return False, f"short_centerline ({center_yx.shape[0]} < {min_len_pts})"

    # sort by x just for continuity check (not for extraction)
    xy = center_yx[:, [1, 0]]  # x,y
    xy = xy[np.argsort(xy[:, 0])]

    # continuity: successive jumps not insane
    d = np.linalg.norm(np.diff(xy, axis=0), axis=1)
    if np.median(d) > max_jump_px:
        return False, f"discontinuous (median_step={float(np.median(d)):.2f}px > {max_jump_px})"

    span = float(xy[-1, 0] - xy[0, 0])
    if span < min_span_px:
        return False, f"too_short_span (span={span:.1f}px < {min_span_px})"

    return True, "ok"
