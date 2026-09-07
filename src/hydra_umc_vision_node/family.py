# =============================================================================
# HYDRA-UMC-VISION-NODE - src/hydra_umc_vision_node/family.py
# Copyright (C) 2026 JuanenRac (Electro Hobby 3D) <electrohobby3d@gmail.com>
# GPL-3.0 - see LICENSE
# =============================================================================
"""Real family-readiness check: this node's actual job today.

This repository is the Vision AI Node family's integration parent - it
does not run the Hailo-8 runtime, the camera pipeline, the intrusion
detection or the visual servoing math itself (see the README's own
"integration parent" framing). A real v0 for an integration parent that
runs none of that yet is checking whether its real children are actually
present and what maturity they've really reached - reading each
sibling's own real hydra-umc.project.json, the same manifest the
ecosystem-wide dashboard and updater already trust, rather than a second
hand-maintained list.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from .manifest import ChildManifest, read_child_manifest

# The four real children this node's own README names - kept here as the
# one place this project declares them.
EXPECTED_CHILDREN: tuple[str, ...] = (
    "HYDRA-UMC-VISION-STREAMER",
    "HYDRA-UMC-DETECTION-HEF",
    "HYDRA-UMC-SAFETY-ZONES",
    "HYDRA-UMC-VISUAL-SERVOING-API",
)


@dataclass(frozen=True)
class ChildStatus:
    name: str
    present: bool
    manifest: ChildManifest | None


def check_family_status(workspace_root: Path) -> list[ChildStatus]:
    """Real check of every expected child under `workspace_root`.

    `workspace_root` is the directory that contains this repo's own
    checkout as a sibling of the others (e.g. the parent of
    `HYDRA-UMC-VISION-NODE/` itself) - the same layout every real
    checkout of this ecosystem already uses.
    """
    statuses = []
    for child_name in EXPECTED_CHILDREN:
        manifest = read_child_manifest(workspace_root / child_name)
        statuses.append(ChildStatus(name=child_name, present=manifest is not None, manifest=manifest))
    return statuses
