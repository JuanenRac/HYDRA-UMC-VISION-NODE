# =============================================================================
# HYDRA-UMC-VISION-NODE - tests/test_service_unit.py
# Copyright (C) 2026 JuanenRac (Electro Hobby 3D) <electrohobby3d@gmail.com>
# GPL-3.0 - see LICENSE
# =============================================================================
"""The systemd unit must not hide the devices the node probes for."""
from __future__ import annotations

from pathlib import Path

UNIT = Path(__file__).resolve().parent.parent / "systemd" / "hydra-umc-vision-node.service"


def _directives() -> list[str]:
    lines = (line.strip() for line in UNIT.read_text(encoding="utf-8").splitlines())
    return [line for line in lines if line and not line.startswith("#")]


def test_the_unit_does_not_hide_dev_video_and_dev_hailo() -> None:
    assert "PrivateDevices=true" not in _directives()


def test_the_unit_still_restricts_devices_to_what_the_node_needs() -> None:
    directives = _directives()
    assert "DevicePolicy=closed" in directives
    assert "DeviceAllow=/dev/hailo0 r" in directives
    assert any(line.startswith("DeviceAllow=char-video4linux") for line in directives)
