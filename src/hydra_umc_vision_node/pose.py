# =============================================================================
# HYDRA-UMC-VISION-NODE - src/hydra_umc_vision_node/pose.py
# Copyright (C) 2026 JuanenRac (Electro Hobby 3D) <electrohobby3d@gmail.com>
# GPL-3.0-or-later - see LICENSE
# =============================================================================
"""The source of data of `PoseEstimate`: where, in meters, the camera sees the detections of a frame, without a depth sensor.

This is a model of a camera that looks straight down at a flat work surface at a known distance (the `pose` block of the loopback configuration holds the
intrinsics of the camera and that distance). A detection is a box in pixels; the centre of the box is a ray through the pinhole camera, and the ray meets the
work surface at one point, which is the position in meters in the camera frame (x to the right, y down, z along the axis of the camera). A box whose centre
lies outside the picture, or whose score is under the minimum of the configuration, gets no estimate: a pose is never made up. The orientation is the identity
quaternion, because a box carries no orientation; the field is there because the contract requires it, and a consumer that needs the turn of the object has to
wait for a model that measures it.

Nothing here reads a camera or a depth sensor: it is the simulation of the pose stage of the pipeline, in the data format of the contract, so that the stage
after the detector (SAFETY-ZONES, VISUAL-SERVOING) can be tried with data that has the real shape. It ships off (`pose.enabled` is false unless the file says true).
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any

from .contract import MAX_DETECTIONS, box_to_pixels

_KEYS = {"enabled", "camera", "plane", "minScore"}
_CAMERA_KEYS = {"fx", "fy", "cx", "cy", "width", "height"}
_PLANE_KEYS = {"distanceM"}
MAX_DISTANCE_M = 100.0
MAX_FOCAL_PX = 100000.0
MAX_CALIBRATION_PX = 65535
DEFAULT_CAMERA = {"fx": 600.0, "fy": 600.0, "cx": 320.0, "cy": 240.0, "width": 640, "height": 480}
DEFAULT_DISTANCE_M = 0.5


class PoseError(ValueError):
    """The configuration of the pose stage cannot be used."""


@dataclass(frozen=True)
class CameraModel:
    """A pinhole camera: focal lengths and centre of the picture in pixels of the picture the calibration was made on (`width` x `height`)."""

    fx: float
    fy: float
    cx: float
    cy: float
    width: int
    height: int

    def describe(self) -> dict[str, Any]:
        return {"fx": self.fx, "fy": self.fy, "cx": self.cx, "cy": self.cy, "width": self.width, "height": self.height}


@dataclass(frozen=True)
class PoseSettings:
    enabled: bool = False
    camera: CameraModel = CameraModel(**DEFAULT_CAMERA)  # type: ignore[arg-type]
    distance_m: float = DEFAULT_DISTANCE_M
    min_score: float = 0.5

    def describe(self) -> dict[str, Any]:
        return {"enabled": self.enabled, "camera": self.camera.describe(), "plane": {"distanceM": self.distance_m}, "minScore": self.min_score}


def _number(block: dict, key: str, default: float, low: float, high: float, label: str, *, open_low: bool = False) -> float:
    value = block.get(key, default)
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or value > high or (value <= low if open_low else value < low):
        bound = f"above {low:g}" if open_low else f"from {low:g}"
        raise PoseError(f"{label}.{key} must be a number {bound} and at most {high:g}")
    return float(value)


def _whole(block: dict, key: str, default: int, low: int, high: int, label: str) -> int:
    value = block.get(key, default)
    if isinstance(value, bool) or not isinstance(value, int) or not low <= value <= high:
        raise PoseError(f"{label}.{key} must be an integer from {low} to {high}")
    return value


def parse_pose_config(config: object) -> PoseSettings | None:
    """The optional `pose` block of the loopback configuration. None when absent; `enabled` is false unless the block says true."""
    if config is None:
        return None
    if not isinstance(config, dict) or set(config) - _KEYS:
        raise PoseError(f"pose is an object with the keys {sorted(_KEYS)}")
    enabled = config.get("enabled", False)
    if not isinstance(enabled, bool):
        raise PoseError("pose.enabled must be true or false")
    camera = config.get("camera", {})
    if not isinstance(camera, dict) or set(camera) - _CAMERA_KEYS:
        raise PoseError(f"pose.camera is an object with the keys {sorted(_CAMERA_KEYS)}")
    width = _whole(camera, "width", DEFAULT_CAMERA["width"], 1, MAX_CALIBRATION_PX, "pose.camera")  # type: ignore[arg-type]
    height = _whole(camera, "height", DEFAULT_CAMERA["height"], 1, MAX_CALIBRATION_PX, "pose.camera")  # type: ignore[arg-type]
    model = CameraModel(
        fx=_number(camera, "fx", DEFAULT_CAMERA["fx"], 0, MAX_FOCAL_PX, "pose.camera", open_low=True),  # type: ignore[arg-type]
        fy=_number(camera, "fy", DEFAULT_CAMERA["fy"], 0, MAX_FOCAL_PX, "pose.camera", open_low=True),  # type: ignore[arg-type]
        cx=_number(camera, "cx", DEFAULT_CAMERA["cx"], 0, width, "pose.camera"),  # type: ignore[arg-type]
        cy=_number(camera, "cy", DEFAULT_CAMERA["cy"], 0, height, "pose.camera"),  # type: ignore[arg-type]
        width=width,
        height=height,
    )
    plane = config.get("plane", {})
    if not isinstance(plane, dict) or set(plane) - _PLANE_KEYS:
        raise PoseError(f"pose.plane is an object with the keys {sorted(_PLANE_KEYS)}")
    distance = _number(plane, "distanceM", DEFAULT_DISTANCE_M, 0, MAX_DISTANCE_M, "pose.plane", open_low=True)
    min_score = _number(config, "minScore", 0.5, 0, 1, "pose")
    return PoseSettings(enabled, model, distance, min_score)


def estimate_poses(detections: list[dict[str, Any]], box_unit: str, width: int, height: int, settings: PoseSettings) -> list[dict[str, Any]]:
    """The estimates of the detections of one frame of `width` x `height` pixels, in the order of the detections, for those that have one.

    The centre of the box, in the pixels of the frame, is scaled to the picture the camera was calibrated on, turned into a ray with the intrinsics, and
    taken to the work surface: `x = (u - cx) * d / fx`, `y = (v - cy) * d / fy`, `z = d`. The confidence is the score of the detection."""
    camera = settings.camera
    sx, sy = camera.width / width, camera.height / height
    estimates: list[dict[str, Any]] = []
    for index, item in enumerate(detections[:MAX_DETECTIONS]):
        if item["score"] < settings.min_score:
            continue
        box = box_to_pixels(item["box"], box_unit, width, height)
        u = (box["x_min"] + box["x_max"]) / 2.0
        v = (box["y_min"] + box["y_max"]) / 2.0
        if not (0.0 <= u <= width and 0.0 <= v <= height):
            continue
        position = {
            "x": (u * sx - camera.cx) * settings.distance_m / camera.fx,
            "y": (v * sy - camera.cy) * settings.distance_m / camera.fy,
            "z": settings.distance_m,
        }
        estimates.append({"detection_index": index, "position": position, "orientation": {"w": 1.0, "x": 0.0, "y": 0.0, "z": 0.0}, "confidence": item["score"]})
    return estimates
