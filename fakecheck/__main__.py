"""fakecheck: catch USB drives and SD cards that lie about their capacity.

  py -3.12 -m fakecheck                 open the window
  py -3.12 -m fakecheck list            list drives with red flags (read-only)
  py -3.12 -m fakecheck inspect E:      details for one drive (read-only)
  py -3.12 -m fakecheck test E:         full write/verify test (asks first)
"""
from __future__ import annotations

import argparse
import sys
import threading

from . import drives, hints, report, verify
from .hints import gib


def _find(letter: str):
    found = drives.disk_for_letter(drives.list_disks(), letter)
    if not found:
        sys.exit(f"No volume with drive letter {letter.rstrip(':')}: found.")
    return found


def cmd_list(_args):
    for d in drives.list_disks():
        flags = [h for h in hints.hints(d) if h.level in ("red", "warn")]
        mark = "RED FLAG" if any(h.level == "red" for h in flags) else ("check" if flags else "ok")
        letters = ",".join(f"{x}:" for x in d.letters) or "-"
        print(f"[{mark:8}] disk {d.number}  {letters:8} {d.bus:5} {gib(d.size):>14}  {d.model}")
    print("\nHints only. `test <letter>` is the real check.")


def cmd_inspect(args):
    disk, _vol = _find(args.drive)
    print(report.describe_disk(disk))


def _progress_printer():
    def show(phase, done, total, rate):
        pct = 100 * done / total if total else 100
        eta = (total - done) / rate / 60 if rate else 0
        sys.stdout.write(f"\r{phase:6} {gib(done):>12} / {gib(total):<12} {pct:5.1f}%  "
                         f"{rate / 2**20:6.1f} MB/s  ETA {eta:6.1f} min ")
        sys.stdout.flush()
        if done >= total:
            sys.stdout.write("\n")
    return show


def cmd_test(args):
    disk, vol = _find(args.drive)
    why = hints.blockers(disk, vol, allow_non_usb=args.allow_non_usb)
    if why:
        sys.exit(f"Refusing to test {vol.letter}: because " + "; ".join(why) + ".")
    print(report.describe_disk(disk))
    limit = int(args.limit_gb * 2**30) if args.limit_gb else None
    size = min(vol.free, limit) if limit else vol.free
    print(f"\nThis writes about {gib(size)} of test files to {vol.letter}:\\{verify.TEST_DIR_NAME}, "
          "reads them back, then deletes them.")
    print("WARNING: on a fake drive, writing past the real capacity can overwrite files ALREADY on it.")
    print("Back up anything on this drive first.")
    if not args.yes:
        if input(f"Type the drive letter ({vol.letter}) to start: ").strip().rstrip(":").upper() != vol.letter:
            sys.exit("Cancelled.")
    cancel = threading.Event()
    try:
        res = verify.run_test(f"{vol.letter}:\\", limit_bytes=limit, keep_files=args.keep,
                              cancel=cancel, progress=_progress_printer())
    except KeyboardInterrupt:
        sys.exit("\nStopped. Test files may be left in " + f"{vol.letter}:\\{verify.TEST_DIR_NAME}")
    print("\n" + report.describe_result(res))
    print(f"\nReport saved: {report.save_report(disk, res)}")
    sys.exit(0 if res.verdict in ("PASS", "PASS_PARTIAL") else 1)


def main(argv=None):
    p = argparse.ArgumentParser(prog="fakecheck", description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="cmd")
    sub.add_parser("list", help="list drives and red flags (read-only)").set_defaults(fn=cmd_list)
    i = sub.add_parser("inspect", help="show one drive's details (read-only)")
    i.add_argument("drive")
    i.set_defaults(fn=cmd_inspect)
    t = sub.add_parser("test", help="full write/verify capacity test")
    t.add_argument("drive")
    t.add_argument("--limit-gb", type=float, help="only test this much (quick, but proves less)")
    t.add_argument("--keep", action="store_true", help="keep the test files afterwards")
    t.add_argument("--yes", action="store_true", help="skip the confirmation prompt")
    t.add_argument("--allow-non-usb", action="store_true", help="allow internal non-system disks")
    t.set_defaults(fn=cmd_test)
    args = p.parse_args(argv)
    if not args.cmd:
        from .gui import main as gui_main
        return gui_main()
    args.fn(args)


if __name__ == "__main__":
    main()
