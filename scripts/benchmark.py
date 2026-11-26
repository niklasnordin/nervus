"""Time the simulation of some example models: run before and after a change to the engine.

uv run python scripts/benchmark.py            # each model three times, the best time
uv run python scripts/benchmark.py --profile charging_system    # where the time goes (cProfile)
uv run python scripts/benchmark.py --accelerated    # normal and accelerated (compiled C) side by side
"""

from __future__ import annotations

import cProfile
import pstats
import sys
import time
from dataclasses import replace
from pathlib import Path

from nervus import load, simulate

EXAMPLES = Path(__file__).parent.parent / "examples"
MODELS = ["cruise_control", "abs_braking", "dc_motor_speed", "ntc_sensor", "charging_system", "engine_modes",
          "buck_converter", "gear_shift"]  # fmt: skip
ACCELERATED = ["cruise_control", "abs_braking", "gear_shift", "truck", "ev_range", "fuel_film", "drive_cycle"]


def best_of(name: str, runs: int = 3, mode: str = "normal") -> tuple[float, int]:
    times, points = [], 0
    for _ in range(runs):
        d = load(EXAMPLES / f"{name}.nervus")
        d.settings = replace(d.settings, mode=mode)
        t0 = time.perf_counter()
        res = simulate(d)
        times.append(time.perf_counter() - t0)
        points = len(res.t)
    return min(times), points


def main() -> None:
    if sys.argv[1:2] == ["--profile"]:
        d = load(EXAMPLES / f"{sys.argv[2]}.nervus")
        prof = cProfile.Profile()
        prof.runcall(simulate, d)
        pstats.Stats(prof).sort_stats("tottime").print_stats(20)
        return
    if sys.argv[1:2] == ["--accelerated"]:  # the first accelerated run compiles; the best of three is cached
        print(f"{'model':18s} {'normal [s]':>10s} {'accelerated [s]':>16s} {'faster':>7s}")
        for name in ACCELERATED:
            t, _n = best_of(name)
            ta, _n = best_of(name, mode="accelerated")
            print(f"{name:18s} {t:10.2f} {ta:16.3f} {t / ta:6.0f}x")
        return
    print(f"{'model':18s} {'time [s]':>9s} {'points':>8s}")
    for name in MODELS:
        if (EXAMPLES / f"{name}.nervus").exists():
            t, n = best_of(name)
            print(f"{name:18s} {t:9.2f} {n:8d}")


if __name__ == "__main__":
    main()
