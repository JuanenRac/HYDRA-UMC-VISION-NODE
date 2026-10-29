# =============================================================================
# HYDRA-UMC-VISION-NODE - tests/test_live.py
# Copyright (C) 2026 JuanenRac (Electro Hobby 3D) <electrohobby3d@gmail.com>
# GPL-3.0-or-later - see LICENSE
# =============================================================================
from __future__ import annotations

import json
import socket
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

from hydra_umc_vision_node.family import EXPECTED_CHILDREN, check_family_status
from hydra_umc_vision_node.live import check_family_health, probe_service
from hydra_umc_vision_node.main import main


class _Stats(BaseHTTPRequestHandler):
    status = 200
    body = b'{"version": "1.2.3"}'

    def do_GET(self) -> None:  # noqa: N802 - the name http.server asks for
        self.send_response(self.status)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(self.body)

    def log_message(self, *args) -> None:
        pass


def _serve(handler):
    server = HTTPServer(("127.0.0.1", 0), handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def _manifest(workspace: Path, name: str, port: int | None) -> None:
    repo = workspace / name
    repo.mkdir(parents=True, exist_ok=True)
    data = {"name": name, "version": "0.1.0", "role": "service", "maturity": "functional"}
    if port is not None:
        data["service"] = {"port": port, "health_path": "/stats"}
    (repo / "hydra-umc.project.json").write_text(json.dumps(data), encoding="utf-8")


def test_a_service_that_answers_reports_latency_and_version() -> None:
    server = _serve(_Stats)
    try:
        state, latency, version, detail = probe_service("127.0.0.1", server.server_port, "/stats")
    finally:
        server.shutdown()
    assert (state, version, detail) == ("up", "1.2.3", "")
    assert latency is not None and latency >= 0


def test_an_error_status_and_a_closed_port_are_down() -> None:
    class Broken(_Stats):
        status = 503

    server = _serve(Broken)
    try:
        assert probe_service("127.0.0.1", server.server_port, "/stats")[0] == "down"
    finally:
        server.shutdown()
    state, latency, _, detail = probe_service("127.0.0.1", _free_port(), "/stats", timeout=0.5)
    assert state == "down" and latency is None and detail


def test_children_without_a_declared_service_are_not_probed(tmp_path: Path) -> None:
    server = _serve(_Stats)
    try:
        for index, name in enumerate(EXPECTED_CHILDREN):
            _manifest(tmp_path, name, server.server_port if index == 1 else None)
        health = check_family_health(check_family_status(tmp_path))
    finally:
        server.shutdown()
    assert [h.state for h in health] == ["no_service", "up", "no_service", "no_service"]


def test_family_status_live_fails_when_a_service_does_not_answer(tmp_path: Path, capsys) -> None:
    for name in EXPECTED_CHILDREN:
        _manifest(tmp_path, name, _free_port())
    assert main(["family-status", "--workspace", str(tmp_path)]) == 0
    assert main(["family-status", "--workspace", str(tmp_path), "--live", "--timeout", "0.3"]) == 1
    assert "DOWN" in capsys.readouterr().out
