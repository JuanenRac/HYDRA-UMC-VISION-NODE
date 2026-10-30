# =============================================================================
# HYDRA-UMC-VISION-NODE - tests/test_compose.py
# Copyright (C) 2026 JuanenRac (Electro Hobby 3D) <electrohobby3d@gmail.com>
# GPL-3.0-or-later - see LICENSE
# =============================================================================
"""docker-compose.yml is checked without a Docker runtime: it must parse, every service must say how to get its
image, a dependency must name a service of the file, and a build context that exists must hold a Dockerfile."""
from __future__ import annotations

from pathlib import Path

import pytest

yaml = pytest.importorskip("yaml")

ROOT = Path(__file__).resolve().parent.parent
COMPOSE = ROOT / "docker-compose.yml"


def _services() -> dict:
    document = yaml.safe_load(COMPOSE.read_text(encoding="utf-8"))
    assert isinstance(document, dict), "the compose file must be a mapping"
    services = document.get("services")
    assert isinstance(services, dict) and services, "the compose file must declare services"
    return services


def test_every_service_says_where_its_image_comes_from() -> None:
    for name, service in _services().items():
        assert "build" in service or "image" in service, f"{name} has neither build nor image"


def test_a_dependency_names_a_service_of_the_file() -> None:
    services = _services()
    for name, service in services.items():
        depends = service.get("depends_on", [])
        for dependency in (depends if isinstance(depends, list) else list(depends)):
            assert dependency in services, f"{name} depends on {dependency}, which is not a service"


def test_a_build_context_that_exists_holds_a_dockerfile() -> None:
    for name, service in _services().items():
        build = service.get("build")
        if build is None:
            continue
        context = build if isinstance(build, str) else build.get("context", ".")
        folder = (ROOT / context).resolve()
        if folder.is_dir():  # the sibling checkouts may be absent (CI checks out this repository alone)
            assert (folder / "Dockerfile").is_file(), f"{name}: no Dockerfile in {context}"


def test_a_service_with_a_healthcheck_runs_a_command() -> None:
    for name, service in _services().items():
        check = service.get("healthcheck")
        if check is not None:
            test = check.get("test")
            assert isinstance(test, list) and test and test[0] in ("CMD", "CMD-SHELL"), f"{name}: healthcheck test"


def test_published_ports_are_host_and_container_pairs() -> None:
    for name, service in _services().items():
        for port in service.get("ports", []):
            parts = str(port).split(":")
            assert len(parts) in (2, 3), f"{name}: port {port} must be host:container"
            assert parts[-1].split("/")[0].isdigit() and parts[-2].isdigit(), f"{name}: port {port}"
