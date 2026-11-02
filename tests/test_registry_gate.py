# =============================================================================
# HYDRA-UMC-VISION-NODE - tests/test_registry_gate.py
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
from urllib.parse import parse_qs, urlparse

import pytest

from hydra_umc_vision_node.api import VisionNodeServer
from hydra_umc_vision_node.hardware import PipelineMode, active_stages, determine_mode
from hydra_umc_vision_node.pipeline import build_manifest
from hydra_umc_vision_node.registry_gate import GateError, GateResult, mode_allowed, normalize_arch, parse_registry_config, report_loaded, resolve_model
from hydra_umc_vision_node.vision_sim import VisionConfigError, parse_vision_config

ENTRY = {"name": "yolo", "version": "1.2.0", "sha256": "b" * 64, "hailo_arch": "hailo8", "hef_path": "yolo.hef", "task": "detect"}
SEEN: list[dict[str, str]] = []
ANSWER: dict[str, Any] = {}


class FakeRegistry(BaseHTTPRequestHandler):
    def log_message(self, *args: object) -> None:
        pass

    def do_GET(self) -> None:  # noqa: N802
        parts = urlparse(self.path)
        SEEN.append({k: v[0] for k, v in parse_qs(parts.query).items()} | {"path": parts.path})
        status = ANSWER.get("status", 200)
        body = ANSWER.get("raw")
        if body is None:
            body = json.dumps(ANSWER.get("json", {"outcome": "READY", "isReady": True, "detail": "ok", "entry": ENTRY})).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


@pytest.fixture
def registry() -> Iterator[int]:
    SEEN.clear()
    ANSWER.clear()
    server = ThreadingHTTPServer(("127.0.0.1", 0), FakeRegistry)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield server.server_address[1]
    server.shutdown()
    server.server_close()
    thread.join(timeout=2)


def _config(port: int, **change: Any) -> Any:
    block = {"enabled": True, "url": f"http://127.0.0.1:{port}", "modelName": "yolo", "targetArch": "HAILO8", **change}
    config = parse_registry_config(block)
    assert config is not None
    return config


def test_the_architecture_is_written_the_way_the_registry_writes_it() -> None:
    assert normalize_arch("HAILO8") == "hailo8"
    assert normalize_arch(" Hailo-8L ") == "hailo8l"
    assert normalize_arch("hailo15h") == "hailo15h"
    for bad in ("hailo9", "", 8, None):
        with pytest.raises(GateError):
            normalize_arch(bad)


def test_a_ready_model_is_resolved_with_the_canonical_architecture(registry: int) -> None:
    result = resolve_model(_config(registry, task="detect"))
    assert result.state == "ready" and result.ready is True and result.outcome == "READY"
    assert result.model == {"name": "yolo", "version": "1.2.0", "sha256": "b" * 64, "hailo_arch": "hailo8"}
    assert SEEN == [{"name": "yolo", "target_arch": "hailo8", "task": "detect", "path": "/registry/load"}]


@pytest.mark.parametrize("answer, state", [
    ({"json": {"outcome": "ARCH_MISMATCH", "isReady": False, "detail": "compiled for hailo8l", "entry": ENTRY}}, "not_ready"),
    ({"status": 404, "json": {"error": "no model named 'yolo'"}}, "not_ready"),
    ({"status": 502, "json": {"error": "could not read registry"}}, "not_ready"),
    ({"json": {"outcome": "READY"}}, "invalid_answer"),
    ({"json": {"isReady": "yes"}}, "invalid_answer"),
    ({"json": ["isReady"]}, "invalid_answer"),
    ({"raw": b"<html>"}, "invalid_answer"),
    ({"raw": b"x" * (64 * 1024 + 10)}, "invalid_answer"),
])
def test_everything_but_an_explicit_ready_keeps_the_node_out_of_the_full_mode(registry: int, answer: dict, state: str) -> None:
    ANSWER.update(answer)
    result = resolve_model(_config(registry))
    assert result.state == state and result.ready is False


def test_a_registry_that_does_not_answer_is_not_ready() -> None:
    result = resolve_model(_config(1, timeoutSeconds=0.5))
    assert result.state == "unreachable" and result.ready is False


def test_the_gate_only_asks_for_a_condition_when_it_is_on_and_required() -> None:
    ready, down = GateResult("ready", True), GateResult("unreachable", False)
    config = parse_registry_config({"enabled": True, "url": "http://127.0.0.1:8095", "modelName": "yolo", "targetArch": "hailo8"})
    assert mode_allowed(config, ready) is True and mode_allowed(config, down) is False and mode_allowed(config, None) is False
    assert mode_allowed(None, None) is None
    off = parse_registry_config({"url": "http://127.0.0.1:8095", "modelName": "yolo", "targetArch": "hailo8"})
    assert off is not None and off.enabled is False and mode_allowed(off, None) is None
    lax = parse_registry_config({"enabled": True, "requireReady": False, "url": "http://127.0.0.1:8095", "modelName": "yolo", "targetArch": "hailo8"})
    assert mode_allowed(lax, down) is None


