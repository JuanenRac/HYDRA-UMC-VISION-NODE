# =============================================================================
# HYDRA-UMC-VISION-NODE - tests/test_pose.py
# Copyright (C) 2026 JuanenRac (Electro Hobby 3D) <electrohobby3d@gmail.com>
# GPL-3.0-or-later - see LICENSE
# =============================================================================
"""The pose stage of the loopback pipeline: the pinhole model that turns the box of a detection into a position in meters on the work surface, the refusals of
its configuration, what gets no estimate, and the PoseEstimate messages that the run publishes next to the DetectionFrame. Simulator evidence only."""
from __future__ import annotations

import math
from pathlib import Path

import pytest

from hydra_umc_vision_node.contract import SCHEMA_VERSION, check_estimates_match_detections, validate_pose_estimate
from hydra_umc_vision_node.loopback import LoopbackError, parse_loopback_config, run_loopback
from hydra_umc_vision_node.pose import CameraModel, PoseError, PoseSettings, estimate_poses, parse_pose_config
from hydra_umc_vision_node.sources import Frame

from test_loopback import CONFIG, _call, _serving

CAMERA = CameraModel(fx=100.0, fy=200.0, cx=50.0, cy=40.0, width=100, height=80)


def _settings(**overrides: object) -> PoseSettings:
    return PoseSettings(**{"enabled": True, "camera": CAMERA, "distance_m": 0.5, "min_score": 0.5, **overrides})  # type: ignore[arg-type]


def _detection(x0: float, y0: float, x1: float, y1: float, score: float = 0.9) -> dict:
    return {"class_id": 0, "label": "bright", "score": score, "box": {"x_min": x0, "y_min": y0, "x_max": x1, "y_max": y1}}


def _frame(width: int, height: int, bright: list[tuple[int, int, int, int]]) -> Frame:
    data = bytearray(width * height)
    for x0, y0, x1, y1 in bright:
        for y in range(y0, y1):
            data[y * width + x0:y * width + x1] = b"\xff" * (x1 - x0)
    return Frame(0, width, height, 1, bytes(data))


def test_the_centre_of_the_picture_is_on_the_axis_of_the_camera() -> None:
    [estimate] = estimate_poses([_detection(40, 30, 60, 50)], "pixel", 100, 80, _settings())
    assert estimate["position"] == {"x": 0.0, "y": 0.0, "z": 0.5}
    assert estimate["orientation"] == {"w": 1.0, "x": 0.0, "y": 0.0, "z": 0.0}
    assert estimate["detection_index"] == 0 and estimate["confidence"] == 0.9


def test_a_box_off_the_axis_lands_where_the_pinhole_puts_it() -> None:
    [estimate] = estimate_poses([_detection(80, 60, 100, 70)], "pixel", 100, 80, _settings())  # centre (90, 65)
    assert estimate["position"]["x"] == pytest.approx((90 - 50) * 0.5 / 100.0)
    assert estimate["position"]["y"] == pytest.approx((65 - 40) * 0.5 / 200.0)
    assert estimate["position"]["z"] == 0.5


def test_a_frame_of_another_size_is_scaled_to_the_picture_of_the_calibration() -> None:
    small = estimate_poses([_detection(40, 30, 60, 50)], "pixel", 100, 80, _settings())
    double = estimate_poses([_detection(80, 60, 120, 100)], "pixel", 200, 160, _settings())
    assert double[0]["position"] == pytest.approx(small[0]["position"])


def test_a_normalized_box_gives_the_same_pose_as_the_same_box_in_pixels() -> None:
    in_pixels = estimate_poses([_detection(80, 60, 100, 70)], "pixel", 100, 80, _settings())
    normalized = estimate_poses([_detection(0.8, 0.75, 1.0, 0.875)], "normalized", 100, 80, _settings())
    assert normalized[0]["position"] == pytest.approx(in_pixels[0]["position"])


def test_the_distance_scales_the_position_and_the_depth() -> None:
    near = estimate_poses([_detection(80, 60, 100, 70)], "pixel", 100, 80, _settings(distance_m=0.5))[0]["position"]
    far = estimate_poses([_detection(80, 60, 100, 70)], "pixel", 100, 80, _settings(distance_m=1.0))[0]["position"]
    assert far["z"] == 2 * near["z"] and far["x"] == pytest.approx(2 * near["x"]) and far["y"] == pytest.approx(2 * near["y"])


def test_a_weak_detection_and_a_centre_outside_the_picture_get_no_estimate_and_the_others_keep_their_index() -> None:
    detections = [_detection(40, 30, 60, 50, 0.4), _detection(40, 30, 60, 50, 0.5), _detection(150, 30, 170, 50), _detection(10, 10, 20, 20, 1.0)]
    estimates = estimate_poses(detections, "pixel", 100, 80, _settings())
    assert [item["detection_index"] for item in estimates] == [1, 3]


def test_every_estimate_is_a_valid_message_of_the_contract() -> None:
    detections = [_detection(0, 0, 10, 10), _detection(90, 70, 100, 80), _detection(45, 35, 55, 45, 0.6)]
    estimates = estimate_poses(detections, "pixel", 100, 80, _settings())
    message = {"schema_version": SCHEMA_VERSION, "frame_id": "f0", "capture_ts_utc": "2026-01-01T00:00:00.000Z", "estimates": estimates}
    validate_pose_estimate(message)
    assert all(math.isfinite(item["position"][axis]) for item in estimates for axis in "xyz")


