# =============================================================================
# HYDRA-UMC-VISION-NODE - tests/test_loopback.py
# Copyright (C) 2026 JuanenRac (Electro Hobby 3D) <electrohobby3d@gmail.com>
# GPL-3.0-or-later - see LICENSE
# =============================================================================
from __future__ import annotations

import json
import threading
import urllib.error
import urllib.request
from collections.abc import Iterator
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

import pytest

from hydra_umc_vision_node.api import VisionNodeServer
from hydra_umc_vision_node.contract import validate_detection_frame, validate_frame_meta
from hydra_umc_vision_node.latency import LatencyRecorder
from hydra_umc_vision_node.loopback import (
    DetectorSettings,
    LoopbackError,
    StreamUnavailable,
    StreamerSettings,
    detect,
    parse_loopback_config,
    pillow_decoder,
    read_stream,
    run_loopback,
    source_items,
)
from hydra_umc_vision_node.sources import Frame
from hydra_umc_vision_node.vision_sim import VisionConfigError, parse_vision_config


def _gray(width: int, height: int, bright: list[tuple[int, int, int, int]], level: int = 255) -> Frame:
    data = bytearray(width * height)
    for x0, y0, x1, y1 in bright:
        for y in range(y0, y1):
            data[y * width + x0:y * width + x1] = bytes([level]) * (x1 - x0)
    return Frame(0, width, height, 1, bytes(data))


def test_two_bright_blocks_are_two_detections_largest_first() -> None:
    frame = _gray(32, 16, [(0, 0, 8, 8), (16, 0, 32, 16)])
    found = detect(frame, DetectorSettings(cell=8))
    assert [d["box"] for d in found] == [
        {"x_min": 16.0, "y_min": 0.0, "x_max": 32.0, "y_max": 16.0},
        {"x_min": 0.0, "y_min": 0.0, "x_max": 8.0, "y_max": 8.0},
    ]
    assert all(d["score"] == 1.0 and d["label"] == "bright" for d in found)


def test_touching_cells_are_one_group_and_the_score_is_the_share_of_the_box_that_is_bright() -> None:
    frame = _gray(24, 24, [(0, 0, 8, 8), (8, 0, 16, 8), (8, 8, 16, 16)])  # an L of three cells inside a 2x2 box
    (found,) = detect(frame, DetectorSettings(cell=8))
    assert found["box"] == {"x_min": 0.0, "y_min": 0.0, "x_max": 16.0, "y_max": 16.0}
    assert found["score"] == 0.75


def test_a_dark_frame_the_threshold_and_the_limits() -> None:
    assert detect(_gray(16, 16, []), DetectorSettings()) == []
    dim = _gray(16, 16, [(0, 0, 16, 16)], level=150)
    assert detect(dim, DetectorSettings(threshold=200)) == []
    assert len(detect(dim, DetectorSettings(threshold=150, cell=16))) == 1
    many = _gray(40, 8, [(x, 0, x + 4, 8) for x in range(0, 40, 8)])
    assert len(detect(many, DetectorSettings(cell=4, max_detections=3))) == 3
    assert detect(many, DetectorSettings(cell=4, min_cells=3)) == []


def test_a_frame_that_does_not_hold_its_bytes_and_colour_frames() -> None:
    with pytest.raises(LoopbackError):
        detect(Frame(0, 4, 4, 1, b"\0" * 3), DetectorSettings())
    rgb = Frame(0, 8, 8, 3, bytes([255, 255, 255]) * 64)
    assert len(detect(rgb, DetectorSettings(cell=8))) == 1
    rgba = Frame(0, 8, 8, 4, bytes([0, 0, 0, 255]) * 64)
    assert detect(rgba, DetectorSettings(cell=8)) == []  # the alpha byte is not brightness


def test_the_run_publishes_messages_that_follow_the_contract() -> None:
    config = parse_loopback_config({"enabled": True, "detector": {"cell": 8}})
    assert config is not None
    frame = _gray(16, 16, [(0, 0, 8, 8)])
    items = iter([(frame, "cam0-0", 1.0, None), (frame, "cam0-1", 1.033, "sess")])
    result = run_loopback(items, config, "cam0", 10)
    assert result["frames"] == 2 and result["detections"] == 2 and result["simulated"] is True
    for message in result["messages"]:
        validate_frame_meta(message["meta"])
        validate_detection_frame(message["detections"])
        assert message["detections"]["model"] == result["model"]
        assert message["meta"]["pixel_format"] == "GRAY" and message["meta"]["source_id"] == "cam0"
    assert result["messages"][1]["meta"]["session_id"] == "sess"
    assert result["messages"][0]["detections"]["capture_ts_utc"] == "1970-01-01T00:00:01.000Z"


