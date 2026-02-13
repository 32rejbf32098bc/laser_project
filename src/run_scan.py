#!/usr/bin/env python3
"""
run_scan.py — main orchestrator

Runs the full pipeline:
1) Load config + (optional) calibration yaml
2) Init stage
3) (Optional) auto-exposure calibration
4) Capture scan (move + capture)
5) Return to start (soft)
6) Run centerline extraction + reconstruction placeholders
7) Save scan metadata

NOTE: This file intentionally contains NO helper logic.
"""

from __future__ import annotations

import argparse
import time
from dataclasses import dataclass, asdict
from pathlib import Path
import numpy as np
import cv2

from utils.io_utils import ensure_dir, now_stamp, print_progress, load_config, save_json
from hardware.stage_gpiozero import StepDirStage, StageConfig
from hardware.camera_rpicam import RpiCamConfig, RpiCamStill
from utils.vision_utils import auto_exposure_calibrate, save_centerline_overlay
from utils.processing.ridge import steger_laser_centerline_from_bgr
from utils.processing.centerline import centerline_from_ridge_points

# -----------------------------
# Plans (data only)
# -----------------------------
@dataclass
class MotionPlan:
    total_mm: float
    step_mm: float
    mm_per_s: float
    settle_s: float
    direction: str
    return_home: bool


@dataclass
class CameraPlan:
    width: int
    height: int
    enc: str
    quality: int
    timeout_ms: int
    shutter_us: int
    gain: float
    awb_gains: str
    lens_pos: float | None


@dataclass
class ProcessingPlan:
    cfg_path: str
    orientation: str
    overlay_every: int
    max_frames: int


