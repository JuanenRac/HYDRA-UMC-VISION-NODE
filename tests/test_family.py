# =============================================================================
# HYDRA-UMC-VISION-NODE - tests/test_family.py
# Copyright (C) 2026 JuanenRac (Electro Hobby 3D) <electrohobby3d@gmail.com>
# GPL-3.0 - see LICENSE
# =============================================================================
from __future__ import annotations

import json
from pathlib import Path

from hydra_umc_vision_node.family import EXPECTED_CHILDREN, check_family_status


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


def test_expected_children_matches_the_real_readme_family() -> None:
    assert set(EXPECTED_CHILDREN) == {
        "HYDRA-UMC-VISION-STREAMER",
        "HYDRA-UMC-DETECTION-HEF",
        "HYDRA-UMC-SAFETY-ZONES",
        "HYDRA-UMC-VISUAL-SERVOING-API",
    }


def test_all_children_present(tmp_path: Path) -> None:
    for child in EXPECTED_CHILDREN:
        _write_manifest(tmp_path, child, maturity="functional")

    statuses = check_family_status(tmp_path)

    assert len(statuses) == len(EXPECTED_CHILDREN)
    assert all(status.present for status in statuses)
    assert all(status.manifest is not None and status.manifest.maturity == "functional" for status in statuses)


def test_some_children_missing(tmp_path: Path) -> None:
    _write_manifest(tmp_path, "HYDRA-UMC-SAFETY-ZONES")
    _write_manifest(tmp_path, "HYDRA-UMC-DETECTION-HEF")

    statuses = check_family_status(tmp_path)

    present = {status.name for status in statuses if status.present}
    missing = {status.name for status in statuses if not status.present}
    assert present == {"HYDRA-UMC-SAFETY-ZONES", "HYDRA-UMC-DETECTION-HEF"}
    assert missing == {"HYDRA-UMC-VISION-STREAMER", "HYDRA-UMC-VISUAL-SERVOING-API"}


def test_empty_workspace_reports_all_missing(tmp_path: Path) -> None:
    statuses = check_family_status(tmp_path)

    assert all(not status.present for status in statuses)
