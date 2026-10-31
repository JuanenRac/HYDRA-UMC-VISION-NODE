# =============================================================================
# HYDRA-UMC-VISION-NODE - tests/test_sources.py
# Copyright (C) 2026 JuanenRac (Electro Hobby 3D) <electrohobby3d@gmail.com>
# GPL-3.0-or-later - see LICENSE
# =============================================================================
from __future__ import annotations

from pathlib import Path

import pytest

from hydra_umc_vision_node.sources import Frame, SourceError, build_source, parse_pnm, register_source_kind


def _synthetic(**extra: object) -> dict:
    return {"name": "cam0", "kind": "synthetic", "width": 8, "height": 4, "channels": 3, **extra}


def test_synthetic_source_is_deterministic() -> None:
    a = list(build_source(_synthetic(seed=5, frames=4), Path(".")).frames(10))
    b = list(build_source(_synthetic(seed=5, frames=4), Path(".")).frames(10))
    c = list(build_source(_synthetic(seed=6, frames=4), Path(".")).frames(10))
    assert [f.data for f in a] == [f.data for f in b]
    assert [f.data for f in a] != [f.data for f in c]
    assert len(a) == 4 and all(len(f.data) == 8 * 4 * 3 for f in a)
    assert a[0].data != a[1].data  # the gradient moves


def test_limit_caps_the_frames() -> None:
    assert len(list(build_source(_synthetic(frames=50), Path(".")).frames(3))) == 3


@pytest.mark.parametrize(
    "config",
    [
        "text",
        {"kind": "synthetic"},
        {"name": "bad name", "kind": "synthetic", "width": 1, "height": 1},
        {"name": "a", "kind": "camera"},
        _synthetic(extra=1),
        _synthetic(width=0),
        _synthetic(width=True),
        _synthetic(width=4097),
        _synthetic(seed=-1),
        _synthetic(frames=0),
        {"name": "a", "kind": "synthetic", "width": 4096, "height": 4096, "channels": 4},
    ],
)
def test_refuses_malformed_declarations(config: object) -> None:
    with pytest.raises(SourceError):
        build_source(config, Path("."))


def test_pnm_is_parsed_and_checked() -> None:
    frame = parse_pnm(b"P6\n# c\n2 1\n255\n" + bytes(range(6)), 0)
    assert (frame.width, frame.height, frame.channels, frame.data) == (2, 1, 3, bytes(range(6)))
    assert parse_pnm(b"P5 1 2 255\n\x01\x02", 3).channels == 1
    for bad in (b"P3\n1 1\n255\n0", b"P5\n1 1\n65535\n\0\0", b"P5\n2 2\n255\n\0", b"P5\nx 1\n255\n\0", b"P5\n1", b"P5\n9999 1\n255\n"):
        with pytest.raises(SourceError):
            parse_pnm(bad, 0)


def test_folder_source_reads_in_name_order(tmp_path: Path) -> None:
    folder = tmp_path / "imgs"
    folder.mkdir()
    (folder / "b.pgm").write_bytes(b"P5\n1 1\n255\n\x02")
    (folder / "a.raw").write_bytes(bytes([1, 2, 3, 4]))
    (folder / "notes.txt").write_text("ignored")
    source = build_source({"name": "disk", "kind": "folder", "path": "imgs", "width": 2, "height": 2, "channels": 1}, tmp_path)
    frames = list(source.frames(10))
    assert [f.data for f in frames] == [bytes([1, 2, 3, 4]), b"\x02"]
    assert source.describe()["frames"] == 2 and source.describe()["simulated"] is True


def test_folder_source_fails_closed(tmp_path: Path) -> None:
    (tmp_path / "imgs").mkdir()
    (tmp_path / "imgs" / "a.raw").write_bytes(b"123")
    base = {"name": "d", "kind": "folder"}
    for bad in ({"path": "../x"}, {"path": "/etc"}, {"path": "missing"}, {"path": ""}, {"path": "imgs"}):
        with pytest.raises(SourceError):
            build_source({**base, **bad}, tmp_path)  # imgs holds a .raw but declares no shape
    wrong_size = build_source({**base, "path": "imgs", "width": 2, "height": 2, "channels": 1}, tmp_path)
    with pytest.raises(SourceError):
        list(wrong_size.frames(5))


def test_folder_source_bounds_the_number_of_files(tmp_path: Path) -> None:
    (tmp_path / "many").mkdir()
    for index in range(257):
        (tmp_path / "many" / f"{index:04}.pgm").write_bytes(b"P5\n1 1\n255\n\0")
    with pytest.raises(SourceError):
        build_source({"name": "d", "kind": "folder", "path": "many"}, tmp_path)


def test_a_new_kind_can_be_plugged_in() -> None:
    class Fixed:
        kind = "fixed"

        def __init__(self, name: str) -> None:
            self.name = name

        def describe(self) -> dict:
            return {"name": self.name, "kind": self.kind}

        def frames(self, limit: int):  # type: ignore[no-untyped-def]
            yield Frame(0, 1, 1, 1, b"x")

    register_source_kind("fixed", lambda name, config, base: Fixed(name), set())
    assert list(build_source({"name": "f", "kind": "fixed"}, Path(".")).frames(1))[0].data == b"x"
