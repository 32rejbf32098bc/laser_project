#!/usr/bin/env python3
"""
scan_step_capture.py

Move a lead-screw stage in steps, capture an image at each position,
then return to the start.

Stepper: STEP/DIR (gpiozero, BCM numbering)
Camera : rpicam-still

Example:
python3 scan_step_capture.py \
  --outdir data/raw/scan_001 \
  --total-mm 120 \
  --step-mm 1.0 \
  --mm-per-s 20 \
  --microstep 8
"""

import argparse
import time
import math
import subprocess
import sys
import shutil
from pathlib import Path
from gpiozero import DigitalOutputDevice


# --------------------------------------------------
# Utilities
# --------------------------------------------------

def print_progress(k: int, n: int, prefix: str = "Scan"):
    """
    Draw a single-line progress bar in the terminal.
    k = current index (0-based), n = total frames
    """
    cols = shutil.get_terminal_size((80, 20)).columns
    bar_w = max(10, cols - 35)  # leave room for text

    done = k + 1
    frac = done / n
    filled = int(bar_w * frac)
    bar = "█" * filled + "░" * (bar_w - filled)

    sys.stdout.write(f"\r{prefix}: [{bar}] {done}/{n} ({frac*100:5.1f}%)")
    sys.stdout.flush()

    if done == n:
        sys.stdout.write("\n")

def now_stamp():
    return time.strftime("%Y%m%d_%H%M%S")


def mm_to_steps(mm, steps_per_rev, microstep, lead_mm_per_rev):
    steps_per_mm = (steps_per_rev * microstep) / lead_mm_per_rev
    return int(round(mm * steps_per_mm))


def steps_to_mm(steps, steps_per_rev, microstep, lead_mm_per_rev):
    mm_per_step = lead_mm_per_rev / (steps_per_rev * microstep)
    return steps * mm_per_step


def step_pulses(step_dev, n_steps, delay_s):
    for _ in range(n_steps):
        step_dev.on()
        time.sleep(delay_s)
        step_dev.off()
        time.sleep(delay_s)


def build_rpicam_cmd(out_path, args):

    cmd = [
        "rpicam-still",
        "--nopreview",
        "-o", out_path,
        "--width", str(args.width),
        "--height", str(args.height),
        "--timeout", str(args.timeout_ms),
        "--shutter", str(args.shutter_us),
        "--gain", str(args.gain),
    ]

    if args.enc == "png":
        cmd += ["--encoding", "png"]
    else:
        cmd += ["--encoding", "jpg", "--quality", str(args.quality)]

    if args.awb_gains:
        cmd += ["--awb", "manual", "--awbgains", args.awb_gains]

    if args.lens_pos is not None:
        cmd += ["--autofocus-mode", "manual",
                "--lens-position", str(args.lens_pos)]

    return cmd


# --------------------------------------------------
# Main
# --------------------------------------------------

