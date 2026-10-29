# =============================================================================
# HYDRA-UMC-VISION-NODE - src/hydra_umc_vision_node/live.py
# Copyright (C) 2026 JuanenRac (Electro Hobby 3D) <electrohobby3d@gmail.com>
# GPL-3.0-or-later - see LICENSE
# =============================================================================
"""Live health of the children: does the service answer, how fast and with which version.

`family-status` alone says a child is "present" when its manifest is on disk. With `--live` each child that declares a service
(a port and a health path in its own manifest) is asked once, with a short timeout, on the loopback address unless the operator
names another host. Only the port and the path the manifest declares are used, redirects are not followed and the answer is read
up to a small limit, so a child cannot send this node anywhere else or make it read without bound.
"""
from __future__ import annotations

import http.client
import json
import time
from dataclasses import dataclass

from .family import ChildStatus

DEFAULT_TIMEOUT_SECONDS = 1.5
MAX_BODY_BYTES = 64 * 1024


@dataclass(frozen=True)
class ChildHealth:
    name: str
    state: str  # "up", "down" or "no_service"
    latency_ms: float | None = None
    served_version: str | None = None
    detail: str = ""


def probe_service(
    host: str, port: int, path: str, timeout: float = DEFAULT_TIMEOUT_SECONDS
) -> tuple[str, float | None, str | None, str]:
    """One GET of `path`: (state, latency in ms, version the service says it is, detail)."""
    started = time.monotonic()
    connection = http.client.HTTPConnection(host, port, timeout=timeout)
    try:
        connection.request("GET", path, headers={"Accept": "application/json"})
        response = connection.getresponse()
        body = response.read(MAX_BODY_BYTES)
        latency = round((time.monotonic() - started) * 1000, 1)
        if response.status != 200:
            return "down", latency, None, f"HTTP {response.status}"
        version = None
        try:
            data = json.loads(body.decode("utf-8", errors="replace"))
            if isinstance(data, dict) and isinstance(data.get("version"), str):
                version = data["version"][:40]
        except ValueError:
            pass
        return "up", latency, version, ""
    except (OSError, http.client.HTTPException) as error:
        return "down", None, None, type(error).__name__
    finally:
        connection.close()


def check_family_health(
    statuses: list[ChildStatus], host: str = "127.0.0.1", timeout: float = DEFAULT_TIMEOUT_SECONDS
) -> list[ChildHealth]:
    results = []
    for status in statuses:
        manifest = status.manifest
        if manifest is None or manifest.service_port is None:
            detail = "no service declared in the manifest" if manifest else "not present"
            results.append(ChildHealth(status.name, "no_service", detail=detail))
            continue
        state, latency, version, detail = probe_service(host, manifest.service_port, manifest.health_path or "/stats", timeout)
        results.append(ChildHealth(status.name, state, latency, version, detail))
    return results
