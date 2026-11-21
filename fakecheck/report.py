"""Turn disks, hints and test results into plain-language text."""
from __future__ import annotations

import os
import re
import time

from .drives import Disk
from .hints import gib, hints
from .verify import KIND_TEXT, Result

REPORTS_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "reports")

HEADLINE = {
    "PASS": "PASS: every byte written came back intact.",
    "PASS_PARTIAL": "PASS (partial): no errors in the part tested, but you capped the test, "
                    "so this does NOT prove the full capacity.",
    "FAKE": "FAKE CAPACITY: the drive claims more space than it can actually store.",
    "FAIL": "FAIL: data was lost or corrupted. The drive is unreliable.",
    "INCOMPLETE": "INCOMPLETE: the test didn't finish, so there's no verdict.",
}


def describe_disk(disk: Disk) -> str:
    lines = [
        f"Disk {disk.number}: {disk.model or '(no name)'}",
        f"  Claimed size : {gib(disk.size)}  ({disk.size:,} bytes)",
        f"  Bus          : {disk.bus}",
        f"  USB ID       : {disk.vid}:{disk.pid}" if disk.vid else "  USB ID       : (not found)",
        f"  Serial       : {disk.serial or '(none)'}",
        f"  Partitioning : {disk.partition_style}",
    ]
    for v in disk.volumes:
        if v.letter:
            lines.append(f"  Volume {v.letter}:    : {gib(v.size)}, {gib(v.free)} free, "
                         f"{v.filesystem or '?'} '{v.label}'")
    lines.append("")
    lines.append("What the drive reports (hints, not proof):")
    marks = {"red": "[RED FLAG]", "warn": "[warning] ", "info": "[info]    "}
    lines += [f"  {marks[h.level]} {h.text}" for h in hints(disk)]
    return "\n".join(lines)


def describe_result(res: Result) -> str:
    v = res.verdict
    lines = [HEADLINE[v], ""]
    lines.append(f"Filesystem size : {gib(res.total_size)} (already used before test: {gib(res.used_before)})")
    lines.append(f"Planned to write: {gib(res.planned)}")
    lines.append(f"Written         : {gib(res.written)} in {res.write_seconds / 60:.1f} min"
                 + (f" ({res.written / 2**20 / res.write_seconds:.1f} MB/s)" if res.write_seconds else ""))
    lines.append(f"Read back OK    : {gib(res.good)}"
                 + (f" ({res.good / 2**20 / res.verify_seconds:.1f} MB/s)" if res.verify_seconds else ""))
    if res.bad:
        lines.append(f"Bad             : {gib(res.bad_total)}, first problem {gib(res.first_bad or 0)} into the test data")
        for kind, n in sorted(res.bad.items(), key=lambda kv: -kv[1]):
            lines.append(f"  - {gib(n)} {KIND_TEXT.get(kind, kind)}")
    est = res.real_capacity_estimate
    if est is not None:
        qualifier = "at most " if res.disconnected and not res.good else "about "
        lines += ["", f"Real usable capacity: {qualifier}{gib(est)} ({est / 1e9:,.1f} GB), "
                      f"vs {gib(res.total_size)} claimed."]
    if res.wrap_period:
        lines.append(f"Wrap-around period: {gib(res.wrap_period)}. Writing past that point overwrites "
                     "data from the start of the drive.")
    notes = []
    if res.early_stop:
        notes.append(f"Writing stopped early: {res.early_stop}.")
    if res.write_error:
        notes.append(f"Write error: {res.write_error}" + (" (disk full, normal)" if res.disk_full else ""))
    if res.disconnected:
        notes.append("The drive disconnected during the test. Counterfeit controllers often hang when "
                     "they hit the fake zone. Unplug it and plug it back in.")
    notes += [f"Read error: {e}" for e in res.read_errors[:5]]
    if res.cancelled:
        notes.append("You stopped the test.")
    if notes:
        lines += ["", "Notes:"] + [f"  - {n}" for n in notes]
    lines += ["", "What to do:"] + [f"  - {a}" for a in advice(res)]
    return "\n".join(lines)


def advice(res: Result) -> list[str]:
    v = res.verdict
    if v == "PASS":
        return ["The capacity is real. Still keep a second copy of anything important."]
    if v == "PASS_PARTIAL":
        return ["Run it again with no limit to check the whole drive."]
    if v == "FAKE":
        out = ["Don't trust it with anything you can't lose. Files saved past the real capacity are gone."]
        est = res.real_capacity_estimate
        if est:
            safe = int(est * 0.95) // 2**20
            out.append(f"To use it anyway: back up, then repartition it to about {safe:,} MiB (95% of the "
                       "real size, starting at the beginning of the disk) and leave the rest unallocated.")
        out.append("Ask the seller for a refund. Save this report as evidence.")
        return out
    if v == "FAIL":
        return ["Copy off anything you need now, then stop using this drive.",
                "If the cable or hub might be loose, plug it straight into the PC and run the test again."]
    return ["Run the test again and let it finish."]


def save_report(disk: Disk | None, res: Result) -> str:
    os.makedirs(REPORTS_DIR, exist_ok=True)
    name = re.sub(r"[^A-Za-z0-9]+", "-", disk.model if disk else "drive").strip("-") or "drive"
    path = os.path.join(REPORTS_DIR, f"{time.strftime('%Y-%m-%d_%H%M%S')}_{name}_{res.verdict}.txt")
    body = [f"fakecheck report, {time.strftime('%Y-%m-%d %H:%M:%S')}, tested {res.root}", ""]
    if disk:
        body += [describe_disk(disk), ""]
    body.append(describe_result(res))
    with open(path, "w", encoding="utf-8") as f:
        f.write("\n".join(body) + "\n")
    return path
