# =============================================================================
# HYDRA-UMC-VISION-NODE - tests/test_health_route_edges.py
# Copyright (C) 2026 JuanenRac (Electro Hobby 3D) <electrohobby3d@gmail.com>
# GPL-3.0-or-later - see LICENSE
# =============================================================================
"""The uniform health route at its edges: closed it answers like a route that does not exist, open it answers a caller on loopback and asks everyone else for the
bearer token, a service without a token never answers a caller away from this machine, the identity of the SERVER closes every caller that has not got one, and
the name and the version in the answer come from the handler or, failing that, from the package."""
from __future__ import annotations

import io
import json

import pytest

from hydra_umc_vision_node import health_route as hr
from hydra_umc_vision_node.server_identity import sign_token

KEY = b"a-signing-key-of-the-server-0123456789"


class _Handler:
    """Enough of a request handler for the route: path, headers, client address, a server with the switches and a place to write."""

    def __init__(self, path: str = "/health", client: str = "127.0.0.1", headers: dict[str, str] | None = None, *, opened: bool = True, key: bytes | None = None,
                 command: str = "GET") -> None:
        self.path = path
        self.client_address = (client, 50000)
        self.headers = dict(headers or {})
        self.command = command
        self.server = type("Server", (), {"health_open": opened, "identity_key": key})()
        self.wfile = io.BytesIO()
        self.status: int | None = None

    def send_response(self, status: int) -> None:
        self.status = status

    def send_header(self, name: str, value: str) -> None:
        pass

    def end_headers(self) -> None:
        pass

    def body(self) -> dict:
        return json.loads(self.wfile.getvalue())


def _serve(handler: _Handler, token: str | None = None) -> bool:
    return hr.HealthRouteMixin.serve_health(handler, token)  # type: ignore[arg-type]


def test_a_path_that_is_not_the_health_path_is_left_to_the_handler():
    for path in ("/", "/healthz", "/health/x", "/stats?health=1"):
        handler = _Handler(path)
        assert _serve(handler) is False and handler.status is None


def test_the_query_string_does_not_hide_the_route():
    handler = _Handler("/health?probe=1")
    assert _serve(handler) is True and handler.status == 200


def test_closed_it_answers_like_a_route_that_does_not_exist():
    handler = _Handler(opened=False)
    assert _serve(handler) is True and handler.status == 404 and handler.body() == {"error": "not found"}
    closed_with_identity = _Handler(opened=False, key=KEY)
    assert _serve(closed_with_identity) is True and closed_with_identity.status == 404


def test_open_it_answers_a_caller_on_loopback_with_the_name_and_the_version_and_nothing_else():
    handler = _Handler()
    assert _serve(handler) is True and handler.status == 200
    body = handler.body()
    assert body["status"] == "ok" and set(body) == {"status", "service", "version"} and body["version"] == "unknown"
    assert body["service"] == hr._PACKAGE.replace("_", "-")  # noqa: SLF001


def test_the_handler_may_name_the_service_and_its_version():
    handler = _Handler()
    handler.metrics_service, handler.metrics_version = "my-service", "9.9.9"  # type: ignore[attr-defined]
    _serve(handler)
    assert handler.body() == {"status": "ok", "service": "my-service", "version": "9.9.9"}


@pytest.mark.parametrize("client", ["::1", "127.0.0.1", "127.9.9.9", "::1%lo"])
def test_every_form_of_loopback_is_a_caller_on_this_machine(client):
    handler = _Handler(client=client)
    assert _serve(handler) is True and handler.status == 200


@pytest.mark.parametrize("client", ["192.168.0.20", "10.0.0.1", "8.8.8.8", "not-an-address", ""])
def test_a_caller_away_from_this_machine_without_the_token_is_refused(client):
    handler = _Handler(client=client)
    assert _serve(handler, "secret-token") is True and handler.status == 401
    assert _serve(handler := _Handler(client=client), None) is True and handler.status == 401  # a service without a token never answers it


def test_a_caller_away_from_this_machine_with_the_exact_token_is_answered():
    good = _Handler(client="192.168.0.20", headers={"Authorization": "Bearer secret-token"})
    assert _serve(good, "secret-token") is True and good.status == 200
    for header in ("Bearer wrong", "Basic secret-token", "bearer secret-token", "secret-token", ""):
        refused = _Handler(client="192.168.0.20", headers={"Authorization": header} if header else {})
        assert _serve(refused, "secret-token") is True and refused.status == 401, header


def test_with_the_identity_of_the_server_on_even_loopback_needs_an_identity():
    anonymous = _Handler(key=KEY)
    assert _serve(anonymous) is True and anonymous.status == 401 and "identity" in anonymous.body()["error"]
    signed = _Handler(key=KEY, headers={"Authorization": f"Bearer {sign_token(KEY)}"})
    assert _serve(signed) is True and signed.status == 200
    forged = _Handler(key=KEY, headers={"Authorization": f"Bearer {sign_token(b'x' * 40)}"})
    assert _serve(forged) is True and forged.status == 401
    own_token = _Handler(key=KEY, headers={"Authorization": "Bearer service-token"})
    assert _serve(own_token, "service-token") is True and own_token.status == 200


@pytest.mark.parametrize(("flag", "value", "expected"), [
    (True, "", True), (False, "", False), (False, "1", True), (False, "TRUE", True), (False, " yes ", True), (False, "on", True),
    (False, "0", False), (False, "no", False), (False, "off", False), (False, "maybe", False),
])
def test_the_route_is_opened_by_the_flag_or_by_the_environment_and_by_nothing_else(monkeypatch, flag, value, expected):
    monkeypatch.delenv(hr.ENV_HEALTH_OPEN, raising=False)
    if value:
        monkeypatch.setenv(hr.ENV_HEALTH_OPEN, value)
    assert hr.health_open_requested(flag) is expected


def test_an_address_that_is_not_an_address_is_not_loopback():
    assert hr._is_loopback("127.0.0.1") and hr._is_loopback("::1") and not hr._is_loopback("garbage") and not hr._is_loopback("")  # noqa: SLF001
