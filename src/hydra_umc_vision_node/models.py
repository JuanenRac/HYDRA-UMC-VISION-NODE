# =============================================================================
# HYDRA-UMC-VISION-NODE - src/hydra_umc_vision_node/models.py
# Copyright (C) 2026 JuanenRac (Electro Hobby 3D) <electrohobby3d@gmail.com>
# GPL-3.0-or-later - see LICENSE
# =============================================================================
"""Registry of models with declared capabilities and the choice of the best one for a host.

Nothing is detected: the devices a host offers come from its configuration. The choice is a pure function of the models,
the host and the constraints, so the same input always gives the same answer, in whatever order the models were listed,
and a model whose device the host does not offer is never chosen. Every model gets a reason: chosen, outranked or discarded.

Order of preference among the models that fit: the device the host lists first, then the larger input, then the precision
the host lists first, then the name.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

FORMATS = ("onnx", "hef", "tflite", "sim")
PRECISIONS = ("fp32", "fp16", "int8")
DEVICES = ("cpu", "hailo")
MAX_MODELS = 64
MAX_INPUT_DIMENSION = 8192
NAME = re.compile(r"[A-Za-z0-9_.:-]{1,64}")


class ModelError(ValueError):
    """A model, a host or a constraint cannot be used."""


@dataclass(frozen=True)
class ModelSpec:
    name: str
    format: str
    input_width: int
    input_height: int
    precision: str
    device: str
    sim_latency_ms: float = 10.0

    @property
    def pixels(self) -> int:
        return self.input_width * self.input_height

    def describe(self) -> dict:
        return {"name": self.name, "format": self.format, "inputWidth": self.input_width, "inputHeight": self.input_height,
                "precision": self.precision, "device": self.device, "simLatencyMs": self.sim_latency_ms}


@dataclass(frozen=True)
class HostCapabilities:
    """What the host declares, in order of preference (first is preferred)."""

    devices: tuple[str, ...]
    formats: tuple[str, ...]
    precisions: tuple[str, ...]

    def describe(self) -> dict:
        return {"devices": list(self.devices), "formats": list(self.formats), "precisions": list(self.precisions)}


def _choice_list(value: object, allowed: tuple[str, ...], what: str, *, empty_ok: bool = False) -> tuple[str, ...]:
    if not isinstance(value, list) or (not value and not empty_ok):
        raise ModelError(f"{what} must be a non-empty list")
    out: list[str] = []
    for item in value:
        if not isinstance(item, str) or item not in allowed:
            raise ModelError(f"{what}: each item must be one of {list(allowed)}")
        if item in out:
            raise ModelError(f"{what}: {item} is repeated")
        out.append(item)
    return tuple(out)


def parse_host(config: object) -> HostCapabilities:
    if not isinstance(config, dict) or set(config) - {"devices", "formats", "precisions"}:
        raise ModelError("host must be an object with devices, formats and precisions")
    # an empty device list is a valid host: nothing can run, and every model is discarded with its reason
    devices = _choice_list(config.get("devices", []), DEVICES, "host.devices", empty_ok=True)
    formats = _choice_list(config.get("formats", list(FORMATS)), FORMATS, "host.formats")
    precisions = _choice_list(config.get("precisions", list(PRECISIONS)), PRECISIONS, "host.precisions")
    return HostCapabilities(devices, formats, precisions)


def parse_model(config: object) -> ModelSpec:
    keys = {"name", "format", "inputWidth", "inputHeight", "precision", "device", "simLatencyMs"}
    if not isinstance(config, dict) or set(config) - keys:
        raise ModelError(f"a model is an object with the keys {sorted(keys)}")
    name = config.get("name")
    if not isinstance(name, str) or not NAME.fullmatch(name):
        raise ModelError("a model name is 1 to 64 characters of letters, digits and _ . : -")
    for key, allowed in (("format", FORMATS), ("precision", PRECISIONS), ("device", DEVICES)):
        if config.get(key) not in allowed:
            raise ModelError(f"model {name}: {key} must be one of {list(allowed)}")
    dims = []
    for key in ("inputWidth", "inputHeight"):
        value = config.get(key)
        if isinstance(value, bool) or not isinstance(value, int) or not 1 <= value <= MAX_INPUT_DIMENSION:
            raise ModelError(f"model {name}: {key} must be an integer from 1 to {MAX_INPUT_DIMENSION}")
        dims.append(value)
    latency = config.get("simLatencyMs", 10.0)
    if isinstance(latency, bool) or not isinstance(latency, (int, float)) or not 0.0 <= latency <= 10_000.0:
        raise ModelError(f"model {name}: simLatencyMs must be a number from 0 to 10000")
    return ModelSpec(name, config["format"], dims[0], dims[1], config["precision"], config["device"], float(latency))


def parse_models(config: object) -> tuple[ModelSpec, ...]:
    if not isinstance(config, list) or len(config) > MAX_MODELS:
        raise ModelError(f"models must be a list of at most {MAX_MODELS} models")
    models = tuple(parse_model(item) for item in config)
    names = [m.name for m in models]
    if len(set(names)) != len(names):
        raise ModelError("model names must be unique")
    return models


@dataclass(frozen=True)
class Constraints:
    devices: tuple[str, ...] | None = None
    formats: tuple[str, ...] | None = None
    precisions: tuple[str, ...] | None = None
    max_input_pixels: int | None = None
    min_input_width: int | None = None
    min_input_height: int | None = None


def parse_constraints(config: object) -> Constraints:
    if config is None:
        return Constraints()
    keys = {"devices", "formats", "precisions", "maxInputPixels", "minInputWidth", "minInputHeight"}
    if not isinstance(config, dict) or set(config) - keys:
        raise ModelError(f"constraints are an object with the keys {sorted(keys)}")
    ints: dict[str, int | None] = {}
    for key in ("maxInputPixels", "minInputWidth", "minInputHeight"):
        value = config.get(key)
        if value is not None and (isinstance(value, bool) or not isinstance(value, int) or not 1 <= value <= MAX_INPUT_DIMENSION**2):
            raise ModelError(f"{key} must be a positive integer")
        ints[key] = value
    return Constraints(
        devices=_choice_list(config["devices"], DEVICES, "devices") if "devices" in config else None,
        formats=_choice_list(config["formats"], FORMATS, "formats") if "formats" in config else None,
        precisions=_choice_list(config["precisions"], PRECISIONS, "precisions") if "precisions" in config else None,
        max_input_pixels=ints["maxInputPixels"], min_input_width=ints["minInputWidth"], min_input_height=ints["minInputHeight"],
    )


def _misfit(model: ModelSpec, host: HostCapabilities, wish: Constraints) -> str | None:
    """The first reason this model cannot be used, or None."""
    if model.device not in host.devices:
        return f"device {model.device} is not available on this host (available: {', '.join(host.devices) or 'none'})"
    if model.format not in host.formats:
        return f"format {model.format} is not supported by this host"
    if model.precision not in host.precisions:
        return f"precision {model.precision} is not supported by this host"
    if wish.devices is not None and model.device not in wish.devices:
        return f"device {model.device} is not among the requested devices"
    if wish.formats is not None and model.format not in wish.formats:
        return f"format {model.format} is not among the requested formats"
    if wish.precisions is not None and model.precision not in wish.precisions:
        return f"precision {model.precision} is not among the requested precisions"
    if wish.max_input_pixels is not None and model.pixels > wish.max_input_pixels:
        return f"input of {model.pixels} pixels is above the limit of {wish.max_input_pixels}"
    if wish.min_input_width is not None and model.input_width < wish.min_input_width:
        return f"input width {model.input_width} is below the minimum {wish.min_input_width}"
    if wish.min_input_height is not None and model.input_height < wish.min_input_height:
        return f"input height {model.input_height} is below the minimum {wish.min_input_height}"
    return None


def _rank(model: ModelSpec, host: HostCapabilities) -> tuple[int, int, int, str]:
    return (host.devices.index(model.device), -model.pixels, host.precisions.index(model.precision), model.name)


_RANK_REASONS = (
    "the host prefers the device of {winner}",
    "{winner} has a larger input",
    "the host prefers the precision of {winner}",
    "{winner} comes first by name",
)


def evaluate(models: tuple[ModelSpec, ...], host: HostCapabilities, wish: Constraints | None = None) -> dict:
    """{"selected": name or None, "decisions": [...]}: one decision per model, ordered by name."""
    wish = wish or Constraints()
    fits: list[ModelSpec] = []
    decisions: dict[str, dict] = {}
    for model in models:
        problem = _misfit(model, host, wish)
        if problem:
            decisions[model.name] = {"model": model.name, "status": "discarded", "reason": problem}
        else:
            fits.append(model)
    fits.sort(key=lambda m: _rank(m, host))
    for position, model in enumerate(fits):
        if position == 0:
            decisions[model.name] = {"model": model.name, "status": "selected", "reason": "best of the models that fit"}
        else:
            winner = fits[0]
            first = next(i for i, (a, b) in enumerate(zip(_rank(model, host), _rank(winner, host), strict=True)) if a != b)
            decisions[model.name] = {"model": model.name, "status": "outranked",
                                     "reason": "outranked: " + _RANK_REASONS[first].format(winner=winner.name)}
    return {"selected": fits[0].name if fits else None, "decisions": [decisions[name] for name in sorted(decisions)]}