def test_the_full_mode_needs_a_ready_model_only_when_the_question_is_asked() -> None:
    assert determine_mode(True, True) is PipelineMode.FULL
    assert determine_mode(True, True, None) is PipelineMode.FULL
    assert determine_mode(True, True, True) is PipelineMode.FULL
    assert determine_mode(True, True, False) is PipelineMode.DEGRADED_MODEL_NOT_READY
    assert determine_mode(False, True, False) is PipelineMode.DEGRADED_NO_CAMERA
    assert determine_mode(False, False, True) is PipelineMode.DEGRADED_NO_HARDWARE
    runnable, skipped = active_stages(build_manifest(), PipelineMode.DEGRADED_MODEL_NOT_READY)
    assert [s.name for s in skipped] == ["inference"] and "capture" in [s.name for s in runnable]


@pytest.mark.parametrize("block", [
    "text", {"url": "http://127.0.0.1:1"}, {"unknown": 1}, {"enabled": "yes", "url": "http://127.0.0.1:1", "modelName": "m", "targetArch": "hailo8"},
    {"url": "http://example.com", "modelName": "m", "targetArch": "hailo8"}, {"url": "https://127.0.0.1", "modelName": "m", "targetArch": "hailo8"},
    {"url": "http://127.0.0.1:1", "modelName": "a b", "targetArch": "hailo8"}, {"url": "http://127.0.0.1:1", "modelName": "m", "targetArch": "x"},
    {"url": "http://127.0.0.1:1", "modelName": "m", "targetArch": "hailo8", "timeoutSeconds": 0},
    {"url": "http://127.0.0.1:1", "modelName": "m", "targetArch": "hailo8", "timeoutSeconds": 61},
    {"url": "http://127.0.0.1:1", "modelName": "m", "targetArch": "hailo8", "task": "a b"},
    {"url": "http://127.0.0.1:1", "modelName": "m", "targetArch": "hailo8", "requireReady": 1},
])
def test_a_bad_registry_block_is_refused(block: object) -> None:
    with pytest.raises(GateError):
        parse_registry_config(block)


def test_a_remote_registry_needs_allow_remote() -> None:
    config = parse_registry_config({"url": "http://registry.local:8095/base/", "allowRemote": True, "modelName": "m", "targetArch": "hailo8l"})
    assert config is not None and (config.host, config.port, config.path) == ("registry.local", 8095, "/base")


# ------------------------------------------------------------------------------------------------------ the routes


def _call(url: str, body: object = None) -> tuple[int, dict]:
    data = json.dumps(body).encode() if body is not None else None
    request = urllib.request.Request(url, data=data, method="POST" if body is not None else "GET")
    try:
        with urllib.request.urlopen(request, timeout=5) as response:
            return response.status, json.loads(response.read())
    except urllib.error.HTTPError as error:
        return error.code, json.loads(error.read())


def _serving(tmp_path: Path, port: int, **block: Any) -> Iterator[tuple[str, VisionNodeServer]]:
    config = {"host": {"devices": ["cpu"]}, "registry": {"enabled": True, "url": f"http://127.0.0.1:{port}", "modelName": "yolo", "targetArch": "HAILO8", **block}}
    server = VisionNodeServer(("127.0.0.1", 0), tmp_path, vision=parse_vision_config(config, tmp_path))
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{server.server_address[1]}", server
    server.shutdown()
    server.server_close()
    thread.join(timeout=2)


def test_resolve_asks_the_registry_and_the_status_reports_the_gate(tmp_path: Path, registry: int, monkeypatch: pytest.MonkeyPatch) -> None:
    import hydra_umc_vision_node.api as api

    monkeypatch.setattr(api, "camera_available", lambda: True)
    monkeypatch.setattr(api, "accelerator_available", lambda: True)
    for url, _server in _serving(tmp_path, registry):
        status, before = _call(url + "/pipeline-status")
        assert status == 200 and before["mode"] == "degraded_model_not_ready" and before["modelGate"]["state"] == "not_asked"
        assert _call(url + "/models/registry")[1]["last"] is None
        status, resolved = _call(url + "/models/resolve", {})
        assert status == 200 and resolved["state"] == "ready" and resolved["config"]["targetArch"] == "hailo8"
        after = _call(url + "/pipeline-status")[1]
        assert after["mode"] == "full" and after["modelGate"]["model"]["version"] == "1.2.0"
        ANSWER.update({"json": {"outcome": "CHECKSUM_MISMATCH", "isReady": False, "detail": "the file changed", "entry": ENTRY}})
        _call(url + "/models/resolve", {})
        assert _call(url + "/pipeline-status")[1]["mode"] == "degraded_model_not_ready"
        assert _call(url + "/models/resolve", {"extra": 1})[0] == 400