@pytest.mark.parametrize("block", [
    "text", {"unknown": 1}, {"enabled": "yes"}, {"camera": "x"}, {"camera": {"zoom": 1}}, {"camera": {"fx": 0}}, {"camera": {"fy": -1}}, {"camera": {"fx": True}},
    {"camera": {"fx": float("nan")}}, {"camera": {"fx": 100001}}, {"camera": {"cx": -1}}, {"camera": {"cx": 641}}, {"camera": {"cy": 481}}, {"camera": {"width": 0}},
    {"camera": {"width": 1.5}}, {"camera": {"height": 65536}}, {"plane": "x"}, {"plane": {"height": 1}}, {"plane": {"distanceM": 0}}, {"plane": {"distanceM": 101}},
    {"minScore": -0.1}, {"minScore": 1.1}, {"minScore": "high"},
])
def test_a_bad_pose_block_is_refused(block: object) -> None:
    with pytest.raises(PoseError):
        parse_pose_config(block)


def test_the_block_is_off_unless_it_says_so_and_the_defaults_describe_a_camera() -> None:
    assert parse_pose_config(None) is None
    off = parse_pose_config({})
    assert off is not None and off.enabled is False and off.distance_m == 0.5 and off.min_score == 0.5
    assert off.camera.describe() == {"fx": 600.0, "fy": 600.0, "cx": 320.0, "cy": 240.0, "width": 640, "height": 480}
    on = parse_pose_config({"enabled": True, "camera": {"fx": 500, "width": 320, "height": 240, "cx": 160, "cy": 120}, "plane": {"distanceM": 0.25}, "minScore": 0.3})
    assert on is not None and on.enabled and on.camera.fx == 500.0 and on.distance_m == 0.25 and on.describe()["minScore"] == 0.3


def test_the_loopback_block_carries_the_pose_block_and_names_a_bad_one() -> None:
    config = parse_loopback_config({"enabled": True, "pose": {"enabled": True}})
    assert config is not None and config.pose_on and config.describe()["pose"]["enabled"] is True
    without = parse_loopback_config({"enabled": True})
    assert without is not None and not without.pose_on and without.describe()["pose"] is None
    off = parse_loopback_config({"enabled": True, "pose": {}})
    assert off is not None and not off.pose_on
    with pytest.raises(LoopbackError, match="pose.plane.distanceM"):
        parse_loopback_config({"enabled": True, "pose": {"plane": {"distanceM": 0}}})


def test_the_run_publishes_a_pose_next_to_each_detection_frame_when_the_stage_is_on() -> None:
    config = parse_loopback_config({"enabled": True, "detector": {"cell": 8}, "pose": {"enabled": True, "camera": {"fx": 16, "fy": 16, "cx": 8, "cy": 8, "width": 16, "height": 16}}})
    assert config is not None
    frame = _frame(16, 16, [(0, 0, 8, 8)])
    result = run_loopback(iter([(frame, "cam0-0", 1.0, "sess"), (_frame(16, 16, []), "cam0-1", 1.033, "sess")]), config, "cam0", 10)
    assert result["poses"] == 1
    first, second = result["messages"]
    check_estimates_match_detections(first["pose"], first["detections"])
    assert first["pose"]["session_id"] == "sess" and first["pose"]["frame_id"] == "cam0-0"
    [estimate] = first["pose"]["estimates"]
    assert estimate["position"]["x"] == pytest.approx((4 - 8) * 0.5 / 16) and estimate["position"]["z"] == 0.5
    assert second["pose"]["estimates"] == [] and second["detections"]["detections"] == []


def test_without_the_stage_the_messages_and_the_summary_are_as_they_were() -> None:
    for block in ({"enabled": True}, {"enabled": True, "pose": {"enabled": False}}):
        config = parse_loopback_config(block)
        assert config is not None
        result = run_loopback(iter([(_frame(16, 16, [(0, 0, 8, 8)]), "f0", 1.0, None)]), config, "cam0", 1)
        assert "poses" not in result and set(result["messages"][0]) == {"meta", "detections"}


def test_the_route_answers_the_pose_of_each_frame_and_describes_the_stage(tmp_path: Path) -> None:
    config = {**CONFIG, "loopback": {**CONFIG["loopback"], "pose": {"enabled": True, "camera": {"fx": 16, "fy": 16, "cx": 8, "cy": 4, "width": 16, "height": 8}}}}
    for url in _serving(tmp_path, config):
        status, described = _call(url + "/loopback")
        assert status == 200 and described["pose"]["enabled"] is True and described["pose"]["camera"]["fx"] == 16.0
        status, result = _call(url + "/loopback/run", {"source": "cam0", "frames": 3})
        assert status == 200 and result["frames"] == 3 and result["poses"] == sum(len(m["pose"]["estimates"]) for m in result["messages"])
        for message in result["messages"]:
            check_estimates_match_detections(message["pose"], message["detections"])
