# =============================================================================
# HYDRA-UMC-VISION-NODE - src/hydra_umc_vision_node/compat_record.py
# Copyright (C) 2026 JuanenRac (Electro Hobby 3D) <electrohobby3d@gmail.com>
# GPL-3.0 - see LICENSE
# =============================================================================
"""What a set of detections was produced with, and whether it still fits.

A detection is only meaningful together with the model that made it: the
model's name, version and SHA-256, the Hailo architecture it was compiled
for, and the pipeline version that decoded its output. `CompatibilityRecord`
holds exactly those facts (the same field names the model registry of
HYDRA-UMC-DETECTION-HEF uses), round-trips through JSON so it can travel
with the results, and `mismatches()` names every reason it does not fit the
node that is about to use it. Nothing here runs a model or needs hardware.
"""
from __future__ import annotations

import re
from dataclasses import asdict, dataclass

from .pipeline import PIPELINE_VERSION

_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")


class CompatibilityRecordError(ValueError):
    """The record is malformed and cannot be trusted."""


@dataclass(frozen=True)
class CompatibilityRecord:
    model_name: str
    model_version: str
    model_sha256: str
    hailo_arch: str
    pipeline_version: str

    def __post_init__(self) -> None:
        for name in ("model_name", "model_version", "hailo_arch", "pipeline_version"):
            value = getattr(self, name)
            if not isinstance(value, str) or not value.strip():
                raise CompatibilityRecordError(f"{name} must be a non-empty string")
        if not isinstance(self.model_sha256, str) or not _SHA256_RE.match(self.model_sha256):
            raise CompatibilityRecordError("model_sha256 must be 64 lowercase hexadecimal digits")

    def to_dict(self) -> dict[str, str]:
        return asdict(self)

    @classmethod
    def from_dict(cls, raw: object) -> "CompatibilityRecord":
        if not isinstance(raw, dict):
            raise CompatibilityRecordError("the record must be a JSON object")
        fields = ("model_name", "model_version", "model_sha256", "hailo_arch", "pipeline_version")
        missing = [f for f in fields if f not in raw]
        if missing:
            raise CompatibilityRecordError(f"missing field(s) {missing}")
        unknown = sorted(set(raw) - set(fields))
        if unknown:
            raise CompatibilityRecordError(f"unknown field(s) {unknown}")
        return cls(**{f: raw[f] for f in fields})


def mismatches(
    record: CompatibilityRecord,
    *,
    target_arch: str,
    pipeline_version: str = PIPELINE_VERSION,
    expected_sha256: str | None = None,
) -> list[str]:
    """Every reason `record` does not fit this node; empty means it fits.

    The architecture must match exactly (the compiler bakes the chip into
    the model), the pipeline version must be the one this node decodes with,
    and, when the registry's recorded SHA-256 is given, it must match too.
    """
    reasons: list[str] = []
    if record.hailo_arch != target_arch:
        reasons.append(f"model compiled for {record.hailo_arch!r}, this node targets {target_arch!r}")
    if record.pipeline_version != pipeline_version:
        reasons.append(
            f"results decoded by pipeline {record.pipeline_version!r}, this node runs {pipeline_version!r}"
        )
    if expected_sha256 is not None and record.model_sha256 != expected_sha256.lower():
        reasons.append("model SHA-256 differs from the registry's recorded value")
    return reasons
