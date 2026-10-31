# =============================================================================
# HYDRA-UMC-VISION-NODE - src/hydra_umc_vision_node/sources.py
# Copyright (C) 2026 JuanenRac (Electro Hobby 3D) <electrohobby3d@gmail.com>
# GPL-3.0-or-later - see LICENSE
# =============================================================================
"""Pluggable frame sources for the simulation. NO camera and NO video decoder here.

A `FrameSource` hands out `Frame`s one by one. Two sources exist, both tests of the pipeline and nothing more:

* `synthetic`: a deterministic generator (same seed, same bytes) of a moving gradient;
* `folder`: the files of a folder, in name order, read as raw frames of the declared size or as binary PGM/PPM images.

A real source (a camera, an RTSP stream) is one more class registered with `register_source_kind`; it needs hardware and
is not provided. Everything is bounded: frame size, number of files, number of frames per run.
"""
from __future__ import annotations

import re
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

NAME = re.compile(r"[A-Za-z0-9_.:-]{1,64}")
MAX_SOURCE_FRAME_BYTES = 4 * 1024 * 1024
MAX_SOURCE_DIMENSION = 4096
MAX_FOLDER_FILES = 256
MAX_SYNTHETIC_FRAMES = 100_000
MAX_SEED = 2**32 - 1


class SourceError(ValueError):
    """A source declaration or a frame cannot be used."""


@dataclass(frozen=True)
class Frame:
    index: int
    width: int
    height: int
    channels: int
    data: bytes


class FrameSource(Protocol):
    name: str
    kind: str

    def describe(self) -> dict: ...

    def frames(self, limit: int) -> Iterator[Frame]:
        """At most `limit` frames; fewer when the source has fewer."""
        ...


def _int(config: dict, key: str, low: int, high: int, default: int | None = None) -> int:
    value = config.get(key, default)
    if isinstance(value, bool) or not isinstance(value, int) or not low <= value <= high:
        raise SourceError(f"{key} must be an integer from {low} to {high}")
    return value


def _shape(config: dict) -> tuple[int, int, int]:
    width = _int(config, "width", 1, MAX_SOURCE_DIMENSION)
    height = _int(config, "height", 1, MAX_SOURCE_DIMENSION)
    channels = _int(config, "channels", 1, 4, 3)
    if width * height * channels > MAX_SOURCE_FRAME_BYTES:
        raise SourceError(f"a frame may hold at most {MAX_SOURCE_FRAME_BYTES} bytes")
    return width, height, channels


class SyntheticSource:
    kind = "synthetic"

    def __init__(self, name: str, width: int, height: int, channels: int, seed: int, count: int) -> None:
        self.name, self.width, self.height, self.channels, self.seed, self.count = name, width, height, channels, seed, count

    def describe(self) -> dict:
        return {"name": self.name, "kind": self.kind, "simulated": True, "width": self.width, "height": self.height,
                "channels": self.channels, "seed": self.seed, "frames": self.count}

    def frame(self, index: int) -> Frame:
        row_len = self.width * self.channels
        base = bytes((x * 31 + self.seed * 17) & 255 for x in range(row_len))
        rows = []
        for y in range(self.height):
            shift = (y * 7 + index * 13 + self.seed) % row_len
            rows.append(base[shift:] + base[:shift])
        return Frame(index, self.width, self.height, self.channels, b"".join(rows))

    def frames(self, limit: int) -> Iterator[Frame]:
        for index in range(min(limit, self.count)):
            yield self.frame(index)


def _pnm_tokens(data: bytes, wanted: int) -> tuple[list[bytes], int]:
    tokens: list[bytes] = []
    position = 0
    while len(tokens) < wanted:
        while position < len(data) and data[position:position + 1].isspace():
            position += 1
        if position < len(data) and data[position:position + 1] == b"#":
            while position < len(data) and data[position:position + 1] != b"\n":
                position += 1
            continue
        start = position
        while position < len(data) and not data[position:position + 1].isspace():
            position += 1
        if start == position:
            raise SourceError("truncated PNM header")
        tokens.append(data[start:position])
    return tokens, position + 1  # one whitespace byte separates the header from the pixels


def parse_pnm(data: bytes, index: int) -> Frame:
    """Binary PGM (P5) or PPM (P6) with 8 bits per sample."""
    tokens, offset = _pnm_tokens(data[:512], 4)
    magic = tokens[0]
    if magic not in (b"P5", b"P6"):
        raise SourceError("only binary PGM (P5) and PPM (P6) images are read")
    try:
        width, height, maxval = (int(t) for t in tokens[1:4])
    except ValueError:
        raise SourceError("the PNM header is not numeric") from None
    if maxval != 255 or not 1 <= width <= MAX_SOURCE_DIMENSION or not 1 <= height <= MAX_SOURCE_DIMENSION:
        raise SourceError("the PNM image must be 8 bits per sample and at most 4096 pixels per side")
    channels = 1 if magic == b"P5" else 3
    if width * height * channels > MAX_SOURCE_FRAME_BYTES:
        raise SourceError(f"a frame may hold at most {MAX_SOURCE_FRAME_BYTES} bytes")
    pixels = data[offset:]
    if len(pixels) != width * height * channels:
        raise SourceError("the PNM image does not hold the bytes its header announces")
    return Frame(index, width, height, channels, pixels)


