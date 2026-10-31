# =============================================================================
# HYDRA-UMC-VISION-NODE - src/hydra_umc_vision_node/vision_sim.py
# Copyright (C) 2026 JuanenRac (Electro Hobby 3D) <electrohobby3d@gmail.com>
# GPL-3.0-or-later - see LICENSE
# =============================================================================
"""Simulated capture and inference: the configuration of the simulation and the loop that runs it.

There is NO real inference and NO real camera here. A frame comes from a simulated source (sources.py); the "model" is a
deterministic function of the frame bytes and the model name (a hash decides how many detections it reports and how long
each stage lasts, around the `simLatencyMs` the model declares). The stage instants are on a virtual clock, so a run
gives the same numbers every time and takes no real time; they go into the existing LatencyRecorder, so `GET /latency`
shows them like those of a real pipeline. What the numbers say is only that the plumbing works.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path

from .latency import LatencyRecorder
from .models import HostCapabilities, ModelError, ModelSpec, parse_host, parse_models
from .sources import FrameSource, SourceError, build_source

MAX_CONFIG_BYTES = 256 * 1024
MAX_SOURCES = 16
MAX_RUN_FRAMES = 1000
FRAME_PERIOD_MS = 33.0


class VisionConfigError(ValueError):
    """The configuration of the simulation cannot be used."""


@dataclass(frozen=True)
class VisionConfig:
    host: HostCapabilities
    models: tuple[ModelSpec, ...]
    sources: dict[str, FrameSource]

    def model(self, name: str) -> ModelSpec | None:
        return next((m for m in self.models if m.name == name), None)


def _reject_constant(name: str) -> float:
    raise ValueError(f"{name} is not a valid JSON number")


def parse_vision_config(config: object, base: Path) -> VisionConfig:
    if not isinstance(config, dict) or set(config) - {"host", "models", "sources"}:
        raise VisionConfigError("the configuration is an object with host, models and sources")
    try:
        host = parse_host(config.get("host", {}))
        models = parse_models(config.get("models", []))
        raw_sources = config.get("sources", [])
        if not isinstance(raw_sources, list) or len(raw_sources) > MAX_SOURCES:
            raise VisionConfigError(f"sources must be a list of at most {MAX_SOURCES} sources")
        sources: dict[str, FrameSource] = {}
        for item in raw_sources:
            source = build_source(item, base)
            if source.name in sources:
                raise VisionConfigError(f"source names must be unique: {source.name}")
            sources[source.name] = source
    except (ModelError, SourceError) as error:
        raise VisionConfigError(str(error)) from None
    return VisionConfig(host, models, sources)


def load_vision_config(path: str | Path) -> VisionConfig:
    file = Path(path)
    try:
        if file.stat().st_size > MAX_CONFIG_BYTES:
            raise VisionConfigError(f"the configuration file is larger than {MAX_CONFIG_BYTES} bytes")
        config = json.loads(file.read_text(encoding="utf-8"), parse_constant=_reject_constant)
    except OSError as error:
        raise VisionConfigError(f"the configuration file cannot be read: {error.strerror}") from None
    except ValueError as error:
        if isinstance(error, VisionConfigError):
            raise
        raise VisionConfigError(f"the configuration file is not valid JSON: {error}") from None
    return parse_vision_config(config, file.resolve().parent)


def run_simulation(source: FrameSource, model: ModelSpec, recorder: LatencyRecorder, frames: int) -> dict:
    """Run `frames` frames of `source` through the fake `model`, recording the stage instants of each in `recorder`."""
    if isinstance(frames, bool) or not isinstance(frames, int) or not 1 <= frames <= MAX_RUN_FRAMES:
        raise VisionConfigError(f"frames must be an integer from 1 to {MAX_RUN_FRAMES}")
    detections = 0
    count = 0
    total_ms: list[float] = []
    roll = hashlib.blake2b(digest_size=8)
    try:
        for frame in source.frames(frames):
            digest = hashlib.blake2b(frame.data + model.name.encode(), digest_size=8).digest()
            roll.update(digest)
            unit = [byte / 255.0 for byte in digest]
            start = frame.index * FRAME_PERIOD_MS / 1000.0
            pre = start + (0.5 + 1.5 * unit[0]) / 1000.0
            inf = pre + model.sim_latency_ms * (0.9 + 0.2 * unit[1]) / 1000.0
            post = inf + (0.2 + 0.3 * unit[2]) / 1000.0
            pub = post + (0.1 + 0.2 * unit[3]) / 1000.0
            durations = recorder.record(source.name, {"capture": start, "preprocess": pre, "inference": inf, "postprocess": post, "publish": pub})
            total_ms.append(durations["total"])
            detections += digest[4] % 4
            count += 1
    except SourceError as error:
        raise VisionConfigError(str(error)) from None
    if count == 0:
        raise VisionConfigError(f"source {source.name} holds no frames")
    ordered = sorted(total_ms)
    return {
        "simulated": True,
        "source": source.name,
        "model": model.name,
        "device": model.device,
        "frames": count,
        "detections": detections,
        "outputDigest": roll.hexdigest(),
        "totalMs": {"mean": sum(total_ms) / count, "max": ordered[-1]},
    }
