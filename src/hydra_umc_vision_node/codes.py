# =============================================================================
# HYDRA-UMC-VISION-NODE - src/hydra_umc_vision_node/codes.py
# Copyright (C) 2026 JuanenRac (Electro Hobby 3D) <electrohobby3d@gmail.com>
# GPL-3.0-or-later - see LICENSE
# =============================================================================
"""Read the DataMatrix codes in one raw frame.

A board that goes into a pick-and-place cell carries a DataMatrix with its identity; the bridge of the cell compares it with the
board the job names. This module is the reading: it takes a frame that has already been validated against its `FrameSpec`
(the pixels exactly as many as the spec says) and returns the strings it could decode.

The decoder is `pylibdmtx`, imported lazily the first time it is needed, so that a node without it still starts, serves every
other route and answers `POST /read-code` with `503` and a message saying what to install. `decode_codes` takes the decoder as a
parameter, so that everything around it is tested without the library and without a camera.

A code is data from a printed label: the text is bounded, must be printable ASCII and is cut at `MAX_CODES` codes of
`MAX_CODE_CHARS` characters. What the cell does with it is the cell's business; nothing here acts on it.
"""
from __future__ import annotations

from typing import Callable, Sequence

from .frame import FrameSpec

MAX_CODES = 16
MAX_CODE_CHARS = 256

# A decoder takes (pixels, width, height, channels) and returns the raw bytes of every code it found.
Decoder = Callable[[bytes, int, int, int], Sequence[bytes]]


class DecoderUnavailable(RuntimeError):
    """The library that decodes DataMatrix codes is not installed on this node."""


def pylibdmtx_decoder(pixels: bytes, width: int, height: int, channels: int) -> list[bytes]:
    """Decode DataMatrix codes with pylibdmtx (an optional dependency, imported here and nowhere else)."""
    try:
        from pylibdmtx import pylibdmtx  # type: ignore[import-not-found]
    except ImportError as error:
        raise DecoderUnavailable(
            "pylibdmtx is not installed on this node - install it (and libdmtx) to read DataMatrix codes"
        ) from error
    return [item.data for item in pylibdmtx.decode((pixels, width, height), timeout=2000, max_count=MAX_CODES)]


def decode_codes(data: bytes, spec: FrameSpec, decoder: Decoder = pylibdmtx_decoder) -> list[str]:
    """The DataMatrix codes in a frame, as text. The frame must be exactly `spec.expected_bytes` long.

    A code that is not printable ASCII, is empty or is longer than `MAX_CODE_CHARS` is dropped, a code read twice is returned
    once, and at most `MAX_CODES` are returned. A decoder that fails for any reason other than being absent is "no code".
    """
    if len(data) != spec.expected_bytes:
        raise ValueError(f"the frame must be {spec.expected_bytes} bytes for {spec.width}x{spec.height}x{spec.channels}, got {len(data)}")
    try:
        raw = list(decoder(data, spec.width, spec.height, spec.channels))
    except DecoderUnavailable:
        raise
    except Exception:  # noqa: BLE001 - a decoder that chokes on a frame has found nothing in it
        return []
    codes: list[str] = []
    for item in raw:
        if not isinstance(item, (bytes, bytearray)) or not item or len(item) > MAX_CODE_CHARS:
            continue
        if not all(0x20 <= byte < 0x7F for byte in item):
            continue
        text = bytes(item).decode("ascii")
        if text not in codes:
            codes.append(text)
        if len(codes) >= MAX_CODES:
            break
    return codes
