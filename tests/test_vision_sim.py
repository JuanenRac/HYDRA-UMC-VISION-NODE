# =============================================================================
# HYDRA-UMC-VISION-NODE - tests/test_vision_sim.py
# Copyright (C) 2026 JuanenRac (Electro Hobby 3D) <electrohobby3d@gmail.com>
# GPL-3.0-or-later - see LICENSE
# =============================================================================
from __future__ import annotations

import json
import threading
import urllib.error
import urllib.request
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

from hydra_umc_vision_node.api import VisionNodeServer
from hydra_umc_vision_node.latency import LatencyRecorder
from hydra_umc_vision_node.main import main
from hydra_umc_vision_node.vision_sim import VisionConfigError, load_vision_config, parse_vision_config

CONFIG: dict[str, Any] = {
    "host": {"devices": ["cpu"], "precisions": ["fp32", "int8"]},
    "models": [
        {"name": "hailo-yolo", "format": "hef", "inputWidth": 640, "inputHeight": 640, "precision": "int8", "device": "hailo", "simLatencyMs": 4},
        {"name": "cpu-small", "format": "onnx", "inputWidth": 320, "inputHeight": 320, "precision": "fp32", "device": "cpu", "simLatencyMs": 40},
        {"name": "cpu-tiny", "format": "onnx", "inputWidth": 160, "inputHeight": 160, "precision": "int8", "device": "cpu", "simLatencyMs": 15},
    ],
    "sources": [{"name": "cam0", "kind": "synthetic", "width": 16, "height": 8, "channels": 3, "seed": 3, "frames": 20}],
}


def _call(url: str, body: object = None) -> tuple[int, dict]:
    data = json.dumps(body).encode() if body is not None else None
    request = urllib.request.Request(url, data=data, method="POST" if body is not None else "GET")
    try:
        with urllib.request.urlopen(request, timeout=5) as response:
            return response.status, json.loads(response.read())
    except urllib.error.HTTPError as error:
        return error.code, json.loads(error.read())


def _serving(tmp_path: Path, **kwargs: object) -> Iterator[str]:
    server = VisionNodeServer(("127.0.0.1", 0), tmp_path, **kwargs)  # type: ignore[arg-type]
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{server.server_address[1]}"
    server.shutdown()
    server.server_close()
    thread.join(timeout=2)


@pytest.fixture
def base(tmp_path: Path) -> Iterator[str]:
    yield from _serving(tmp_path, vision=parse_vision_config(CONFIG, tmp_path), latency=LatencyRecorder(64))


@pytest.fixture
def base_no_latency(tmp_path: Path) -> Iterator[str]:
    yield from _serving(tmp_path, vision=parse_vision_config(CONFIG, tmp_path))


def test_models_and_sources_are_listed(base: str) -> None:
    status, body = _call(base + "/models")
    assert status == 200 and body["simulated"] is True
    assert [m["name"] for m in body["models"]] == ["hailo-yolo", "cpu-small", "cpu-tiny"]
    assert body["host"]["devices"] == ["cpu"]
    status, body = _call(base + "/sources")
    assert status == 200 and body["sources"][0]["name"] == "cam0" and body["sources"][0]["simulated"] is True


def test_select_gives_the_reason_of_every_model(base: str) -> None:
    status, body = _call(base + "/models/select", {})
    assert status == 200 and body["selected"] == "cpu-small"
    reasons = {d["model"]: d for d in body["decisions"]}
    assert reasons["hailo-yolo"]["status"] == "discarded" and "hailo is not available" in reasons["hailo-yolo"]["reason"]
    assert reasons["cpu-tiny"]["status"] == "outranked"
    status, body = _call(base + "/models/select", {"constraints": {"precisions": ["int8"]}})
    assert status == 200 and body["selected"] == "cpu-tiny"
    status, body = _call(base + "/models/select", {"constraints": {"devices": ["hailo"]}})
    assert status == 200 and body["selected"] is None


@pytest.mark.parametrize(
    "path,body",
    [
        ("/models/select", {"nope": 1}),
        ("/models/select", {"constraints": {"devices": "cpu"}}),
        ("/models/select", {"constraints": []}),
        ("/models/select", []),
        ("/inference/simulate", {}),
        ("/inference/simulate", {"source": "nope"}),
        ("/inference/simulate", {"source": "cam0", "frames": 0}),
        ("/inference/simulate", {"source": "cam0", "frames": 1001}),
        ("/inference/simulate", {"source": "cam0", "frames": True}),
        ("/inference/simulate", {"source": "cam0", "model": "nope"}),
        ("/inference/simulate", {"source": "cam0", "model": 5}),
        ("/inference/simulate", {"source": "cam0", "model": "hailo-yolo"}),
        ("/inference/simulate", {"source": "cam0", "extra": 1}),
    ],
)
def test_malformed_requests_are_refused(base: str, path: str, body: object) -> None:
    status, answer = _call(base + path, body)
    assert status == 400 and "error" in answer


