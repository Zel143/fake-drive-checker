"""Red flags you can see without writing anything, plus the rules for which drives may be tested.

Hints are only hints. A genuine drive can have a blank serial; a fake can have a believable
name. Only the write/verify test proves capacity.
"""
from __future__ import annotations

import os
from dataclasses import dataclass

from .drives import Disk, Volume

# USB IDs seen on counterfeit drives. Add your own as you find them.
KNOWN_FAKE_IDS = {
    ("048D", "1234"): "ITE Tech ID with a placeholder product number, seen on counterfeit "
                      "flash drives and 'SSDs' with inflated capacity",
}
PLACEHOLDER_PIDS = {"0000", "1234", "FFFF"}
PLACEHOLDER_NAMES = ("vendorco", "productcode", "generic usb", "usb disk 3.0", "mass storage")
TESTABLE_BUSES = {"USB", "SD", "MMC"}


@dataclass
class Hint:
    level: str   # "red", "warn" or "info"
    text: str


def gib(n: int) -> str:
    return f"{n / 2**30:,.2f} GiB"


def hints(disk: Disk) -> list[Hint]:
    out: list[Hint] = []
    ids = (disk.vid or "", disk.pid or "")
    if ids in KNOWN_FAKE_IDS:
        out.append(Hint("red", f"USB ID {ids[0]}:{ids[1]} is on the known-fake list: {KNOWN_FAKE_IDS[ids]}."))
    elif disk.pid in PLACEHOLDER_PIDS:
        out.append(Hint("warn", f"USB product ID {disk.pid} looks like a placeholder, not a real product."))
    name = disk.model.lower()
    for p in PLACEHOLDER_NAMES:
        if p in name:
            out.append(Hint("warn", f"Model name '{disk.model}' is a generic placeholder ('{p}')."))
            break
    if disk.bus.upper() in TESTABLE_BUSES and not disk.serial:
        out.append(Hint("warn", "No serial number. Common on cheap fakes (some genuine cheap drives too)."))
    mib = disk.size / 2**20
    if disk.size and disk.size % 2**20 == 0 and int(mib) % 10_000 == 0:
        out.append(Hint("warn", f"Claimed size is a suspiciously round {int(mib):,} MiB. Real flash "
                                "rarely comes out that even."))
    if disk.bus.upper() == "USB" and disk.size >= 4 * 10**12:
        out.append(Hint("warn", f"Claims {disk.size / 1e12:,.1f} TB over USB. Huge cheap USB drives "
                                "are the classic fake; check the price you paid."))
    if disk.size and disk.partitioned < 0.5 * disk.size and disk.volumes:
        out.append(Hint("info", f"Only {gib(disk.partitioned)} of {gib(disk.size)} is partitioned. "
                                "If you shrank it after finding fake capacity, that's expected: "
                                "never partition the rest."))
    if not out:
        out.append(Hint("info", "No red flags in what the drive reports. Run the test to be sure."))
    return out


def blockers(disk: Disk, volume: Volume, *, allow_non_usb: bool = False) -> list[str]:
    """Reasons this volume must NOT be test-filled. Empty list = OK to test."""
    why = []
    system_letter = os.environ.get("SystemDrive", "C:").rstrip(":\\").upper()
    if disk.is_system or disk.is_boot or volume.letter == system_letter:
        why.append("this is your system disk")
    if disk.bus.upper() not in TESTABLE_BUSES and not allow_non_usb:
        why.append(f"it's an internal {disk.bus or 'unknown-bus'} disk, not a USB/SD drive")
    if not volume.letter:
        why.append("the partition has no drive letter")
    if volume.free < (256 << 20):
        why.append("less than 256 MiB free: nothing to test")
    return why
