# =============================================================================
# HYDRA-UMC-VISION-NODE - tests/test_models.py
# Copyright (C) 2026 JuanenRac (Electro Hobby 3D) <electrohobby3d@gmail.com>
# GPL-3.0-or-later - see LICENSE
# =============================================================================
from __future__ import annotations

import random

import pytest

from hydra_umc_vision_node.models import (
    DEVICES,
    FORMATS,
    PRECISIONS,
    Constraints,
    HostCapabilities,
    ModelError,
    ModelSpec,
    evaluate,
    parse_constraints,
    parse_host,
    parse_model,
    parse_models,
)


def _model(name: str, device: str = "cpu", size: int = 320, precision: str = "fp32", fmt: str = "onnx") -> ModelSpec:
    return ModelSpec(name, fmt, size, size, precision, device)


CPU_ONLY = HostCapabilities(("cpu",), FORMATS, PRECISIONS)
HAILO_FIRST = HostCapabilities(("hailo", "cpu"), FORMATS, ("int8", "fp16", "fp32"))


def _status(result: dict) -> dict[str, str]:
    return {d["model"]: d["status"] for d in result["decisions"]}


def test_a_model_for_a_missing_device_is_discarded_with_its_reason() -> None:
    result = evaluate((_model("fast", "hailo", fmt="hef", precision="int8"), _model("slow")), CPU_ONLY)
    assert result["selected"] == "slow"
    assert _status(result) == {"fast": "discarded", "slow": "selected"}
    assert "hailo is not available" in result["decisions"][0]["reason"]


def test_nothing_fits_selects_nothing() -> None:
    result = evaluate((_model("fast", "hailo"),), HostCapabilities((), FORMATS, PRECISIONS))
    assert result["selected"] is None and _status(result) == {"fast": "discarded"}
    assert evaluate((), CPU_ONLY) == {"selected": None, "decisions": []}


def test_preference_order_device_then_size_then_precision_then_name() -> None:
    models = (_model("a", "cpu", 640), _model("b", "hailo", 320, "int8"), _model("c", "hailo", 320, "fp16"), _model("d", "hailo", 320, "int8"))
    result = evaluate(models, HAILO_FIRST)
    assert result["selected"] == "b"
    reasons = {d["model"]: d["reason"] for d in result["decisions"]}
    assert "device of b" in reasons["a"]
    assert "precision of b" in reasons["c"]
    assert "by name" in reasons["d"]
    big = evaluate((_model("small", "hailo", 320), _model("big", "hailo", 640)), HAILO_FIRST)
    assert big["selected"] == "big" and "larger input" in big["decisions"][1]["reason"]


def test_constraints_discard_with_a_reason() -> None:
    models = (_model("small", size=320, precision="int8"), _model("big", size=640))
    wish = parse_constraints({"maxInputPixels": 320 * 320})
    assert evaluate(models, CPU_ONLY, wish)["selected"] == "small"
    assert evaluate(models, CPU_ONLY, parse_constraints({"precisions": ["fp32"]}))["selected"] == "big"
    assert evaluate(models, CPU_ONLY, parse_constraints({"minInputWidth": 700}))["selected"] is None
    assert evaluate(models, CPU_ONLY, parse_constraints({"devices": ["hailo"]}))["selected"] is None
    assert evaluate(models, CPU_ONLY, parse_constraints({"formats": ["hef"]}))["selected"] is None
    assert evaluate(models, CPU_ONLY, parse_constraints({"minInputHeight": 700}))["selected"] is None


@pytest.mark.parametrize("seed", range(20))
def test_selection_is_deterministic_and_never_picks_an_unavailable_device(seed: int) -> None:
    rng = random.Random(1000 + seed)
    models = tuple(
        ModelSpec(f"m{i}", rng.choice(FORMATS), rng.choice((64, 160, 320, 640)), rng.choice((64, 160, 320, 640)),
                  rng.choice(PRECISIONS), rng.choice(DEVICES))
        for i in range(rng.randint(0, 12))
    )
    devices = tuple(rng.sample(DEVICES, rng.randint(0, 2)))
    host = HostCapabilities(devices, tuple(rng.sample(FORMATS, rng.randint(1, 4))), tuple(rng.sample(PRECISIONS, rng.randint(1, 3))))
    wish = Constraints(max_input_pixels=rng.choice((None, 100_000, 400_000)))
    first = evaluate(models, host, wish)
    for _ in range(5):
        shuffled = list(models)
        rng.shuffle(shuffled)
        assert evaluate(tuple(shuffled), host, wish) == first
    by_name = {m.name: m for m in models}
    if first["selected"] is not None:
        chosen = by_name[first["selected"]]
        assert chosen.device in host.devices and chosen.format in host.formats and chosen.precision in host.precisions
    else:
        assert all(d["status"] == "discarded" for d in first["decisions"])
    assert len(first["decisions"]) == len(models)
    assert sum(d["status"] == "selected" for d in first["decisions"]) == (first["selected"] is not None)


def test_parsers_accept_the_documented_shape() -> None:
    model = parse_model({"name": "m", "format": "hef", "inputWidth": 640, "inputHeight": 640, "precision": "int8", "device": "hailo", "simLatencyMs": 4})
    assert model.sim_latency_ms == 4.0 and model.describe()["device"] == "hailo"
    assert parse_host({"devices": ["cpu"]}).formats == FORMATS
    assert parse_host({}).devices == ()
    assert len(parse_models([model.describe()])) == 1


@pytest.mark.parametrize(
    "bad",
    [
        {"name": "m"},
        {"name": "bad name", "format": "onnx", "inputWidth": 1, "inputHeight": 1, "precision": "fp32", "device": "cpu"},
        {"name": "m", "format": "pt", "inputWidth": 1, "inputHeight": 1, "precision": "fp32", "device": "cpu"},
        {"name": "m", "format": "onnx", "inputWidth": 0, "inputHeight": 1, "precision": "fp32", "device": "cpu"},
        {"name": "m", "format": "onnx", "inputWidth": True, "inputHeight": 1, "precision": "fp32", "device": "cpu"},
        {"name": "m", "format": "onnx", "inputWidth": 1, "inputHeight": 1, "precision": "fp32", "device": "tpu"},
        {"name": "m", "format": "onnx", "inputWidth": 1, "inputHeight": 1, "precision": "fp32", "device": "cpu", "simLatencyMs": -1},
        {"name": "m", "format": "onnx", "inputWidth": 1, "inputHeight": 1, "precision": "fp32", "device": "cpu", "extra": 1},
        [],
    ],
)
def test_refuses_malformed_models(bad: object) -> None:
    with pytest.raises(ModelError):
        parse_model(bad)


def test_refuses_malformed_lists_hosts_and_constraints() -> None:
    one = {"name": "m", "format": "onnx", "inputWidth": 1, "inputHeight": 1, "precision": "fp32", "device": "cpu"}
    with pytest.raises(ModelError):
        parse_models([one, one])
    with pytest.raises(ModelError):
        parse_models([{**one, "name": f"m{i}"} for i in range(65)])
    with pytest.raises(ModelError):
        parse_models("x")
    for host in ({"devices": ["cpu", "cpu"]}, {"devices": ["tpu"]}, {"formats": []}, {"x": 1}, "cpu"):
        with pytest.raises(ModelError):
            parse_host(host)
    for wish in ({"devices": "cpu"}, {"maxInputPixels": 0}, {"maxInputPixels": True}, {"minInputWidth": 1.5}, {"x": 1}, 3):
        with pytest.raises(ModelError):
            parse_constraints(wish)
    assert parse_constraints(None) == Constraints()
