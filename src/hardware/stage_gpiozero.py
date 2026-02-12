#!/usr/bin/env python3
# src/hardware/stage_gpiozero.py

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Optional

from gpiozero import DigitalOutputDevice


@dataclass
class StageConfig:
    step_pin: int = 23
    dir_pin: int = 24
    steps_per_rev: int = 200
    microstep: int = 8
    lead_mm_per_rev: float = 8.0
    min_step_delay_us: int = 250  # conservative on Pi


class StepDirStage:
    """
    STEP/DIR stage driver using gpiozero.
    Tracks steps moved so we can return to start (soft home).
    """

    def __init__(self, cfg: StageConfig):
        self.cfg = cfg
        self.step = DigitalOutputDevice(cfg.step_pin)
        self.dirp = DigitalOutputDevice(cfg.dir_pin)
        self._moves_done_steps = 0

    def close(self) -> None:
        self.step.off()
        self.dirp.off()
        self.step.close()
        self.dirp.close()

    # ---- mechanics ----
    def mm_to_steps(self, mm: float) -> int:
        steps_per_mm = (self.cfg.steps_per_rev * self.cfg.microstep) / self.cfg.lead_mm_per_rev
        return int(round(mm * steps_per_mm))

    def steps_to_mm(self, steps: int) -> float:
        mm_per_step = self.cfg.lead_mm_per_rev / (self.cfg.steps_per_rev * self.cfg.microstep)
        return steps * mm_per_step

    def compute_step_delay(self, mm_per_s: float) -> float:
        steps_per_mm = (self.cfg.steps_per_rev * self.cfg.microstep) / self.cfg.lead_mm_per_rev
        steps_per_s = max(1.0, abs(mm_per_s) * steps_per_mm)
        delay = 1.0 / (2.0 * steps_per_s)
        min_delay = self.cfg.min_step_delay_us / 1e6
        return max(delay, min_delay)

    # ---- IO ----
    def set_direction(self, direction: str) -> None:
        if direction not in ("forward", "backward"):
            raise ValueError("direction must be 'forward' or 'backward'")

        # Matches your working script:
        # forward -> dirp.off()
        if direction == "forward":
            self.dirp.off()
        else:
            self.dirp.on()

    def _step_pulses(self, n_steps: int, delay_s: float) -> None:
        for _ in range(n_steps):
            self.step.on()
            time.sleep(delay_s)
            self.step.off()
            time.sleep(delay_s)

    def move_mm(self, step_mm: float, mm_per_s: float, direction: str) -> int:
        """
        Move by step_mm in the given direction.
        Returns signed steps moved (positive = forward, negative = backward).
        """
        self.set_direction(direction)
        delay_s = self.compute_step_delay(mm_per_s)

        n = abs(self.mm_to_steps(step_mm))
        if n == 0:
            return 0

        self._step_pulses(n, delay_s)

        signed = n if direction == "forward" else -n
        self._moves_done_steps += signed
        return signed

    def return_to_start(self, mm_per_s: float) -> None:
        """
        Return by undoing accumulated steps (soft home).
        """
        if self._moves_done_steps == 0:
            return

        delay_s = self.compute_step_delay(mm_per_s)
        back_steps = -self._moves_done_steps  # undo

        direction = "forward" if back_steps > 0 else "backward"
        self.set_direction(direction)
        self._step_pulses(abs(back_steps), delay_s)

        self._moves_done_steps = 0


# ============================================================
# Public module API (what run_scan.py should call)
# ============================================================

def stage_init(
    step_pin: int = 23,
    dir_pin: int = 24,
    steps_per_rev: int = 200,
    microstep: int = 8,
    lead_mm_per_rev: float = 8.0,
    min_step_delay_us: int = 250,
) -> StepDirStage:
    cfg = StageConfig(
        step_pin=step_pin,
        dir_pin=dir_pin,
        steps_per_rev=steps_per_rev,
        microstep=microstep,
        lead_mm_per_rev=lead_mm_per_rev,
        min_step_delay_us=min_step_delay_us,
    )
    return StepDirStage(cfg)


def stage_set_direction(stage: StepDirStage, direction: str) -> None:
    stage.set_direction(direction)


def stage_move_mm(stage: StepDirStage, mm: float, mm_per_s: float, direction: Optional[str] = None) -> int:
    """
    Move stage by mm in direction.
    If direction is None, assumes caller has already set direction.
    Returns signed steps moved.
    """
    if direction is None:
        # keep current direction pin state (caller-controlled)
        # but our class requires a direction; assume forward if None
        direction = "forward"
    return stage.move_mm(mm, mm_per_s, direction)


def stage_return_home(stage: StepDirStage, mm_per_s: float) -> None:
    stage.return_to_start(mm_per_s)


def stage_cleanup(stage: StepDirStage) -> None:
    stage.close()
