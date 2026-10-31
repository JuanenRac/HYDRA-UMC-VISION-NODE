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
from hydra_umc_vision_node.sources import DEFAULT_CHUNK_BYTES, Frame
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


# --- the time is also checked INSIDE a frame, between two pieces of its generation -------------------------------------

BIG = {"name": "big", "kind": "synthetic", "width": 256, "height": 256, "channels": 3, "seed": 2, "frames": 50}


class CountingClock:
    """Each reading advances the time by `step`; `reads` counts them."""

    def __init__(self, step: float) -> None:
        self.now = 0.0
        self.step = step
        self.reads = 0

    def __call__(self) -> float:
        self.reads += 1
        self.now += self.step
        return self.now


def _big(tmp_path: Path) -> Any:
    return parse_vision_config({**CONFIG, "sources": [BIG]}, tmp_path).sources["big"]


def test_a_frame_in_one_piece_is_never_interrupted(tmp_path: Path) -> None:
    # characterization: with the default piece size a small frame is one piece, the limit acts between frames only
    summary = run_simulation(_big(tmp_path), MODEL, LatencyRecorder(64), 10, max_seconds=1.0, clock=FakeClock(5.0), chunk_bytes=4 * 1024 * 1024)
    assert summary["frames"] == 1 and summary["truncated"] is True


def test_the_limit_cuts_a_frame_in_the_middle_and_drops_it(tmp_path: Path) -> None:
    recorder = LatencyRecorder(64)
    clock = CountingClock(1.0)
    # 256x256x3 frame, 768-byte rows, pieces of 7680 bytes = 10 rows: 25 checkpoints per frame; the limit is 8 s
    summary = run_simulation(_big(tmp_path), MODEL, recorder, 10, max_seconds=8.0, clock=clock, chunk_bytes=7680)
    assert summary["truncated"] is True and summary["frames"] == 0 and summary["totalMs"] is None
    assert recorder.snapshot()["cameras"] == {}  # the half frame was never recorded
    assert "time limit" in summary["truncatedReason"] and "0 of 10 frames" in summary["truncatedReason"]
    assert clock.reads <= 8 + 2  # the start reading, 8 checkpoints at most: overshoot of one piece, not of a whole frame


def test_the_overshoot_is_bounded_by_one_piece_with_completed_frames(tmp_path: Path) -> None:
    source = _big(tmp_path)
    clock = CountingClock(1.0)
    summary = run_simulation(source, MODEL, LatencyRecorder(64), 10, max_seconds=40.0, clock=clock, chunk_bytes=7680)
    # each frame is 24 checkpoints + 1 frame-boundary reading: 40 s are reached inside the second frame
    assert summary["truncated"] is True and summary["frames"] == 1
    assert clock.reads <= 40 + 2


def test_a_cut_run_is_deterministic_and_its_complete_part_matches_an_uncut_run(tmp_path: Path) -> None:
    source = _big(tmp_path)
    cut = run_simulation(source, MODEL, LatencyRecorder(64), 10, max_seconds=40.0, clock=CountingClock(1.0), chunk_bytes=7680)
    again = run_simulation(source, MODEL, LatencyRecorder(64), 10, max_seconds=40.0, clock=CountingClock(1.0), chunk_bytes=7680)
    one = run_simulation(source, MODEL, LatencyRecorder(64), 1)
    assert cut == again
    assert cut["frames"] == 1 and cut["outputDigest"] == one["outputDigest"]  # pieces do not change the bytes


def test_pieces_do_not_change_the_bytes_of_a_frame(tmp_path: Path) -> None:
    source = _big(tmp_path)
    whole = list(source.frames(2))
    pieces = list(source.frames(2, checkpoint=lambda: None, chunk_bytes=1024))
    assert [f.data for f in whole] == [f.data for f in pieces]


def test_a_complete_run_without_limit_ignores_the_piece_size(tmp_path: Path) -> None:
    source = _big(tmp_path)
    a = run_simulation(source, MODEL, LatencyRecorder(64), 3)
    b = run_simulation(source, MODEL, LatencyRecorder(64), 3, chunk_bytes=1024)
    assert a == b and a["truncated"] is False


def test_a_folder_source_is_read_in_pieces_and_cut_between_them(tmp_path: Path) -> None:
    from hydra_umc_vision_node.sources import RunLimitReached, build_source

    folder = tmp_path / "frames"
    folder.mkdir()
    for i in range(3):
        (folder / f"f{i}.raw").write_bytes(bytes([i]) * 4096)
    source = build_source({"name": "fs", "kind": "folder", "path": "frames", "width": 64, "height": 64, "channels": 1}, tmp_path)
    reads: list[int] = []
    assert [f.data for f in source.frames(3, checkpoint=lambda: reads.append(1), chunk_bytes=1024)] == [bytes([i]) * 4096 for i in range(3)]
    assert len(reads) == 3 * 3  # 4 reads per file, a checkpoint before each one but the first

    def stop() -> None:
        raise RunLimitReached

    with pytest.raises(RunLimitReached):
        list(source.frames(3, checkpoint=stop, chunk_bytes=1024))
    summary = run_simulation(source, MODEL, LatencyRecorder(64), 3, max_seconds=2.0, clock=CountingClock(1.0), chunk_bytes=1024)
    assert summary["truncated"] is True and summary["frames"] == 0


def test_a_custom_source_without_the_new_arguments_still_works() -> None:
    summary = run_simulation(Source(), MODEL, LatencyRecorder(64), 5, max_seconds=100.0, clock=FakeClock(1.0), chunk_bytes=1024)
    assert summary["frames"] == 5 and summary["truncated"] is False


def test_chunk_bytes_comes_from_the_configuration(tmp_path: Path) -> None:
    assert parse_vision_config(CONFIG, tmp_path).chunk_bytes == DEFAULT_CHUNK_BYTES
    assert parse_vision_config({**CONFIG, "limits": {"chunkBytes": 4096}}, tmp_path).chunk_bytes == 4096


@pytest.mark.parametrize("bad", [0, 1023, -5, True, "4096", None, 4096.5, 10**9, []])
def test_a_malformed_chunk_size_refuses_the_configuration(bad: object, tmp_path: Path) -> None:
    with pytest.raises(VisionConfigError):
        parse_vision_config({**CONFIG, "limits": {"chunkBytes": bad}}, tmp_path)


def test_the_route_cuts_inside_a_frame_with_the_configured_piece_size(tmp_path: Path) -> None:
    config = {**CONFIG, "sources": [BIG], "limits": {"maxRunSeconds": 3, "chunkBytes": 7680}}
    server = VisionNodeServer(("127.0.0.1", 0), tmp_path, vision=parse_vision_config(config, tmp_path),
                              latency=LatencyRecorder(2000), simulation_clock=CountingClock(1.0))
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        status, body = _call(f"http://127.0.0.1:{server.server_address[1]}/inference/simulate", {"source": "big", "frames": 5})
        assert status == 200 and body["truncated"] is True and body["frames"] == 0 and body["totalMs"] is None
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)
