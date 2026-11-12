# =============================================================================
# HYDRA-UMC-VISION-NODE - tests/test_http_common.py
# Copyright (C) 2026 JuanenRac (Electro Hobby 3D) <electrohobby3d@gmail.com>
# GPL-3.0-or-later - see LICENSE
# =============================================================================
"""The shared HTTP helpers on their own: the body reader (a body that is absent, too large, cut short or not JSON), the query reader, the bearer
comparison, the 401 answer and the server that refuses the request it has no thread for."""
from __future__ import annotations

import io
import json
import socket
import threading
from http.server import BaseHTTPRequestHandler

import pytest

from hydra_umc_vision_node import http_common as hc


class FakeHandler:
    """The part of a request handler the helpers touch."""

    def __init__(self, body=b"", headers=None, path="/"):
        self.rfile = io.BytesIO(body)
        self.wfile = io.BytesIO()
        self.headers = headers if headers is not None else {"Content-Length": str(len(body))}
        self.path = path
        self.sent = []

    def send_response(self, status):
        self.sent.append(("status", status))

    def send_header(self, name, value):
        self.sent.append((name, value))

    def end_headers(self):
        self.sent.append(("end", None))


def test_nan_and_infinity_are_not_json_numbers():
    for name in ("NaN", "Infinity", "-Infinity"):
        with pytest.raises(ValueError, match="not a valid JSON number"):
            hc.reject_constant(name)
        with pytest.raises(ValueError):
            hc.read_json_body(FakeHandler(b'{"x": %s}' % name.encode()))


def test_a_body_that_is_absent_gives_the_empty_value():
    assert hc.read_json_body(FakeHandler(b"")) == {}
    assert hc.read_json_body(FakeHandler(headers={}), empty=[]) == []
    assert hc.read_json_body(FakeHandler(headers={"Content-Length": ""})) == {}


def test_a_content_length_that_is_not_a_number_or_is_out_of_range_is_refused():
    with pytest.raises(ValueError, match="integer"):
        hc.read_json_body(FakeHandler(headers={"Content-Length": "abc"}))
    with pytest.raises(ValueError, match="request body must contain"):
        hc.read_json_body(FakeHandler(headers={"Content-Length": "-1"}))


def test_a_body_over_the_limit_is_read_off_the_wire_before_the_refusal():
    handler = FakeHandler(b"x" * 100)
    with pytest.raises(ValueError, match="0-10 bytes"):
        hc.read_json_body(handler, max_bytes=10)
    assert handler.rfile.read() == b""  # drained, so the client can still read the answer
    # far over the limit: the server does not read it at all
    huge = FakeHandler(b"x" * 10, headers={"Content-Length": str(10 * hc.DRAIN_FACTOR + 1)})
    with pytest.raises(ValueError):
        hc.read_json_body(huge, max_bytes=10)
    assert huge.rfile.read() == b"x" * 10


def test_discard_stops_when_the_peer_sends_less_than_it_announced():
    stream = io.BytesIO(b"abc")
    hc._discard(stream, 1000, chunk=2)
    assert stream.read() == b""


def test_a_body_must_be_json_and_for_the_object_reader_an_object():
    assert hc.read_json_body(FakeHandler(b'{"a": 1}')) == {"a": 1}
    with pytest.raises(ValueError):
        hc.read_json_body(FakeHandler(b"{nope"))
    assert hc.read_json_object(FakeHandler(b'{"a": 1}')) == {"a": 1}
    for body, kind in ((b"[1]", "list"), (b'"s"', "str"), (b"3", "int"), (b"null", "NoneType")):
        with pytest.raises(ValueError, match=kind):
            hc.read_json_object(FakeHandler(body))


def test_write_json_and_write_error_set_the_length_and_the_type():
    handler = FakeHandler()
    hc.write_json(handler, 201, {"ok": True})
    body = handler.wfile.getvalue()
    assert json.loads(body) == {"ok": True}
    assert ("status", 201) in handler.sent and ("Content-Length", str(len(body))) in handler.sent
    assert ("Content-Type", "application/json") in handler.sent
    handler = FakeHandler()
    hc.write_error(handler, 400, "bad")
    assert ("status", 400) in handler.sent and json.loads(handler.wfile.getvalue()) == {"error": "bad"}
    handler = FakeHandler()
    hc.write_json(handler, 200, {"v": {1, 2}}, default=sorted)
    assert json.loads(handler.wfile.getvalue()) == {"v": [1, 2]}


def test_the_query_reader_refuses_a_repeated_name_unless_told_otherwise():
    handler = FakeHandler(path="/x?a=1&b=2&c=")
    assert hc.query_params(handler) == {"a": "1", "b": "2", "c": ""}
    twice = FakeHandler(path="/x?a=1&a=2&b=3")
    with pytest.raises(ValueError, match=r"\['a'\]"):
        hc.query_params(twice)
    assert hc.query_params(twice, repeated="first") == {"a": "1", "b": "3"}
    assert hc.query_params(FakeHandler(path="/x")) == {}


def test_the_bearer_comparison():
    def request(value):
        return FakeHandler(headers={"Authorization": value} if value is not None else {})

    assert hc.bearer_matches(request(None), "") is True  # no token configured: open
    assert hc.bearer_matches(request("Bearer secret-token"), "secret-token") is True
    for presented in (None, "", "Bearer", "Bearer wrong", "Basic secret-token", "bearer secret-token", "Bearer secret-token2"):
        assert hc.bearer_matches(request(presented), "secret-token") is False, presented


def test_the_401_answer_asks_for_a_bearer_token():
    handler = FakeHandler()
    hc.reject_unauthorized(handler)
    assert ("status", 401) in handler.sent and ("WWW-Authenticate", "Bearer") in handler.sent
    assert "bearer token" in json.loads(handler.wfile.getvalue())["error"]


@pytest.mark.parametrize("bad", [0, 1025, True, 2.5, "3", None])
def test_the_thread_limit_must_be_an_integer_from_1_to_1024(bad):
    with pytest.raises(ValueError, match="max_threads"):
        hc.BoundedThreadingHTTPServer(("127.0.0.1", 0), BaseHTTPRequestHandler, max_threads=bad)


def test_the_request_over_the_limit_gets_a_503_and_the_slot_is_given_back():
    started, release = threading.Event(), threading.Event()

    class Slow(BaseHTTPRequestHandler):
        def do_GET(self):
            started.set()
            release.wait(5)
            self.send_response(200)
            self.send_header("Content-Length", "0")
            self.end_headers()

        def log_message(self, *_a):
            pass

    server = hc.BoundedThreadingHTTPServer(("127.0.0.1", 0), Slow, max_threads=1)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    port = server.server_address[1]
    try:
        first = socket.create_connection(("127.0.0.1", port), timeout=5)
        first.sendall(b"GET / HTTP/1.1\r\nHost: x\r\n\r\n")
        assert started.wait(5)
        second = socket.create_connection(("127.0.0.1", port), timeout=5)
        second.sendall(b"GET / HTTP/1.1\r\nHost: x\r\n\r\n")
        assert second.recv(200).startswith(b"HTTP/1.1 503")
        assert server.refused == 1
        second.close()
        release.set()
        assert b" 200 " in first.recv(200)  # the request that held the slot is still answered
        first.close()
        # the slot comes back once the first request has been handled
        for _ in range(100):
            if server._slots.acquire(blocking=False):
                server._slots.release()
                break
            threading.Event().wait(0.05)
        else:
            pytest.fail("the slot was not given back")
    finally:
        release.set()
        server.shutdown()
        server.server_close()
