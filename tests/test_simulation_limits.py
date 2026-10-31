# =============================================================================
# HYDRA-UMC-VISION-NODE - tests/test_simulation_limits.py
# Copyright (C) 2026 JuanenRac (Electro Hobby 3D) <electrohobby3d@gmail.com>
# GPL-3.0-or-later - see LICENSE
# =============================================================================
"""Time limit and one-at-a-time rule of POST /inference/simulate (clock injected, no real waiting)."""
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
from hydra_umc_vision_node.models import ModelSpec
from hydra_umc_vision_node.sources import Frame
from hydra_umc_vision_node.vision_sim import (
    DEFAULT_MAX_RUN_SECONDS,
    VisionConfigError,
    parse_vision_config,
    run_simulation,
)

CONFIG: dict[str, Any] = {
    "host": {"devices": ["cpu"], "precisions": ["fp32"]},
    "models": [{"name": "m", "format": "onnx", "inputWidth": 32, "inputHeight": 32, "precision": "fp32", "device": "cpu", "simLatencyMs": 5}],
    "sources": [{"name": "cam0", "kind": "synthetic", "width": 8, "height": 8, "channels": 3, "seed": 1, "frames": 500}],
}
MODEL = ModelSpec("m", "onnx", 32, 32, "fp32", "cpu", 5.0)


class FakeClock:
    """Every reading advances the time by `step` seconds."""

    def __init__(self, step: float) -> None:
        self.now = 0.0
        self.step = step

    def __call__(self) -> float:
        self.now += self.step
        return self.now


class Source:
    name = "slow"
    kind = "fake"

    def describe(self) -> dict:
        return {"name": self.name}

    def frames(self, limit: int) -> Iterator[Frame]:
        for i in range(limit):
            yield Frame(i, 2, 2, 1, bytes([i % 256]) * 4)


def test_no_limit_runs_every_frame_and_is_not_truncated() -> None:
    summary = run_simulation(Source(), MODEL, LatencyRecorder(64), 20)
    assert summary["frames"] == 20 and summary["truncated"] is False and "truncatedReason" not in summary


def test_the_limit_cuts_with_a_partial_marked_result() -> None:
    recorder = LatencyRecorder(64)
    summary = run_simulation(Source(), MODEL, recorder, 50, max_seconds=5.0, clock=FakeClock(1.0))
    assert summary["truncated"] is True
    assert 1 <= summary["frames"] < 50 and summary["requestedFrames"] == 50
    assert "time limit" in summary["truncatedReason"]
    assert recorder.snapshot()["cameras"]["slow"]["inference"]["count"] == summary["frames"]


def test_a_run_that_finishes_exactly_at_the_limit_is_complete() -> None:
    summary = run_simulation(Source(), MODEL, LatencyRecorder(64), 3, max_seconds=1.0, clock=FakeClock(5.0))
    assert summary["frames"] == 1 and summary["truncated"] is True
    summary = run_simulation(Source(), MODEL, LatencyRecorder(64), 1, max_seconds=1.0, clock=FakeClock(5.0))
    assert summary["frames"] == 1 and summary["truncated"] is False


def test_a_partial_result_is_deterministic() -> None:
    first = run_simulation(Source(), MODEL, LatencyRecorder(64), 50, max_seconds=4.0, clock=FakeClock(1.0))
    second = run_simulation(Source(), MODEL, LatencyRecorder(64), 50, max_seconds=4.0, clock=FakeClock(1.0))
    assert first == second


def test_the_default_limit_is_safe_and_comes_from_the_configuration(tmp_path: Path) -> None:
    assert parse_vision_config(CONFIG, tmp_path).max_run_seconds == DEFAULT_MAX_RUN_SECONDS == 10.0
    assert parse_vision_config({**CONFIG, "limits": {"maxRunSeconds": 2.5}}, tmp_path).max_run_seconds == 2.5


@pytest.mark.parametrize("bad", [0, -1, -0.5, True, "5", None, float("inf"), float("nan"), 99999, []])
def test_a_zero_negative_or_malformed_limit_refuses_the_configuration(bad: object, tmp_path: Path) -> None:
    with pytest.raises(VisionConfigError):
        parse_vision_config({**CONFIG, "limits": {"maxRunSeconds": bad}}, tmp_path)


def test_unknown_limit_keys_are_refused(tmp_path: Path) -> None:
    with pytest.raises(VisionConfigError):
        parse_vision_config({**CONFIG, "limits": {"other": 1}}, tmp_path)


def _call(url: str, body: object = None) -> tuple[int, dict]:
    data = json.dumps(body).encode() if body is not None else None
    request = urllib.request.Request(url, data=data, method="POST" if body is not None else "GET")
    try:
        with urllib.request.urlopen(request, timeout=10) as response:
            return response.status, json.loads(response.read())
    except urllib.error.HTTPError as error:
        return error.code, json.loads(error.read())


@pytest.fixture
def served(tmp_path: Path) -> Iterator[tuple[str, VisionNodeServer]]:
    config = {**CONFIG, "limits": {"maxRunSeconds": 5}}
    server = VisionNodeServer(
        ("127.0.0.1", 0), tmp_path, vision=parse_vision_config(config, tmp_path),
        latency=LatencyRecorder(2000), simulation_clock=FakeClock(2.0),
    )
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{server.server_address[1]}", server
    server.shutdown()
    server.server_close()
    thread.join(timeout=2)


def test_the_route_answers_a_truncated_partial_result(served: tuple[str, VisionNodeServer]) -> None:
    url, _ = served
    status, body = _call(url + "/inference/simulate", {"source": "cam0", "frames": 400})
    assert status == 200 and body["truncated"] is True and body["frames"] < 400 and body["requestedFrames"] == 400
    status, body = _call(url + "/inference/simulate", {"source": "cam0", "frames": 1})
    assert status == 200 and body["truncated"] is False


def test_a_second_simulation_while_one_runs_is_a_409_and_other_routes_answer(served: tuple[str, VisionNodeServer]) -> None:
    url, server = served
    assert server.simulation_lock.acquire(blocking=False)  # a run is "in progress"
    try:
        status, body = _call(url + "/inference/simulate", {"source": "cam0", "frames": 5})
        assert status == 409 and "already running" in body["error"]
        assert _call(url + "/stats")[0] == 200 and _call(url + "/models")[0] == 200
    finally:
        server.simulation_lock.release()
    assert _call(url + "/inference/simulate", {"source": "cam0", "frames": 5})[0] == 200


def test_load_many_requests_in_parallel_never_overlap_and_never_hang(served: tuple[str, VisionNodeServer]) -> None:
    url, server = served
    statuses: list[int] = []

    def worker() -> None:
        statuses.append(_call(url + "/inference/simulate", {"source": "cam0", "frames": 300})[0])

    threads = [threading.Thread(target=worker) for _ in range(12)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=30)
    assert not any(t.is_alive() for t in threads)
    assert len(statuses) == 12 and set(statuses) <= {200, 409} and 200 in statuses
    assert not server.simulation_lock.locked()
    assert _call(url + "/stats")[0] == 200
