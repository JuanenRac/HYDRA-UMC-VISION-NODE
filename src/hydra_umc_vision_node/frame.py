# =============================================================================
# HYDRA-UMC-VISION-NODE - src/hydra_umc_vision_node/frame.py
# Copyright (C) 2026 JuanenRac (Electro Hobby 3D) <electrohobby3d@gmail.com>
# GPL-3.0 - see LICENSE
# =============================================================================
"""Real, hardware-independent structural validation of a raw camera
frame buffer, against its own declared FrameSpec.

This deliberately does NOT need a real camera to be real: a "frame" here
is just a raw byte buffer plus the dimensions it claims to have - the
same shape a real capture stage would hand to `preprocess` in
pipeline.py's DEFAULT_STAGES, and the same shape a corrupted/truncated
read from a flaky camera driver, a torn network frame, or a bad test
fixture would produce. Checking that shape is real, useful validation
whether or not any actual camera is attached - which is exactly what
lets this be tested honestly without CM5+Hailo-8 hardware.
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class FrameSpec:
    """The expected shape of one frame - what `capture` in pipeline.py
    is supposed to hand `preprocess`."""

    width: int
    height: int
    channels: int = 3  # RGB by default

    @property
    def expected_bytes(self) -> int:
        return self.width * self.height * self.channels


@dataclass(frozen=True)
class FrameValidationIssue:
    kind: str
    detail: str


def validate_frame(data: bytes, spec: FrameSpec) -> list[FrameValidationIssue]:
    """Real structural validation of a raw frame buffer against `spec`.

    Checks the buffer is neither truncated nor oversized (the exact
    corruption pattern a torn read, a dropped network fragment, or a
    driver returning a partial frame produces), and flags a
    suspiciously uniform buffer (every byte identical) - a real, common
    symptom of a stuck/frozen camera sensor or a zeroed/blank capture,
    not proof of corruption on its own, but worth surfacing rather than
    silently accepting as a normal frame.
    """
    issues: list[FrameValidationIssue] = []

    if spec.width <= 0 or spec.height <= 0 or spec.channels <= 0:
        issues.append(
            FrameValidationIssue(
                "invalid_spec",
                f"FrameSpec has non-positive dimensions: {spec.width}x{spec.height}x{spec.channels}",
            )
        )
        return issues

    expected = spec.expected_bytes
    actual = len(data)
    if actual != expected:
        issues.append(
            FrameValidationIssue(
                "size_mismatch",
                f"frame buffer is {actual} bytes, expected {expected} bytes for "
                f"{spec.width}x{spec.height}x{spec.channels} - likely truncated or corrupt",
            )
        )
        return issues  # further checks need a correctly-sized buffer

    if len(data) > 0 and data.count(data[0:1]) == len(data):
        issues.append(
            FrameValidationIssue(
                "uniform_buffer",
                f"every byte in the frame is identical (0x{data[0]:02x}) - "
                "likely a frozen sensor or a blank/zeroed capture",
            )
        )

    return issues


def is_frame_valid(data: bytes, spec: FrameSpec) -> bool:
    return len(validate_frame(data, spec)) == 0
