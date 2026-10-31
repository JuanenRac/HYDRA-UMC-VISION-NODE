# =============================================================================
# HYDRA-UMC-VISION-NODE - tests/test_characterization.py
# Copyright (C) 2026 JuanenRac (Electro Hobby 3D) <electrohobby3d@gmail.com>
# GPL-3.0-or-later - see LICENSE
# =============================================================================
"""Fix the behaviour of the HTTP surface and of the latency module as they are, before new routes are added."""
from __future__ import annotations

import json
import threading
import urllib.error
import urllib.request
from collections.abc import Iterator
from pathlib import Path

import pytest

from hydra_umc_vision_node.api import VisionNodeServer
from hydra_umc_vision_node.latency import STAGE_NAMES, LatencyRecorder
from hydra_umc_vision_node.main import build_parser


def _call(url: str, body: bytes | None = None) -> tuple[int, dict]:
    request = urllib.request.Request(url, data=body, method="POST" if body is not None else "GET")
    try:
        with urllib.request.urlopen(request, timeout=5) as response:
            return response.status, json.loads(response.read())
    except urllib.error.HTTPError as error:
        return error.code, json.loads(error.read())


@pytest.fixture
def base(tmp_path: Path) -> Iterator[str]:
    server = VisionNodeServer(("127.0.0.1", 0), tmp_path)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{server.server_address[1]}"
    server.shutdown()
    server.server_close()
    thread.join(timeout=2)


def test_default_server_answers_the_existing_routes(base: str, tmp_path: Path) -> None:
    status, body = _call(base + "/stats")
    assert status == 200 and body == {"workspace": str(tmp_path)}
    status, body = _call(base + "/pipeline-status")
    assert status == 200 and body["mode"] == "degraded_no_hardware"
    assert body["skippedStages"] == ["capture", "inference"]


@pytest.mark.parametrize("path", ["/latency", "/models", "/sources"])
def test_optional_get_routes_are_404_by_default(base: str, path: str) -> None:
    assert _call(base + path) == (404, {"error": "not found"})


@pytest.mark.parametrize("path", ["/latency/record", "/models/select", "/inference/simulate"])
def test_optional_post_routes_are_404_by_default(base: str, path: str) -> None:
    assert _call(base + path, b"{}") == (404, {"error": "not found"})


def test_serve_options_today() -> None:
    args = build_parser().parse_args(["serve"])
    assert args.latency_window is None and args.port == 8094 and args.addr == "127.0.0.1"


def test_stage_names_are_the_pipeline_order() -> None:
    assert STAGE_NAMES == ("capture", "preprocess", "inference", "postprocess", "publish")


def test_snapshot_of_an_empty_recorder() -> None:
    assert LatencyRecorder(8).snapshot() == {"window": 8, "cameras": {}}
