# =============================================================================
# HYDRA-UMC-VISION-NODE - tests/test_latency.py
# Copyright (C) 2026 JuanenRac (Electro Hobby 3D) <electrohobby3d@gmail.com>
# GPL-3.0-or-later - see LICENSE
# =============================================================================
from __future__ import annotations

import json
import threading
import urllib.error
import urllib.request
from pathlib import Path

import pytest

from hydra_umc_vision_node.api import VisionNodeServer
from hydra_umc_vision_node.latency import MAX_CAMERAS, TOTAL, LatencyError, LatencyRecorder, percentile


def _frame(capture: float, pre: float = 0.002, inf: float = 0.010, post: float = 0.001, pub: float = 0.001) -> dict:
    t1 = capture + pre
    t2 = t1 + inf
    t3 = t2 + post
    return {"capture": capture, "preprocess": t1, "inference": t2, "postprocess": t3, "publish": t3 + pub}


def test_percentile_is_nearest_rank() -> None:
    values = [float(v) for v in range(1, 101)]
    assert percentile(values, 0.50) == 50.0
    assert percentile(values, 0.95) == 95.0
    assert percentile([7.0], 0.95) == 7.0


def test_records_the_duration_of_each_stage_and_the_total() -> None:
    recorder = LatencyRecorder()
    durations = recorder.record("cam0", _frame(100.0))
    assert durations["preprocess"] == pytest.approx(2.0)
    assert durations["inference"] == pytest.approx(10.0)
    assert durations[TOTAL] == pytest.approx(14.0)


def test_percentiles_over_a_simulated_stream() -> None:
    recorder = LatencyRecorder(window=1000)
    for index in range(100):
        # inference takes 10 ms, except the slowest 5 frames that take 40 ms
        recorder.record("cam0", _frame(index * 0.033, inf=0.040 if index >= 95 else 0.010))
    stats = recorder.snapshot()["cameras"]["cam0"]
    assert stats["inference"]["count"] == 100
    assert stats["inference"]["p50Ms"] == pytest.approx(10.0)
    assert stats["inference"]["p95Ms"] == pytest.approx(10.0)
    assert stats["inference"]["maxMs"] == pytest.approx(40.0)
    assert stats[TOTAL]["p50Ms"] == pytest.approx(14.0)
    assert stats[TOTAL]["maxMs"] == pytest.approx(44.0)


def test_only_the_last_window_frames_count() -> None:
    recorder = LatencyRecorder(window=10)
    for index in range(50):
        recorder.record("cam0", _frame(float(index), inf=0.100 if index < 40 else 0.001))
    assert recorder.snapshot()["cameras"]["cam0"]["inference"] == {"count": 10, "p50Ms": pytest.approx(1.0), "p95Ms": pytest.approx(1.0), "maxMs": pytest.approx(1.0)}


def test_cameras_are_kept_apart() -> None:
    recorder = LatencyRecorder()
    recorder.record("a", _frame(0.0, inf=0.010))
    recorder.record("b", _frame(0.0, inf=0.050))
    cams = recorder.snapshot()["cameras"]
    assert set(cams) == {"a", "b"}
    assert cams["a"]["inference"]["p50Ms"] < cams["b"]["inference"]["p50Ms"]


def test_a_skipped_stage_is_measured_from_the_one_before() -> None:
    recorder = LatencyRecorder()
    durations = recorder.record("cam0", {"capture": 0.0, "preprocess": 0.003, "publish": 0.005})
    assert "inference" not in durations and durations["publish"] == pytest.approx(2.0) and durations[TOTAL] == pytest.approx(5.0)


@pytest.mark.parametrize(
    "camera,stamps",
    [
        ("", {"capture": 0.0, "publish": 1.0}),
        ("bad name", {"capture": 0.0, "publish": 1.0}),
        (5, {"capture": 0.0, "publish": 1.0}),
        ("c", None),
        ("c", {}),
        ("c", {"capture": 0.0}),
        ("c", {"preprocess": 0.0, "publish": 1.0}),
        ("c", {"capture": 0.0, "magic": 1.0}),
        ("c", {"capture": 1.0, "publish": 0.5}),
        ("c", {"capture": 0.0, "publish": float("nan")}),
        ("c", {"capture": True, "publish": 1.0}),
    ],
)
def test_refuses_malformed_frames(camera: object, stamps: object) -> None:
    with pytest.raises(LatencyError):
        LatencyRecorder().record(camera, stamps)


def test_bounds_the_number_of_cameras() -> None:
    recorder = LatencyRecorder(window=2)
    for index in range(MAX_CAMERAS):
        recorder.record(f"c{index}", _frame(0.0))
    with pytest.raises(LatencyError):
        recorder.record("one-too-many", _frame(0.0))
    recorder.record("c0", _frame(1.0))  # a camera already tracked still records


@pytest.mark.parametrize("bad", [0, -1, 10_001, True, 2.5])
def test_refuses_a_bad_window(bad: object) -> None:
    with pytest.raises(LatencyError):
        LatencyRecorder(window=bad)  # type: ignore[arg-type]


def _call(url: str, body: dict | None = None) -> tuple[int, dict]:
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, headers={"Content-Type": "application/json"}, method="POST" if body is not None else "GET")
    try:
        with urllib.request.urlopen(req, timeout=5) as resp:
            return resp.status, json.loads(resp.read())
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read())


def _serve(server: VisionNodeServer) -> threading.Thread:
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return thread


def _stop(server: VisionNodeServer, thread: threading.Thread) -> None:
    server.shutdown()
    server.server_close()
    thread.join(timeout=2)


def test_routes_answer_404_while_off(tmp_path: Path) -> None:
    server = VisionNodeServer(("127.0.0.1", 0), tmp_path)
    thread = _serve(server)
    try:
        base = f"http://127.0.0.1:{server.server_address[1]}"
        assert _call(f"{base}/latency")[0] == 404
        assert _call(f"{base}/latency/record", {"camera": "c", "stamps": {"capture": 0.0, "publish": 1.0}})[0] == 404
    finally:
        _stop(server, thread)


def test_routes_record_and_report_when_on(tmp_path: Path) -> None:
    server = VisionNodeServer(("127.0.0.1", 0), tmp_path, latency=LatencyRecorder(window=50))
    thread = _serve(server)
    try:
        base = f"http://127.0.0.1:{server.server_address[1]}"
        for index in range(20):
            status, body = _call(f"{base}/latency/record", {"camera": "cam0", "stamps": _frame(index * 0.05)})
            assert status == 200 and body["totalMs"] == pytest.approx(14.0)
        status, body = _call(f"{base}/latency")
        assert status == 200 and body["window"] == 50
        assert body["cameras"]["cam0"][TOTAL]["count"] == 20
        status, body = _call(f"{base}/latency/record", {"camera": "cam0", "stamps": {"capture": 2.0, "publish": 1.0}})
        assert status == 400 and "earlier" in body["error"]
    finally:
        _stop(server, thread)
