"""The write/verify test: the only thing that actually proves a drive's capacity.

1. Fill free space with stamped test files (see pattern.py), fsync'ing each one.
2. After each file, spot-check uncached: the chunk just written, the very first chunk,
   and a random earlier one. Fakes fail this quickly (blank reads, or wrap-around
   overwriting the start), so we stop early instead of "writing" 20 TB at 8 MB/s.
3. Read everything back uncached and account for every 4 KiB sector.
"""
from __future__ import annotations

import errno
import os
import random
import shutil
import threading
import time
from dataclasses import dataclass, field
from typing import Callable

from . import pattern
from .pattern import CHUNK, SECTOR, SECTORS_PER_CHUNK
from .winio import UncachedFile

TEST_DIR_NAME = "fakecheck-test"
CHUNKS_PER_FILE = 64      # 256 MiB per file
RESERVE = 64 << 20        # leave a little free space so the filesystem stays happy

ProgressFn = Callable[[str, int, int, float], None]  # phase, done bytes, total bytes, bytes/s

KIND_TEXT = {
    "zeros": "read back as all zeros: nothing was stored there",
    "erased": "read back blank (0xFF): the writes were silently thrown away",
    "aliased": "held data written somewhere else: the drive wraps around and overwrites itself",
    "corrupt": "came back with changed bytes",
    "missing": "came back short (the drive returned less data than asked for)",
    "unreadable": "could not be read at all (I/O error)",
    "unwritable": "could not be written: the drive refused writes or disconnected",
}


@dataclass
class TestFile:
    path: str
    first_chunk: int   # global chunk index of this file's first chunk
    n_chunks: int = 0


@dataclass
class Result:
    root: str
    run_id: int
    total_size: int = 0        # filesystem size
    used_before: int = 0
    planned: int = 0           # bytes we meant to write
    limited: bool = False      # user capped the test below the free space
    written: int = 0
    good: int = 0
    bad: dict[str, int] = field(default_factory=dict)     # kind -> bytes
    first_bad: int | None = None   # byte offset into the test data (write order)
    tested_end: int = 0            # bytes of test data accounted for
    alias_pairs: list[tuple[int, int]] = field(default_factory=list)  # (offset, offset it really held)
    min_alias_gap: int | None = None   # wrapping N times gives gaps of 1x..Nx the real size
    early_stop: str | None = None  # why writing stopped before the plan
    write_error: str | None = None
    disk_full: bool = False
    disconnected: bool = False
    read_errors: list[str] = field(default_factory=list)
    cancelled: bool = False
    write_seconds: float = 0.0
    verify_seconds: float = 0.0

    def add_bad(self, kind: str, offset: int, nbytes: int, other_offset: int | None = None):
        self.bad[kind] = self.bad.get(kind, 0) + nbytes
        if self.first_bad is None or offset < self.first_bad:
            self.first_bad = offset
        self.tested_end = max(self.tested_end, offset + nbytes)
        if other_offset is not None:
            gap = abs(other_offset - offset)
            self.min_alias_gap = gap if self.min_alias_gap is None else min(self.min_alias_gap, gap)
            if len(self.alias_pairs) < 1000:
                self.alias_pairs.append((offset, other_offset))

    @property
    def bad_total(self) -> int:
        return sum(self.bad.values())

    @property
    def wrap_period(self) -> int | None:
        return self.min_alias_gap

    @property
    def verdict(self) -> str:
        """PASS, PASS_PARTIAL, FAKE, FAIL or INCOMPLETE."""
        if self.alias_pairs:
            return "FAKE"
        if self.first_bad is None:
            if self.cancelled or self.disconnected or self.written == 0 or self.tested_end < self.written:
                return "INCOMPLETE"
            if (self.write_error and not self.disk_full) or self.early_stop:
                return "FAIL"  # e.g. a spot check failed but the later full read passed: flaky
            return "PASS_PARTIAL" if self.limited else "PASS"
        # Fake capacity looks like: good data up to a point, then (almost) nothing survives.
        span = max(1, self.tested_end - self.first_bad)
        if self.bad_total / span >= 0.9 and (self.first_bad > 0 or self.used_before > 0):
            return "FAKE"
        return "FAIL"

    @property
    def real_capacity_estimate(self) -> int | None:
        """Rough usable size in bytes, when the drive looks fake."""
        if self.verdict != "FAKE":
            return None
        if self.wrap_period:
            return self.wrap_period
        return self.used_before + (self.first_bad or 0)


class _Reporter:
    def __init__(self, fn: ProgressFn | None, phase: str, total: int):
        self.fn, self.phase, self.total = fn, phase, total
        self.t0 = self.last = time.monotonic()

    def __call__(self, done: int, force: bool = False):
        now = time.monotonic()
        if self.fn and (force or now - self.last >= 0.25):
            self.last = now
            self.fn(self.phase, done, self.total, done / max(now - self.t0, 1e-6))


def _is_disk_full(e: OSError) -> bool:
    return e.errno == errno.ENOSPC or getattr(e, "winerror", None) in (39, 112)


def _check_chunk(res: Result, data: bytes, g: int, *, record: bool) -> bool:
    """Compare chunk `g` with what it should be. Returns True if perfect."""
    expected = pattern.make_chunk(res.run_id, g)
    if data == expected:
        if record:
            res.good += CHUNK
            res.tested_end = max(res.tested_end, (g + 1) * CHUNK)
        return True
    if record:
        base = g * CHUNK
        for s in range(SECTORS_PER_CHUNK):
            off = s * SECTOR
            got = data[off:off + SECTOR]
            if got == expected[off:off + SECTOR]:
                res.good += SECTOR
                continue
            kind, other = pattern.classify(got, res.run_id, g * SECTORS_PER_CHUNK + s)
            res.add_bad(kind, base + off, SECTOR, None if other is None else other * SECTOR)
        res.tested_end = max(res.tested_end, base + CHUNK)
    return False