def main():

    ap = argparse.ArgumentParser()

    # Output
    ap.add_argument("--outdir", default="data/raw/scan")

    # Scan geometry
    ap.add_argument("--total-mm", type=float, required=True)
    ap.add_argument("--step-mm", type=float, default=1.0)
    ap.add_argument("--mm-per-s", type=float, default=20.0)
    ap.add_argument("--settle-s", type=float, default=0.2)
    ap.add_argument("--direction", choices=["forward", "backward"],
                    default="forward")

    # GPIO
    ap.add_argument("--step-pin", type=int, default=23)
    ap.add_argument("--dir-pin", type=int, default=24)

    # Mechanics
    ap.add_argument("--steps-per-rev", type=int, default=200)
    ap.add_argument("--microstep", type=int, default=8)
    ap.add_argument("--lead-mm-per-rev", type=float, default=8.0)

    # Timing
    ap.add_argument("--min-step-delay-us", type=int, default=250)

    # Camera
    ap.add_argument("--width", type=int, default=4608)
    ap.add_argument("--height", type=int, default=2592)
    ap.add_argument("--enc", choices=["jpg", "png"], default="jpg")
    ap.add_argument("--quality", type=int, default=95)
    ap.add_argument("--timeout-ms", type=int, default=300)
    ap.add_argument("--shutter-us", type=int, default=8000)
    ap.add_argument("--gain", type=float, default=1.0)
    ap.add_argument("--awb-gains", default="")
    ap.add_argument("--lens-pos", type=float, default=None)

    ap.add_argument("--dry-run", action="store_true")

    args = ap.parse_args()

    # --------------------------------------------------

    outdir = Path(args.outdir)
    outdir.mkdir(parents=True, exist_ok=True)

    # Frames
    n_frames = int(math.floor(args.total_mm / args.step_mm)) + 1
    total_planned = (n_frames - 1) * args.step_mm

    print("\n=== Scan Plan ===")
    print(f"Output dir : {outdir}")
    print(f"Total req  : {args.total_mm:.3f} mm")
    print(f"Step       : {args.step_mm:.3f} mm")
    print(f"Frames     : {n_frames}")
    print(f"Total act  : {total_planned:.3f} mm")
    print(f"Microstep  : 1/{args.microstep}")
    print()

    # --------------------------------------------------
    # GPIO
    # --------------------------------------------------

    step = DigitalOutputDevice(args.step_pin)
    dirp = DigitalOutputDevice(args.dir_pin)

    forward = (args.direction == "forward")

    if forward:
        dirp.off()
    else:
        dirp.on()

    # --------------------------------------------------
    # Motion maths
    # --------------------------------------------------

    steps_per_mm = (args.steps_per_rev * args.microstep) / args.lead_mm_per_rev

    steps_per_s = max(1.0, abs(args.mm_per_s) * steps_per_mm)

    step_delay = 1.0 / (2.0 * steps_per_s)

    min_delay = args.min_step_delay_us / 1e6
    if step_delay < min_delay:
        step_delay = min_delay

    move_steps = mm_to_steps(
        args.step_mm,
        args.steps_per_rev,
        args.microstep,
        args.lead_mm_per_rev
    )

    move_mm_actual = steps_to_mm(
        move_steps,
        args.steps_per_rev,
        args.microstep,
        args.lead_mm_per_rev
    )

    total_move_steps = move_steps * (n_frames - 1)

    print(f"Per frame : {move_steps} steps ≈ {move_mm_actual:.4f} mm")
    print(f"Delay     : {step_delay*1e6:.0f} µs")
    print()

    moves_done = 0

    # --------------------------------------------------
    # Scan loop
    # --------------------------------------------------

    try:

        for k in range(n_frames):
            print_progress(k, n_frames, prefix="Scan")
			
            name = f"scan_{now_stamp()}_{k:04d}.{args.enc}"
            out_path = str(outdir / name)

            cmd = build_rpicam_cmd(out_path, args)

            #print(f"[{k+1}/{n_frames}] Capture -> {name}")

            if args.dry_run:
                print("  DRY-RUN:", " ".join(cmd))
            else:
                res = subprocess.run(
                    cmd,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    text=True,
                )
                if res.returncode != 0:
                    print("rpicam-still failed:", res.returncode)
                    print(res.stderr.strip())
                    raise SystemExit(1)


            if k == n_frames - 1:
                break

            #print(f"           Move {args.step_mm:.3f} mm")

            if not args.dry_run:
                step_pulses(step, abs(move_steps), step_delay)
                time.sleep(args.settle_s)

            moves_done += 1

        print("\nScan complete.")

    except KeyboardInterrupt:
        print("\nInterrupted by user.")

    finally:

        # --------------------------------------------------
        # Return to start
        # --------------------------------------------------

        if not args.dry_run and moves_done > 0:

            back_steps = move_steps * moves_done

            print(f"Returning: {abs(back_steps)} steps")

            if forward:
                dirp.on()
            else:
                dirp.off()

            step_pulses(step, abs(back_steps), step_delay)

        # --------------------------------------------------
        # Cleanup
        # --------------------------------------------------

        step.off()
        dirp.off()

        step.close()
        dirp.close()

        print("GPIO released. Done.")


# --------------------------------------------------

if __name__ == "__main__":
    main()
