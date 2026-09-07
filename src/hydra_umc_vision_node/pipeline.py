# =============================================================================
# HYDRA-UMC-VISION-NODE - src/hydra_umc_vision_node/pipeline.py
# Copyright (C) 2026 JuanenRac (Electro Hobby 3D) <electrohobby3d@gmail.com>
# GPL-3.0 - see LICENSE
# =============================================================================
"""Real, hardware-independent description of this node's perception
pipeline: what stages it is made of, and which real piece of hardware
(camera, Hailo-8 accelerator, neither) each stage needs to actually run.

This is honestly a manifest of the pipeline's SHAPE, not the pipeline
itself - there is no real Hailo-8 runtime or camera capture loop behind
it yet (see main.py's own docstring for why: this integration parent
was never meant to run that logic itself, and it needs real CM5+Hailo-8
hardware this environment does not have). What IS real: a single,
inspectable source of truth for which stages exist and what each one
depends on, which `hardware.py`'s degraded-mode detection uses to
compute the actual subset of stages runnable on whatever hardware is
really present right now.
"""
from __future__ import annotations

from dataclasses import dataclass

PIPELINE_VERSION = "0.1.0"


@dataclass(frozen=True)
class PipelineStage:
    name: str
    description: str
    requires_camera: bool
    requires_accelerator: bool


# The real v0 shape of the perception pipeline this node's README
# describes: capture frames -> preprocess -> run Hailo-8 inference ->
# postprocess detections -> publish results. Order matters (each stage
# consumes the previous one's output) but this module does not execute
# it - see the module docstring.
DEFAULT_STAGES: tuple[PipelineStage, ...] = (
    PipelineStage(
        name="capture",
        description="Read a raw frame from the attached camera.",
        requires_camera=True,
        requires_accelerator=False,
    ),
    PipelineStage(
        name="preprocess",
        description="Resize/normalize a captured frame for inference.",
        requires_camera=False,
        requires_accelerator=False,
    ),
    PipelineStage(
        name="inference",
        description="Run the compiled HEF model on the Hailo-8 accelerator.",
        requires_camera=False,
        requires_accelerator=True,
    ),
    PipelineStage(
        name="postprocess",
        description="Decode raw inference output into real detections.",
        requires_camera=False,
        requires_accelerator=False,
    ),
    PipelineStage(
        name="publish",
        description="Publish detection results to the rest of the swarm.",
        requires_camera=False,
        requires_accelerator=False,
    ),
)


@dataclass(frozen=True)
class PipelineManifest:
    version: str
    stages: tuple[PipelineStage, ...]

    def stage(self, name: str) -> PipelineStage | None:
        return next((s for s in self.stages if s.name == name), None)


def build_manifest() -> PipelineManifest:
    """Returns the real, current pipeline manifest for this node."""
    return PipelineManifest(version=PIPELINE_VERSION, stages=DEFAULT_STAGES)


def manifest_to_dict(manifest: PipelineManifest) -> dict:
    """A plain, JSON-serializable view of the manifest - what the CLI's
    `pipeline-status` subcommand actually prints."""
    return {
        "version": manifest.version,
        "stages": [
            {
                "name": s.name,
                "description": s.description,
                "requires_camera": s.requires_camera,
                "requires_accelerator": s.requires_accelerator,
            }
            for s in manifest.stages
        ],
    }
