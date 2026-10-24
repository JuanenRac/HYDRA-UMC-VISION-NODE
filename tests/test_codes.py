# =============================================================================
# HYDRA-UMC-VISION-NODE - tests/test_codes.py
# Copyright (C) 2026 JuanenRac (Electro Hobby 3D) <electrohobby3d@gmail.com>
# GPL-3.0-or-later - see LICENSE
# =============================================================================
"""Reading DataMatrix codes: the decoding around the library (with a fake decoder) and the POST /read-code route."""
from __future__ import annotations

import json
import threading
import urllib.error
import urllib.request

import pytest

from hydra_umc_vision_node.api import VisionNodeServer
from hydra_umc_vision_node.codes import DecoderUnavailable, MAX_CODES, decode_codes, pylibdmtx_decoder
from hydra_umc_vision_node.frame import FrameSpec

SPEC = FrameSpec(4, 4, 1)
FRAME = bytes(range(16))  # not uniform


def fake(*codes):
    return lambda pixels, width, height, channels: list(codes)


def test_the_codes_of_a_frame_are_returned_as_text():
    assert decode_codes(FRAME, SPEC, fake(b"PCB-001", b"LOT-7")) == ["PCB-001", "LOT-7"]


def test_the_decoder_gets_the_pixels_and_the_shape():
    seen = []

    def decoder(pixels, width, height, channels):
        seen.append((pixels, width, height, channels))
        return []

    decode_codes(FRAME, SPEC, decoder)
    assert seen == [(FRAME, 4, 4, 1)]


def test_a_frame_of_the_wrong_size_is_not_decoded():
    with pytest.raises(ValueError):
        decode_codes(FRAME[:-1], SPEC, fake(b"A"))


def test_a_code_read_twice_is_one_code():
    assert decode_codes(FRAME, SPEC, fake(b"A-1", b"A-1")) == ["A-1"]


@pytest.mark.parametrize("junk", [b"", b"\x00\x01", b"has\xffbyte", b"line\nbreak", b"x" * 300, "text", None, 5])
def test_what_is_not_printable_ascii_text_of_a_sane_length_is_dropped(junk):
    assert decode_codes(FRAME, SPEC, fake(junk, b"OK-1")) == ["OK-1"]


def test_at_most_a_bounded_number_of_codes_is_returned():
    assert len(decode_codes(FRAME, SPEC, fake(*[f"C-{i}".encode() for i in range(40)]))) == MAX_CODES


def test_a_decoder_that_fails_has_found_nothing():
    def broken(*args):
        raise RuntimeError("library crashed")

    assert decode_codes(FRAME, SPEC, broken) == []


def test_a_missing_library_is_said_not_hidden():
    def absent(*args):
        raise DecoderUnavailable("not installed")

    with pytest.raises(DecoderUnavailable):
        decode_codes(FRAME, SPEC, absent)


def test_the_default_decoder_says_what_to_install_when_the_library_is_missing():
    try:
        import pylibdmtx  # noqa: F401
    except ImportError:
        with pytest.raises(DecoderUnavailable) as error:
            pylibdmtx_decoder(FRAME, 4, 4, 1)
        assert "pylibdmtx" in str(error.value)
    else:
        pytest.skip("pylibdmtx is installed in this environment")


# --- the route ---------------------------------------------------------------------------------------------------------


def _server(tmp_path, decoder, token=""):
    server = VisionNodeServer(("127.0.0.1", 0), tmp_path, token, code_decoder=decoder)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server, f"http://127.0.0.1:{server.server_address[1]}"


def _post(url, data, token=None):
    request = urllib.request.Request(url, data=data, method="POST")
    if token:
        request.add_header("Authorization", f"Bearer {token}")
    try:
        with urllib.request.urlopen(request, timeout=5) as response:
            return response.status, json.loads(response.read())
    except urllib.error.HTTPError as error:
        return error.code, json.loads(error.read())


@pytest.fixture
def served(tmp_path):
    servers = []

    def start(decoder, token=""):
        server, url = _server(tmp_path, decoder, token)
        servers.append(server)
        return url

    yield start
    for server in servers:
        server.shutdown()
        server.server_close()


def test_the_route_returns_the_codes(served):
    url = served(fake(b"PCB-001"))
    assert _post(f"{url}/read-code?width=4&height=4&channels=1", FRAME) == (200, {"codes": ["PCB-001"], "count": 1})


def test_the_route_returns_an_empty_list_when_nothing_is_read(served):
    url = served(fake())
    assert _post(f"{url}/read-code?width=4&height=4&channels=1", FRAME) == (200, {"codes": [], "count": 0})


def test_a_blank_frame_is_still_read(served):
    # a plain white label is a frame that is uniform on purpose: it is not refused for being uniform
    url = served(fake(b"A-1"))
    assert _post(f"{url}/read-code?width=4&height=4&channels=1", b"\xff" * 16)[1]["codes"] == ["A-1"]


def test_a_frame_that_is_not_the_size_it_says_is_refused(served):
    url = served(fake(b"A-1"))
    status, body = _post(f"{url}/read-code?width=4&height=4&channels=1", FRAME[:-2])
    assert status == 400


def test_a_missing_or_wrong_query_is_refused(served):
    url = served(fake(b"A-1"))
    assert _post(f"{url}/read-code?width=4", FRAME)[0] == 400
    assert _post(f"{url}/read-code?width=x&height=4", FRAME)[0] == 400
    assert _post(f"{url}/read-code?width=99999&height=4&channels=1", FRAME)[0] == 400


def test_without_the_library_the_route_says_503_and_what_to_install(served):
    def absent(*args):
        raise DecoderUnavailable("pylibdmtx is not installed on this node")

    status, body = _post(f"{served(absent)}/read-code?width=4&height=4&channels=1", FRAME)
    assert status == 503
    assert "pylibdmtx" in body["error"]


def test_the_route_needs_the_token_when_there_is_one(served):
    url = served(fake(b"A-1"), token="a-secret-of-sixteen-chars")
    assert _post(f"{url}/read-code?width=4&height=4&channels=1", FRAME)[0] == 401
    assert _post(f"{url}/read-code?width=4&height=4&channels=1", FRAME, token="a-secret-of-sixteen-chars")[0] == 200


def test_validate_frame_still_works_after_the_frame_reading_was_shared(served):
    url = served(fake())
    status, body = _post(f"{url}/validate-frame?width=4&height=4&channels=1", FRAME)
    assert status == 200 and body["valid"] is True
