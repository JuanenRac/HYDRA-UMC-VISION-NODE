# =============================================================================
# HYDRA-UMC-VISION-NODE - src/hydra_umc_vision_node/contract.py
# Copyright (C) 2026 JuanenRac (Electro Hobby 3D) <electrohobby3d@gmail.com>
# GPL-3.0-or-later - see LICENSE
# =============================================================================
"""The data contract of the vision pipeline, as this node writes it: `FrameMeta` (which frame it is) and `DetectionFrame` (what a model found in it).

The contract is the one the SDK publishes as `frame-meta` and `detection-frame` (JSON Schema v1, closed): ids of 1 to 64 safe characters, the instant
the frame was captured as an RFC 3339 date-time, boxes by their corners `x_min`, `y_min`, `x_max`, `y_max` with the origin at the top left corner of the
frame, in `pixel` or `normalized` (0 to 1) units, and the identity of the model as the five fields of `CompatibilityRecord`. This module checks the same
rules without depending on the SDK, so the node keeps running with the standard library only; a test compares the constants with the SDK checkout when it is
next to this one.
"""
from __future__ import annotations

import math
import re
from datetime import datetime, timezone
from typing import Any

SCHEMA_VERSION = "1.0"
PIXEL_FORMATS: tuple[str, ...] = ("RGB", "BGR", "RGBA", "BGRA", "GRAY", "YUYV", "NV12", "JPEG")
BOX_UNITS: tuple[str, ...] = ("pixel", "normalized")
MAX_DIMENSION = 8192
MAX_DETECTIONS = 1000
BOX_FIELDS: tuple[str, ...] = ("x_min", "y_min", "x_max", "y_max")
MODEL_FIELDS: tuple[str, ...] = ("model_name", "model_version", "model_sha256", "hailo_arch", "pipeline_version")

_IDENTIFIER = re.compile(r"[A-Za-z0-9._:-]{1,64}")
_SHA256 = re.compile(r"[0-9a-f]{64}")
_DATE_TIME = re.compile(r"^\d{4}-\d{2}-\d{2}[Tt]\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:[Zz]|[+-]\d{2}:\d{2})$")
_FRAME_REQUIRED = ("schema_version", "frame_id", "capture_ts_utc", "width", "height", "pixel_format")
_FRAME_OPTIONAL = ("session_id", "source_id", "stride", "sequence")
_DETECTION_REQUIRED = ("schema_version", "frame_id", "capture_ts_utc", "box_unit", "detections")
_DETECTION_OPTIONAL = ("session_id", "model")
_ITEM_REQUIRED = ("class_id", "label", "score", "box")


class ContractError(ValueError):
    """A message does not follow the pipeline contract."""


def _fields(payload: Any, required: tuple[str, ...], optional: tuple[str, ...], label: str) -> None:
    if not isinstance(payload, dict):
        raise ContractError(f"{label} must be an object")
    for name in required:
        if name not in payload:
            raise ContractError(f"{label}: missing required field: {name}")
    unknown = sorted(set(payload) - set(required) - set(optional))
    if unknown:
        raise ContractError(f"{label}: unsupported field(s): {', '.join(unknown)}")
    if "schema_version" in payload and payload["schema_version"] != SCHEMA_VERSION:
        raise ContractError(f"{label}: schema_version must be '{SCHEMA_VERSION}'")


def _identifier(payload: dict, name: str) -> None:
    value = payload[name]
    if not (isinstance(value, str) and _IDENTIFIER.fullmatch(value)):
        raise ContractError(f"{name} must be 1 to 64 letters, digits, dots, colons, dashes or underscores")


def _date_time(payload: dict, name: str) -> None:
    value = payload[name]
    if not (isinstance(value, str) and _DATE_TIME.fullmatch(value)):
        raise ContractError(f"{name} must be an RFC 3339 date-time")
    try:
        datetime.fromisoformat(value.upper().replace("Z", "+00:00"))
    except ValueError:
        raise ContractError(f"{name} must be a valid RFC 3339 date-time") from None


def _number(payload: dict, name: str, low: float | None = None, high: float | None = None) -> None:
    value = payload[name]
    if type(value) not in (int, float) or not math.isfinite(value):
        raise ContractError(f"{name} must be a finite number")
    if low is not None and value < low:
        raise ContractError(f"{name} must be at least {low}")
    if high is not None and value > high:
        raise ContractError(f"{name} must be at most {high}")


def _whole(payload: dict, name: str, low: int, high: int | None = None) -> None:
    value = payload[name]
    if type(value) is not int or value < low or (high is not None and value > high):
        raise ContractError(f"{name} must be an integer from {low}" + (f" to {high}" if high is not None else ""))


