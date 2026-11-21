# Desktop app: a disconnect is FAIL, not FAKE

Status: ready-for-agent
Part: both

## Why

The two versions disagree about a drive that disconnects partway through writing. The web
version says **FAIL**, and the desktop app says **FAKE** with "real capacity: at most X".
`GLOSSARY.md` now settles it: a **FAKE** verdict comes only from test data that came back
wrong. A disconnect alone is **FAIL**, because a loose cable and a counterfeit controller look
the same.

The web version already does this, so only the desktop app's behaviour changes. `Part: both`
is there because the two versions' reports must end up saying the same thing.

## What happens now (desktop app)

In `fakecheck/verify.py`, when a write fails for any reason other than "disk full", the space
that was never written is recorded as bad (`unwritable`). That makes everything from the
failure point on count as bad, so the verdict comes out **FAKE**, with a capacity estimate of
"at most X" in `fakecheck/report.py`.

## Acceptance criteria

- [ ] A drive that disconnects while writing gets **FAIL** in the desktop app, whatever was verified before the disconnect.
- [ ] Same for a write that's refused while the drive stays connected (not disk full). Per the glossary, FAKE needs data that came back wrong.
- [ ] If the data that *was* written comes back wrong (zeros, blank, wrap-around), the verdict is still **FAKE**. A disconnect doesn't hide real evidence.
- [ ] The desktop report keeps the useful hint, worded like the web version: the drive disconnected after X; counterfeit controllers often do this at their real capacity; if the cable wasn't bumped, treat X as the most it can hold.
- [ ] No "Real usable capacity" line for a FAIL.
- [ ] `tests/test_verify.py::test_write_error_marks_rest_unwritable` is updated to expect FAIL, and a test covers "disconnect after good data, then wrong data before it" → FAKE.
- [ ] `py -3.12 -m pytest -q` and `node --test web/test/*.test.mjs` both pass.
- [ ] Web version: no behaviour change. Check its report wording matches the desktop app's.

## Comments
