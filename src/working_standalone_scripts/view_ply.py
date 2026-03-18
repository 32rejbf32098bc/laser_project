#!/usr/bin/env python3
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import matplotlib.pyplot as plt


def load_xyz(path: Path) -> np.ndarray:
    suffix = path.suffix.lower()

    if suffix == ".npz":
        data = np.load(path)
        if "xyz" not in data:
            raise KeyError(f"{path} does not contain an 'xyz' array")
        xyz = np.asarray(data["xyz"], dtype=float)
        if xyz.ndim != 2 or xyz.shape[1] != 3:
            raise ValueError(f"'xyz' must have shape (N, 3), got {xyz.shape}")
        return xyz

    raise ValueError("Supported format for this simple viewer: .npz")


def main() -> int:
    if len(sys.argv) < 2:
        print("Usage: python view_cloud.py <cloud.npz> [stride]")
        return 1

    path = Path(sys.argv[1])
    stride = int(sys.argv[2]) if len(sys.argv) > 2 else 1

    xyz = load_xyz(path)

    if stride > 1:
        xyz = xyz[::stride]

    x = xyz[:, 0]
    y = xyz[:, 1]
    z = xyz[:, 2]

    print(f"Loaded {xyz.shape[0]:,} points from {path}")

    fig = plt.figure("Simple Point Cloud Viewer")
    ax = fig.add_subplot(111, projection="3d")
    ax.scatter(x, y, z, s=0.5)

    ax.set_xlabel("X")
    ax.set_ylabel("Y")
    ax.set_zlabel("Z")
    ax.set_title(path.name)

    # Equal-ish aspect ratio
    xmid = 0.5 * (x.min() + x.max())
    ymid = 0.5 * (y.min() + y.max())
    zmid = 0.5 * (z.min() + z.max())

    span = max(
        float(x.max() - x.min()),
        float(y.max() - y.min()),
        float(z.max() - z.min()),
    ) * 0.5

    ax.set_xlim(xmid - span, xmid + span)
    ax.set_ylim(ymid - span, ymid + span)
    ax.set_zlim(zmid - span, zmid + span)

    plt.show()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())