def test_the_identity_changes_with_the_settings_and_the_frames_are_limited() -> None:
    a = DetectorSettings().identity()
    assert a == DetectorSettings().identity() and a != DetectorSettings(threshold=100).identity()
    assert a.hailo_arch == "cpu"
    config = parse_loopback_config({"enabled": True})
    assert config is not None
    frame = _gray(8, 8, [])
    assert run_loopback(iter([(frame, f"f{i}", 0.0, None) for i in range(5)]), config, "s", 2)["frames"] == 2
    for bad in (0, 101, True, "3"):
        with pytest.raises(LoopbackError):
            run_loopback(iter([]), config, "s", bad)  # type: ignore[arg-type]


def test_the_run_records_the_latency_of_every_stage() -> None:
    config = parse_loopback_config({"enabled": True})
    assert config is not None
    recorder = LatencyRecorder(8)
    ticks = iter(range(1000))
    run_loopback(iter([(_gray(8, 8, []), "f0", 0.0, None)]), config, "cam0", 1, recorder, clock=lambda: next(ticks) / 1000.0)
    stages = recorder.snapshot()["cameras"]["loopback-cam0"]
    assert set(stages) == {"preprocess", "inference", "postprocess", "publish", "total"}


@pytest.mark.parametrize("block", [
    "text", {"unknown": 1}, {"enabled": "yes"}, {"detector": {"threshold": 0}}, {"detector": {"threshold": 256}}, {"detector": {"cell": 65}},
    {"detector": {"maxDetections": 65}}, {"detector": {"label": "a b"}}, {"detector": {"x": 1}}, {"streamer": {"url": "ftp://127.0.0.1/x"}},
    {"streamer": {"url": "http://example.com/stream"}}, {"streamer": {"url": "http://user:pw@127.0.0.1/s"}},
    {"streamer": {"url": "http://127.0.0.1:8100/stream", "timeoutSeconds": 0}}, {"streamer": {"url": "http://127.0.0.1:8100/stream", "allowRemote": "no"}},
    {"streamer": {"url": "http://127.0.0.1:8100/stream", "maxFrameBytes": 10}},
])
def test_a_bad_loopback_block_is_refused(block: object) -> None:
    with pytest.raises(LoopbackError):
        parse_loopback_config(block)


def test_the_block_is_off_unless_it_says_so_and_a_remote_streamer_needs_allow_remote() -> None:
    assert parse_loopback_config(None) is None
    off = parse_loopback_config({})
    assert off is not None and off.enabled is False and off.streamer is None
    remote = parse_loopback_config({"streamer": {"url": "http://cm5.local:8100/stream", "allowRemote": True}})
    assert remote is not None and remote.streamer is not None and remote.streamer.host == "cm5.local"


# ------------------------------------------------------------------------------------------------------- the stream


def _jpeg(number: int) -> bytes:
    return b"\xff\xd8" + bytes([number]) * 20 + b"\xff\xd9"


def _stream_handler(parts: int, content_type: str = "multipart/x-mixed-replace; boundary=hydraumcframe", size_header: str | None = None) -> type:
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args: object) -> None:
            pass

        def do_GET(self) -> None:  # noqa: N802
            self.send_response(200)
            self.send_header("Content-Type", content_type)
            self.end_headers()
            for number in range(parts):
                payload = _jpeg(number)
                head = (f"--hydraumcframe\r\nContent-Type: image/jpeg\r\nContent-Length: {size_header or len(payload)}\r\n"
                        f"X-Frame-Id: {number + 7}\r\nX-Session-Id: sess-1\r\nX-Capture-Ts: {1700000000000 + number * 33}\r\n\r\n").encode()
                self.wfile.write(head + payload + b"\r\n")
                self.wfile.flush()

    return Handler


