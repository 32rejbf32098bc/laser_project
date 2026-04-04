#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
from pathlib import Path

import cv2
import numpy as np
import yaml

from src.utils.processing.ridge import steger_laser_centerline_from_bgr


def load_roi(config_path: Path) -> tuple[int, int, int, int] | None:
    with open(config_path, "r", encoding="utf-8") as f:
        cfg = yaml.safe_load(f) or {}

    roi = cfg.get("processing", {}).get("roi")
    if roi is None:
        return None
    if len(roi) != 4:
        raise ValueError("processing.roi must have 4 values: [x0, y0, x1, y1]")
    return tuple(int(v) for v in roi)


def load_frame_metadata(json_path: Path) -> list[dict]:
    with open(json_path, "r", encoding="utf-8") as f:
        data = json.load(f)
    return data.get("frame_metadata", [])


def pick_frame_indices(total_frames: int) -> list[int]:
    if total_frames <= 0:
        return []
    idxs = sorted(set([0, total_frames // 2, max(0, total_frames - 1)]))
    return idxs


def draw_overlay(
    frame_bgr: np.ndarray,
    pts_yx: np.ndarray,
    roi: tuple[int, int, int, int] | None,
    lines: list[str],
) -> np.ndarray:
    overlay = frame_bgr.copy()

    if roi is not None:
        x0, y0, x1, y1 = roi
        cv2.rectangle(overlay, (x0, y0), (x1, y1), (255, 0, 255), 2)

    if pts_yx.size > 0:
        step = max(1, pts_yx.shape[0] // 1500)
        for y, x in pts_yx[::step]:
            cv2.circle(overlay, (int(round(x)), int(round(y))), 1, (0, 255, 0), -1)

    y = 30
    for line in lines:
        cv2.putText(
            overlay,
            line,
            (20, y),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.8,
            (255, 255, 255),
            2,
            cv2.LINE_AA,
        )
        y += 28

    return overlay


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--video", required=True, help="Path to AVI video")
    ap.add_argument("--meta-json", required=True, help="Path to capture JSON metadata")
    ap.add_argument("--config", required=True, help="YAML config containing processing.roi")
    ap.add_argument("--outdir", required=True, help="Directory for saved overlays")
    ap.add_argument("--sigma", type=float, default=2.0)
    ap.add_argument("--ridge-thresh", type=float, default=6.0)
    ap.add_argument("--tmax", type=float, default=1.0)
    args = ap.parse_args()

    video_path = Path(args.video)
    meta_path = Path(args.meta_json)
    config_path = Path(args.config)
    outdir = Path(args.outdir)
    outdir.mkdir(parents=True, exist_ok=True)

    roi = load_roi(config_path)
    frame_meta = load_frame_metadata(meta_path)

    cap = cv2.VideoCapture(str(video_path))
    if not cap.isOpened():
        raise RuntimeError(f"Could not open video: {video_path}")

    total_frames = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
    fps = float(cap.get(cv2.CAP_PROP_FPS))

    test_indices = pick_frame_indices(total_frames)
    if not test_indices:
        raise RuntimeError("No frames found in video")

    print(f"[INFO] Video: {video_path}")
    print(f"[INFO] Total frames: {total_frames}")
    print(f"[INFO] FPS: {fps}")
    print(f"[INFO] ROI: {roi}")
    print(f"[INFO] Testing frames: {test_indices}")

    results: list[dict] = []

    for idx in test_indices:
        ok = cap.set(cv2.CAP_PROP_POS_FRAMES, idx)
        if not ok:
            print(f"[WARN] Failed to seek to frame {idx}")

        ret, frame_bgr = cap.read()
        if not ret or frame_bgr is None:
            print(f"[WARN] Failed to read frame {idx}")
            continue

        pts_yx, strength = steger_laser_centerline_from_bgr(
            frame_bgr,
            sigma=args.sigma,
            ridge_thresh=args.ridge_thresh,
            t_max=args.tmax,
            roi=roi,
        )

        md = frame_meta[idx] if idx < len(frame_meta) else {}

        sensor_ts = md.get("sensor_timestamp_ns")
        exposure_us = md.get("exposure_time_us")
        gain = md.get("analogue_gain")
        lens = md.get("lens_position")

        result = {
            "frame_index": idx,
            "points": int(pts_yx.shape[0]),
            "mean_strength": float(np.mean(strength)) if strength.size else None,
            "sensor_timestamp_ns": sensor_ts,
            "exposure_time_us": exposure_us,
            "analogue_gain": gain,
            "lens_position": lens,
        }
        results.append(result)

        lines = [
            f"frame={idx}",
            f"pts={pts_yx.shape[0]}",
            f"mean_strength={result['mean_strength']:.2f}" if result["mean_strength"] is not None else "mean_strength=None",
            f"sensor_ts={sensor_ts}",
        ]

        overlay = draw_overlay(frame_bgr, pts_yx, roi, lines)
        out_path = outdir / f"{video_path.stem}_frame_{idx:05d}_overlay.jpg"
        cv2.imwrite(str(out_path), overlay)

        print(
            f"[OK] frame={idx:05d} "
            f"pts={pts_yx.shape[0]:5d} "
            f"mean_strength={result['mean_strength'] if result['mean_strength'] is not None else 'None'} "
            f"sensor_ts={sensor_ts}"
        )

    cap.release()

    summary_path = outdir / f"{video_path.stem}_ridge_test_summary.json"
    with open(summary_path, "w", encoding="utf-8") as f:
        json.dump(
            {
                "video": str(video_path),
                "meta_json": str(meta_path),
                "config": str(config_path),
                "roi": list(roi) if roi is not None else None,
                "fps": fps,
                "total_frames": total_frames,
                "tested_frames": results,
                "ridge_params": {
                    "sigma": args.sigma,
                    "ridge_thresh": args.ridge_thresh,
                    "tmax": args.tmax,
                },
            },
            f,
            indent=2,
        )

    print(f"[INFO] Wrote summary: {summary_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())