# =============================================================================
# HYDRA-UMC-VISION-NODE - src/hydra_umc_vision_node/netguard.py
# Copyright (C) 2026 JuanenRac (Electro Hobby 3D) <electrohobby3d@gmail.com>
# GPL-3.0-or-later - see LICENSE
# =============================================================================
"""Where this node may call out to: the address of another service of the family named in the configuration.

The configuration is written by the operator, but a URL in it is still a place the node will connect to. By default only the loopback address is
accepted; a service on another machine needs `allowRemote: true` next to the URL. Only `http` is spoken, redirects are never followed and the
answer is read up to a limit.
"""
from __future__ import annotations

from dataclasses import dataclass
from urllib.parse import urlparse

from .access import is_loopback


class EndpointError(ValueError):
    """An address in the configuration cannot be used."""


@dataclass(frozen=True)
class Endpoint:
    host: str
    port: int
    path: str

    def describe(self) -> str:
        return f"http://{self.host}:{self.port}{self.path}"


def parse_endpoint(url: object, allow_remote: bool, what: str) -> Endpoint:
    if not isinstance(url, str) or not url or len(url) > 512:
        raise EndpointError(f"{what} must be an http URL of at most 512 characters")
    parts = urlparse(url)
    if parts.scheme != "http" or not parts.hostname:
        raise EndpointError(f"{what} must be an http URL with a host")
    if parts.username or parts.password:
        raise EndpointError(f"{what} must not carry a user name or a password")
    if parts.fragment:
        raise EndpointError(f"{what} must not have a fragment")
    try:
        port = parts.port or 80
    except ValueError:
        raise EndpointError(f"{what} has an invalid port") from None
    if not allow_remote and not is_loopback(parts.hostname):
        raise EndpointError(f"{what} points away from this machine: set allowRemote to true to allow it")
    path = parts.path or "/"
    if parts.query:
        path += "?" + parts.query
    return Endpoint(parts.hostname, port, path)
