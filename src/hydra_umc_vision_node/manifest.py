# =============================================================================
# HYDRA-UMC-VISION-NODE - src/hydra_umc_vision_node/manifest.py
# Copyright (C) 2026 JuanenRac (Electro Hobby 3D) <electrohobby3d@gmail.com>
# GPL-3.0 - see LICENSE
# =============================================================================
"""Real, minimal reader for a sibling repository's own hydra-umc.project.json.

Deliberately a small local reader, not a dependency on
HYDRA-UMC-UPDATER's own full project_manifest.py validator (that lives in
a different repository) - this only needs the handful of fields the
family status check actually displays, read defensively so a missing or
malformed sibling manifest degrades to "not found", never a crash.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

MANIFEST_FILE = "hydra-umc.project.json"


@dataclass(frozen=True)
class ChildManifest:
    name: str
    version: str
    maturity: str
    role: str


def read_child_manifest(repo_path: Path) -> ChildManifest | None:
    """Read `repo_path/hydra-umc.project.json` for real, or return None.

    None covers every real reason this can fail - the sibling isn't
    checked out, its manifest is missing, or it's malformed JSON/missing
    a field - callers treat all of these as "not ready", not an error.
    """
    manifest_path = repo_path / MANIFEST_FILE
    if not manifest_path.is_file():
        return None
    try:
        data = json.loads(manifest_path.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            return None
        required = ("name", "version", "maturity", "role")
        if any(not isinstance(data.get(field), str) or not data[field].strip() for field in required):
            return None
        return ChildManifest(
            name=data["name"],
            version=data["version"],
            maturity=data["maturity"],
            role=data["role"],
        )
    except (OSError, json.JSONDecodeError, KeyError, TypeError):
        return None
