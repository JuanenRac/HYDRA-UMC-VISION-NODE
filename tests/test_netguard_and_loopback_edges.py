# =============================================================================
# HYDRA-UMC-VISION-NODE - tests/test_netguard_and_loopback_edges.py
# Copyright (C) 2026 JuanenRac (Electro Hobby 3D) <electrohobby3d@gmail.com>
# GPL-3.0-or-later - see LICENSE
# =============================================================================
"""The addresses the node may call and the optional loopback block at their edges: every URL that is refused and why, the remote switch, and every key of the block
that is wrong."""
from __future__ import annotations

import pytest

from hydra_umc_vision_node.loopback import LoopbackError, StreamUnavailable, _boundary, parse_loopback_config
from hydra_umc_vision_node.netguard import EndpointError, parse_endpoint


@pytest.mark.parametrize("url", [None, 3, "", "x" * 513, "ftp://127.0.0.1/a", "https://127.0.0.1/a", "http:///path", "127.0.0.1:8080", "http://user:pw@127.0.0.1/", "http://user@127.0.0.1/",
                                 "http://127.0.0.1/a#frag", "http://127.0.0.1:99999/", "http://127.0.0.1:abc/"])
def test_an_address_that_cannot_be_used_is_refused(url):
    with pytest.raises(EndpointError):
        parse_endpoint(url, True, "address")


def test_the_name_of_the_setting_is_in_the_message():
    with pytest.raises(EndpointError, match="loopback.streamer.url"):
        parse_endpoint("ftp://x/", False, "loopback.streamer.url")


@pytest.mark.parametrize("host", ["192.168.0.20", "10.1.2.3", "camera.example", "0.0.0.0"])
def test_an_address_away_from_this_machine_needs_the_remote_switch(host):
    with pytest.raises(EndpointError, match="allowRemote"):
        parse_endpoint(f"http://{host}:8080/stream", False, "address")
    assert parse_endpoint(f"http://{host}:8080/stream", True, "address").host == host


@pytest.mark.parametrize("url", ["http://127.0.0.1:8080/stream", "http://localhost:8080/stream", "http://[::1]:8080/stream"])
def test_loopback_needs_no_switch(url):
    assert parse_endpoint(url, False, "address").port == 8080


def test_the_port_the_path_and_the_query_are_kept_and_the_defaults_are_filled():
    endpoint = parse_endpoint("http://127.0.0.1/a/b?x=1&y=2", False, "address")
    assert (endpoint.port, endpoint.path) == (80, "/a/b?x=1&y=2") and endpoint.describe() == "http://127.0.0.1:80/a/b?x=1&y=2"
    assert parse_endpoint("http://127.0.0.1:81", False, "address").path == "/"


@pytest.mark.parametrize("block", [
    [], "on", 3, {"surprise": 1}, {"enabled": "yes"}, {"detector": []}, {"detector": {"surprise": 1}}, {"detector": {"label": ""}}, {"detector": {"label": "a b"}},
    {"detector": {"label": "x" * 65}}, {"detector": {"threshold": 0}}, {"detector": {"threshold": 256}}, {"detector": {"threshold": True}}, {"detector": {"cell": 0}},
    {"detector": {"minCells": 0}}, {"detector": {"maxDetections": 0}}, {"streamer": []}, {"streamer": {"surprise": 1}}, {"streamer": {"url": "http://127.0.0.1/s", "allowRemote": "yes"}},
    {"streamer": {"url": "http://192.168.0.2/s"}}, {"streamer": {}}, {"streamer": {"url": "http://127.0.0.1/s", "timeoutSeconds": 0}},
    {"streamer": {"url": "http://127.0.0.1/s", "timeoutSeconds": True}}, {"streamer": {"url": "http://127.0.0.1/s", "timeoutSeconds": float("nan")}},
])
def test_a_loopback_block_that_cannot_work_is_refused(block):
    with pytest.raises(LoopbackError):
        parse_loopback_config(block)


def test_a_loopback_block_is_off_until_it_says_otherwise_and_describes_itself():
    assert parse_loopback_config(None) is None
    config = parse_loopback_config({})
    assert config is not None and config.enabled is False and config.streamer is None
    full = parse_loopback_config({"enabled": True, "detector": {"label": "part"}, "streamer": {"url": "http://127.0.0.1:8081/stream", "timeoutSeconds": 3}})
    assert full is not None and full.enabled and full.detector.label == "part"
    described = full.describe()
    assert described["streamer"]["url"] == "http://127.0.0.1:8081/stream" and described["model"]["pipeline_version"]


def test_the_identity_of_the_detector_depends_on_its_settings():
    a = parse_loopback_config({"detector": {"threshold": 100}})
    b = parse_loopback_config({"detector": {"threshold": 101}})
    assert a is not None and b is not None and a.detector.identity() == a.detector.identity() and a.detector.identity() != b.detector.identity()


@pytest.mark.parametrize("content_type", ["", "text/html", "multipart/mixed; boundary=x", "multipart/x-mixed-replace", "multipart/x-mixed-replace; charset=utf-8"])
def test_a_stream_that_is_not_multipart_x_mixed_replace_with_a_boundary_is_refused(content_type):
    with pytest.raises(StreamUnavailable):
        _boundary(content_type)


@pytest.mark.parametrize(("content_type", "expected"), [("multipart/x-mixed-replace; boundary=frame", "frame"), ('Multipart/X-Mixed-Replace; BOUNDARY="my frame"', "my"),
                                                        ("multipart/x-mixed-replace;boundary=--cut;charset=x", "--cut")])
def test_the_boundary_is_read_from_the_content_type(content_type, expected):
    assert _boundary(content_type) == expected
