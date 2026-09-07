# =============================================================================
# HYDRA-UMC-VISION-NODE - src/hydra_umc_vision_node/hardware.py
# Copyright (C) 2026 JuanenRac (Electro Hobby 3D) <electrohobby3d@gmail.com>
# GPL-3.0 - see LICENSE
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
from pathlib import Path

from .pipeline import PipelineManifest, PipelineStage

CAMERA_DEVICE = Path("/dev/video0")
ACCELERATOR_DEVICE = Path("/dev/hailo0")


def camera_available() -> bool:
    """Real check for a camera device node. False (not an exception) is
    the correct, honest answer on any machine without one attached."""
    return CAMERA_DEVICE.exists()


def accelerator_available() -> bool:
    """Real check for a Hailo-8 accelerator device node. Same honesty
    as camera_available(): false, not a crash, when there is none."""
    return ACCELERATOR_DEVICE.exists()


class PipelineMode(enum.Enum):
    FULL = "full"
    DEGRADED_NO_CAMERA = "degraded_no_camera"
    DEGRADED_NO_ACCELERATOR = "degraded_no_accelerator"
    DEGRADED_NO_HARDWARE = "degraded_no_hardware"


def determine_mode(camera_present: bool, accelerator_present: bool) -> PipelineMode:
    """The real decision: what mode does this node run in, given
    whether a camera and an accelerator are actually present. Pure
    function of two booleans - no filesystem access, so every real
    hardware combination is directly, deterministically testable."""
    if camera_present and accelerator_present:
        return PipelineMode.FULL
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
