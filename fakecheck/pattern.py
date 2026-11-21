"""Deterministic test data.

Every 4 KiB sector starts with a 16-byte stamp (magic, run id, sector index) followed by
pseudorandom filler. When a sector reads back wrong, the stamp tells us *how* it went wrong:
blank (nothing stored), or holding a different sector's data (the drive wraps around).
"""
from __future__ import annotations

import random
import struct

SECTOR = 4096
CHUNK = 4 << 20  # 4 MiB: sector- and page-aligned, required for uncached I/O
SECTORS_PER_CHUNK = CHUNK // SECTOR
MAGIC = b"FDC1"
STAMP = struct.Struct("<4sIQ")  # magic, run id, global sector index = 16 bytes

ZEROS = bytes(SECTOR)
ERASED = b"\xff" * SECTOR


def make_chunk(run_id: int, chunk_index: int) -> bytes:
    """The exact bytes chunk `chunk_index` of run `run_id` must contain."""
    data = bytearray(random.Random((run_id << 40) ^ chunk_index).randbytes(CHUNK))
    first = chunk_index * SECTORS_PER_CHUNK
    for i in range(SECTORS_PER_CHUNK):
        STAMP.pack_into(data, i * SECTOR, MAGIC, run_id, first + i)
    return bytes(data)


def classify(sector: bytes, run_id: int, expected_index: int) -> tuple[str, int | None]:
    """Explain a sector that did NOT match. Returns (kind, other_sector_index)."""
    if len(sector) < SECTOR:
        return "missing", None
    if sector == ZEROS:
        return "zeros", None
    if sector == ERASED:
        return "erased", None
    magic, rid, idx = STAMP.unpack_from(sector)
    if magic == MAGIC and rid == run_id and idx != expected_index:
        return "aliased", idx
    return "corrupt", None
