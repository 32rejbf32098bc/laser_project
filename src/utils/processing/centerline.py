from __future__ import annotations
import numpy as np
from scipy.spatial import cKDTree


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
    evals, evecs = np.linalg.eigh(C)  # ascending
    u = evecs[:, 1]                   # dominant (along)
    v = evecs[:, 0]                   # across
    s = X @ u
    t = X @ v
    return mu, u, v, s, t


def _cluster_points_by_proximity(
    pts_xy: np.ndarray,
    radius_px: float = 3.0,
    min_cluster_size: int = 25,
) -> list[np.ndarray]:
    """
    Group points into local connected segments using radius-neighbour graph.

    Args:
      pts_xy: (N,2) array of (x,y)
      radius_px: points within this distance are considered connected
      min_cluster_size: discard tiny clusters

    Returns:
      list of index arrays, one per cluster
    """
    if pts_xy.size == 0:
        return []

    tree = cKDTree(pts_xy)
    neighbours = tree.query_ball_point(pts_xy, r=radius_px)

    n = pts_xy.shape[0]
    visited = np.zeros(n, dtype=bool)
    clusters: list[np.ndarray] = []

    for seed in range(n):
        if visited[seed]:
            continue

        stack = [seed]
        visited[seed] = True
        comp = []

        while stack:
            i = stack.pop()
            comp.append(i)
            for j in neighbours[i]:
                if not visited[j]:
                    visited[j] = True
                    stack.append(j)

        comp = np.asarray(comp, dtype=np.int32)
        if comp.size >= min_cluster_size:
            clusters.append(comp)

    return clusters


def _centerline_from_single_segment(
    pts_yx: np.ndarray,
    strength: np.ndarray | None,
    bin_step_px: float = 1.0,
    smooth_win: int = 31,
    max_gap_px: float = 80.0,
    min_bins: int = 50,
) -> np.ndarray:
    """
    Existing PCA/bin/smooth logic, but applied to one local segment only.
    """
    if pts_yx is None or pts_yx.size == 0:
        return np.zeros((0, 2), np.float32)

    pts_xy = np.stack([pts_yx[:, 1], pts_yx[:, 0]], axis=1).astype(np.float32)
    mu, u, v, s, t = _pca_axes_from_pts_xy(pts_xy)

    s_min, s_max = float(s.min()), float(s.max())
    span = s_max - s_min
    if span < 10.0:
        return np.zeros((0, 2), np.float32)

    step = float(bin_step_px)
    nbins = int(np.floor(span / step)) + 1
    if nbins <= 0:
        return np.zeros((0, 2), np.float32)

    bin_idx = np.clip(((s - s_min) / step).astype(np.int32), 0, nbins - 1)

    best_s = np.full(nbins, np.nan, dtype=np.float32)
    best_t = np.full(nbins, np.nan, dtype=np.float32)
    best_strength = np.full(nbins, -np.inf, dtype=np.float32)

    has_strength = (strength is not None) and (strength.size == pts_yx.shape[0])

    for i in range(pts_xy.shape[0]):
        b = int(bin_idx[i])
        si = float(s[i])
        ti = float(t[i])
        st = float(strength[i]) if has_strength else 0.0

        if (st > best_strength[b]) or (
            st == best_strength[b] and (np.isnan(best_t[b]) or abs(ti) < abs(best_t[b]))
        ):
            best_strength[b] = st
            best_s[b] = si
            best_t[b] = ti

    valid = np.isfinite(best_s) & np.isfinite(best_t)
    if int(valid.sum()) < min_bins:
        return np.zeros((0, 2), np.float32)

    s_bins = s_min + step * np.arange(nbins, dtype=np.float32)
    t_bins = best_t.copy()

    t_interp = np.interp(s_bins, s_bins[valid], t_bins[valid]).astype(np.float32)

    if max_gap_px is not None and max_gap_px > 0:
        valid_idx = np.where(valid)[0]
        max_gap_bins = int(np.ceil(max_gap_px / step))
        gaps = np.diff(valid_idx)
        for k in np.where(gaps > max_gap_bins)[0]:
            a = valid_idx[k]
            b = valid_idx[k + 1]
            t_interp[a:b + 1] = np.nan

    if smooth_win and smooth_win >= 3:
        if smooth_win % 2 == 0:
            smooth_win += 1
        kernel = np.ones(smooth_win, dtype=np.float32) / float(smooth_win)

        finite = np.isfinite(t_interp)
        if finite.sum() > 10:
            t_fill = t_interp.copy()
            t_fill[~finite] = np.interp(
                s_bins[~finite], s_bins[finite], t_interp[finite]
            ).astype(np.float32)
            t_smooth = np.convolve(t_fill, kernel, mode="same").astype(np.float32)
            t_interp = np.where(finite, t_smooth, np.nan).astype(np.float32)

    keep = np.isfinite(t_interp)
    if not np.any(keep):
        return np.zeros((0, 2), np.float32)

    s_keep = s_bins[keep]
    t_keep = t_interp[keep]

    center_xy = mu + np.outer(s_keep, u) + np.outer(t_keep, v)
    center_yx = np.stack([center_xy[:, 1], center_xy[:, 0]], axis=1).astype(np.float32)
    return center_yx


