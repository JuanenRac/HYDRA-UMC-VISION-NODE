# =============================================================================
# HYDRA-UMC-VISION-NODE - tests/test_manifest.py
# Copyright (C) 2026 JuanenRac (Electro Hobby 3D) <electrohobby3d@gmail.com>
# GPL-3.0 - see LICENSE
# =============================================================================
from __future__ import annotations

import json
from pathlib import Path

from hydra_umc_vision_node.manifest import read_child_manifest


def _write_manifest(repo_path: Path, **fields: str) -> None:
    repo_path.mkdir(parents=True, exist_ok=True)
    data = {
        "schema_version": "1.0",
        "ecosystem": "HYDRA-UMC",
        "name": "HYDRA-UMC-EXAMPLE",
        "version": "0.0.1",
        "role": "service",
        "maturity": "scaffolding",
        **fields,
    }
    (repo_path / "hydra-umc.project.json").write_text(json.dumps(data), encoding="utf-8")


def test_reads_a_real_manifest(tmp_path: Path) -> None:
    repo = tmp_path / "HYDRA-UMC-EXAMPLE"
    _write_manifest(repo, version="0.0.5", maturity="functional", role="tool")

    manifest = read_child_manifest(repo)

    assert manifest is not None
    assert manifest.name == "HYDRA-UMC-EXAMPLE"
    assert manifest.version == "0.0.5"
    assert manifest.maturity == "functional"
    assert manifest.role == "tool"


def test_missing_repository_returns_none(tmp_path: Path) -> None:
    assert read_child_manifest(tmp_path / "does-not-exist") is None


def test_missing_manifest_file_returns_none(tmp_path: Path) -> None:
    repo = tmp_path / "empty-repo"
    repo.mkdir()

    assert read_child_manifest(repo) is None


def test_malformed_json_returns_none_not_a_crash(tmp_path: Path) -> None:
    repo = tmp_path / "broken-repo"
    repo.mkdir()
    (repo / "hydra-umc.project.json").write_text("{not valid json", encoding="utf-8")

    assert read_child_manifest(repo) is None


def test_missing_required_field_returns_none(tmp_path: Path) -> None:
    repo = tmp_path / "incomplete-repo"
    repo.mkdir()
    (repo / "hydra-umc.project.json").write_text(json.dumps({"name": "X"}), encoding="utf-8")

    assert read_child_manifest(repo) is None


def test_non_text_or_empty_required_manifest_field_returns_none(tmp_path: Path) -> None:
    repo = tmp_path / "invalid-types"
    _write_manifest(repo, version=42)  # type: ignore[arg-type]
    assert read_child_manifest(repo) is None

    _write_manifest(repo, maturity="   ")
    assert read_child_manifest(repo) is None
