# =============================================================================
# HYDRA-UMC-VISION-NODE - tests/test_frame.py
# Copyright (C) 2026 JuanenRac (Electro Hobby 3D) <electrohobby3d@gmail.com>
# GPL-3.0 - see LICENSE
# =============================================================================
from __future__ import annotations

from hydra_umc_vision_node.frame import FrameSpec, is_frame_valid, validate_frame


def _real_frame(spec: FrameSpec) -> bytes:
    """A real, non-uniform frame buffer of exactly the right size - not
    all identical bytes, so it doesn't also trip the uniform-buffer check."""
    return bytes((i % 251) for i in range(spec.expected_bytes))


def test_a_correctly_sized_non_uniform_frame_is_valid() -> None:
    spec = FrameSpec(width=4, height=4, channels=3)
    data = _real_frame(spec)

    assert validate_frame(data, spec) == []
    assert is_frame_valid(data, spec) is True


def test_a_truncated_frame_is_flagged_as_size_mismatch() -> None:
    spec = FrameSpec(width=4, height=4, channels=3)
    data = _real_frame(spec)[: spec.expected_bytes // 2]

    issues = validate_frame(data, spec)
    assert len(issues) == 1
    assert issues[0].kind == "size_mismatch"
    assert not is_frame_valid(data, spec)


def test_an_oversized_frame_is_flagged_as_size_mismatch() -> None:
    spec = FrameSpec(width=4, height=4, channels=3)
    data = _real_frame(spec) + b"\x00" * 10

    issues = validate_frame(data, spec)
    assert len(issues) == 1
    assert issues[0].kind == "size_mismatch"


def test_a_correctly_sized_but_all_zero_frame_is_flagged_as_uniform() -> None:
    spec = FrameSpec(width=2, height=2, channels=3)
    data = bytes(spec.expected_bytes)  # all zero bytes

    issues = validate_frame(data, spec)
    assert len(issues) == 1
    assert issues[0].kind == "uniform_buffer"


def test_a_correctly_sized_but_all_same_nonzero_byte_frame_is_flagged_as_uniform() -> None:
    spec = FrameSpec(width=2, height=2, channels=3)
    data = bytes([255]) * spec.expected_bytes

    issues = validate_frame(data, spec)
    assert len(issues) == 1
    assert issues[0].kind == "uniform_buffer"


def test_an_invalid_spec_is_flagged_before_checking_the_buffer() -> None:
    spec = FrameSpec(width=0, height=4, channels=3)
    issues = validate_frame(b"", spec)
    assert len(issues) == 1
    assert issues[0].kind == "invalid_spec"


def test_grayscale_single_channel_frame_is_supported() -> None:
    spec = FrameSpec(width=8, height=8, channels=1)
    data = _real_frame(spec)
    assert is_frame_valid(data, spec)
