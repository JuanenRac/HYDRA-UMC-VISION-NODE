# =============================================================================
# HYDRA-UMC-VISION-NODE - tests/test_api.py
# Copyright (C) 2026 JuanenRac (Electro Hobby 3D) <electrohobby3d@gmail.com>
# GPL-3.0 - see LICENSE
# =============================================================================
"""Real end-to-end HTTP tests: a real VisionNodeServer (ThreadingHTTPServer)
hit with real urllib requests - same convention as this family's other
test_api.py files. Reuses this repo's own tests/test_family.py fixture
shape for a real sibling-checkout workspace."""
from __future__ import annotations

import json
import threading
import urllib.error
import urllib.request
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

from hydra_umc_vision_node.api import VisionNodeServer
from hydra_umc_vision_node.family import EXPECTED_CHILDREN


def _write_manifest(workspace: Path, name: str, *, maturity: str = "scaffolding") -> None:
    repo = workspace / name
    repo.mkdir(parents=True, exist_ok=True)
    data = {
        "schema_version": "1.0",
        "ecosystem": "HYDRA-UMC",
        "name": name,
        "version": "0.0.1",
        "role": "service",
        "maturity": maturity,
    }
    (repo / "hydra-umc.project.json").write_text(json.dumps(data), encoding="utf-8")


def _get(url: str) -> tuple[int, dict]:
    try:
        with urllib.request.urlopen(url, timeout=5) as resp:
            return resp.status, json.loads(resp.read())
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read())


def _post(url: str, data: bytes) -> tuple[int, dict]:
    req = urllib.request.Request(url, data=data, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=5) as resp:
            return resp.status, json.loads(resp.read())
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read())


@contextmanager
def running_server(workspace: Path) -> Iterator[str]:
    server = VisionNodeServer(("127.0.0.1", 0), workspace)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        port = server.server_address[1]
        yield f"http://127.0.0.1:{port}"
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


def test_family_status_all_present(tmp_path: Path) -> None:
    for child in EXPECTED_CHILDREN:
        _write_manifest(tmp_path, child, maturity="functional")
    with running_server(tmp_path) as base:
        status, body = _get(f"{base}/family-status")
        assert status == 200
        assert body["allPresent"] is True
        assert body["missing"] == []
        assert len(body["children"]) == len(EXPECTED_CHILDREN)


def test_family_status_reports_missing(tmp_path: Path) -> None:
    with running_server(tmp_path) as base:
        status, body = _get(f"{base}/family-status")
        assert status == 200
        assert body["allPresent"] is False
        assert set(body["missing"]) == set(EXPECTED_CHILDREN)


def test_family_status_workspace_override(tmp_path: Path) -> None:
    other = tmp_path / "other-workspace"
    other.mkdir()
    for child in EXPECTED_CHILDREN:
        _write_manifest(other, child, maturity="functional")
    with running_server(tmp_path) as base:  # server's default workspace has nothing
        status, body = _get(f"{base}/family-status?workspace={other}")
        assert status == 200
        assert body["allPresent"] is True


def test_pipeline_status(tmp_path: Path) -> None:
    with running_server(tmp_path) as base:
        status, body = _get(f"{base}/pipeline-status")
        assert status == 200
        assert "manifest" in body
        assert body["mode"] in {"full", "degraded_no_camera", "degraded_no_accelerator", "degraded_no_hardware"}
        assert isinstance(body["runnableStages"], list)


def test_validate_frame_valid(tmp_path: Path) -> None:
    width, height, channels = 4, 4, 3
    # Deliberately NOT all-zero: validate_frame() also flags a
    # suspiciously uniform buffer (every byte identical) as an issue, so
    # a real "valid" fixture needs varied byte content, not just the
    # right length.
    data = bytes((i * 37 + 11) % 256 for i in range(width * height * channels))
    with running_server(tmp_path) as base:
        status, body = _post(f"{base}/validate-frame?width={width}&height={height}&channels={channels}", data)
        assert status == 200
        assert body["valid"] is True
        assert body["expectedBytes"] == width * height * channels
        assert body["actualBytes"] == len(data)
        assert body["issues"] == []


def test_validate_frame_truncated(tmp_path: Path) -> None:
    width, height, channels = 4, 4, 3
    data = bytes(width * height * channels - 10)  # deliberately short
    with running_server(tmp_path) as base:
        status, body = _post(f"{base}/validate-frame?width={width}&height={height}&channels={channels}", data)
        assert status == 200
        assert body["valid"] is False
        assert len(body["issues"]) >= 1


def test_validate_frame_missing_params(tmp_path: Path) -> None:
    with running_server(tmp_path) as base:
        status, body = _post(f"{base}/validate-frame?width=4", b"")
        assert status == 400


def test_stats(tmp_path: Path) -> None:
    with running_server(tmp_path) as base:
        status, body = _get(f"{base}/stats")
        assert status == 200
        assert body["workspace"] == str(tmp_path)


def test_not_found(tmp_path: Path) -> None:
    with running_server(tmp_path) as base:
        status, body = _get(f"{base}/nope")
        assert status == 404
