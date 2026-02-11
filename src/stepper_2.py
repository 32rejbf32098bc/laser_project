from gpiozero import DigitalOutputDevice
from time import sleep

# =========================
# Wiring (BCM numbering)
# =========================
STEP_PIN = 23
DIR_PIN  = 24

# =========================
# Mechanics / motor
# =========================
STEPS_PER_REV = 200
MICROSTEP = 8
LEAD_MM_PER_REV = 8.0  # TR8x8

# =========================
# Timing
# =========================
MIN_STEP_DELAY_S = 0.00025  # 250 µs (=> max ~2000 steps/s). Start safe.

step = DigitalOutputDevice(STEP_PIN)
dirp = DigitalOutputDevice(DIR_PIN)

STEPS_PER_MM = (STEPS_PER_REV * MICROSTEP) / LEAD_MM_PER_REV  # 800 steps/mm

def mm_to_steps(mm: float) -> int:
    return int(round(abs(mm) * STEPS_PER_MM))

def set_dir_for_mm(mm: float) -> None:
    # Change these if direction is reversed
    if mm >= 0:
        dirp.off()
    else:
        dirp.on()

def step_pulses(n_steps: int, step_delay_s: float) -> None:
    for _ in range(n_steps):
        step.on()
        sleep(step_delay_s)
        step.off()
        sleep(step_delay_s)

def move_mm(mm: float, mm_per_s: float = 2.0) -> None:
    """
    Move by mm at approx mm_per_s (best-effort using sleep()).
    """
    if mm == 0:
        return

    set_dir_for_mm(mm)
    n = mm_to_steps(mm)

    steps_per_s = abs(mm_per_s) * STEPS_PER_MM
    step_delay_s = 1.0 / (2.0 * steps_per_s)

    # Clamp for Pi timing stability
    if step_delay_s < MIN_STEP_DELAY_S:
        step_delay_s = MIN_STEP_DELAY_S

    achieved_steps_per_s = 1.0 / (2.0 * step_delay_s)
    achieved_mm_per_s = achieved_steps_per_s / STEPS_PER_MM

    print(f"Move {mm:+.3f} mm -> {n} steps | target {mm_per_s:.2f} mm/s | "
          f"achieved ~{achieved_mm_per_s:.2f} mm/s")

    step_pulses(n, step_delay_s)

if __name__ == "__main__":
    try:
        # Start slow and prove motion
        move_mm(+10.0, mm_per_s=10.0)
        sleep(0.5)
        move_mm(-10.0, mm_per_s=10.0)

        # Example scan stepping:
        # for i in range(50):
        #     move_mm(+0.5, mm_per_s=2.0)  # 0.5 mm step
        #     sleep(0.05)

    except KeyboardInterrupt:
        print("Stopping...")

    finally:
        step.off()
        dirp.off()
        step.close()
        dirp.close()
        print("Done.")
