# =============================================================================
# HYDRA-UMC-VISION-NODE - tests/test_cli.py
# Copyright (C) 2026 JuanenRac (Electro Hobby 3D) <electrohobby3d@gmail.com>
# GPL-3.0 - see LICENSE
# =============================================================================
from __future__ import annotations

import json
from pathlib import Path

import pytest

from hydra_umc_vision_node.main import main


def _write_manifest(workspace: Path, name: str) -> None:
    repo = workspace / name
    repo.mkdir(parents=True, exist_ok=True)
    data = {
        "schema_version": "1.0", "ecosystem": "HYDRA-UMC", "name": name,
        "version": "0.0.2", "role": "service", "maturity": "functional",
    }
    (repo / "hydra-umc.project.json").write_text(json.dumps(data), encoding="utf-8")


def test_bare_invocation_prints_identity(capsys: pytest.CaptureFixture[str]) -> None:
    exit_code = main([])

    captured = capsys.readouterr()
    assert exit_code == 0
    assert "HYDRA-UMC-VISION-NODE v" in captured.out


def test_family_status_all_present(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    for child in (
        "HYDRA-UMC-VISION-STREAMER",
        "HYDRA-UMC-DETECTION-HEF",
        "HYDRA-UMC-SAFETY-ZONES",
        "HYDRA-UMC-VISUAL-SERVOING-API",
    ):
        _write_manifest(tmp_path, child)

    exit_code = main(["family-status", "--workspace", str(tmp_path)])

    captured = capsys.readouterr()
    assert exit_code == 0
    assert "All 4 children present" in captured.out
    assert "maturity=functional" in captured.out


def test_family_status_missing_children(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    exit_code = main(["family-status", "--workspace", str(tmp_path)])

    captured = capsys.readouterr()
    assert exit_code == 1
    assert "NOT FOUND" in captured.out
    assert "4 of 4 children not found" in captured.out


def test_pipeline_status_prints_real_json_and_reports_actual_hardware(
    capsys: pytest.CaptureFixture[str],
) -> None:
    exit_code = main(["pipeline-status"])

    captured = capsys.readouterr()
    output = json.loads(captured.out)
    assert output["manifest"]["stages"][0]["name"] == "capture"
    assert isinstance(output["camera_present"], bool)
    assert isinstance(output["accelerator_present"], bool)
    assert output["mode"] in (
        "full",
        "degraded_no_camera",
        "degraded_no_accelerator",
        "degraded_no_hardware",
    )
    # This CI/dev machine has no real camera or Hailo-8 accelerator -
    # the honest, real answer is a degraded mode, not "full".
    assert exit_code == 1
    assert output["mode"] != "full"
    assert "capture" in output["skipped_stages"]
    assert "inference" in output["skipped_stages"]


def test_validate_frame_accepts_a_real_correctly_sized_frame(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    frame_path = tmp_path / "frame.raw"
    frame_path.write_bytes(bytes((i % 251) for i in range(4 * 4 * 3)))

    exit_code = main(["validate-frame", str(frame_path), "--width", "4", "--height", "4"])

    captured = capsys.readouterr()
    assert exit_code == 0
    assert "Frame OK" in captured.out


def test_validate_frame_rejects_a_real_truncated_file(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    frame_path = tmp_path / "frame.raw"
    full = bytes((i % 251) for i in range(4 * 4 * 3))
    frame_path.write_bytes(full[: len(full) // 2])  # a real truncated write

    exit_code = main(["validate-frame", str(frame_path), "--width", "4", "--height", "4"])

    captured = capsys.readouterr()
    assert exit_code == 1
    assert "size_mismatch" in captured.out


def test_validate_frame_reports_a_missing_file(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    exit_code = main(
        ["validate-frame", str(tmp_path / "does-not-exist.raw"), "--width", "4", "--height", "4"]
    )

    captured = capsys.readouterr()
    assert exit_code == 2
    assert "could not read" in captured.out
