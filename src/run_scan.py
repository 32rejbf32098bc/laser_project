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

from utils.calibration_utils import load_system_calibration
from utils.goem_utils import triangulate_centerline_to_points, points3d_quality_ok
from utils.io_utils import ensure_dir, now_stamp, print_progress, load_config, save_json
from hardware.stage_gpiozero import StepDirStage, StageConfig
from hardware.camera_rpicam import RpiCamConfig, RpiCamStill
from utils.vision_utils import auto_exposure_calibrate, save_centerline_overlay, save_rejected_frame
from utils.processing.ridge import steger_laser_centerline_from_bgr, ridge_quality_ok
from utils.processing.centerline import centerline_from_ridge_points, centerline_quality_ok

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

    # Process Only (for testing processing pipeline without capture/motion)
    ap.add_argument("--process-only", action="store_true",
                help="Skip stage init/motion/capture and only process existing raw images")
    ap.add_argument("--process-scan-id", default="",
                help="Existing scan_id to reprocess (folder name under raw-root/proc-root)")
    ap.add_argument("--output-scan-id", default="",
                help="Optional output scan_id for processed results (e.g. scan_XXX_reprocessed)")
    args = ap.parse_args()

    # -----------------------------
    # Load config (ROI lives here)
    # -----------------------------
    cfg = load_config(args.cfg)

    stage_dict = dict(cfg.get("stage", {}))
    cam_dict = dict(cfg.get("camera", {}))
    roi = cfg.get("processing", {}).get("roi", None)

    # -----------------------------
    # Load calibration
    # -----------------------------
    calib_path = cfg.get("camera_calibration_yaml", None)
    if calib_path is not None:
        try:
            K, dist, plane_n, plane_d = load_system_calibration(calib_path)
            print(f"Loaded calibration from {calib_path}")
        except Exception as e:
            print(f"Error loading calibration: {e}")
            return
    else:
        print("No calibration YAML specified in config, cannot proceed.")
        return
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

    # Directories and metadata setup
    input_scan_id = args.scan_id.strip()

    if not input_scan_id:
        print("ERROR: --scan-id required for reprocessing")
        return

    output_scan_id = args.output_scan_id.strip() or input_scan_id

    raw_dir = Path(args.raw_root) / input_scan_id
    proc_dir = Path(args.proc_root) / output_scan_id

    ensure_dir(raw_dir)
    ensure_dir(proc_dir)

    reject_dir = proc_dir / "rejected"
    ensure_dir(reject_dir)

    reject_img_dir = reject_dir / "images"
    reject_overlay_dir = reject_dir / "overlays"
    ensure_dir(reject_img_dir)
    ensure_dir(reject_overlay_dir)

    reject_log = reject_dir / "reject_log.csv"


    print("\n=== RUN SCAN ===")
    print(f"input_scan_id  : {input_scan_id}")
    print(f"output_scan_id : {output_scan_id}")
    print(f"frames   : {n_frames}")
    print(f"direction: {args.direction}")
    print(f"auto_exp : {args.auto_exposure}")
    print()

    meta = {
        "input_scan_id": input_scan_id,
        "output_scan_id": output_scan_id,
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
        if args.process_only:
            print("PROCESS-ONLY: skipping init, AE, capture, return-home")
        else:


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
                fname = f"{input_scan_id}_{k:04d}.{cam_cfg.enc}"
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
        # 5) Processing (ridge detection, centerline extraction + triangulation)
        # -----------------------------
        print("5) processing (ridge detection, centerline extraction + triangulation)")
        overlay_dir = proc_dir / "overlays"
        ensure_dir(overlay_dir)

        raw_paths = sorted(raw_dir.glob(f"{input_scan_id}_*.{cam_cfg.enc}"))
        if not raw_paths:
            print("   No raw images found to process.")
            return

        # Accumulators for one combined output
        xyz_chunks = []
        frame_idx_chunks = []
        z_mm_chunks = []

        # Stage position per frame (simple model: k * step_mm, sign by direction)
        dir_sign = 1.0 if args.direction == "forward" else -1.0

        for i, img_path in enumerate(raw_paths):
            print_progress(i + 1, len(raw_paths))

            img_bgr = cv2.imread(str(img_path), cv2.IMREAD_COLOR)
            if img_bgr is None:
                print(f"\nWarning: could not read {img_path}")
                continue
            red = img_bgr[:, :, 2]

            pts_yx, strength = steger_laser_centerline_from_bgr(
                img_bgr,
                sigma=2.0,
                ridge_thresh=3.0,
                t_max=1.0,
                roi=roi,
            )
            ok_ridge, why = ridge_quality_ok(red, pts_yx, strength)
            if not ok_ridge:
                print(f"ERROR: frame {i} ridge quality not ok: {why}")
                save_rejected_frame(img_path, img_bgr, why, reject_img_dir, reject_overlay_dir, pts_yx)
                with open(reject_log, "a") as f:
                    f.write(f"{img_path.name},{why}\n")
                continue
            """
           center_yx = centerline_from_ridge_points(
                pts_yx,
                strength,
                bin_step_px=0.5, # changed from 1.0 to allow for finer binning and better preservation of details in the centerline
                smooth_win=5, # changed from 31 to allow for less smoothing and better preservation of details in the centerline
                max_gap_px=0.0, # changed from 80.0 to allow for larger gaps in the centerline
                min_bins=3000, # increased from 50 to allow for shorter centerlines to be accepted, which can be useful in cases where the centerline is partially occluded or has missing segments
            )
            ok_cl, why = centerline_quality_ok(center_yx)
            if not ok_cl:
                print(f"ERROR: frame {i} centerline quality not ok: {why}")
                save_rejected_frame(img_path, img_bgr, why, reject_img_dir, reject_overlay_dir, center_yx)
                with open(reject_log, "a") as f:
                    f.write(f"{img_path.name},{why}\n")
                continue
            if center_yx.size == 0:
                continue
                """
            # For crack scanning, use ridge points directly (no centerline reconstruction)
            center_yx = pts_yx

            if center_yx.size == 0:
                print(f"ERROR: frame {i} no ridge points")
                continue

            points_3d_cam = triangulate_centerline_to_points(
                center_yx,
                K,
                dist,
                plane_n,
                plane_d,
            )
            ok_xyz, why = points3d_quality_ok(points_3d_cam)
            if not ok_xyz:
                print(f"ERROR: frame {i} 3D points quality not ok: {why}")
                save_rejected_frame(img_path, img_bgr, why, reject_img_dir, reject_overlay_dir, center_yx)
                with open(reject_log, "a") as f:
                    f.write(f"{img_path.name},{why}\n")
                continue
            if points_3d_cam.size == 0:
                continue

            # Put points into a simple "scan/world" frame using stage motion along Z
            z_mm = dir_sign * (i * motion_step_mm)
            points_3d = points_3d_cam.copy()
            points_3d[:, 2] += z_mm

            # Accumulate
            xyz_chunks.append(points_3d.astype(np.float32))
            frame_idx_chunks.append(np.full((points_3d.shape[0],), i, dtype=np.int32))
            z_mm_chunks.append(np.full((points_3d.shape[0],), z_mm, dtype=np.float32))

            # Optional debug overlay
            if args.debug_every > 0 and i % args.debug_every == 0:
                out_overlay = overlay_dir / (img_path.stem + "_overlay.jpg")
                save_centerline_overlay(out_overlay, img_bgr, center_yx, roi=roi)

        # Write combined point cloud once
        if not xyz_chunks:
            print("\n   No valid 3D points produced.")
        else:
            xyz_all = np.concatenate(xyz_chunks, axis=0)
            frame_idx_all = np.concatenate(frame_idx_chunks, axis=0)
            z_mm_all = np.concatenate(z_mm_chunks, axis=0)

            out_pc = proc_dir / "pointcloud.npz"
            np.savez_compressed(
                out_pc,
                xyz=xyz_all,
                frame_idx=frame_idx_all,
                z_mm=z_mm_all,
            )
            print(f"\n   Wrote: {out_pc}  (N={xyz_all.shape[0]})")

        print("\n   processing complete.")


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
