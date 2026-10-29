# =============================================================================
# HYDRA-UMC-VISION-NODE - tests/test_frame.py
# Copyright (C) 2026 JuanenRac (Electro Hobby 3D) <electrohobby3d@gmail.com>
# GPL-3.0-or-later - see LICENSE
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


# ---- pixel formats, stride and frozen frames

import pytest  # noqa: E402

from hydra_umc_vision_node.frame import FrozenFrameWatcher  # noqa: E402


@pytest.mark.parametrize(
    "fmt,size",
    [("GRAY", 4 * 2), ("RGB", 4 * 2 * 3), ("BGRA", 4 * 2 * 4), ("YUYV", 4 * 2 * 2), ("NV12", 4 * 2 * 3 // 2)],
)
def test_each_pixel_format_has_its_own_size(fmt, size):
    spec = FrameSpec(width=4, height=2, pixel_format=fmt)
    assert spec.expected_bytes == size
    data = bytes(range(size))
    assert validate_frame(data, spec) == []
    assert [i.kind for i in validate_frame(data + b"\x00", spec)] == ["size_mismatch"]


def test_stride_describes_padded_rows():
    spec = FrameSpec(width=4, height=2, pixel_format="RGB", stride=16)  # 12 bytes of pixels + 4 of padding per row
    assert spec.expected_bytes == 32
    assert validate_frame(bytes(range(32)), spec) == []


@pytest.mark.parametrize(
    "spec",
    [
        FrameSpec(4, 2, pixel_format="RGB565"),
        FrameSpec(4, 2, pixel_format="RGB", stride=11),
        FrameSpec(4, 2, pixel_format="NV12", stride=4),
        FrameSpec(3, 2, pixel_format="YUYV"),
        FrameSpec(4, 3, pixel_format="NV12"),
        FrameSpec(4, 2, stride=12),
    ],
)
def test_impossible_specs_are_reported_as_invalid(spec):
    assert [i.kind for i in validate_frame(b"\x01\x02" * 100, spec)] == ["invalid_spec"]


def test_a_spec_without_pixel_format_behaves_as_before():
    spec = FrameSpec(width=2, height=2, channels=3)
    assert spec.expected_bytes == 12 and validate_frame(bytes(range(12)), spec) == []


def test_frozen_frame_is_flagged_after_the_repeats_and_reset_by_a_new_frame():
    watcher = FrozenFrameWatcher(repeats=2)
    a, b = bytes(range(12)), bytes(range(1, 13))
    assert watcher.check(a) is None
    assert watcher.check(a) is None
    issue = watcher.check(a)
    assert issue is not None and issue.kind == "frozen_frame"
    assert watcher.check(b) is None
    assert watcher.check(b) is None


@pytest.mark.parametrize("repeats", [0, -1, True, 1.5])
def test_watcher_rejects_bad_repeats(repeats):
    with pytest.raises(ValueError):
        FrozenFrameWatcher(repeats=repeats)
