# =============================================================================
# HYDRA-UMC-VISION-NODE - src/hydra_umc_vision_node/health_route.py
# Copyright (C) 2026 JuanenRac (Electro Hobby 3D) <electrohobby3d@gmail.com>
# GPL-3.0-or-later - see LICENSE
# =============================================================================
"""The uniform `GET /health` of the services of the family, closed unless the operator opens it.

Every service of the family can answer `GET /health` with the same small body, `{"status": "ok", "service": ..., "version": ...}`, so that a watchdog or the
service registry can read the health of any of them the same way. It is an opt-in:

* Closed (the default): the route answers `404` like any route that does not exist, and nothing else about the service changes.
* Open (`--health-open` on the command line or `HYDRA_UMC_VISION_NODE_HEALTH_OPEN=1`): the route answers `200` to a caller on loopback. A caller that
  is not on loopback must present the bearer token of the service; a service without a token never answers a caller that is not on loopback.

The body names the service and its version and nothing else: no path, no configuration, no secret.
"""
from __future__ import annotations

import ipaddress
import os
from http.server import BaseHTTPRequestHandler

from .http_common import bearer_matches, write_json

HEALTH_PATH = "/health"
ENV_HEALTH_OPEN = "HYDRA_UMC_VISION_NODE_HEALTH_OPEN"
_TRUE = {"1", "true", "yes", "on"}


def health_open_requested(flag: bool = False) -> bool:
    """Whether the operator opened `GET /health`: the flag of the command line, or the environment variable set to 1/true/yes/on."""
    return bool(flag) or os.environ.get(ENV_HEALTH_OPEN, "").strip().lower() in _TRUE


def _is_loopback(address: str) -> bool:
    try:
        return ipaddress.ip_address(address.split("%", 1)[0]).is_loopback
    except ValueError:
        return False


class HealthRouteMixin(BaseHTTPRequestHandler):
    """Put it before `BaseHTTPRequestHandler` in the bases of the handler and ask `serve_health` first in `do_GET`. The server opens the route with `health_open = True`."""

    def serve_health(self, token: str | None = None) -> bool:
        """Answer `GET /health` and return True; return False for any other path so the handler carries on."""
        if self.path.split("?", 1)[0] != HEALTH_PATH:
            return False
        if not getattr(self.server, "health_open", False):
            write_json(self, 404, {"error": "not found"})
            return True
        if not _is_loopback(self.client_address[0]):
            if not token or not bearer_matches(self, token):
                write_json(self, 401, {"error": "a valid bearer token is required"})
                return True
        write_json(
            self,
            200,
            {
                "status": "ok",
                "service": getattr(self, "metrics_service", "") or "hydra-umc-vision-node",
                "version": getattr(self, "metrics_version", "") or "unknown",
            },
        )
        return True
