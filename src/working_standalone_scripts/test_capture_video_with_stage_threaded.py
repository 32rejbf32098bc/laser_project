#!/usr/bin/env python3
from __future__ import annotations

import json
import threading
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
W = 2304
H = 1296
FPS = 50

SHUTTER_US = 2000
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

MOVE_MM = 100.0
MOVE_SPEED_MM_S = 1.0
DIRECTION = "forward"

PRE_ROLL_S = 0.5
POST_ROLL_S = 0.5

RETURN_HOME = True
RETURN_HOME_SPEED_MM_S = 5.0
# --------------------------------


def stage_worker(
    stage,
    move_mm: float,
    move_speed_mm_s: float,
    direction: str,
    timing: dict,
    result: dict,
):
    try:
        timing["stage_start_monotonic_ns"] = time.monotonic_ns()
        steps_moved = stage_move_mm(
            stage,
            mm=move_mm,
            mm_per_s=move_speed_mm_s,
            direction=direction,
        )
        timing["stage_end_monotonic_ns"] = time.monotonic_ns()
        result["steps_moved"] = int(steps_moved)
        result["ok"] = True
    except Exception as e:
        timing["stage_end_monotonic_ns"] = time.monotonic_ns()
        result["ok"] = False
        result["error"] = str(e)


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
        "AfMode": 0,
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

    print(f"[INFO] Video output: {video_path}")
    print(f"[INFO] Stage move: {MOVE_MM} mm at {MOVE_SPEED_MM_S} mm/s ({DIRECTION})")
    print(f"[INFO] Pre-roll: {PRE_ROLL_S} s, Post-roll: {POST_ROLL_S} s")

    frame_metadata: list[dict] = []
    capture_start_monotonic_ns = time.monotonic_ns()
    frame_idx = 0

    timing = {
        "capture_start_monotonic_ns": capture_start_monotonic_ns,
        "stage_start_monotonic_ns": None,
        "stage_end_monotonic_ns": None,
    }
    stage_result = {
        "ok": None,
        "steps_moved": None,
        "error": None,
    }

    stage_thread = None

    try:
        print("[INFO] Capturing pre-roll...")
        pre_roll_start_ns = time.monotonic_ns()
        while (time.monotonic_ns() - pre_roll_start_ns) / 1e9 < PRE_ROLL_S:
            with picam2.captured_request() as request:
                frame_rgb = request.make_array("main")
                md = request.get_metadata() or {}

            frame_bgr = frame_rgb
            writer.write(frame_bgr)

            frame_metadata.append({
                "frame_index": frame_idx,
                "capture_elapsed_s": (time.monotonic_ns() - capture_start_monotonic_ns) / 1e9,
                "sensor_timestamp_ns": md.get("SensorTimestamp"),
                "exposure_time_us": md.get("ExposureTime"),
                "analogue_gain": md.get("AnalogueGain"),
                "lens_position": md.get("LensPosition"),
                "frame_duration_us": md.get("FrameDuration"),
                "colour_gains": md.get("ColourGains"),
                "lux": md.get("Lux"),
            })
            frame_idx += 1

        print("[INFO] Starting stage thread...")
        stage_thread = threading.Thread(
            target=stage_worker,
            args=(stage, MOVE_MM, MOVE_SPEED_MM_S, DIRECTION, timing, stage_result),
            daemon=True,
        )
        stage_thread.start()

        print("[INFO] Capturing during motion...")
        while stage_thread.is_alive():
            with picam2.captured_request() as request:
                frame_rgb = request.make_array("main")
                md = request.get_metadata() or {}

            frame_bgr = frame_rgb
            writer.write(frame_bgr)

            frame_metadata.append({
                "frame_index": frame_idx,
                "capture_elapsed_s": (time.monotonic_ns() - capture_start_monotonic_ns) / 1e9,
                "sensor_timestamp_ns": md.get("SensorTimestamp"),
                "exposure_time_us": md.get("ExposureTime"),
                "analogue_gain": md.get("AnalogueGain"),
                "lens_position": md.get("LensPosition"),
                "frame_duration_us": md.get("FrameDuration"),
                "colour_gains": md.get("ColourGains"),
                "lux": md.get("Lux"),
            })

            if frame_idx % 10 == 0:
                print(f"[INFO] frame={frame_idx:04d}")

            frame_idx += 1

        stage_thread.join()

        print("[INFO] Capturing post-roll...")
        post_roll_start_ns = time.monotonic_ns()
        while (time.monotonic_ns() - post_roll_start_ns) / 1e9 < POST_ROLL_S:
            with picam2.captured_request() as request:
                frame_rgb = request.make_array("main")
                md = request.get_metadata() or {}

            frame_bgr = frame_rgb
            writer.write(frame_bgr)

            frame_metadata.append({
                "frame_index": frame_idx,
                "capture_elapsed_s": (time.monotonic_ns() - capture_start_monotonic_ns) / 1e9,
                "sensor_timestamp_ns": md.get("SensorTimestamp"),
                "exposure_time_us": md.get("ExposureTime"),
                "analogue_gain": md.get("AnalogueGain"),
                "lens_position": md.get("LensPosition"),
                "frame_duration_us": md.get("FrameDuration"),
                "colour_gains": md.get("ColourGains"),
                "lux": md.get("Lux"),
            })
            frame_idx += 1

    except KeyboardInterrupt:
        print("\n[INFO] Interrupted by user")
        if stage_thread is not None:
            stage_thread.join()

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
            "pre_roll_s": PRE_ROLL_S,
            "post_roll_s": POST_ROLL_S,
            "return_home": RETURN_HOME,
            "return_home_speed_mm_s": RETURN_HOME_SPEED_MM_S,
        },
        "timing": timing,
        "stage_result": stage_result,
        "frame_metadata": frame_metadata,
    }

    print(f"[INFO] Writing metadata to: {meta_path}")
    with open(meta_path, "w", encoding="utf-8") as f:
        json.dump(save, f, indent=2)

    print("[INFO] Done")
    print(f"[INFO] Frames written: {frame_idx}")
    print(f"[INFO] Stage ok: {stage_result['ok']}, steps moved: {stage_result['steps_moved']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())