# =============================================================================
# HYDRA-UMC-VISION-NODE - tests/test_pipeline.py
# Copyright (C) 2026 JuanenRac (Electro Hobby 3D) <electrohobby3d@gmail.com>
# GPL-3.0 - see LICENSE
# =============================================================================
from __future__ import annotations

from hydra_umc_vision_node.pipeline import build_manifest, manifest_to_dict


def test_build_manifest_has_a_real_ordered_shape() -> None:
    manifest = build_manifest()

    names = [s.name for s in manifest.stages]
    assert names == ["capture", "preprocess", "inference", "postprocess", "publish"]


def test_capture_requires_a_camera_not_an_accelerator() -> None:
    manifest = build_manifest()
    capture = manifest.stage("capture")
    assert capture is not None
    assert capture.requires_camera is True
    assert capture.requires_accelerator is False


def test_inference_requires_an_accelerator_not_a_camera() -> None:
    manifest = build_manifest()
    inference = manifest.stage("inference")
    assert inference is not None
    assert inference.requires_accelerator is True
    assert inference.requires_camera is False


def test_preprocess_postprocess_publish_need_no_hardware() -> None:
    manifest = build_manifest()
    for name in ("preprocess", "postprocess", "publish"):
        stage = manifest.stage(name)
        assert stage is not None
        assert stage.requires_camera is False
        assert stage.requires_accelerator is False


def test_stage_returns_none_for_an_unknown_name() -> None:
    manifest = build_manifest()
    assert manifest.stage("does-not-exist") is None


def test_manifest_to_dict_round_trips_every_stage() -> None:
    manifest = build_manifest()
    data = manifest_to_dict(manifest)

    assert data["version"] == manifest.version
    assert len(data["stages"]) == len(manifest.stages)
    assert data["stages"][0]["name"] == "capture"
    assert data["stages"][0]["requires_camera"] is True
