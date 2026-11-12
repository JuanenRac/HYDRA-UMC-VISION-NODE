# =============================================================================
# HYDRA-UMC-VISION-NODE - tests/test_main_commands.py
# Copyright (C) 2026 JuanenRac (Electro Hobby 3D) <electrohobby3d@gmail.com>
# GPL-3.0-or-later - see LICENSE
# =============================================================================
"""The command line in this process (the other CLI tests run it in a child process, which a coverage run does not see): the family status with
and without the live check, the frame check, and every refusal of `serve` before the port is bound. The HTTP server class is replaced by a
recorder, so nothing here opens a socket."""
from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from hydra_umc_vision_node import main as node_main


@pytest.fixture(autouse=True)
def clean_env(monkeypatch):
    for name in ("HYDRA_UMC_VISION_NODE_TOKEN", "HYDRA_UMC_VISION_NODE_IDENTITY_KEY_FILE", "HYDRA_UMC_VISION_NODE_HEALTH_OPEN"):
        monkeypatch.delenv(name, raising=False)


class RecordingServer:
    built: list = []

    def __init__(self, address, workspace, token, **kwargs):
        self.address, self.workspace, self.token, self.kwargs = address, workspace, token, kwargs
        self.closed = False
        RecordingServer.built.append(self)

    def serve_forever(self):
        raise KeyboardInterrupt

    def server_close(self):
        self.closed = True


@pytest.fixture
def server(monkeypatch):
    RecordingServer.built = []
    monkeypatch.setattr(node_main, "VisionNodeServer", RecordingServer)
    monkeypatch.setattr(node_main, "configure_hardware", lambda _hw: None)
    return RecordingServer.built


def status(name, present=True):
    manifest = SimpleNamespace(version="1.2.3", maturity="beta", role="a role") if present else None
    return SimpleNamespace(name=name, present=present, manifest=manifest)


def health(name, state, **extra):
    return SimpleNamespace(name=name, state=state, latency_ms=extra.get("latency_ms", 4), served_version=extra.get("served_version"), detail=extra.get("detail", ""))


# ---- identity and family status ----

def test_version_text_falls_back_when_not_installed(monkeypatch):
    def missing(_name):
        raise node_main.PackageNotFoundError()
    monkeypatch.setattr(node_main, "version", missing)
    assert "not installed" in node_main.get_version()


def test_bare_invocation_prints_the_role(capsys):
    assert node_main.main([]) == 0
    assert node_main.ROLE in capsys.readouterr().out


def test_family_status_lists_present_and_missing_children(monkeypatch, capsys, tmp_path):
    monkeypatch.setattr(node_main, "check_family_status", lambda _w: [status("a"), status("b", present=False)])
    assert node_main.main(["family-status", "--workspace", str(tmp_path)]) == 1
    out = capsys.readouterr().out
    assert "a: v1.2.3, maturity=beta" in out and "b: NOT FOUND" in out and "1 of 2 children not found: b" in out
    monkeypatch.setattr(node_main, "check_family_status", lambda _w: [status("a"), status("c")])
    assert node_main.main(["family-status", "--workspace", str(tmp_path)]) == 0
    assert "All 2 children present." in capsys.readouterr().out


def test_family_status_live_reports_up_without_service_and_down(monkeypatch, capsys, tmp_path):
    monkeypatch.setattr(node_main, "check_family_status", lambda _w: [status("a"), status("b"), status("c")])
    seen = {}

    def fake_health(statuses, host, timeout):
        seen.update(host=host, timeout=timeout)
        return [health("a", "up", served_version="2.0"), health("b", "no_service", detail="no service declared"), health("c", "down", detail="refused")]

    monkeypatch.setattr(node_main, "check_family_health", fake_health)
    assert node_main.main(["family-status", "--workspace", str(tmp_path), "--live", "--host", "10.0.0.9", "--timeout", "0.5"]) == 1
    out = capsys.readouterr().out
    assert seen == {"host": "10.0.0.9", "timeout": 0.5}
    assert "a: up, 4 ms, version 2.0" in out and "b: no service declared" in out and "c: DOWN (refused)" in out and "1 service(s) did not answer." in out

    monkeypatch.setattr(node_main, "check_family_health", lambda *_a: [health("a", "up")])
    assert node_main.main(["family-status", "--workspace", str(tmp_path), "--live"]) == 0
    assert "version not reported" in capsys.readouterr().out


# ---- pipeline status and the frame check ----

def test_pipeline_status_exit_code_follows_the_mode(monkeypatch, capsys):
    monkeypatch.setattr(node_main, "camera_available", lambda: False)
    monkeypatch.setattr(node_main, "accelerator_available", lambda: False)
    assert node_main.main(["pipeline-status"]) == 1
    assert '"' in capsys.readouterr().out
    monkeypatch.setattr(node_main, "camera_available", lambda: True)
    monkeypatch.setattr(node_main, "accelerator_available", lambda: True)
    assert node_main.main(["pipeline-status"]) == 0