def iso_utc(epoch_seconds: float) -> str:
    """RFC 3339 text (UTC, milliseconds) of an instant given in seconds since the epoch."""
    return datetime.fromtimestamp(epoch_seconds, tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"


def validate_frame_meta(payload: Any) -> None:
    """The checks of the FrameMeta contract."""
    _fields(payload, _FRAME_REQUIRED, _FRAME_OPTIONAL, "FrameMeta")
    for name in ("frame_id", "session_id", "source_id"):
        if name in payload:
            _identifier(payload, name)
    _date_time(payload, "capture_ts_utc")
    _whole(payload, "width", 1, MAX_DIMENSION)
    _whole(payload, "height", 1, MAX_DIMENSION)
    if payload["pixel_format"] not in PIXEL_FORMATS:
        raise ContractError(f"pixel_format must be one of {', '.join(PIXEL_FORMATS)}")
    if "stride" in payload:
        _whole(payload, "stride", 1)
    if "sequence" in payload:
        _whole(payload, "sequence", 0)


def _check_model(model: Any) -> None:
    _fields(model, MODEL_FIELDS, (), "model")
    for name in ("model_name", "model_version", "hailo_arch", "pipeline_version"):
        value = model[name]
        if not isinstance(value, str) or not 1 <= len(value) <= 64:
            raise ContractError(f"{name} must be a text of 1 to 64 characters")
    if not (isinstance(model["model_sha256"], str) and _SHA256.fullmatch(model["model_sha256"])):
        raise ContractError("model_sha256 must be 64 lowercase hexadecimal digits")


def _check_detection(item: Any, box_unit: str, index: int) -> None:
    label = f"detections[{index}]"
    _fields(item, _ITEM_REQUIRED, (), label)
    _whole(item, "class_id", 0)
    if not isinstance(item["label"], str) or not 1 <= len(item["label"]) <= 64:
        raise ContractError(f"{label}.label must be a text of 1 to 64 characters")
    _number(item, "score", 0, 1)
    box = item["box"]
    _fields(box, BOX_FIELDS, (), f"{label}.box")
    for name in BOX_FIELDS:
        _number(box, name, 0, 1 if box_unit == "normalized" else MAX_DIMENSION)
    if box["x_min"] > box["x_max"] or box["y_min"] > box["y_max"]:
        raise ContractError(f"{label}.box must have x_min <= x_max and y_min <= y_max")


def validate_detection_frame(payload: Any) -> None:
    """The checks of the DetectionFrame contract."""
    _fields(payload, _DETECTION_REQUIRED, _DETECTION_OPTIONAL, "DetectionFrame")
    _identifier(payload, "frame_id")
    if "session_id" in payload:
        _identifier(payload, "session_id")
    _date_time(payload, "capture_ts_utc")
    if payload["box_unit"] not in BOX_UNITS:
        raise ContractError(f"box_unit must be one of {', '.join(BOX_UNITS)}")
    detections = payload["detections"]
    if not isinstance(detections, list) or len(detections) > MAX_DETECTIONS:
        raise ContractError(f"detections must be an array of at most {MAX_DETECTIONS} detections")
    for index, item in enumerate(detections):
        _check_detection(item, payload["box_unit"], index)
    if "model" in payload:
        _check_model(payload["model"])


def box_to_pixels(box: dict[str, float], box_unit: str, width: int, height: int) -> dict[str, float]:
    """The corners of a box in pixels of a frame of `width` x `height`, whatever the unit it came in."""
    _fields(box, BOX_FIELDS, (), "box")
    if box_unit == "pixel":
        return {name: float(box[name]) for name in BOX_FIELDS}
    if box_unit != "normalized":
        raise ContractError(f"box_unit must be one of {', '.join(BOX_UNITS)}")
    return {"x_min": box["x_min"] * width, "y_min": box["y_min"] * height, "x_max": box["x_max"] * width, "y_max": box["y_max"] * height}


def build_frame_meta(frame_id: str, capture_ts_utc: str, width: int, height: int, pixel_format: str, **optional: Any) -> dict[str, Any]:
    """A validated FrameMeta message (`session_id`, `source_id`, `stride` and `sequence` are the optional fields)."""
    message = {"schema_version": SCHEMA_VERSION, "frame_id": frame_id, "capture_ts_utc": capture_ts_utc, "width": width, "height": height,
               "pixel_format": pixel_format, **{k: v for k, v in optional.items() if v is not None}}
    validate_frame_meta(message)
    return message


def build_detection_frame(frame_id: str, capture_ts_utc: str, box_unit: str, detections: list[dict[str, Any]], *,
                          model: dict[str, str] | None = None, session_id: str | None = None) -> dict[str, Any]:
    """A validated DetectionFrame message."""
    message: dict[str, Any] = {"schema_version": SCHEMA_VERSION, "frame_id": frame_id, "capture_ts_utc": capture_ts_utc, "box_unit": box_unit,
                               "detections": detections}
    if model is not None:
        message["model"] = model
    if session_id is not None:
        message["session_id"] = session_id
    validate_detection_frame(message)
    return message
