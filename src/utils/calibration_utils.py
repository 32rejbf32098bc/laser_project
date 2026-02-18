from pathlib import Path
from typing import Tuple
import numpy as np
import yaml


def load_system_calibration(path: Path) -> Tuple[np.ndarray, np.ndarray, np.ndarray, float]:
    """
    Load full triangulation calibration.

    Supported YAML formats:

    Format A (flat):
        K: [9 values] OR [[3],[3],[3]]
        dist: [...]
        laser_plane:
            n: [3]
            d: float

    Format B (OpenCV style):
        camera_matrix:
            data: [...]
        dist_coeff:
            data: [...]

    Format C (nested camera block):
        camera:
            K: ...
            dist: ...
        laser_plane:
            n: ...
            d: ...

    Returns:
        K        : (3,3) float64
        dist     : (N,)  float64
        plane_n  : (3,)  float64 (unit normal)
        plane_d  : float
    """

    path = Path(path)
    if not path.exists():
        raise FileNotFoundError(f"Calibration file not found: {path}")

    with open(path, "r", encoding="utf-8") as f:
        data = yaml.safe_load(f)

    # -------------------------------------------------
    # Allow nested camera block
    # -------------------------------------------------

    cam = data.get("camera", data)

    # -------------------------------------------------
    # Camera matrix
    # -------------------------------------------------

    if "camera_matrix" in cam:
        K = np.array(cam["camera_matrix"]["data"], dtype=np.float64).reshape(3, 3)

    elif "K" in cam:

        K = np.array(cam["K"], dtype=np.float64)

        if K.shape == (9,):
            K = K.reshape(3, 3)

        elif K.shape == (3, 3):
            pass

        else:
            raise ValueError("Invalid K shape")

    else:
        raise KeyError("Camera matrix not found in calibration YAML")

    # -------------------------------------------------
    # Distortion
    # -------------------------------------------------

    if "dist_coeff" in cam:

        dist = np.array(cam["dist_coeff"]["data"], dtype=np.float64).ravel()

    elif "dist" in cam:

        dist = np.array(cam["dist"], dtype=np.float64).ravel()

    else:
        raise KeyError("Distortion coefficients not found in calibration YAML")

    # -------------------------------------------------
    # Laser plane
    # -------------------------------------------------

    if "laser_plane" not in data:
        raise KeyError("laser_plane not found in calibration YAML")

    plane = data["laser_plane"]

    if "n" not in plane or "d" not in plane:
        raise KeyError("laser_plane must contain 'n' and 'd'")

    plane_n = np.array(plane["n"], dtype=np.float64).reshape(3)
    plane_d = float(plane["d"])

    # -------------------------------------------------
    # Normalise plane
    # -------------------------------------------------

    norm = np.linalg.norm(plane_n)

    if norm < 1e-12:
        raise ValueError("Laser plane normal has zero magnitude")

    plane_n = plane_n / norm
    plane_d = plane_d / norm

    # -------------------------------------------------

    return K, dist, plane_n, float(plane_d)
