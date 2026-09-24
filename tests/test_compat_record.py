# =============================================================================
# HYDRA-UMC-VISION-NODE - tests/test_compat_record.py
# Copyright (C) 2026 JuanenRac (Electro Hobby 3D) <electrohobby3d@gmail.com>
# GPL-3.0 - see LICENSE
# =============================================================================
from __future__ import annotations

import json

import pytest

from hydra_umc_vision_node.compat_record import CompatibilityRecord, CompatibilityRecordError, mismatches
from hydra_umc_vision_node.pipeline import PIPELINE_VERSION

SHA = "ab" * 32


def _record(**overrides) -> CompatibilityRecord:
    data = dict(model_name="yolo", model_version="1.2.0", model_sha256=SHA, hailo_arch="hailo8", pipeline_version=PIPELINE_VERSION)
    data.update(overrides)
    return CompatibilityRecord(**data)


def test_a_matching_record_has_no_mismatches() -> None:
    assert mismatches(_record(), target_arch="hailo8") == []


def test_it_round_trips_through_json() -> None:
    record = _record()
    assert CompatibilityRecord.from_dict(json.loads(json.dumps(record.to_dict()))) == record


def test_every_reason_is_reported_not_just_the_first() -> None:
    reasons = mismatches(_record(hailo_arch="hailo15h", pipeline_version="9.9.9"), target_arch="hailo8", expected_sha256="cd" * 32)
    assert len(reasons) == 3
    assert any("hailo15h" in r for r in reasons)
    assert any("9.9.9" in r for r in reasons)
    assert any("SHA-256" in r for r in reasons)


def test_the_expected_digest_is_compared_case_insensitively() -> None:
    assert mismatches(_record(), target_arch="hailo8", expected_sha256=SHA.upper()) == []


@pytest.mark.parametrize(
    "change",
    [{"model_name": " "}, {"model_version": ""}, {"hailo_arch": ""}, {"pipeline_version": 3}, {"model_sha256": "xyz"}, {"model_sha256": SHA.upper()}],
)
def test_malformed_values_are_refused(change) -> None:
    with pytest.raises(CompatibilityRecordError):
        _record(**change)


def test_from_dict_refuses_missing_unknown_and_non_object_input() -> None:
    good = _record().to_dict()
    with pytest.raises(CompatibilityRecordError):
        CompatibilityRecord.from_dict([])
    with pytest.raises(CompatibilityRecordError):
        CompatibilityRecord.from_dict({k: v for k, v in good.items() if k != "hailo_arch"})
    with pytest.raises(CompatibilityRecordError):
        CompatibilityRecord.from_dict({**good, "extra": 1})
