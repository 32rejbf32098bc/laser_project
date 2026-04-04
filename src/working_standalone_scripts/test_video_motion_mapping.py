#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
from pathlib import Path

import cv2
import numpy as np
import yaml

from src.utils.processing.ridge import steger_laser_centerline_from_bgr


def load_roi(cfg_path: Path):
    with open(cfg_path, "r", encoding="utf-8") as f:
        cfg = yaml.safe_load(f) or {}
    roi = cfg.get("processing", {}).get("roi", None)
    return tuple(roi) if roi is not None else None


def load_camera_yaml(path: Path):
    with open(path, "r", encoding="utf-8") as f:
        data = yaml.safe_load(f) or {}
    cam = data["camera"]
    K = np.array(cam["K"], dtype=np.float64).reshape(3, 3)
    dist = np.array(cam["dist"], dtype=np.float64).ravel()
    return K, dist


def load_laser_plane_yaml(path: Path):
    with open(path, "r", encoding="utf-8") as f:
        data = yaml.safe_load(f) or {}
    plane = data["laser_plane"]
    n = np.array(plane["n"], dtype=np.float64)
    d = float(plane["d"])
    n /= np.linalg.norm(n) + 1e-12
    return n, d


def undistort_points(uv: np.ndarray, K: np.ndarray, dist: np.ndarray) -> np.ndarray:
    if uv.shape[0] == 0:
        return np.zeros((0, 2), dtype=np.float64)
    pts = uv.reshape(-1, 1, 2).astype(np.float64)
    und = cv2.undistortPoints(pts, K, dist, P=None)
    return und.reshape(-1, 2)


def intersect_rays_with_plane(xy_norm: np.ndarray, n: np.ndarray, d: float) -> np.ndarray:
    if xy_norm.shape[0] == 0:
        return np.zeros((0, 3), dtype=np.float64)

    rays = np.concatenate(
        [xy_norm, np.ones((xy_norm.shape[0], 1), dtype=np.float64)],
        axis=1,
    )

    denom = rays @ n
    valid = np.abs(denom) > 1e-12
    rays = rays[valid]
    denom = denom[valid]

    s = -d / denom
    xyz = rays * s[:, None]
    return xyz


def save_ply_xyz(path: Path, xyz: np.ndarray) -> None:
    with open(path, "w", encoding="utf-8") as f:
        f.write("ply\n")
        f.write("format ascii 1.0\n")
        f.write(f"element vertex {xyz.shape[0]}\n")
        f.write("property float x\n")
        f.write("property float y\n")
        f.write("property float z\n")
        f.write("end_header\n")
        for p in xyz:
            f.write(f"{p[0]} {p[1]} {p[2]}\n")


def save_npz(path: Path, xyz: np.ndarray, frame_index: np.ndarray, t_rel_s: np.ndarray) -> None:
    np.savez_compressed(
        path,
        xyz=xyz.astype(np.float32),
        frame_index=frame_index.astype(np.int32),
        t_rel_s=t_rel_s.astype(np.float32),
    )


