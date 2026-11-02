# =============================================================================
# HYDRA-UMC-VISION-NODE - tests/test_contract.py
# Copyright (C) 2026 JuanenRac (Electro Hobby 3D) <electrohobby3d@gmail.com>
# GPL-3.0-or-later - see LICENSE
# =============================================================================
from __future__ import annotations

import copy
import importlib
from pathlib import Path

import pytest

from hydra_umc_vision_node import contract
from hydra_umc_vision_node.contract import (
    ContractError,
    box_to_pixels,
    build_detection_frame,
    build_frame_meta,
    iso_utc,
    validate_detection_frame,
    validate_frame_meta,
)

TS = "2026-01-02T03:04:05.678Z"
MODEL = {"model_name": "m", "model_version": "1", "model_sha256": "a" * 64, "hailo_arch": "cpu", "pipeline_version": "0.1.0"}
ITEM = {"class_id": 0, "label": "bright", "score": 0.5, "box": {"x_min": 1.0, "y_min": 2.0, "x_max": 10.0, "y_max": 20.0}}


def test_iso_utc_is_rfc3339_with_milliseconds() -> None:
    assert iso_utc(0) == "1970-01-01T00:00:00.000Z"
    assert iso_utc(1.5) == "1970-01-01T00:00:01.500Z"


def test_a_frame_meta_and_a_detection_frame_are_built_validated() -> None:
    meta = build_frame_meta("cam0-1", TS, 16, 8, "RGB", session_id="s1", sequence=3, source_id=None)
    assert "source_id" not in meta and meta["sequence"] == 3 and meta["schema_version"] == "1.0"
    frame = build_detection_frame("cam0-1", TS, "pixel", [ITEM], model=MODEL, session_id="s1")
    assert frame["model"] == MODEL and frame["detections"] == [ITEM]


@pytest.mark.parametrize("change", [
    {"frame_id": ""}, {"frame_id": "a b"}, {"capture_ts_utc": "yesterday"}, {"capture_ts_utc": "2026-13-45T25:00:00Z"}, {"width": 0}, {"width": True},
    {"width": 8193}, {"pixel_format": "CMYK"}, {"stride": 0}, {"sequence": -1}, {"extra": 1},
])
def test_frame_meta_refuses_what_the_schema_refuses(change: dict) -> None:
    good = build_frame_meta("f", TS, 4, 4, "GRAY")
    with pytest.raises(ContractError):
        validate_frame_meta({**good, **change})


def test_frame_meta_needs_every_required_field() -> None:
    good = build_frame_meta("f", TS, 4, 4, "GRAY")
    for name in ("schema_version", "frame_id", "capture_ts_utc", "width", "height", "pixel_format"):
        broken = {k: v for k, v in good.items() if k != name}
        with pytest.raises(ContractError):
            validate_frame_meta(broken)
    with pytest.raises(ContractError):
        validate_frame_meta([])


def test_detection_frame_refuses_a_bad_box_a_bad_unit_and_a_bad_model() -> None:
    good = build_detection_frame("f", TS, "pixel", [ITEM], model=MODEL)
    def broken(**change: object) -> dict:
        return {**copy.deepcopy(good), **change}
    with pytest.raises(ContractError):
        validate_detection_frame(broken(box_unit="meters"))
    inverted = copy.deepcopy(ITEM)
    inverted["box"]["x_min"], inverted["box"]["x_max"] = 10.0, 1.0
    with pytest.raises(ContractError, match="x_min <= x_max"):
        validate_detection_frame(broken(detections=[inverted]))
    outside = copy.deepcopy(ITEM)
    outside["box"]["x_max"] = 1.5
    with pytest.raises(ContractError):
        validate_detection_frame(broken(box_unit="normalized", detections=[outside]))
    validate_detection_frame(broken(box_unit="pixel", detections=[outside]))
    for bad in ({"score": 1.5}, {"score": True}, {"class_id": -1}, {"label": ""}, {"box": {"x_min": 0}}):
        with pytest.raises(ContractError):
            validate_detection_frame(broken(detections=[{**ITEM, **bad}]))
    with pytest.raises(ContractError):
        validate_detection_frame(broken(detections=[ITEM] * 1001))
    with pytest.raises(ContractError):
        validate_detection_frame(broken(model={**MODEL, "model_sha256": "A" * 64}))
    with pytest.raises(ContractError):
        validate_detection_frame(broken(model={**MODEL, "hailo_arch": ""}))
    with pytest.raises(ContractError):
        validate_detection_frame(broken(model={"model_name": "m"}))


def test_box_to_pixels_scales_normalized_boxes_and_keeps_pixel_boxes() -> None:
    box = {"x_min": 0.25, "y_min": 0.5, "x_max": 0.5, "y_max": 1.0}
    assert box_to_pixels(box, "normalized", 100, 40) == {"x_min": 25.0, "y_min": 20.0, "x_max": 50.0, "y_max": 40.0}
    assert box_to_pixels({"x_min": 1, "y_min": 2, "x_max": 3, "y_max": 4}, "pixel", 100, 40)["x_max"] == 3.0
    with pytest.raises(ContractError):
        box_to_pixels(box, "meters", 1, 1)


def _sdk_module(monkeypatch: pytest.MonkeyPatch):
    source = Path(__file__).resolve().parents[2] / "HYDRA-UMC-SDK" / "clients" / "python" / "src"
    if not (source / "hydra_umc_sdk" / "vision_pipeline.py").is_file():
        pytest.skip("the SDK checkout is not next to this repository")
    monkeypatch.syspath_prepend(str(source))
    return importlib.import_module("hydra_umc_sdk.vision_pipeline")


def test_the_constants_and_the_rules_are_those_of_the_sdk(monkeypatch: pytest.MonkeyPatch) -> None:
    sdk = _sdk_module(monkeypatch)
    for name in ("PIXEL_FORMATS", "BOX_UNITS", "MAX_DIMENSION", "MAX_DETECTIONS", "BOX_FIELDS", "MODEL_FIELDS"):
        assert getattr(contract, name) == getattr(sdk, name), name
    meta = build_frame_meta("cam0-1", TS, 16, 8, "RGB", session_id="s1", sequence=3)
    frame = build_detection_frame("cam0-1", TS, "pixel", [ITEM], model=MODEL, session_id="s1")
    sdk.validate_frame_meta(meta)
    sdk.validate_detection_frame(frame)
