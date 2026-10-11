"""``.hxb`` backup files - an ``AF6L`` container of zlib sections.

Format (from public write-ups, e.g. AntonyCorbett/HelixBackupFiles):

* the file starts with the ASCII magic ``AF6L``;
* the footer is a list of 36-byte descriptors (``IDXH``, ``CSED``, ``MNLS``,
  ``BOLG``, IR sections, setlist sections) giving each section's offset/length;
* the address of the footer is a little-endian value at file offset ``0x08``;
* globals and setlists inflate to JSON, IRs inflate to WAV (RIFF).

Only the container header/footer is decoded here (enough for ``backup``
inspection); section decoding and writing land in M8.
"""

from __future__ import annotations

import struct
from dataclasses import dataclass
from pathlib import Path
from typing import Union

PathLike = Union[str, Path]

MAGIC = b"AF6L"
#: Descriptor signature tags seen in real backups.
KNOWN_TAGS = (b"IDXH", b"CSED", b"MNLS", b"BOLG")


class HxbError(Exception):
    """A .hxb file could not be read or decoded."""


@dataclass
class HxbDescriptor:
    tag: bytes
    offset: int
    length: int


@dataclass
class HxbFile:
    version: int
    footer_address: int
    descriptors: list[HxbDescriptor]


def read_header(data: bytes) -> HxbFile:
    """Decode the container header and footer descriptor table."""
    if len(data) < 16:
        raise HxbError("file is too short to be a .hxb")
    if data[:4] != MAGIC:
        raise HxbError(f"bad magic {data[:4]!r}, expected {MAGIC!r}")
    footer_address = struct.unpack_from("<I", data, 0x08)[0]
    version = struct.unpack_from("<I", data, 0x04)[0]
    descriptors = _read_descriptors(data, footer_address)
    return HxbFile(version=version, footer_address=footer_address, descriptors=descriptors)


def load(path: PathLike) -> HxbFile:
    p = Path(path)
    try:
        return read_header(p.read_bytes())
    except OSError as exc:
        raise HxbError(f"could not read {p}: {exc}") from exc


def _read_descriptors(data: bytes, footer_address: int) -> list[HxbDescriptor]:
    out: list[HxbDescriptor] = []
    # The footer is a run of 36-byte descriptors terminated by the section count
    # being exhausted; walk until the address runs past the file or a tag is
    # unrecognised. This is intentionally conservative until M8 pins it.
    pos = footer_address
    while pos + 36 <= len(data):
        tag = data[pos : pos + 4]
        if tag not in KNOWN_TAGS:
            break
        # Layout: tag(4) + reserved(?) + offset(u32 LE) + length(u32 LE).
        offset, length = struct.unpack_from("<II", data, pos + 24)
        out.append(HxbDescriptor(tag=tag, offset=offset, length=length))
        pos += 36
    return out