def get_time_rel_s(frame_md: dict, mode: str, t0_sensor_ns: int | None) -> float:
    if mode == "sensor":
        ts = frame_md.get("sensor_timestamp_ns")
        if ts is None or t0_sensor_ns is None:
            raise RuntimeError("sensor_timestamp_ns missing, cannot use --time-mode sensor")
        return (ts - t0_sensor_ns) * 1e-9

    # default: capture_elapsed_s
    t = frame_md.get("capture_elapsed_s")
    if t is None:
        raise RuntimeError("capture_elapsed_s missing from metadata")
    return float(t)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--video", required=True)
    ap.add_argument("--meta-json", required=True)
    ap.add_argument("--config", required=True)
    ap.add_argument("--cam-yaml", required=True)
    ap.add_argument("--laser-yaml", required=True)
    ap.add_argument("--outdir", required=True)

    ap.add_argument("--velocity-mm-s", type=float, required=True)
    ap.add_argument(
        "--motion-axis",
        choices=["x", "y", "z"],
        default="z",
        help="Global axis along which stage motion is applied",
    )
    ap.add_argument(
        "--time-mode",
        choices=["capture_elapsed", "sensor"],
        default="capture_elapsed",
        help="Use capture_elapsed_s or sensor_timestamp_ns for frame timing",
    )

    ap.add_argument("--frame-step", type=int, default=1, help="Process every Nth frame")
    ap.add_argument("--max-frames", type=int, default=0, help="If >0, stop after this many processed frames")

    ap.add_argument("--sigma", type=float, default=2.0)
    ap.add_argument("--ridge-thresh", type=float, default=6.0)
    ap.add_argument("--tmax", type=float, default=1.0)

    ap.add_argument("--save-overlays", action="store_true")
    args = ap.parse_args()

    outdir = Path(args.outdir)
    outdir.mkdir(parents=True, exist_ok=True)
    overlays_dir = outdir / "overlays"
    if args.save_overlays:
        overlays_dir.mkdir(parents=True, exist_ok=True)

    roi = load_roi(Path(args.config))
    K, dist = load_camera_yaml(Path(args.cam_yaml))
    n, d = load_laser_plane_yaml(Path(args.laser_yaml))

    with open(args.meta_json, "r", encoding="utf-8") as f:
        meta = json.load(f)

    frame_meta = meta["frame_metadata"]
    t0_sensor_ns = None
    if args.time_mode == "sensor":
        if not frame_meta or frame_meta[0].get("sensor_timestamp_ns") is None:
            raise RuntimeError("No sensor timestamps available in metadata")
        t0_sensor_ns = int(frame_meta[0]["sensor_timestamp_ns"])

    cap = cv2.VideoCapture(str(args.video))
    if not cap.isOpened():
        raise RuntimeError(f"Could not open video: {args.video}")

    total_frames_video = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
    total_frames_meta = len(frame_meta)
    total_frames = min(total_frames_video, total_frames_meta)

    print(f"[INFO] Video frames: {total_frames_video}")
    print(f"[INFO] Metadata frames: {total_frames_meta}")
    print(f"[INFO] Using frames: {total_frames}")
    print(f"[INFO] ROI: {roi}")
    print(f"[INFO] Velocity: {args.velocity_mm_s} mm/s")
    print(f"[INFO] Motion axis: {args.motion_axis}")
    print(f"[INFO] Time mode: {args.time_mode}")

    axis_idx = {"x": 0, "y": 1, "z": 2}[args.motion_axis]

    all_xyz = []
    all_frame_idx = []
    all_t_rel_s = []

    processed = 0
    used_frames = 0
    skipped_no_ridge = 0

    frame_idx = 0
    while frame_idx < total_frames:
        ret, frame_bgr = cap.read()
        if not ret or frame_bgr is None:
            break

        if frame_idx % args.frame_step != 0:
            frame_idx += 1
            continue

        md = frame_meta[frame_idx]
        t_rel_s = get_time_rel_s(md, args.time_mode, t0_sensor_ns)

        pts_yx, strength = steger_laser_centerline_from_bgr(
            frame_bgr,
            sigma=args.sigma,
            ridge_thresh=args.ridge_thresh,
            t_max=args.tmax,
            roi=roi,
        )

        if pts_yx.shape[0] == 0:
            skipped_no_ridge += 1
            frame_idx += 1
            continue

        uv = pts_yx[:, ::-1].astype(np.float64)  # (y, x) -> (x, y)
        xy = undistort_points(uv, K, dist)
        xyz_cam = intersect_rays_with_plane(xy, n, d)

        if xyz_cam.shape[0] == 0:
            frame_idx += 1
            continue

        motion_mm = args.velocity_mm_s * t_rel_s
        xyz_global = xyz_cam.copy()
        xyz_global[:, axis_idx] += motion_mm

        all_xyz.append(xyz_global)
        all_frame_idx.append(np.full((xyz_global.shape[0],), frame_idx, dtype=np.int32))
        all_t_rel_s.append(np.full((xyz_global.shape[0],), t_rel_s, dtype=np.float32))

        used_frames += 1
        processed += 1

        if processed % 10 == 0 or processed == 1:
            print(
                f"[OK] frame={frame_idx:05d} "
                f"pts={xyz_global.shape[0]:5d} "
                f"t={t_rel_s:7.4f}s "
                f"motion={motion_mm:7.3f} mm"
            )

        if args.save_overlays:
            overlay = frame_bgr.copy()
            if roi is not None:
                x0, y0, x1, y1 = roi
                cv2.rectangle(overlay, (x0, y0), (x1, y1), (255, 0, 255), 2)

            step = max(1, pts_yx.shape[0] // 1500)
            for y, x in pts_yx[::step]:
                cv2.circle(overlay, (int(round(x)), int(round(y))), 1, (0, 255, 0), -1)

            lines = [
                f"frame={frame_idx}",
                f"pts={pts_yx.shape[0]}",
                f"t={t_rel_s:.4f}s",
                f"motion={motion_mm:.3f}mm",
            ]
            ytxt = 30
            for line in lines:
                cv2.putText(
                    overlay,
                    line,
                    (20, ytxt),
                    cv2.FONT_HERSHEY_SIMPLEX,
                    0.8,
                    (255, 255, 255),
                    2,
                    cv2.LINE_AA,
                )
                ytxt += 28

            cv2.imwrite(str(overlays_dir / f"frame_{frame_idx:05d}.jpg"), overlay)

        if args.max_frames > 0 and processed >= args.max_frames:
            break

        frame_idx += 1

    cap.release()

    if not all_xyz:
        raise RuntimeError("No 3D points generated from video")

    xyz_cat = np.vstack(all_xyz)
    frame_idx_cat = np.concatenate(all_frame_idx)
    t_rel_cat = np.concatenate(all_t_rel_s)

    ply_path = outdir / "video_motion_test.ply"
    npz_path = outdir / "video_motion_test.npz"
    summary_path = outdir / "summary.json"

    save_ply_xyz(ply_path, xyz_cat)
    save_npz(npz_path, xyz_cat, frame_idx_cat, t_rel_cat)

    summary = {
        "video": args.video,
        "meta_json": args.meta_json,
        "config": args.config,
        "cam_yaml": args.cam_yaml,
        "laser_yaml": args.laser_yaml,
        "roi": list(roi) if roi is not None else None,
        "velocity_mm_s": args.velocity_mm_s,
        "motion_axis": args.motion_axis,
        "time_mode": args.time_mode,
        "frame_step": args.frame_step,
        "max_frames": args.max_frames,
        "ridge_params": {
            "sigma": args.sigma,
            "ridge_thresh": args.ridge_thresh,
            "tmax": args.tmax,
        },
        "stats": {
            "total_frames_video": total_frames_video,
            "total_frames_meta": total_frames_meta,
            "frames_considered": total_frames,
            "frames_used": used_frames,
            "frames_skipped_no_ridge": skipped_no_ridge,
            "points_total": int(xyz_cat.shape[0]),
        },
        "outputs": {
            "ply": str(ply_path),
            "npz": str(npz_path),
        },
    }

    with open(summary_path, "w", encoding="utf-8") as f:
        json.dump(summary, f, indent=2)

    print(f"[DONE] frames_used={used_frames}, points_total={xyz_cat.shape[0]}")
    print(f"[DONE] Wrote: {ply_path}")
    print(f"[DONE] Wrote: {npz_path}")
    print(f"[DONE] Wrote: {summary_path}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())