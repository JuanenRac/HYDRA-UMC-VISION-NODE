# =============================================================================
# HYDRA-UMC-VISION-NODE - package init: src/hydra_umc_vision_node/__init__.py
# Copyright (C) 2026 JuanenRac (Electro Hobby 3D) <electrohobby3d@gmail.com>
# GPL-3.0-or-later - see LICENSE
# =============================================================================
"""HYDRA-UMC-VISION-NODE - high-speed perception edge AI node (Hailo-8 + CM5).

Integration parent of the Vision AI Node family: HYDRA-UMC-VISION-STREAMER,
HYDRA-UMC-DETECTION-HEF, HYDRA-UMC-SAFETY-ZONES and
HYDRA-UMC-VISUAL-SERVOING-API. See docker-compose.yml at the project root
for how the 4 children are meant to be wired together once each one ships a
real container image.

Being the integration parent is also why this is the one project of the 5
that reserves an `os/` (shared HydraOS image for the CM5 host) and a
`models/` folder (compiled .hef models served to the Hailo-8 NPU): both are
resources the 4 children consume rather than own individually. They are
populated at deployment and are not in version control: no image or model is
versioned here, and on the CM5 the models live where DETECTION-HEF's service
installs them. Conversely,
there is no `hardware/` or `firmware/` folder here at all - CM5 + Hailo-8 is
off-the-shelf hardware with no board or auxiliary microcontroller of this
project's own to design.

The installed package version is the single source of truth in
pyproject.toml (read at runtime via importlib.metadata), never duplicated
here, so bump_version.py only ever has one place to edit.
"""
