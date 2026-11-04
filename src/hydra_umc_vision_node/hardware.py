# =============================================================================
# HYDRA-UMC-VISION-NODE - src/hydra_umc_vision_node/hardware.py
# Copyright (C) 2026 JuanenRac (Electro Hobby 3D) <electrohobby3d@gmail.com>
# GPL-3.0-or-later - see LICENSE
# =============================================================================
"""Real degraded-mode detection: which pipeline stages can actually run
given whatever camera/accelerator hardware is really present right now.

The probe functions below check for the real device nodes a camera
(`/dev/video0`) or a Hailo-8 accelerator (`/dev/hailo0`) would expose on
Linux - on THIS development machine (no CM5, no Hailo-8, not even
Linux) they honestly report "not available", which is exactly the real
degraded-mode path this module exists to prove works, not a mock
standing in for hardware this environment doesn't have. `determine_mode`
and `active_stages` take plain booleans, not the probes themselves, so
their real decision logic is fully testable without touching a
filesystem or any hardware at all - the probes are the only part that
ever needs real hardware to observe something interesting.
"""
from __future__ import annotations

import enum
import glob
import os
import stat
from dataclasses import dataclass
from pathlib import Path

from .pipeline import PipelineManifest, PipelineStage

CAMERA_DEVICE = Path("/dev/video0")
ACCELERATOR_DEVICE = Path("/dev/hailo0")

# Device nodes that count as a camera / an accelerator. They are glob patterns, so a second camera
# (/dev/video2) or a second accelerator (/dev/hailo1) is found too; the `hardware` block of the
# --vision-sim file replaces both lists (the values are configuration, not code).
DEFAULT_CAMERA_PATTERNS = ("/dev/video*",)
DEFAULT_ACCELERATOR_PATTERNS = ("/dev/hailo*",)
MAX_PATTERNS = 16
MAX_PATTERN_LENGTH = 256


class HardwareConfigError(ValueError):
    """The hardware block of the configuration cannot be used."""


@dataclass(frozen=True)
class HardwareConfig:
    camera_patterns: tuple[str, ...] = DEFAULT_CAMERA_PATTERNS
    accelerator_patterns: tuple[str, ...] = DEFAULT_ACCELERATOR_PATTERNS


_config = HardwareConfig()


def _patterns(value: object, name: str, default: tuple[str, ...]) -> tuple[str, ...]:
    if value is None:
        return default
    if (
        not isinstance(value, list)
        or not 1 <= len(value) <= MAX_PATTERNS
        or not all(isinstance(item, str) and 0 < len(item) <= MAX_PATTERN_LENGTH and "\x00" not in item for item in value)
    ):
        raise HardwareConfigError(f"hardware.{name} must be a list of 1 to {MAX_PATTERNS} non-empty paths or patterns")
    return tuple(value)


def parse_hardware_config(block: object) -> HardwareConfig:
    """The optional `hardware` block: `cameraDevices` and `acceleratorDevices`, lists of paths or glob patterns."""
    if block is None:
        return HardwareConfig()
    if not isinstance(block, dict) or set(block) - {"cameraDevices", "acceleratorDevices"}:
        raise HardwareConfigError("hardware is an object with cameraDevices and acceleratorDevices")
    return HardwareConfig(
        _patterns(block.get("cameraDevices"), "cameraDevices", DEFAULT_CAMERA_PATTERNS),
        _patterns(block.get("acceleratorDevices"), "acceleratorDevices", DEFAULT_ACCELERATOR_PATTERNS),
    )


def configure_hardware(config: HardwareConfig | None) -> None:
    """Sets the patterns the probes use (None goes back to the defaults)."""
    global _config
    _config = config if config is not None else HardwareConfig()


def _usable_node(path: str) -> bool:
    """A device node that exists, is a character device (or a symlink to one) and can be opened for reading.
    A leftover regular file, a directory or a node the service may not read does not count as hardware."""
    try:
        mode = os.stat(path).st_mode
    except OSError:
        return False
    return stat.S_ISCHR(mode) and os.access(path, os.R_OK)


def find_devices(patterns: tuple[str, ...]) -> list[str]:
    found: list[str] = []
    for pattern in patterns:
        for path in sorted(glob.glob(pattern)) if glob.has_magic(pattern) else [pattern]:
            if path not in found and _usable_node(path):
                found.append(path)
    return found


def camera_available() -> bool:
    """Real check for a camera device node. False (not an exception) is
    the correct, honest answer on any machine without one attached."""
    return bool(find_devices(_config.camera_patterns))


def accelerator_available() -> bool:
    """Real check for a Hailo-8 accelerator device node. Same honesty
    as camera_available(): false, not a crash, when there is none."""
    return bool(find_devices(_config.accelerator_patterns))


class PipelineMode(enum.Enum):
    FULL = "full"
    DEGRADED_NO_CAMERA = "degraded_no_camera"
    DEGRADED_NO_ACCELERATOR = "degraded_no_accelerator"
    DEGRADED_NO_HARDWARE = "degraded_no_hardware"
    # camera and accelerator are there but the registry does not say the model is ready (registry_gate.py)
    DEGRADED_MODEL_NOT_READY = "degraded_model_not_ready"


def determine_mode(camera_present: bool, accelerator_present: bool, model_ready: bool | None = None) -> PipelineMode:
    """The real decision: what mode does this node run in, given
    whether a camera and an accelerator are actually present. Pure
    function of two booleans - no filesystem access, so every real
    hardware combination is directly, deterministically testable.

    `model_ready` is None when nothing is asked of the model registry (the default, as before). With it False the node
    does not reach the full mode even if both devices are there: the model it would run is not ready."""
    if camera_present and accelerator_present:
        return PipelineMode.FULL if model_ready is not False else PipelineMode.DEGRADED_MODEL_NOT_READY
    if not camera_present and not accelerator_present:
        return PipelineMode.DEGRADED_NO_HARDWARE
    if not camera_present:
        return PipelineMode.DEGRADED_NO_CAMERA
    return PipelineMode.DEGRADED_NO_ACCELERATOR


def detect_mode() -> PipelineMode:
    """The real entry point a live node would call: probes actual
    hardware, then applies the same pure decision logic `determine_mode`
    does. Kept separate from `determine_mode` precisely so tests never
    need to touch a real filesystem to exercise every mode."""
    return determine_mode(camera_available(), accelerator_available())


def active_stages(
    manifest: PipelineManifest, mode: PipelineMode
) -> tuple[tuple[PipelineStage, ...], tuple[PipelineStage, ...]]:
    """Splits `manifest.stages` into (runnable, skipped) for `mode` - the
    real, concrete meaning of "degraded mode": which stages of the real
    pipeline can actually execute right now, and which are skipped
    because the hardware they need isn't present. `FULL` mode always
    returns every stage as runnable, none skipped."""
    camera_present = mode not in (PipelineMode.DEGRADED_NO_CAMERA, PipelineMode.DEGRADED_NO_HARDWARE)
    accelerator_present = mode not in (
        PipelineMode.DEGRADED_NO_ACCELERATOR,
        PipelineMode.DEGRADED_NO_HARDWARE,
        PipelineMode.DEGRADED_MODEL_NOT_READY,
    )

    runnable = []
    skipped = []
    for stage in manifest.stages:
        if stage.requires_camera and not camera_present:
            skipped.append(stage)
        elif stage.requires_accelerator and not accelerator_present:
            skipped.append(stage)
        else:
            runnable.append(stage)
    return tuple(runnable), tuple(skipped)
