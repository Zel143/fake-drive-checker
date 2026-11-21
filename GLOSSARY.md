# Fake Drive Checker

Tests whether a USB drive, SD card or external SSD can really store as much data as it claims, and explains how it fails when it can't.

## Capacity

**Claimed capacity**:
The size a drive reports to the computer, which is what the label and the operating system show.
_Avoid_: advertised size, total size, declared size

**Real capacity**:
How much data the drive can actually store and give back intact.
_Avoid_: true size, validated size, usable size

**Fake zone**:
Everything between the real capacity and the claimed capacity: space the drive accepts writes for but cannot keep.
_Avoid_: bad area, dead zone

**Wrap-around**:
A fake behaviour where writes past the real capacity land on the start of the drive and overwrite what was stored there.
_Avoid_: aliasing, looping, mirroring

## The test

**Test files**:
The files of stamped, checkable data the test writes and then reads back, kept in their own folder on the drive.
_Avoid_: dummy files, filler

**Spot check**:
A re-read of a few test files while writing is still going on, so a fake is caught before the whole drive is filled.
_Avoid_: probe, sample read

**Replug step**:
In the web version, the point between writing and reading back where the person ejects the drive and plugs it in again, so the read-back comes from the drive and not from memory.
_Avoid_: remount, reconnect step

**Demo drive**:
A simulated drive that behaves like a genuine drive or a particular kind of fake, used to show the test without real hardware.
_Avoid_: mock drive, fake drive (a fake drive is a real counterfeit)

## Verdicts

**Verdict**:
The test's one-word conclusion about a drive: PASS, PASS (partial), FAKE, FAIL or INCOMPLETE.
_Avoid_: result, status, score

**PASS**:
Every byte of test data written to the whole drive came back intact.

**PASS (partial)**:
Every byte tested came back intact, but the test stopped before filling the drive, so the claimed capacity is not proven.

**FAKE**:
Test data that was stored came back wrong (blank or wrapped around) from some point on, showing the real capacity is below the claimed capacity. A disconnect alone never makes a drive FAKE.
_Avoid_: counterfeit (fine in prose, but not as a verdict name)

**FAIL**:
Data was lost in a way that doesn't fit fake capacity (scattered errors), or the drive disconnected during the test. A disconnect is FAIL because a loose cable and a counterfeit controller look the same.

**INCOMPLETE**:
The test was stopped or interrupted before there was enough evidence for any other verdict.

## Versions

**Web version**:
The browser page that anyone can open, with demo drives and the replug step.
_Avoid_: website, online version

**Desktop app**:
The Windows program that also reads the drive's hardware identity and needs no replug step.
_Avoid_: CLI, Python version, native app