def centerline_from_ridge_points(
    pts_yx: np.ndarray,
    strength: np.ndarray,
    bin_step_px: float = 1.0,
    smooth_win: int = 31,
    max_gap_px: float = 80.0,
    min_bins: int = 50,
    segment_radius_px: float = 3.0,
    min_segment_points: int = 25,
    return_segments: bool = False,
) -> np.ndarray | list[np.ndarray]:
    """
    Build centerline(s) from ridge points using LOCAL PCA per connected segment.

    Method:
      - Convert points to XY
      - Cluster points by local proximity
      - For each cluster:
          * run PCA locally
          * bin along local stripe axis
          * interpolate small gaps only within that segment
          * smooth locally
      - Return all valid centerline segments, or one concatenated array

    Args:
      pts_yx: (N,2) float32 array of (y,x)
      strength: (N,) float32 strength
      segment_radius_px: connectivity radius for grouping local segments
      min_segment_points: minimum number of raw ridge points to consider a segment
      return_segments: if True, return list of segments instead of concatenated array

    Returns:
      Either:
        - concatenated (M,2) float32 centerline points
        - or list of (Mi,2) float32 segments
    """
    if pts_yx is None or pts_yx.size == 0:
        return [] if return_segments else np.zeros((0, 2), np.float32)

    pts_xy = np.stack([pts_yx[:, 1], pts_yx[:, 0]], axis=1).astype(np.float32)
    clusters = _cluster_points_by_proximity(
        pts_xy,
        radius_px=segment_radius_px,
        min_cluster_size=min_segment_points,
    )

    if not clusters:
        return [] if return_segments else np.zeros((0, 2), np.float32)

    segments: list[np.ndarray] = []

    for idx in clusters:
        seg_pts = pts_yx[idx]
        seg_strength = strength[idx] if strength is not None and strength.size == pts_yx.shape[0] else None

        center_seg = _centerline_from_single_segment(
            seg_pts,
            seg_strength,
            bin_step_px=bin_step_px,
            smooth_win=smooth_win,
            max_gap_px=max_gap_px,
            min_bins=min_bins,
        )

        if center_seg.size > 0:
            segments.append(center_seg)

    if return_segments:
        return segments

    if not segments:
        return np.zeros((0, 2), np.float32)

    # concatenate, ordered by mean y then x for stable plotting/debug
    segments = sorted(
        segments,
        key=lambda a: (float(np.mean(a[:, 0])), float(np.mean(a[:, 1])))
    )
    return np.vstack(segments).astype(np.float32)


def centerline_quality_ok(
    center_yx: np.ndarray,
    min_len_pts: int = 400,
    max_jump_px: float = 8.0,
    min_span_px: float = 0.0,
) -> tuple[bool, str]:
    if center_yx is None or center_yx.size == 0:
        return False, "empty_centerline"
    if center_yx.shape[0] < min_len_pts:
        return False, f"short_centerline ({center_yx.shape[0]} < {min_len_pts})"

    xy = center_yx[:, [1, 0]]
    xy = xy[np.argsort(xy[:, 0])]

    d = np.linalg.norm(np.diff(xy, axis=0), axis=1)
    if d.size > 0 and np.median(d) > max_jump_px:
        return False, f"discontinuous (median_step={float(np.median(d)):.2f}px > {max_jump_px})"

    span = float(xy[-1, 0] - xy[0, 0])
    if span < min_span_px:
        return False, f"too_short_span (span={span:.1f}px < {min_span_px})"

    return True, "ok"


def centerline_segments_quality_ok(
    segments: list[np.ndarray],
    min_segments: int = 1,
    min_total_pts: int = 200,
) -> tuple[bool, str]:
    if not segments:
        return False, "no_segments"

    nseg = len(segments)
    total = int(sum(seg.shape[0] for seg in segments))
    if nseg < min_segments:
        return False, f"too_few_segments ({nseg} < {min_segments})"
    if total < min_total_pts:
        return False, f"too_few_total_pts ({total} < {min_total_pts})"

    return True, "ok"