def _stream_server(handler: type) -> Iterator[ThreadingHTTPServer]:
    server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield server
    server.shutdown()
    server.server_close()
    thread.join(timeout=2)


def _settings(server: ThreadingHTTPServer, **change: Any) -> StreamerSettings:
    values: dict[str, Any] = {"host": "127.0.0.1", "port": server.server_address[1], "path": "/stream", "timeout_seconds": 3.0, "max_frame_bytes": 4096}
    values.update(change)
    return StreamerSettings(**values)


@pytest.fixture
def stream() -> Iterator[ThreadingHTTPServer]:
    yield from _stream_server(_stream_handler(5))


def test_the_parts_of_the_mjpeg_stream_carry_the_identity_the_streamer_announces(stream: ThreadingHTTPServer) -> None:
    parts = list(read_stream(_settings(stream), 3))
    assert [p.frame_id for p in parts] == ["7", "8", "9"]
    assert {p.session_id for p in parts} == {"sess-1"}
    assert parts[1].capture_ts_ms == 1700000000033 and parts[0].jpeg == _jpeg(0)
    assert len(list(read_stream(_settings(stream), 50))) == 5  # the stream ends before the limit


def test_a_stream_that_is_not_multipart_that_lies_about_its_size_or_is_not_there() -> None:
    for handler, text in ((_stream_handler(1, content_type="text/html"), "multipart"), (_stream_handler(1, size_header="999999"), "at most")):
        gen = _stream_server(handler)
        server = next(gen)
        try:
            with pytest.raises(StreamUnavailable, match=text):
                list(read_stream(_settings(server), 1))
        finally:
            gen.close()
    with pytest.raises(StreamUnavailable, match="cannot be reached"):
        list(read_stream(StreamerSettings("127.0.0.1", 1, "/stream", 0.5, 4096), 1))


def test_the_pillow_decoder_says_what_is_missing(monkeypatch: pytest.MonkeyPatch) -> None:
    import builtins

    real_import = builtins.__import__

    def refuse(name: str, *args: Any, **kwargs: Any) -> Any:
        if name == "PIL":
            raise ImportError("no PIL")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", refuse)
    with pytest.raises(StreamUnavailable, match="Pillow is not installed"):
        pillow_decoder(_jpeg(1))


# ------------------------------------------------------------------------------------------------------ the routes

CONFIG: dict[str, Any] = {
    "host": {"devices": ["cpu"]},
    "models": [{"name": "cpu-small", "format": "onnx", "inputWidth": 320, "inputHeight": 320, "precision": "fp32", "device": "cpu"}],
    "sources": [{"name": "cam0", "kind": "synthetic", "width": 16, "height": 8, "channels": 3, "seed": 3, "frames": 20}],
    "loopback": {"enabled": True, "detector": {"threshold": 100, "cell": 4}},
}


def _call(url: str, body: object = None) -> tuple[int, dict]:
    data = json.dumps(body).encode() if body is not None else None
    request = urllib.request.Request(url, data=data, method="POST" if body is not None else "GET")
    try:
        with urllib.request.urlopen(request, timeout=5) as response:
            return response.status, json.loads(response.read())
    except urllib.error.HTTPError as error:
        return error.code, json.loads(error.read())


def _serving(tmp_path: Path, config: dict, **kwargs: object) -> Iterator[str]:
    server = VisionNodeServer(("127.0.0.1", 0), tmp_path, vision=parse_vision_config(config, tmp_path), **kwargs)  # type: ignore[arg-type]
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{server.server_address[1]}"
    server.shutdown()
    server.server_close()
    thread.join(timeout=2)


@pytest.fixture
def base(tmp_path: Path) -> Iterator[str]:
    yield from _serving(tmp_path, CONFIG, latency=LatencyRecorder(32))


def test_the_loopback_routes_run_a_configured_source(base: str) -> None:
    status, described = _call(base + "/loopback")
    assert status == 200 and described["enabled"] is True and described["model"]["hailo_arch"] == "cpu"
    status, result = _call(base + "/loopback/run", {"source": "cam0", "frames": 3})
    assert status == 200 and result["frames"] == 3
    for message in result["messages"]:
        validate_frame_meta(message["meta"])
        validate_detection_frame(message["detections"])
    assert _call(base + "/latency")[1]["cameras"]["loopback-cam0"]["total"]["count"] == 3


