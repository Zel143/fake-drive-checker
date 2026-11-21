"""Read files straight from the device, bypassing the Windows file cache.

Without this, reading back what we just wrote would come from RAM and a fake drive
would pass. FILE_FLAG_NO_BUFFERING needs a sector-aligned buffer and sector-multiple
sizes; VirtualAlloc gives page-aligned memory and CHUNK is a multiple of 4 KiB.
"""
from __future__ import annotations

import ctypes
import sys

from .pattern import CHUNK

if sys.platform == "win32":
    from ctypes import wintypes

    _k32 = ctypes.WinDLL("kernel32", use_last_error=True)
    _k32.CreateFileW.restype = wintypes.HANDLE
    _k32.CreateFileW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD, ctypes.c_void_p,
                                 wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE]
    _k32.ReadFile.argtypes = [wintypes.HANDLE, ctypes.c_void_p, wintypes.DWORD,
                              ctypes.POINTER(wintypes.DWORD), ctypes.c_void_p]
    _k32.SetFilePointerEx.argtypes = [wintypes.HANDLE, ctypes.c_longlong,
                                      ctypes.c_void_p, wintypes.DWORD]
    _k32.CloseHandle.argtypes = [wintypes.HANDLE]
    _k32.VirtualAlloc.restype = ctypes.c_void_p
    _k32.VirtualAlloc.argtypes = [ctypes.c_void_p, ctypes.c_size_t, wintypes.DWORD, wintypes.DWORD]
    _k32.VirtualFree.argtypes = [ctypes.c_void_p, ctypes.c_size_t, wintypes.DWORD]
    _INVALID = wintypes.HANDLE(-1).value

    GENERIC_READ = 0x80000000
    SHARE_READ_WRITE = 0x1 | 0x2  # our own writer may still have the file open
    OPEN_EXISTING = 3
    NO_BUFFERING = 0x20000000
    SEQUENTIAL_SCAN = 0x08000000

    class UncachedFile:
        def __init__(self, path: str):
            self._h = _k32.CreateFileW(path, GENERIC_READ, SHARE_READ_WRITE, None, OPEN_EXISTING,
                                       NO_BUFFERING | SEQUENTIAL_SCAN, None)
            if self._h == _INVALID:
                raise ctypes.WinError(ctypes.get_last_error())
            self._buf = _k32.VirtualAlloc(None, CHUNK, 0x3000, 0x04)  # MEM_COMMIT|RESERVE, RW
            if not self._buf:
                _k32.CloseHandle(self._h)
                raise MemoryError("VirtualAlloc failed")

        def read_chunk(self, index: int | None = None) -> bytes:
            """Read the next chunk, or chunk `index` of this file."""
            if index is not None and not _k32.SetFilePointerEx(self._h, index * CHUNK, None, 0):
                raise ctypes.WinError(ctypes.get_last_error())
            got = wintypes.DWORD()
            if not _k32.ReadFile(self._h, self._buf, CHUNK, ctypes.byref(got), None):
                raise ctypes.WinError(ctypes.get_last_error())
            return ctypes.string_at(self._buf, got.value)

        def close(self):
            if self._h:
                _k32.CloseHandle(self._h)
                self._h = None
            if self._buf:
                _k32.VirtualFree(self._buf, 0, 0x8000)  # MEM_RELEASE
                self._buf = None

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            self.close()

else:  # not Windows: plain reads (cache NOT bypassed; fine for running the tests)
    class UncachedFile:
        def __init__(self, path: str):
            self._f = open(path, "rb", buffering=0)

        def read_chunk(self, index: int | None = None) -> bytes:
            if index is not None:
                self._f.seek(index * CHUNK)
            return self._f.read(CHUNK)

        def close(self):
            self._f.close()

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            self.close()
