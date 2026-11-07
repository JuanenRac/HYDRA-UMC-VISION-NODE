# =============================================================================
# HYDRA-UMC-VISION-NODE - tests/test_fake_camera_and_accelerator.py
# Copyright (C) 2026 JuanenRac (Electro Hobby 3D) <electrohobby3d@gmail.com>
# GPL-3.0-or-later - see LICENSE
# =============================================================================
"""A fake camera that is plugged in as a kind of source, and fake device nodes that make the node believe a camera and an accelerator are attached (evidence: simulator).

The camera draws a bright square that moves a known step each frame; the node reads it through the same source interface a real camera would be registered with, the
real loopback detector finds the square and the boxes follow it. The device nodes are files that the probe is told to count as character devices, so the real
detection of the mode (full, no camera, no accelerator, nothing, model not ready) and the real choice of the stages that run are exercised. The detector is the simple
brightness one of the loopback, not a neural network; nothing here has run with a real camera or a real accelerator.
"""
from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest

from hydra_umc_vision_node import hardware, sources
from hydra_umc_vision_node.contract import validate_detection_frame, validate_frame_meta
from hydra_umc_vision_node.hardware import HardwareConfig, PipelineMode, active_stages, configure_hardware, detect_mode, determine_mode
from hydra_umc_vision_node.loopback import DetectorSettings, LoopbackConfig, run_loopback, source_items
from hydra_umc_vision_node.pipeline import build_manifest
from hydra_umc_vision_node.sources import Frame, SourceError, build_source

WIDTH, HEIGHT, SIZE, STEP = 64, 48, 8, 8


class FakeCamera:
    """A camera that shows a bright square moving to the right by STEP pixels per frame."""

    kind = "fake-camera"

    def __init__(self, name: str, frames: int) -> None:
        self.name, self._frames = name, frames

    def describe(self) -> dict:
        return {"name": self.name, "kind": self.kind, "width": WIDTH, "height": HEIGHT, "channels": 1, "frames": self._frames}

    def frames(self, limit: int) -> Iterator[Frame]:
        for index in range(min(limit, self._frames)):
            data = bytearray(WIDTH * HEIGHT)
            x0 = index * STEP
            for y in range(16, 16 + SIZE):
                data[y * WIDTH + x0 : y * WIDTH + x0 + SIZE] = b"\xff" * SIZE
            yield Frame(index, WIDTH, HEIGHT, 1, bytes(data))


@pytest.fixture
def camera_kind(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setitem(sources.SOURCE_KINDS, "fake-camera", lambda name, config, base: FakeCamera(name, int(config.get("frames", 4))))
    monkeypatch.setitem(sources._ALLOWED_KEYS, "fake-camera", {"name", "kind", "frames"})


def test_the_boxes_follow_the_square_of_the_fake_camera_frame_by_frame(camera_kind: None, tmp_path: Path) -> None:
    source = build_source({"name": "cam0", "kind": "fake-camera", "frames": 5}, tmp_path)
    config = LoopbackConfig(True, DetectorSettings(), None)
    result = run_loopback(source_items(source, 5), config, "cam0", 5)
    assert result["simulated"] is True and result["frames"] == 5 and result["detections"] == 5
    for index, message in enumerate(result["messages"]):
        validate_frame_meta(message["meta"])
        validate_detection_frame(message["detections"])
        (box,) = [d["box"] if "box" in d else d for d in message["detections"]["detections"]]
        assert box["x_min"] == index * STEP and box["x_max"] == index * STEP + SIZE
        assert box["y_min"] == 16 and box["y_max"] == 16 + SIZE


def test_the_fake_camera_obeys_the_limit_and_an_unknown_key_is_refused(camera_kind: None, tmp_path: Path) -> None:
    source = build_source({"name": "cam0", "kind": "fake-camera", "frames": 10}, tmp_path)
    assert len(run_loopback(source_items(source, 3), LoopbackConfig(True, DetectorSettings(), None), "cam0", 3)["messages"]) == 3
    with pytest.raises(SourceError):
        build_source({"name": "cam0", "kind": "fake-camera", "device": "/dev/video0"}, tmp_path)


def test_a_kind_that_was_never_registered_is_refused(tmp_path: Path) -> None:
    with pytest.raises(SourceError):
        build_source({"name": "cam0", "kind": "fake-camera"}, tmp_path)


@pytest.fixture
def device_nodes(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    """Files that the probe counts as character devices, found through the same patterns the hardware block configures."""
    monkeypatch.setattr(hardware, "_usable_node", lambda path: Path(path).is_file())
    configure_hardware(HardwareConfig(camera_patterns=(str(tmp_path / "video*"),), accelerator_patterns=(str(tmp_path / "hailo*"),)))
    yield tmp_path
    configure_hardware(None)


def test_the_mode_follows_the_fake_device_nodes(device_nodes: Path) -> None:
    assert detect_mode() is PipelineMode.DEGRADED_NO_HARDWARE
    (device_nodes / "video0").write_bytes(b"")
    assert detect_mode() is PipelineMode.DEGRADED_NO_ACCELERATOR
    (device_nodes / "hailo0").write_bytes(b"")
    assert detect_mode() is PipelineMode.FULL
    (device_nodes / "video0").unlink()
    assert detect_mode() is PipelineMode.DEGRADED_NO_CAMERA
    assert hardware.accelerator_available() and not hardware.camera_available()


def test_a_second_camera_and_a_second_accelerator_are_found_by_the_pattern(device_nodes: Path) -> None:
    for name in ("video0", "video2", "hailo0", "hailo1"):
        (device_nodes / name).write_bytes(b"")
    assert len(hardware.find_devices(hardware._config.camera_patterns)) == 2
    assert len(hardware.find_devices(hardware._config.accelerator_patterns)) == 2


def test_the_stages_that_run_follow_the_fake_hardware_and_the_state_of_the_model(device_nodes: Path) -> None:
    manifest = build_manifest()
    (device_nodes / "video0").write_bytes(b"")
    (device_nodes / "hailo0").write_bytes(b"")
    runnable, skipped = active_stages(manifest, detect_mode())
    assert not skipped and len(runnable) == len(manifest.stages)
    not_ready = determine_mode(hardware.camera_available(), hardware.accelerator_available(), model_ready=False)
    assert not_ready is PipelineMode.DEGRADED_MODEL_NOT_READY
    runnable, skipped = active_stages(manifest, not_ready)
    assert skipped and all(stage.requires_accelerator for stage in skipped)
    assert all(not stage.requires_accelerator for stage in runnable)
    (device_nodes / "hailo0").unlink()
    _, skipped = active_stages(manifest, detect_mode())
    assert skipped and all(stage.requires_accelerator for stage in skipped)
