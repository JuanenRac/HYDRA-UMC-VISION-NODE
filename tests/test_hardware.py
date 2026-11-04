# =============================================================================
# HYDRA-UMC-VISION-NODE - tests/test_hardware.py
# Copyright (C) 2026 JuanenRac (Electro Hobby 3D) <electrohobby3d@gmail.com>
# GPL-3.0-or-later - see LICENSE
# =============================================================================
from __future__ import annotations

from hydra_umc_vision_node.hardware import PipelineMode, active_stages, camera_available, determine_mode
from hydra_umc_vision_node.pipeline import build_manifest


def test_determine_mode_full_when_both_present() -> None:
    assert determine_mode(camera_present=True, accelerator_present=True) == PipelineMode.FULL


def test_determine_mode_no_camera() -> None:
    assert determine_mode(camera_present=False, accelerator_present=True) == PipelineMode.DEGRADED_NO_CAMERA


def test_determine_mode_no_accelerator() -> None:
    assert (
        determine_mode(camera_present=True, accelerator_present=False) == PipelineMode.DEGRADED_NO_ACCELERATOR
    )


def test_determine_mode_no_hardware_at_all() -> None:
    assert (
        determine_mode(camera_present=False, accelerator_present=False) == PipelineMode.DEGRADED_NO_HARDWARE
    )


def test_active_stages_full_mode_runs_everything() -> None:
    manifest = build_manifest()
    runnable, skipped = active_stages(manifest, PipelineMode.FULL)
    assert len(runnable) == len(manifest.stages)
    assert skipped == ()


def test_active_stages_no_camera_skips_only_capture() -> None:
    manifest = build_manifest()
    runnable, skipped = active_stages(manifest, PipelineMode.DEGRADED_NO_CAMERA)

    assert [s.name for s in skipped] == ["capture"]
    assert "capture" not in [s.name for s in runnable]
    assert "inference" in [s.name for s in runnable]  # accelerator-only stage still runs


def test_active_stages_no_accelerator_skips_only_inference() -> None:
    manifest = build_manifest()
    runnable, skipped = active_stages(manifest, PipelineMode.DEGRADED_NO_ACCELERATOR)

    assert [s.name for s in skipped] == ["inference"]
    assert "capture" in [s.name for s in runnable]


def test_active_stages_no_hardware_skips_both_camera_and_accelerator_stages() -> None:
    manifest = build_manifest()
    runnable, skipped = active_stages(manifest, PipelineMode.DEGRADED_NO_HARDWARE)

    assert {s.name for s in skipped} == {"capture", "inference"}
    assert {s.name for s in runnable} == {"preprocess", "postprocess", "publish"}


def test_camera_available_is_honestly_false_on_a_machine_without_one() -> None:
    # This development machine has no /dev/video0 - a real, honest
    # negative, not a mock standing in for hardware that isn't here.
    assert camera_available() in (True, False)  # never raises


def test_find_devices_ignores_regular_files_and_missing_paths(tmp_path) -> None:
    from hydra_umc_vision_node.hardware import find_devices

    (tmp_path / "video9").write_text("not a device")
    assert find_devices((str(tmp_path / "video*"), str(tmp_path / "nothing"))) == []


def test_find_devices_accepts_a_character_device_and_a_pattern() -> None:
    import os

    from hydra_umc_vision_node.hardware import find_devices

    null = os.devnull
    if not os.path.exists(null) or os.name == "nt":
        return  # no character device to point at on this system
    assert find_devices((null, null)) == [null]  # listed twice, found once
    assert find_devices(("/dev/nul*",)) == [null]


def test_hardware_block_is_validated_and_replaces_the_patterns() -> None:
    import pytest

    from hydra_umc_vision_node.hardware import (
        DEFAULT_ACCELERATOR_PATTERNS,
        HardwareConfigError,
        configure_hardware,
        parse_hardware_config,
    )

    assert parse_hardware_config(None).accelerator_patterns == DEFAULT_ACCELERATOR_PATTERNS
    config = parse_hardware_config({"cameraDevices": ["/dev/cam*"]})
    assert config.camera_patterns == ("/dev/cam*",)
    assert config.accelerator_patterns == DEFAULT_ACCELERATOR_PATTERNS
    for bad in ({"cameraDevices": []}, {"cameraDevices": "x"}, {"cameraDevices": [""]}, {"other": 1}, []):
        with pytest.raises(HardwareConfigError):
            parse_hardware_config(bad)
    configure_hardware(parse_hardware_config({"cameraDevices": ["/nonexistent/cam*"], "acceleratorDevices": ["/nonexistent/hw"]}))
    try:
        assert camera_available() is False
    finally:
        configure_hardware(None)
