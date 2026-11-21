"""Simulate how real fakes misbehave by tampering with test files between write and verify."""
import os
import shutil
import threading

import pytest

from fakecheck import pattern, verify
from fakecheck.pattern import CHUNK, SECTOR

MiB = 1 << 20


def _write(tmp_path, chunks=8, per_file=2, used_before=0):
    res = verify.Result(root=str(tmp_path), run_id=12345, used_before=used_before)
    res.planned = chunks * CHUNK
    files = verify.write_phase(res, str(tmp_path / "t"), chunks_per_file=per_file,
                               cancel=threading.Event(), progress=None)
    return res, files


def _verify(res, files):
    verify.verify_phase(res, files, cancel=threading.Event(), progress=None)
    return res


# ---- pattern ----

def test_chunks_are_deterministic_and_distinct():
    assert pattern.make_chunk(1, 0) == pattern.make_chunk(1, 0)
    assert pattern.make_chunk(1, 0) != pattern.make_chunk(1, 1)
    assert pattern.make_chunk(1, 0) != pattern.make_chunk(2, 0)


def test_classify_tells_failures_apart():
    chunk = pattern.make_chunk(7, 3)
    sector5 = chunk[5 * SECTOR:6 * SECTOR]
    expected_idx = 3 * pattern.SECTORS_PER_CHUNK + 5
    assert pattern.classify(pattern.ZEROS, 7, 0) == ("zeros", None)
    assert pattern.classify(pattern.ERASED, 7, 0) == ("erased", None)
    assert pattern.classify(sector5, 7, 0) == ("aliased", expected_idx)
    flipped = bytearray(sector5)
    flipped[100] ^= 1
    assert pattern.classify(bytes(flipped), 7, expected_idx) == ("corrupt", None)
    assert pattern.classify(b"short", 7, 0) == ("missing", None)


# ---- verdicts ----

def test_genuine_drive_passes(tmp_path, monkeypatch):
    # Pretend the filesystem has exactly 6 chunks + reserve free.
    usage = shutil.disk_usage(tmp_path)._replace(used=0, free=6 * CHUNK + verify.RESERVE)
    monkeypatch.setattr(verify.shutil, "disk_usage", lambda _p: usage)
    res = verify.run_test(str(tmp_path), chunks_per_file=2)
    assert res.verdict == "PASS"
    assert res.good == res.written == 6 * CHUNK
    assert not (tmp_path / verify.TEST_DIR_NAME).exists()


def test_limit_gives_partial_pass(tmp_path):
    res = verify.run_test(str(tmp_path), limit_bytes=3 * CHUNK)
    assert res.verdict == "PASS_PARTIAL"


def test_fake_zone_reading_zeros_is_fake(tmp_path):
    # Real capacity ends after file 1 (4 chunks = 16 MiB); everything later reads as zeros.
    res, files = _write(tmp_path, chunks=8, per_file=2, used_before=100 * MiB)
    for f in files[2:]:
        with open(f.path, "r+b") as fh:
            fh.write(bytes(f.n_chunks * CHUNK))
    _verify(res, files)
    assert res.verdict == "FAKE"
    assert res.first_bad == 4 * CHUNK
    assert res.bad == {"zeros": 4 * CHUNK}
    assert res.real_capacity_estimate == 100 * MiB + 4 * CHUNK


def test_discarded_writes_reading_ff_is_fake(tmp_path):
    res, files = _write(tmp_path)
    with open(files[-1].path, "r+b") as fh:
        fh.write(b"\xff" * (files[-1].n_chunks * CHUNK))
    _verify(res, files)
    assert res.verdict == "FAKE"
    assert set(res.bad) == {"erased"}


def test_wrap_around_is_fake_and_measures_period(tmp_path):
    # A drive that's really 2 files big: files 2-3 land on top of files 0-1.
    res, files = _write(tmp_path, chunks=8, per_file=2)
    for low, high in ((files[0], files[2]), (files[1], files[3])):
        shutil.copyfile(high.path, low.path)
    _verify(res, files)
    assert res.verdict == "FAKE"
    assert "aliased" in res.bad
    assert res.wrap_period == 4 * CHUNK
    assert res.real_capacity_estimate == 4 * CHUNK