def test_without_the_gate_the_status_is_what_it_was(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    import hydra_umc_vision_node.api as api

    monkeypatch.setattr(api, "camera_available", lambda: True)
    monkeypatch.setattr(api, "accelerator_available", lambda: True)
    off = {"host": {"devices": ["cpu"]}, "registry": {"enabled": False, "url": "http://127.0.0.1:1", "modelName": "yolo", "targetArch": "hailo8"}}
    server = VisionNodeServer(("127.0.0.1", 0), tmp_path, vision=parse_vision_config(off, tmp_path))
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        url = f"http://127.0.0.1:{server.server_address[1]}"
        answer = _call(url + "/pipeline-status")[1]
        assert answer["mode"] == "full" and "modelGate" not in answer
        assert _call(url + "/models/resolve", {})[0] == 404 and _call(url + "/models/registry")[0] == 404
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


def test_a_bad_registry_block_refuses_the_file(tmp_path: Path) -> None:
    with pytest.raises(VisionConfigError):
        parse_vision_config({"registry": {"enabled": True}}, tmp_path)


# ------------------------------------------------------------------------------------------------- report of the loaded model


def _reporting(port: int, **extra: Any):
    config = parse_registry_config({"enabled": True, "url": f"http://127.0.0.1:{port}", "modelName": "yolo", "targetArch": "hailo8", "reportLoaded": True, **extra})
    assert config is not None
    return config


READY = GateResult("ready", True, "READY", "ok", {"name": "yolo", "version": "1.2.0", "sha256": "b" * 64, "hailo_arch": "hailo8"})


def test_the_report_is_off_unless_the_block_asks_for_it() -> None:
    config = parse_registry_config({"enabled": True, "url": "http://127.0.0.1:1", "modelName": "yolo", "targetArch": "hailo8"})
    assert config is not None and config.report_loaded is False and config.node_name == "vision-node"
    calls: list[str] = []
    assert report_loaded(config, READY, lambda *a: calls.append("x") or (200, b"{}")) == {"state": "off"} and calls == []


def test_the_report_sends_the_identity_and_the_token_and_keeps_the_verdict(tmp_path: Path) -> None:
    token = tmp_path / "token.txt"
    token.write_text("secret-token\n", encoding="utf-8")
    seen: list[tuple[str, dict, str]] = []

    def post(config: Any, target: str, body: bytes, bearer: str) -> tuple[int, bytes]:
        seen.append((target, json.loads(body), bearer))
        return 200, json.dumps({"verdict": "current"}).encode()

    config = _reporting(8095, nodeName="cell-a", tokenFile=str(token))
    result = report_loaded(config, READY, post)
    assert result == {"state": "reported", "verdict": "current"}
    assert seen == [("/models/loaded", {"node": "cell-a", "model_name": "yolo", "model_version": "1.2.0", "model_sha256": "b" * 64, "hailo_arch": "hailo8"}, "secret-token")]
    assert "secret-token" not in json.dumps(result) and config.describe()["tokenFile"] is True and "secret" not in json.dumps(config.describe())


def test_the_report_never_raises_and_changes_nothing_when_it_fails(tmp_path: Path) -> None:
    config = _reporting(8095)
    assert report_loaded(config, None)["state"] == "skipped"
    assert report_loaded(config, GateResult("not_ready", False))["state"] == "skipped"

    def broken(*args: Any) -> tuple[int, bytes]:
        raise ConnectionRefusedError

    assert report_loaded(config, READY, broken) == {"state": "unreachable", "detail": "ConnectionRefusedError"}
    assert report_loaded(config, READY, lambda *a: (401, b'{"error": "token"}'))["state"] == "refused"
    assert report_loaded(config, READY, lambda *a: (200, b"not json"))["state"] == "invalid_answer"
    assert report_loaded(_reporting(8095, tokenFile=str(tmp_path / "missing")), READY, lambda *a: (200, b"{}")) == {"state": "token_unreadable"}


@pytest.mark.parametrize("extra", [{"reportLoaded": "yes"}, {"nodeName": "a b"}, {"tokenFile": ""}, {"tokenFile": 3}])
def test_a_bad_report_setting_is_refused(extra: dict) -> None:
    block = {"url": "http://127.0.0.1:1", "modelName": "m", "targetArch": "hailo8", **extra}
    with pytest.raises(GateError):
        parse_registry_config(block)


def test_resolve_reports_the_loaded_model_through_a_real_post(tmp_path: Path, registry: int) -> None:
    posted: list[bytes] = []

    def do_post(self: BaseHTTPRequestHandler) -> None:
        posted.append(self.rfile.read(int(self.headers["Content-Length"])))
        body = json.dumps({"verdict": "outdated"}).encode()
        self.send_response(200)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    FakeRegistry.do_POST = do_post  # type: ignore[attr-defined]
    try:
        for url, server in _serving(tmp_path, registry, reportLoaded=True):
            status, resolved = _call(url + "/models/resolve", {})
            assert status == 200 and resolved["report"] == {"state": "reported", "verdict": "outdated"}
            assert _call(url + "/models/registry")[1]["report"]["verdict"] == "outdated" and json.loads(posted[0])["model_sha256"] == "b" * 64
    finally:
        del FakeRegistry.do_POST  # type: ignore[attr-defined]
