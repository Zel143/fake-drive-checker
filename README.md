# Fake Drive Checker

Live web version: https://zel143.github.io/fake-drive-checker/ (Chrome/Edge on a computer; try the demos in any browser).

Catches USB flash drives, SD cards and external SSDs that lie about their capacity: the
"2 TB" stick that really holds 7 GB, the "20 TB SSD" that holds 50. Everything saved past
the real size silently disappears.

It writes checkable data across the drive and reads it all back. Every 4 KiB sector is
stamped with its position, so a failure says **how** the drive is faking it:

| What comes back | Meaning |
|---|---|
| all zeros | nothing was stored there (fake zone) |
| all 0xFF | writes were silently thrown away |
| another sector's stamp | the drive wraps around and overwrites its own beginning |
| changed bytes | bit errors (damaged, not necessarily fake) |

Verdicts: **PASS**, **PASS (partial)**, **FAKE** (with an estimate of the real capacity),
**FAIL** and **INCOMPLETE**.

## Two versions

| | Desktop (Windows, Python) | Web (Chrome/Edge) |
|---|---|---|
| Install | Python 3.12, no packages | none |
| Reads USB ID and flags known fake chips | yes | no (browsers can't see it) |
| Reads bypass the file cache | yes (`FILE_FLAG_NO_BUFFERING`) | no, so you eject and replug between write and read |
| Stops early on a fake | yes (spot checks while writing) | no, it fills the drive or your GB limit |
| Refuses system and internal disks | yes | refuses `C:\` only |

### Desktop

```
run.bat                  open the window
run.bat list             drives + red flags (read-only)
run.bat inspect E:       one drive's details (read-only)
run.bat test E:          full test (asks you to type the letter first)
run.bat test E: --limit-gb 16
```

Reports are saved to `reports/`.

### Web

`serve-web.bat` serves `web/` at http://127.0.0.1:8765 (keep its window open). It's a static site with no backend,
so any static host works (GitHub Pages, Vercel, Netlify).

## Tests

```
py -3.12 -m pytest -q          # desktop engine, hints, parsing
cd web && node --test test/    # web engine, against an in-memory fake drive
```

Both suites simulate the ways real counterfeits fail: a zero-filled fake zone, discarded
writes, wrap-around, and the controller dropping off USB mid-write.

## Safety

The test only writes inside a new `fakecheck-test` folder. But on a counterfeit drive,
writing past the real capacity can corrupt files that are **already on it**, so back up first.
