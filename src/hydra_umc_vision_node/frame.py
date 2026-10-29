# =============================================================================
# HYDRA-UMC-VISION-NODE - src/hydra_umc_vision_node/frame.py
# Copyright (C) 2026 JuanenRac (Electro Hobby 3D) <electrohobby3d@gmail.com>
# GPL-3.0-or-later - see LICENSE
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

import hashlib
from dataclasses import dataclass


# Largest frame this node will accept over HTTP or describe in a spec. A
# declared size above these limits is refused before any body is read.
MAX_DIMENSION = 8192
MAX_CHANNELS = 4
MAX_FRAME_BYTES = 64 * 1024 * 1024


# Pixel formats a spec may name: (bytes per pixel numerator, denominator, channels for packed formats or 0).
# NV12 is planar (full-resolution Y plane plus a half-resolution interleaved UV plane), so it has no row stride.
PIXEL_FORMATS: dict[str, tuple[int, int]] = {
    "GRAY": (1, 1),
    "RGB": (3, 1),
    "BGR": (3, 1),
    "RGBA": (4, 1),
    "BGRA": (4, 1),
    "YUYV": (2, 1),
    "NV12": (3, 2),
}


@dataclass(frozen=True)
class FrameSpec:
    """The expected shape of one frame - what `capture` in pipeline.py
    is supposed to hand `preprocess`.

    Without `pixel_format` the frame is `width x height x channels` bytes, as before. With one (`RGB`, `BGR`,
    `RGBA`, `BGRA`, `GRAY`, `YUYV`, `NV12`) the size follows the format, and `stride` (bytes per row of a packed
    format, at least `width x bytes per pixel`) lets a driver that pads its rows be described exactly.
    """

    width: int
    height: int
    channels: int = 3  # RGB by default
    pixel_format: str | None = None
    stride: int | None = None

    @property
    def expected_bytes(self) -> int:
        if self.pixel_format is None:
            return self.width * self.height * self.channels
        num, den = PIXEL_FORMATS[self.pixel_format]
        if self.stride is not None:
            return self.stride * self.height
        return self.width * self.height * num // den

    def problem(self) -> str | None:
        """Why this spec cannot describe a frame, or None."""
        if self.width <= 0 or self.height <= 0 or self.channels <= 0:
            return f"FrameSpec has non-positive dimensions: {self.width}x{self.height}x{self.channels}"
        if self.width > MAX_DIMENSION or self.height > MAX_DIMENSION or self.channels > MAX_CHANNELS:
            return (
                f"FrameSpec is larger than this node accepts (at most {MAX_DIMENSION}x{MAX_DIMENSION}x{MAX_CHANNELS}): "
                f"{self.width}x{self.height}x{self.channels}"
            )
        if self.pixel_format is None:
            if self.stride is not None:
                return "FrameSpec.stride needs a pixel_format"
            return None
        if self.pixel_format not in PIXEL_FORMATS:
            return f"unknown pixel_format {self.pixel_format!r} (one of {sorted(PIXEL_FORMATS)})"
        num, den = PIXEL_FORMATS[self.pixel_format]
        if self.pixel_format in ("YUYV", "NV12") and (self.width % 2 or (self.pixel_format == "NV12" and self.height % 2)):
            return f"{self.pixel_format} needs an even width" + (" and height" if self.pixel_format == "NV12" else "")
        if self.stride is not None:
            if self.pixel_format == "NV12":
                return "NV12 is planar and has no row stride"
            if isinstance(self.stride, bool) or not isinstance(self.stride, int) or self.stride < self.width * num:
                return f"stride must be an integer of at least {self.width * num} bytes for {self.width} x {self.pixel_format}"
        return None


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

    spec_problem = spec.problem()
    if spec_problem is not None:
        issues.append(FrameValidationIssue("invalid_spec", spec_problem))
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


class FrozenFrameWatcher:
    """Flags a sensor that keeps returning the same frame. A frozen sensor does not always give a uniform buffer; it
    gives the SAME buffer again and again. Feed every frame to `check`: the hash of each frame is compared with the
    previous one and the issue `frozen_frame` is returned once `repeats` identical frames in a row have followed the
    first (so `repeats=2` means three equal frames). A different frame resets the count."""

    def __init__(self, repeats: int = 2) -> None:
        if isinstance(repeats, bool) or not isinstance(repeats, int) or repeats < 1:
            raise ValueError("repeats must be an integer of at least 1")
        self.repeats = repeats
        self._last: str | None = None
        self._same = 0

    def check(self, data: bytes) -> FrameValidationIssue | None:
        digest = hashlib.sha256(data).hexdigest()
        if digest == self._last:
            self._same += 1
        else:
            self._last, self._same = digest, 0
        if self._same >= self.repeats:
            return FrameValidationIssue(
                "frozen_frame", f"the last {self._same + 1} frames are byte-for-byte identical - likely a frozen sensor"
            )
        return None


def is_frame_valid(data: bytes, spec: FrameSpec) -> bool:
    return len(validate_frame(data, spec)) == 0