@pytest.mark.parametrize("body", [{}, {"source": "cam0", "stream": True}, {"source": "nope"}, {"source": "cam0", "frames": 0}, {"source": "cam0", "frames": "x"},
                                  {"source": "cam0", "extra": 1}, {"stream": True}])
def test_the_run_refuses_a_bad_request(base: str, body: dict) -> None:
    assert _call(base + "/loopback/run", body)[0] in (400, 409)


def test_without_the_switch_the_routes_do_not_exist(tmp_path: Path) -> None:
    off = {**CONFIG, "loopback": {"enabled": False}}
    for url in _serving(tmp_path, off):
        assert _call(url + "/loopback")[0] == 404
        assert _call(url + "/loopback/run", {"source": "cam0"})[0] == 404
    bare = {k: v for k, v in CONFIG.items() if k != "loopback"}
    for url in _serving(tmp_path, bare):
        assert _call(url + "/loopback")[0] == 404


def test_the_stream_route_reads_the_streamer_and_decodes_with_the_decoder_of_the_server(tmp_path: Path, stream: ThreadingHTTPServer) -> None:
    port = stream.server_address[1]
    config = {**CONFIG, "loopback": {"enabled": True, "detector": {"cell": 4}, "streamer": {"url": f"http://127.0.0.1:{port}/stream", "maxFrameBytes": 4096}}}

    def decoder(jpeg: bytes) -> Frame:
        level = jpeg[2]
        return _gray(8, 8, [(0, 0, 8, 8)], level=255 if level % 2 == 0 else 0)

    for url in _serving(tmp_path, config, stream_decoder=decoder):
        status, result = _call(url + "/loopback/run", {"stream": True, "frames": 4})
        assert status == 200 and result["frames"] == 4
        assert [m["meta"]["frame_id"] for m in result["messages"]] == ["7", "8", "9", "10"]
        assert {m["detections"]["session_id"] for m in result["messages"]} == {"sess-1"}
        assert [len(m["detections"]["detections"]) for m in result["messages"]] == [1, 0, 1, 0]
        assert result["messages"][0]["meta"]["capture_ts_utc"] == "2023-11-14T22:13:20.000Z"


def test_the_stream_route_answers_503_when_nothing_can_decode_or_the_streamer_is_not_configured(tmp_path: Path, stream: ThreadingHTTPServer) -> None:
    for url in _serving(tmp_path, CONFIG):
        assert _call(url + "/loopback/run", {"stream": True})[0] == 409
    port = stream.server_address[1]
    config = {**CONFIG, "loopback": {"enabled": True, "streamer": {"url": f"http://127.0.0.1:{port}/stream", "maxFrameBytes": 4096}}}

    def no_decoder(jpeg: bytes) -> Frame:
        raise StreamUnavailable("Pillow is not installed")

    for url in _serving(tmp_path, config, stream_decoder=no_decoder):
        status, answer = _call(url + "/loopback/run", {"stream": True})
        assert status == 503 and "Pillow" in answer["error"]


def test_a_folder_source_of_a_real_pgm_runs(tmp_path: Path) -> None:
    folder = tmp_path / "frames"
    folder.mkdir()
    (folder / "a.pgm").write_bytes(b"P5\n8 8\n255\n" + bytes([255]) * 64)
    config = {**CONFIG, "sources": [{"name": "disk", "kind": "folder", "path": "frames"}]}
    for url in _serving(tmp_path, config):
        status, result = _call(url + "/loopback/run", {"source": "disk", "frames": 5})
        assert status == 200 and result["frames"] == 1 and result["detections"] == 1


def test_a_bad_block_in_the_file_refuses_the_whole_file(tmp_path: Path) -> None:
    with pytest.raises(VisionConfigError):
        parse_vision_config({**CONFIG, "loopback": {"enabled": 1}}, tmp_path)


def test_source_items_name_the_frames_after_the_source(tmp_path: Path) -> None:
    config = parse_vision_config(CONFIG, tmp_path)
    items = list(source_items(config.sources["cam0"], 2, wall_clock=lambda: 5.0))
    assert [i[1] for i in items] == ["cam0-0", "cam0-1"] and all(i[2] == 5.0 for i in items)
    assert json.dumps(config.loopback.describe())  # type: ignore[union-attr]