class FolderSource:
    kind = "folder"

    def __init__(self, name: str, folder: Path, shape: tuple[int, int, int] | None) -> None:
        self.name = name
        self.shape = shape
        try:
            files = sorted(p for p in folder.iterdir() if p.suffix.lower() in (".raw", ".pgm", ".ppm"))
        except OSError as error:
            raise SourceError(f"the folder cannot be read: {error.strerror}") from None
        if len(files) > MAX_FOLDER_FILES:
            raise SourceError(f"a folder source holds at most {MAX_FOLDER_FILES} frames")
        root = folder.resolve()
        for path in files:
            if not path.resolve().is_relative_to(root):
                raise SourceError("a file of the folder points outside of it")
            if path.suffix.lower() == ".raw" and shape is None:
                raise SourceError(".raw files need width, height and channels in the source declaration")
        self.files = files

    def describe(self) -> dict:
        info: dict = {"name": self.name, "kind": self.kind, "simulated": True, "frames": len(self.files)}
        if self.shape:
            info.update(width=self.shape[0], height=self.shape[1], channels=self.shape[2])
        return info

    def frames(self, limit: int) -> Iterator[Frame]:
        for index, path in enumerate(self.files[:limit]):
            try:
                if path.stat().st_size > MAX_SOURCE_FRAME_BYTES + 512:
                    raise SourceError(f"{path.name} is too large")
                data = path.read_bytes()
            except OSError as error:
                raise SourceError(f"{path.name} cannot be read: {error.strerror}") from None
            if path.suffix.lower() == ".raw":
                if self.shape is None:
                    raise SourceError(".raw files need a declared shape")
                width, height, channels = self.shape
                if len(data) != width * height * channels:
                    raise SourceError(f"{path.name} does not hold {width}x{height}x{channels} bytes")
                yield Frame(index, width, height, channels, data)
            else:
                yield parse_pnm(data, index)


SourceFactory = Callable[[str, dict, Path], FrameSource]
_ALLOWED_KEYS: dict[str, set[str]] = {
    "synthetic": {"name", "kind", "width", "height", "channels", "seed", "frames"},
    "folder": {"name", "kind", "path", "width", "height", "channels"},
}


def _synthetic(name: str, config: dict, base: Path) -> FrameSource:
    width, height, channels = _shape(config)
    return SyntheticSource(name, width, height, channels, _int(config, "seed", 0, MAX_SEED, 0), _int(config, "frames", 1, MAX_SYNTHETIC_FRAMES, 100))


def _folder(name: str, config: dict, base: Path) -> FrameSource:
    raw = config.get("path")
    if not isinstance(raw, str) or not raw or Path(raw).is_absolute() or ".." in Path(raw).parts:
        raise SourceError("path must be a relative folder inside the folder of the configuration file")
    folder = (base / raw).resolve()
    if not folder.is_relative_to(base.resolve()) or not folder.is_dir():
        raise SourceError("path must be an existing folder inside the folder of the configuration file")
    shape = _shape(config) if "width" in config else None
    return FolderSource(name, folder, shape)


SOURCE_KINDS: dict[str, SourceFactory] = {"synthetic": _synthetic, "folder": _folder}


def register_source_kind(kind: str, factory: SourceFactory, allowed_keys: set[str]) -> None:
    """Plug a new kind of source in (a real camera, an RTSP stream). None is provided here."""
    SOURCE_KINDS[kind] = factory
    _ALLOWED_KEYS[kind] = allowed_keys | {"name", "kind"}


def build_source(config: object, base: Path) -> FrameSource:
    if not isinstance(config, dict):
        raise SourceError("a source must be an object")
    name = config.get("name")
    if not isinstance(name, str) or not NAME.fullmatch(name):
        raise SourceError("a source name is 1 to 64 characters of letters, digits and _ . : -")
    kind = config.get("kind")
    if not isinstance(kind, str) or kind not in SOURCE_KINDS:
        raise SourceError(f"source {name}: kind must be one of {sorted(SOURCE_KINDS)}")
    extra = sorted(set(config) - _ALLOWED_KEYS[kind])
    if extra:
        raise SourceError(f"source {name}: unknown keys {extra}")
    try:
        return SOURCE_KINDS[kind](name, config, base)
    except SourceError as error:
        if str(error).startswith("source "):
            raise
        raise SourceError(f"source {name}: {error}") from None
