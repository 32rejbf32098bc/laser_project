#!/usr/bin/env python3
from __future__ import annotations

import json
import time
from datetime import datetime
from pathlib import Path

import cv2
from picamera2 import Picamera2

from src.hardware.stage_gpiozero import (
    stage_init,
    stage_move_mm,
    stage_return_home,
    stage_cleanup,
)


OUTDIR = Path("captures/video_stage_tests")
OUTDIR.mkdir(parents=True, exist_ok=True)

# -------- Camera settings --------
W = 1280
H = 720
FPS = 30

SHUTTER_US = 8000
GAIN = 1.0
LENS_POS = 2.3276

AE_ENABLE = False
AWB_ENABLE = False

FOURCC = "MJPG"
# ---------------------------------

# -------- Stage settings --------
STEP_PIN = 23
DIR_PIN = 24
STEPS_PER_REV = 200
MICROSTEP = 8
LEAD_MM_PER_REV = 8.0
MIN_STEP_DELAY_US = 250

MOVE_MM = 50.0
MOVE_SPEED_MM_S = 5.0
DIRECTION = "forward"
RETURN_HOME = True
RETURN_HOME_SPEED_MM_S = 5.0
# --------------------------------


def main() -> int:
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    video_path = OUTDIR / f"video_stage_test_{ts}.avi"
    meta_path = OUTDIR / f"video_stage_test_{ts}.json"

    print("[INFO] Initialising stage...")
    stage = stage_init(
        step_pin=STEP_PIN,
        dir_pin=DIR_PIN,
        steps_per_rev=STEPS_PER_REV,
        microstep=MICROSTEP,
        lead_mm_per_rev=LEAD_MM_PER_REV,
        min_step_delay_us=MIN_STEP_DELAY_US,
    )

    picam2 = Picamera2()

    video_config = picam2.create_video_configuration(
        main={"size": (W, H), "format": "RGB888"},
        controls={"FrameRate": FPS},
    )
    picam2.configure(video_config)

    print("[INFO] Starting camera...")
    picam2.start()
    time.sleep(1.0)

    controls = {
        "AeEnable": 1 if AE_ENABLE else 0,
        "AwbEnable": 1 if AWB_ENABLE else 0,
        "AfMode": 0,  # manual focus
        "LensPosition": float(LENS_POS),
    }

    if not AE_ENABLE:
        controls["ExposureTime"] = int(SHUTTER_US)
        controls["AnalogueGain"] = float(GAIN)

    picam2.set_controls(controls)
    time.sleep(0.5)

    fourcc = cv2.VideoWriter_fourcc(*FOURCC)
    writer = cv2.VideoWriter(str(video_path), fourcc, FPS, (W, H))
    if not writer.isOpened():
        stage_cleanup(stage)
        raise RuntimeError(f"Failed to open video writer: {video_path}")

    stage_start_monotonic_ns = None
    stage_end_monotonic_ns = None
    frame_metadata: list[dict] = []

    print(f"[INFO] Video output: {video_path}")
    print(f"[INFO] Stage move: {MOVE_MM} mm at {MOVE_SPEED_MM_S} mm/s ({DIRECTION})")
    print("[INFO] Starting integrated capture...")

    start_monotonic_ns = time.monotonic_ns()
    frame_idx = 0

    try:
        # Start stage motion marker
        stage_start_monotonic_ns = time.monotonic_ns()
        print("[INFO] Starting stage motion...")

        # Run stage motion in the same thread for this first test.
        # We capture frames continuously around it by splitting capture into:
        # 1) pre-roll
        # 2) motion
        # For this first version, we do short pre-roll first.
        pre_roll_s = 0.5
        pre_start_ns = time.monotonic_ns()
        while (time.monotonic_ns() - pre_start_ns) / 1e9 < pre_roll_s:
            with picam2.captured_request() as request:
                frame_rgb = request.make_array("main")
                md = request.get_metadata() or {}

            frame_bgr = frame_rgb
            writer.write(frame_bgr)

            frame_metadata.append({
                "frame_index": frame_idx,
                "capture_elapsed_s": (time.monotonic_ns() - start_monotonic_ns) / 1e9,
                "sensor_timestamp_ns": md.get("SensorTimestamp"),
                "exposure_time_us": md.get("ExposureTime"),
                "analogue_gain": md.get("AnalogueGain"),
                "lens_position": md.get("LensPosition"),
                "frame_duration_us": md.get("FrameDuration"),
            })
            frame_idx += 1

        # Time stage move
        move_start_ns = time.monotonic_ns()
        steps_moved = stage_move_mm(
            stage,
            mm=MOVE_MM,
            mm_per_s=MOVE_SPEED_MM_S,
            direction=DIRECTION,
        )
        move_end_ns = time.monotonic_ns()

        stage_end_monotonic_ns = move_end_ns
        print(f"[INFO] Stage motion complete, steps moved: {steps_moved}")

        # Post-roll
        post_roll_s = 0.5
        post_start_ns = time.monotonic_ns()
        while (time.monotonic_ns() - post_start_ns) / 1e9 < post_roll_s:
            with picam2.captured_request() as request:
                frame_rgb = request.make_array("main")
                md = request.get_metadata() or {}

            frame_bgr = frame_rgb
            writer.write(frame_bgr)

            frame_metadata.append({
                "frame_index": frame_idx,
                "capture_elapsed_s": (time.monotonic_ns() - start_monotonic_ns) / 1e9,
                "sensor_timestamp_ns": md.get("SensorTimestamp"),
                "exposure_time_us": md.get("ExposureTime"),
                "analogue_gain": md.get("AnalogueGain"),
                "lens_position": md.get("LensPosition"),
                "frame_duration_us": md.get("FrameDuration"),
            })
            frame_idx += 1

    except KeyboardInterrupt:
        print("\n[INFO] Interrupted by user")

    finally:
        print("[INFO] Releasing video writer...")
        writer.release()

        if RETURN_HOME:
            print("[INFO] Returning stage to start...")
            stage_return_home(stage, mm_per_s=RETURN_HOME_SPEED_MM_S)

        print("[INFO] Cleaning up stage...")
        stage_cleanup(stage)

        print("[INFO] Stopping camera...")
        picam2.stop()

    save = {
        "created": ts,
        "video_file": str(video_path),
        "frames_written": frame_idx,
        "resolution": {"w": W, "h": H},
        "fps_target": FPS,
        "capture_settings": {
            "shutter_us": SHUTTER_US,
            "gain": GAIN,
            "lens_pos": LENS_POS,
            "ae_enable": AE_ENABLE,
            "awb_enable": AWB_ENABLE,
            "codec": FOURCC,
        },
        "stage_settings": {
            "step_pin": STEP_PIN,
            "dir_pin": DIR_PIN,
            "steps_per_rev": STEPS_PER_REV,
            "microstep": MICROSTEP,
            "lead_mm_per_rev": LEAD_MM_PER_REV,
            "min_step_delay_us": MIN_STEP_DELAY_US,
            "move_mm": MOVE_MM,
            "move_speed_mm_s": MOVE_SPEED_MM_S,
            "direction": DIRECTION,
            "return_home": RETURN_HOME,
            "return_home_speed_mm_s": RETURN_HOME_SPEED_MM_S,
        },
        "timing": {
            "capture_start_monotonic_ns": start_monotonic_ns,
            "stage_start_monotonic_ns": stage_start_monotonic_ns,
            "stage_end_monotonic_ns": stage_end_monotonic_ns,
        },
        "frame_metadata": frame_metadata,
    }

    print(f"[INFO] Writing metadata to: {meta_path}")
    with open(meta_path, "w", encoding="utf-8") as f:
        json.dump(save, f, indent=2)

    print("[INFO] Done")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())