def main():
    ap = argparse.ArgumentParser(description="Full scan orchestrator")

    # IDs and directories
    ap.add_argument("--scan-id", default="")
    ap.add_argument("--raw-root", default="data/raw")
    ap.add_argument("--proc-root", default="data/processed")
    ap.add_argument("--meta-name", default="scan_meta.json")

    # Config
    ap.add_argument("--cfg", default="cfg/config.yaml")

    # Motion (experiment-level)
    ap.add_argument("--total-mm", type=float, required=True)
    ap.add_argument("--step-mm", type=float, required=True)
    ap.add_argument("--mm-per-s", type=float, default=20.0)
    ap.add_argument("--settle-s", type=float, default=0.2)
    ap.add_argument("--direction", choices=["forward", "backward"], default="forward")
    ap.add_argument("--no-return-home", action="store_true", help="Do NOT return stage to start position after scan")

    # Optional overrides (None unless provided)
    ap.add_argument("--step-pin", type=int, default=None)
    ap.add_argument("--dir-pin", type=int, default=None)
    ap.add_argument("--width", type=int, default=None)
    ap.add_argument("--height", type=int, default=None)
    ap.add_argument("--shutter-us", type=int, default=None)
    ap.add_argument("--gain", type=float, default=None)
    ap.add_argument("--lens-pos", type=float, default=None)

    # Auto exposure
    ap.add_argument("--auto-exposure", action="store_true")

    # Safety
    ap.add_argument("--dry-run", action="store_true")

    # Debug
    ap.add_argument("--debug-every", type=int, default=10, help="save overlay every N frames (0 = off)")
    args = ap.parse_args()

    # -----------------------------
    # Load config (ROI lives here)
    # -----------------------------
    cfg = load_config(args.cfg)

    stage_dict = dict(cfg.get("stage", {}))
    cam_dict = dict(cfg.get("camera", {}))
    roi = cfg.get("processing", {}).get("roi", None)

    # -----------------------------
    # Apply CLI overrides
    # -----------------------------
    if args.step_pin is not None:
        stage_dict["step_pin"] = args.step_pin
    if args.dir_pin is not None:
        stage_dict["dir_pin"] = args.dir_pin

    if args.width is not None:
        cam_dict["width"] = args.width
    if args.height is not None:
        cam_dict["height"] = args.height
    if args.shutter_us is not None:
        cam_dict["shutter_us"] = args.shutter_us
    if args.gain is not None:
        cam_dict["gain"] = args.gain
    if args.lens_pos is not None:
        cam_dict["lens_pos"] = args.lens_pos

    #------------------------------
    # Build config objects
    # -----------------------------
    stage_cfg = StageConfig(**stage_dict)
    cam_cfg = RpiCamConfig(**cam_dict)

    motion_total_mm = float(args.total_mm)
    motion_step_mm = float(args.step_mm)

    n_frames = int(motion_total_mm // motion_step_mm) + 1

    scan_id = args.scan_id.strip() or f"scan_{now_stamp()}"
    raw_dir = Path(args.raw_root) / scan_id
    proc_dir = Path(args.proc_root) / scan_id
    ensure_dir(raw_dir)
    ensure_dir(proc_dir)

    print("\n=== RUN SCAN ===")
    print(f"scan_id  : {scan_id}")
    print(f"frames   : {n_frames}")
    print(f"direction: {args.direction}")
    print(f"auto_exp : {args.auto_exposure}")
    print()

    meta = {
        "scan_id": scan_id,
        "created": now_stamp(),
        "motion": {
            "total_mm": motion_total_mm,
            "step_mm": motion_step_mm,
            "direction": args.direction,
        },
        "stage": stage_dict,
        "camera": cam_dict,
        "roi": roi,
    }


    stage = None
    steps_moved_total = 0

    try:
        # -----------------------------
        # 1) Init stage
        # -----------------------------
        print("1) init stage")
        if not args.dry_run:
            stage = StepDirStage(stage_cfg)
            stage.set_direction(args.direction)

            cam = RpiCamStill(cam_cfg)

        # -----------------------------
        # 2) Optional exposure calibration
        # -----------------------------
        print("2) auto exposure calibration")
        if args.auto_exposure and not args.dry_run:
            tmp_dir = proc_dir / "_ae_tmp"
            ensure_dir(tmp_dir)

            def capture_for_ae(path: Path, shutter_us: int):
                cam_cfg.shutter_us = shutter_us
                cam.capture(out_path=path)

            # TODO: This auto exposure calibration is currently a placeholder that you need to implement in vision_utils.py. It should return the  shutter_us value that best meets the target_peak criteria.
            best_shutter = auto_exposure_calibrate(
                capture_fn=capture_for_ae,
                tmp_dir=tmp_dir,
                roi=roi,
            )

            if best_shutter is not None:
                cam_cfg.shutter_us = int(best_shutter)
                meta["camera"]["shutter_us"] = int(best_shutter)
                print(f"   selected shutter_us = {best_shutter}")
            else:
                print("   auto exposure calibration failed, using default shutter_us")
        else: print("   (dry-run) skipping")

        # -----------------------------
        # 3) Capture scan
        # -----------------------------
        print("3) capture scan")
        if args.dry_run:
            print("   (dry-run) skipping capture and motion")
        for k in range(n_frames):
            fname = f"{scan_id}_{k:04d}.{cam_cfg.enc}"
            out_path = raw_dir / fname

            # Update bar on ONE line (no extra print)
            print_progress(k + 1, n_frames)

            if not args.dry_run:
                cam.capture(out_path=out_path)

            if k == n_frames - 1:
                break

            if not args.dry_run and stage is not None:
                stage.move_mm(motion_step_mm, args.mm_per_s, args.direction)
                time.sleep(args.settle_s)

        print("   capture complete.")

        # -----------------------------
        # 4) Return home (soft)
        # -----------------------------
        if not args.no_return_home and not args.dry_run and stage is not None:
            print("4) return home")
            stage.return_to_start(mm_per_s=args.mm_per_s)
        else:
            print("4) skipping return home")

        # -----------------------------
        # 5) Processing (centerline extraction)
        # -----------------------------
        print("5) centerline extraction")
        overlay_dir = proc_dir / "overlays"
        ensure_dir(overlay_dir)
        centerline_dir = proc_dir / "centerlines"
        ensure_dir(centerline_dir)

        raw_paths = sorted(raw_dir.glob(f"{scan_id}_*.{cam_cfg.enc}"))

        for i, img_path in enumerate(raw_paths):
            print_progress(i + 1, len(raw_paths))

            img_bgr = cv2.imread(str(img_path), cv2.IMREAD_COLOR)
            if img_bgr is None:
                print(f"\nWarning: could not read {img_path}")
                continue

            pts_yx, strength = steger_laser_centerline_from_bgr(
                img_bgr,
                sigma=2.0,
                ridge_thresh=6.0,
                t_max=1.0,
            )

            center_yx = centerline_from_ridge_points(
                pts_yx,
                strength,
                bin_step_px=1.0,
                smooth_win=31,
                max_gap_px=80.0,
            )

            if center_yx.size == 0:
                continue

            # Save centerline as compressed numpy
            out_npz = centerline_dir / (img_path.stem + "_centerline.npz")
            np.savez_compressed(out_npz, center_yx=center_yx)

            # Optional debug overlay (every frame or every N frames)
            out_overlay = overlay_dir / (img_path.stem + "_overlay.jpg")
            if args.debug_every > 0 and i % args.debug_every == 0:
                save_centerline_overlay(out_overlay, img_bgr, center_yx)

        print("\n   centerline extraction complete.")
        # -----------------------------
        # 6) Triangulation (TODO)
        # -----------------------------
        # TODO: pass center_yx to triangulation later

        # -----------------------------
        # 7) Mesh (TODO)
        # -----------------------------
        # TODO: pass triangulation output to mesh generation later.
        # 3D point cloud output from triangulation can be saved as .ply or .xyz file.

        # -----------------------------
        # 6) Save metadata
        # -----------------------------
        meta_path = proc_dir / args.meta_name
        save_json(meta_path, meta)
        print(f"\nSaved metadata: {meta_path}")
        print("Done.")

    except KeyboardInterrupt:
        print("\nStopped by user (Ctrl+C).")

    finally:
        if stage is not None and not args.dry_run:
            stage.close()


if __name__ == "__main__":
    main()
