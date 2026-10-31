# =============================================================================
# HYDRA-UMC-VISION-NODE - src/hydra_umc_vision_node/latency.py
# Copyright (C) 2026 JuanenRac (Electro Hobby 3D) <electrohobby3d@gmail.com>
# GPL-3.0-or-later - see LICENSE
# =============================================================================
"""Latency of the perception pipeline, per camera and per stage, as percentiles.

The reflex loop is only as good as the time from the moment a frame is captured to the moment its
detections are published. A stage that records the instants a frame passed through
(`capture`, `preprocess`, `inference`, `postprocess`, `publish`, in seconds on one clock) hands
them to a LatencyRecorder; `GET /latency` then answers the median (p50), the 95th percentile (p95),
the worst case and the number of frames for each stage and for the whole path, over the last
`window` frames of each camera.

Memory is bounded: `window` frames per camera and at most MAX_CAMERAS cameras.
"""
from __future__ import annotations

import math
import re
import threading
from collections import deque

from .pipeline import DEFAULT_STAGES

STAGE_NAMES: tuple[str, ...] = tuple(s.name for s in DEFAULT_STAGES)
DEFAULT_WINDOW = 256
MAX_WINDOW = 10_000
MAX_CAMERAS = 64
_CAMERA = re.compile(r"[A-Za-z0-9_.:-]{1,64}")
TOTAL = "total"


class LatencyError(ValueError):
    """A recorded frame or the configuration cannot be used."""


def percentile(sorted_values: list[float], fraction: float) -> float:
    """Nearest-rank percentile of an already sorted, non-empty list (fraction 0 to 1)."""
    rank = max(1, math.ceil(fraction * len(sorted_values)))
    return sorted_values[rank - 1]


class LatencyRecorder:
    def __init__(self, window: int = DEFAULT_WINDOW) -> None:
        if isinstance(window, bool) or not isinstance(window, int) or not 1 <= window <= MAX_WINDOW:
            raise LatencyError(f"window must be an integer from 1 to {MAX_WINDOW}, got {window!r}")
        self._window = window
        self._lock = threading.Lock()
        self._frames: dict[str, dict[str, deque[float]]] = {}

    def record(self, camera: object, stamps: object) -> dict[str, float]:
        """Stores one frame. `stamps` maps stage names to instants in seconds; the first stage must be capture.

        Stages may be skipped (a node without an accelerator has no inference): a stage lasts from the previous
        recorded stage to its own instant, and the total from capture to the last recorded stage."""
        if not isinstance(camera, str) or not _CAMERA.fullmatch(camera):
            raise LatencyError("camera must be 1 to 64 characters of letters, digits and _ . : -")
        if not isinstance(stamps, dict) or not stamps:
            raise LatencyError("stamps must be an object of stage name to instant in seconds")
        unknown = sorted(set(stamps) - set(STAGE_NAMES))
        if unknown:
            raise LatencyError(f"unknown stages: {', '.join(map(str, unknown))}")
        if STAGE_NAMES[0] not in stamps or len(stamps) < 2:
            raise LatencyError(f"stamps need {STAGE_NAMES[0]} and at least one later stage")
        ordered: list[tuple[str, float]] = []
        for name in STAGE_NAMES:
            if name in stamps:
                value = stamps[name]
                if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
                    raise LatencyError(f"the instant of {name} must be a finite number")
                ordered.append((name, float(value)))
        durations: dict[str, float] = {}
        for (_, before), (name, at) in zip(ordered, ordered[1:]):
            if at < before:
                raise LatencyError(f"the instant of {name} is earlier than the stage before it")
            durations[name] = (at - before) * 1000.0
        durations[TOTAL] = (ordered[-1][1] - ordered[0][1]) * 1000.0
        with self._lock:
            if camera not in self._frames and len(self._frames) >= MAX_CAMERAS:
                raise LatencyError(f"at most {MAX_CAMERAS} cameras are tracked")
            per_stage = self._frames.setdefault(camera, {})
            for name, ms in durations.items():
                per_stage.setdefault(name, deque(maxlen=self._window)).append(ms)
        return durations

    def snapshot(self) -> dict:
        with self._lock:
            copy = {camera: {stage: sorted(values) for stage, values in stages.items()} for camera, stages in self._frames.items()}
        cameras: dict[str, dict] = {}
        for camera, stages in sorted(copy.items()):
            cameras[camera] = {
                stage: {
                    "count": len(values),
                    "p50Ms": percentile(values, 0.50),
                    "p95Ms": percentile(values, 0.95),
                    "maxMs": values[-1],
                }
                for stage, values in stages.items()
                if values
            }
        return {"window": self._window, "cameras": cameras}