def test_validate_frame_ok_invalid_and_unreadable(capsys, tmp_path):
    good = tmp_path / "good.raw"
    good.write_bytes(bytes(range(1, 13)))  # 2x2x3 bytes, not all the same
    assert node_main.main(["validate-frame", str(good), "--width", "2", "--height", "2"]) == 0
    assert "Frame OK" in capsys.readouterr().out
    short = tmp_path / "short.raw"
    short.write_bytes(bytes(5))
    assert node_main.main(["validate-frame", str(short), "--width", "2", "--height", "2"]) == 1
    assert "Frame INVALID" in capsys.readouterr().out
    assert node_main.main(["validate-frame", str(tmp_path / "none.raw"), "--width", "2", "--height", "2"]) == 2
    assert "could not read" in capsys.readouterr().out


# ---- serve: the refusals ----

def serve(*extra):
    return node_main.main(["serve", "--port", "18994", *extra])


def test_serve_refuses_a_bad_token_an_open_address_and_a_bad_identity_key(capsys, tmp_path, server):
    short = tmp_path / "short.txt"
    short.write_text("abc", encoding="utf-8")
    assert serve("--token-file", str(short)) == 2
    assert serve("--token-file", str(tmp_path / "none.txt")) == 2
    assert serve("--identity-key-file", str(tmp_path / "none.key")) == 2
    capsys.readouterr()
    assert serve("--addr", "0.0.0.0") == 2
    assert "not loopback" in capsys.readouterr().err
    assert server == []


def test_serve_refuses_a_latency_window_and_a_simulation_file_that_cannot_be_used(capsys, tmp_path, server):
    assert serve("--latency-window", "0") == 2
    assert "--latency-window" in capsys.readouterr().err
    broken = tmp_path / "sim.json"
    broken.write_text("{not json", encoding="utf-8")
    assert serve("--vision-sim", str(broken)) == 2
    assert "--vision-sim" in capsys.readouterr().err
    assert server == []


# ---- serve: the start ----

def test_serve_warns_about_a_workspace_that_is_not_a_folder_and_starts_closed(capsys, tmp_path, server):
    assert serve("--workspace", str(tmp_path / "missing")) == 0
    captured = capsys.readouterr()
    assert "is not a directory" in captured.err and "no token, loopback only" in captured.out and "shutting down" in captured.out
    built = server[0]
    assert built.address == ("127.0.0.1", 18994) and built.token == "" and built.closed
    assert built.health_open is False and built.identity_key is None
    assert built.kwargs["latency"] is None and built.kwargs["vision"] is None


def test_serve_with_a_latency_window_a_token_and_health_open(tmp_path, capsys, server):
    token = tmp_path / "token.txt"
    token.write_text("t" * 20, encoding="utf-8")
    assert serve("--workspace", str(tmp_path), "--token-file", str(token), "--latency-window", "50", "--health-open") == 0
    built = server[0]
    assert built.token == "t" * 20 and built.kwargs["latency"] is not None and built.health_open is True
    assert "Bearer token required" in capsys.readouterr().out


def test_serve_with_the_simulation_says_what_is_on_and_asks_the_registry(monkeypatch, tmp_path, capsys, server):
    registry = SimpleNamespace(enabled=True, model_name="yolo", target_arch="hailo8")
    vision = SimpleNamespace(hardware=None, registry=registry, loopback=SimpleNamespace(enabled=True))
    monkeypatch.setattr(node_main, "load_vision_config", lambda _path: vision)
    monkeypatch.setattr(node_main, "resolve_model", lambda reg: SimpleNamespace(state="ready"))
    monkeypatch.setattr(node_main, "report_loaded", lambda reg, gate: {"gate": gate.state})
    assert serve("--workspace", str(tmp_path), "--vision-sim", "ignored.json") == 0
    built = server[0]
    assert built.model_gate.state == "ready" and built.model_report == {"gate": "ready"}
    out = capsys.readouterr().out
    assert "model registry: ready (yolo on hailo8)" in out and "SIMULATION ON" in out and "LOOPBACK ON" in out and "MODEL REGISTRY ON" in out


def test_serve_with_a_simulation_and_no_registry_or_loopback(monkeypatch, tmp_path, capsys, server):
    vision = SimpleNamespace(hardware=None, registry=None, loopback=None)
    monkeypatch.setattr(node_main, "load_vision_config", lambda _path: vision)
    assert serve("--workspace", str(tmp_path), "--vision-sim", "ignored.json") == 0
    out = capsys.readouterr().out
    assert "SIMULATION ON" in out and "LOOPBACK ON" not in out and "MODEL REGISTRY ON" not in out
    assert not hasattr(server[0], "model_gate")