def test_wrapping_several_times_finds_the_real_size(tmp_path):
    # Really 2 chunks; 8 chunks written, so the flash wrapped 3 times. Each file ends up
    # holding the LAST data that landed on its physical spot (from file 3).
    res, files = _write(tmp_path, chunks=8, per_file=2)
    for f in files[:3]:
        shutil.copyfile(files[3].path, f.path)
    _verify(res, files)
    assert res.verdict == "FAKE"
    assert res.wrap_period == 2 * CHUNK


def test_scattered_bit_errors_are_fail_not_fake(tmp_path):
    res, files = _write(tmp_path)
    for f in (files[0], files[-1]):
        with open(f.path, "r+b") as fh:
            fh.seek(SECTOR + 50)
            b = fh.read(1)
            fh.seek(SECTOR + 50)
            fh.write(bytes([b[0] ^ 0xFF]))
    _verify(res, files)
    assert res.verdict == "FAIL"
    assert res.bad == {"corrupt": 2 * SECTOR}


def test_spot_check_stops_writing_early_on_a_fake(tmp_path, monkeypatch):
    # Simulate a drive that silently drops every write after 3 chunks.
    calls = {"n": 0}

    class DroppingFile:
        def __init__(self, path, mode, buffering):
            self.f = open(path, mode, buffering=buffering)

        def write(self, data):
            calls["n"] += 1
            return self.f.write(data if calls["n"] <= 3 else bytes(len(data)))

        def fileno(self):
            return self.f.fileno()

        def __enter__(self):
            return self

        def __exit__(self, *e):
            self.f.close()

    monkeypatch.setattr(verify, "open", DroppingFile, raising=False)
    res, files = _write(tmp_path, chunks=40, per_file=2)
    assert res.early_stop and "did not survive" in res.early_stop
    assert res.written < 40 * CHUNK            # didn't fill the whole "drive"
    _verify(res, files)
    assert res.verdict == "FAKE"
    assert res.first_bad == 3 * CHUNK


def test_write_error_marks_rest_unwritable(tmp_path, monkeypatch):
    class FailingFile:
        n = 0

        def __init__(self, path, mode, buffering):
            self.f = open(path, mode, buffering=buffering)

        def write(self, data):
            FailingFile.n += 1
            if FailingFile.n > 5:
                raise OSError(1167, "The device is not connected")
            return self.f.write(data)

        def fileno(self):
            return self.f.fileno()

        def __enter__(self):
            return self

        def __exit__(self, *e):
            self.f.close()

    monkeypatch.setattr(verify, "open", FailingFile, raising=False)
    res, files = _write(tmp_path, chunks=8, per_file=2)
    _verify(res, files)
    assert res.bad == {"unwritable": 3 * CHUNK}
    assert res.good == 5 * CHUNK
    assert res.verdict == "FAKE"
    assert res.real_capacity_estimate == 5 * CHUNK


def test_cancel_is_incomplete(tmp_path):
    cancel = threading.Event()
    cancel.set()
    res = verify.run_test(str(tmp_path), limit_bytes=4 * CHUNK, cancel=cancel)
    assert res.verdict == "INCOMPLETE"


def test_drive_vanishing_during_verify(tmp_path):
    res, files = _write(tmp_path)
    shutil.rmtree(tmp_path / "t")
    _verify(res, files)
    assert res.disconnected
    assert res.verdict == "INCOMPLETE"


def test_reads_bypass_cache_on_windows(tmp_path):
    if os.name != "nt":
        pytest.skip("Windows only")
    from fakecheck.winio import UncachedFile
    p = tmp_path / "x.bin"
    p.write_bytes(pattern.make_chunk(1, 0) + pattern.make_chunk(1, 1))
    with UncachedFile(str(p)) as f:
        assert f.read_chunk(1) == pattern.make_chunk(1, 1)
        assert f.read_chunk(0) == pattern.make_chunk(1, 0)