def _spot_check(res: Result, files: list[TestFile], rng: random.Random) -> str | None:
    """Uncached re-read of a few chunks while writing. Returns a reason if any is bad."""
    last = files[-1]
    picks = [(last, last.n_chunks - 1), (files[0], 0)]
    earlier = rng.randrange(0, last.first_chunk + last.n_chunks)
    for f in files:
        if f.first_chunk <= earlier < f.first_chunk + f.n_chunks:
            picks.append((f, earlier - f.first_chunk))
    for f, local in picks:
        if f.n_chunks == 0:
            continue
        g = f.first_chunk + local
        try:
            with UncachedFile(f.path) as uf:
                data = uf.read_chunk(local)
        except OSError as e:
            return f"spot check could not read back {g * CHUNK / 2**30:.2f} GiB in: {e}"
        if not _check_chunk(res, data, g, record=False):
            return f"spot check: data at {g * CHUNK / 2**30:.2f} GiB in did not survive"
    return None


def write_phase(res: Result, test_dir: str, *, chunks_per_file: int, cancel: threading.Event,
                progress: ProgressFn | None, spot_checks: bool = True) -> list[TestFile]:
    os.makedirs(test_dir, exist_ok=True)
    files: list[TestFile] = []
    total_chunks = res.planned // CHUNK
    rep = _Reporter(progress, "write", total_chunks * CHUNK)
    rng = random.Random(res.run_id)
    t0 = time.monotonic()
    g = 0
    while g < total_chunks and not cancel.is_set():
        n = min(chunks_per_file, total_chunks - g)
        tf = TestFile(os.path.join(test_dir, f"FDC_{len(files):05d}.bin"), g)
        files.append(tf)
        try:
            with open(tf.path, "wb", buffering=0) as f:
                for c in range(n):
                    if cancel.is_set():
                        break
                    data = pattern.make_chunk(res.run_id, g + c)
                    if f.write(data) != CHUNK:
                        raise OSError(errno.EIO, "short write")
                    tf.n_chunks += 1
                    res.written += CHUNK
                    rep(res.written)
                os.fsync(f.fileno())
        except OSError as e:
            res.write_error = str(e)
            res.disk_full = _is_disk_full(e)
            break
        g += tf.n_chunks
        if spot_checks and tf.n_chunks and not cancel.is_set():
            reason = _spot_check(res, files, rng)
            if reason:
                res.early_stop = reason
                break
    res.cancelled = cancel.is_set()
    rep(res.written, force=True)
    res.write_seconds = time.monotonic() - t0
    if res.write_error and not res.disk_full:
        # The filesystem promised this space; the drive couldn't take it.
        lost = res.planned - res.written
        if lost > 0:
            res.add_bad("unwritable", res.written, lost)
        res.disconnected = not os.path.isdir(test_dir)
    return [f for f in files if f.n_chunks]


def verify_phase(res: Result, files: list[TestFile], *, cancel: threading.Event,
                 progress: ProgressFn | None) -> None:
    total = sum(f.n_chunks for f in files) * CHUNK
    rep = _Reporter(progress, "verify", total)
    t0 = time.monotonic()
    done = 0
    for f in files:
        if cancel.is_set():
            res.cancelled = True
            break
        c = 0
        try:
            with UncachedFile(f.path) as uf:
                for c in range(f.n_chunks):
                    if cancel.is_set():
                        res.cancelled = True
                        break
                    _check_chunk(res, uf.read_chunk(), f.first_chunk + c, record=True)
                    done += CHUNK
                    rep(done)
        except OSError as e:
            if not os.path.isdir(os.path.dirname(f.path)):
                res.disconnected = True
                res.read_errors.append(f"drive disappeared while reading {os.path.basename(f.path)}")
                break
            res.read_errors.append(f"{os.path.basename(f.path)}: {e}")
            start = f.first_chunk + c
            res.add_bad("unreadable", start * CHUNK, (f.first_chunk + f.n_chunks - start) * CHUNK)
            done += (f.n_chunks - c) * CHUNK
        if res.cancelled:
            break
    rep(done, force=True)
    res.verify_seconds = time.monotonic() - t0


def run_test(root: str, *, limit_bytes: int | None = None, keep_files: bool = False,
             cancel: threading.Event | None = None, progress: ProgressFn | None = None,
             chunks_per_file: int = CHUNKS_PER_FILE, reserve: int = RESERVE) -> Result:
    """Run the full test on the filesystem at `root` (a drive root like 'E:\\')."""
    cancel = cancel or threading.Event()
    usage = shutil.disk_usage(root)
    res = Result(root=root, run_id=random.getrandbits(32),
                 total_size=usage.total, used_before=usage.used)
    planned = max(0, usage.free - reserve)
    if limit_bytes is not None and limit_bytes < planned:
        planned, res.limited = limit_bytes, True
    res.planned = planned // CHUNK * CHUNK
    test_dir = os.path.join(root, TEST_DIR_NAME)
    try:
        files = write_phase(res, test_dir, chunks_per_file=chunks_per_file,
                            cancel=cancel, progress=progress)
        if files and not res.disconnected:
            verify_phase(res, files, cancel=cancel, progress=progress)
    finally:
        if not keep_files:
            shutil.rmtree(test_dir, ignore_errors=True)
    return res