def test_oversized_and_non_json_bodies_are_refused(base: str) -> None:
    for payload in (b"x" * 5000, b"not json", b'{"frames": NaN}'):
        request = urllib.request.Request(base + "/models/select", data=payload, method="POST")
        with pytest.raises(urllib.error.HTTPError) as caught:
            urllib.request.urlopen(request, timeout=5)
        assert caught.value.code == 400
        caught.value.close()


def test_simulation_is_deterministic_and_feeds_the_latency_module(base: str) -> None:
    status, first = _call(base + "/inference/simulate", {"source": "cam0", "frames": 10})
    assert status == 200 and first["model"] == "cpu-small" and first["frames"] == 10 and first["simulated"] is True
    status, latency = _call(base + "/latency")
    stats = latency["cameras"]["cam0"]
    assert stats["inference"]["count"] == 10
    assert 36.0 <= stats["inference"]["p50Ms"] <= 44.0  # 40 ms +/- 10 %
    status, second = _call(base + "/inference/simulate", {"source": "cam0", "frames": 10})
    assert {k: v for k, v in second.items()} == first
    status, other = _call(base + "/inference/simulate", {"source": "cam0", "frames": 10, "model": "cpu-tiny"})
    assert other["model"] == "cpu-tiny" and other["outputDigest"] != first["outputDigest"]
    assert _call(base + "/latency")[1]["cameras"]["cam0"]["inference"]["count"] == 30


def test_simulation_needs_the_latency_switch(base_no_latency: str) -> None:
    assert _call(base_no_latency + "/models")[0] == 200
    status, body = _call(base_no_latency + "/inference/simulate", {"source": "cam0"})
    assert status == 409 and "latency" in body["error"]


def test_no_model_that_fits_is_a_409_with_the_reasons(tmp_path: Path) -> None:
    config = {**CONFIG, "host": {"devices": []}}
    for url in _serving(tmp_path, vision=parse_vision_config(config, tmp_path), latency=LatencyRecorder(8)):
        status, body = _call(url + "/inference/simulate", {"source": "cam0"})
        assert status == 409 and all(d["status"] == "discarded" for d in body["decisions"])


def test_everything_stays_off_without_the_switch(tmp_path: Path) -> None:
    for url in _serving(tmp_path, latency=LatencyRecorder(8)):
        for path in ("/models", "/sources"):
            assert _call(url + path) == (404, {"error": "not found"})
        for path in ("/models/select", "/inference/simulate"):
            assert _call(url + path, {}) == (404, {"error": "not found"})


@pytest.mark.parametrize(
    "bad",
    [[], {"extra": 1}, {"models": "x"}, {"sources": [{}] * 17}, {"host": {"devices": ["tpu"]}},
     {"sources": [CONFIG["sources"][0], CONFIG["sources"][0]]}, {"models": [{"name": "m"}]}],
)
def test_refuses_a_bad_configuration(bad: object, tmp_path: Path) -> None:
    with pytest.raises(VisionConfigError):
        parse_vision_config(bad, tmp_path)


def test_config_file_loading_fails_closed(tmp_path: Path) -> None:
    good = tmp_path / "vision.json"
    good.write_text(json.dumps(CONFIG), encoding="utf-8")
    assert len(load_vision_config(good).models) == 3
    for name, text in (("bad.json", "{"), ("nan.json", '{"models": [], "sources": [], "x": NaN}'), ("big.json", " " * (256 * 1024 + 1))):
        (tmp_path / name).write_text(text, encoding="utf-8")
        with pytest.raises(VisionConfigError):
            load_vision_config(tmp_path / name)
    with pytest.raises(VisionConfigError):
        load_vision_config(tmp_path / "missing.json")


def test_serve_refuses_a_bad_vision_file(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["serve", "--port", "0", "--vision-sim", str(tmp_path / "missing.json"), "--workspace", str(tmp_path)]) == 2
    assert "--vision-sim" in capsys.readouterr().err
