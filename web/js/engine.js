// The write / replug / verify test, using the browser's File System Access API.
//
// A web page can't bypass the OS file cache, so reading right after writing would mostly
// come from RAM and a fake could pass. So the test is split in two: write, then the user
// ejects and replugs the drive (that empties the cache), then we read everything back.
// Only directory-handle methods are used, so tests can pass in a fake in-memory "drive".

import { CHUNK, SECTOR, SECTORS_PER_CHUNK, classify, makeChunk } from "./pattern.js";

export const TEST_DIR = "fakecheck-test";
export const CHUNKS_PER_FILE = 16; // 64 MiB files: a failed last file loses little
const CACHE_SUSPECT_RATE = 800 * 2 ** 20; // USB flash can't really read this fast

export const KIND_TEXT = {
  zeros: "read back as all zeros: nothing was stored there",
  erased: "read back blank (0xFF): the writes were silently thrown away",
  aliased: "held data written somewhere else: the drive wraps around and overwrites itself",
  corrupt: "came back with changed bytes",
  missing: "came back short, or the file was gone",
  unreadable: "could not be read at all (read error)",
};

export function newRun(runId = (Math.random() * 2 ** 32) >>> 0) {
  return {
    runId, files: [], written: 0, limited: false, writeError: null, endedFull: false,
    writeDisconnected: false, cancelled: false, writeSeconds: 0, startedAt: new Date().toISOString(),
  };
}

const fileName = (n) => `FDC_${String(n).padStart(5, "0")}.dat`;

function progressReporter(fn, phase, total) {
  const t0 = performance.now();
  let last = 0;
  return (done, force = false) => {
    const now = performance.now();
    if (fn && (force || now - last > 250)) {
      last = now;
      fn({ phase, done, total, rate: done / Math.max((now - t0) / 1000, 1e-6) });
    }
  };
}

/** Phase 1: fill the drive (or `limitBytes`) with stamped test files. */
export async function writePhase(root, { limitBytes = null, chunksPerFile = CHUNKS_PER_FILE,
  onProgress = null, signal = null, run = newRun() } = {}) {
  const dir = await root.getDirectoryHandle(TEST_DIR, { create: true });
  run.limited = !!limitBytes;
  const maxChunks = limitBytes ? Math.floor(limitBytes / CHUNK) : Infinity;
  const rep = progressReporter(onProgress, "write", limitBytes ? maxChunks * CHUNK : null);
  const t0 = performance.now();
  let g = 0;
  while (g < maxChunks) {
    if (signal?.aborted) { run.cancelled = true; break; }
    const n = Math.min(chunksPerFile, maxChunks - g);
    const name = fileName(run.files.length);
    let w = null, done = 0;
    try {
      w = await (await dir.getFileHandle(name, { create: true })).createWritable();
      for (; done < n; done++) {
        if (signal?.aborted) { run.cancelled = true; break; }
        await w.write(makeChunk(run.runId, g + done));
        rep((g + done + 1) * CHUNK);
      }
      await w.close();
    } catch (e) {
      // Out of space (normal end of a full test) or the drive vanished. Drop the partial file.
      try { await w?.abort(); } catch { /* already gone */ }
      try { await dir.removeEntry(name); } catch { /* already gone */ }
      run.writeError = `${e.name}: ${e.message}`;
      run.writeDisconnected = !(await stillThere(dir));
      run.endedFull = !run.writeDisconnected;
      break;
    }
    if (done) run.files.push({ name, firstChunk: g, nChunks: done });
    g += done;
    run.written = g * CHUNK;
    if (run.cancelled) break;
  }
  run.writeSeconds = (performance.now() - t0) / 1000;
  rep(run.written, true);
  return run;
}

async function stillThere(dir) {
  try {
    for await (const _ of dir.keys()) break; // any read of the folder
    return true;
  } catch {
    return false;
  }
}

function addBad(res, kind, offset, n, otherOffset = null) {
  res.bad[kind] = (res.bad[kind] || 0) + n;
  if (res.firstBad === null || offset < res.firstBad) res.firstBad = offset;
  res.testedEnd = Math.max(res.testedEnd, offset + n);
  if (otherOffset !== null) {
    // Wrapping more than once gives gaps of 1x, 2x, 3x the real size; the smallest is the size.
    const gap = Math.abs(otherOffset - offset);
    res.minAliasGap = res.minAliasGap === null ? gap : Math.min(res.minAliasGap, gap);
    if (res.aliasPairs.length < 1000) res.aliasPairs.push([offset, otherOffset]);
  }
}

function sameBytes(a, b) {
  if (a.length !== b.length) return false;
  const x = new Uint32Array(a.buffer, a.byteOffset, a.length >> 2);
  const y = new Uint32Array(b.buffer, b.byteOffset, b.length >> 2);
  for (let i = 0; i < x.length; i++) if (x[i] !== y[i]) return false;
  return true;
}

function checkChunk(res, data, g) {
  const expected = makeChunk(res.runId, g);
  const base = g * CHUNK;
  res.testedEnd = Math.max(res.testedEnd, base + CHUNK);
  if (data.length === CHUNK && sameBytes(data, expected)) { res.good += CHUNK; return; }
  for (let s = 0; s < SECTORS_PER_CHUNK; s++) {
    const off = s * SECTOR;
    const got = data.subarray(off, off + SECTOR);
    if (got.length === SECTOR && sameBytes(got, expected.subarray(off, off + SECTOR))) {
      res.good += SECTOR;
      continue;
    }
    const [kind, other] = classify(got, res.runId, g * SECTORS_PER_CHUNK + s);
    addBad(res, kind, base + off, SECTOR, other === null ? null : other * SECTOR);
  }
}

/** Phase 2 (after replug): read every test file back and account for every sector. */
export async function verifyPhase(root, run, { onProgress = null, signal = null } = {}) {
  let dir;
  try {
    dir = await root.getDirectoryHandle(TEST_DIR);
  } catch {
    throw new Error(`There's no "${TEST_DIR}" folder here. Pick the same drive you tested.`);
  }
  const res = { ...run, good: 0, bad: {}, firstBad: null, testedEnd: 0, aliasPairs: [], minAliasGap: null,
    readErrors: [], verifyCancelled: false, verifySeconds: 0, readRate: 0, maybeCached: false };
  const total = run.files.reduce((s, f) => s + f.nChunks * CHUNK, 0);
  const rep = progressReporter(onProgress, "verify", total);
  const t0 = performance.now();
  let done = 0;
  outer: for (const f of run.files) {
    let file;
    try {
      file = await (await dir.getFileHandle(f.name)).getFile();
    } catch (e) {
      res.readErrors.push(`${f.name}: ${e.name}`);
      addBad(res, e.name === "NotFoundError" ? "missing" : "unreadable", f.firstChunk * CHUNK, f.nChunks * CHUNK);
      done += f.nChunks * CHUNK;
      continue;
    }
    for (let c = 0; c < f.nChunks; c++) {
      if (signal?.aborted) { res.verifyCancelled = true; break outer; }
      let data;
      try {
        data = new Uint8Array(await file.slice(c * CHUNK, (c + 1) * CHUNK).arrayBuffer());
      } catch (e) {
        res.readErrors.push(`${f.name}: ${e.name}`);
        addBad(res, "unreadable", (f.firstChunk + c) * CHUNK, (f.nChunks - c) * CHUNK);
        done += (f.nChunks - c) * CHUNK;
        break;
      }
      checkChunk(res, data, f.firstChunk + c);
      done += CHUNK;
      rep(done);
    }
  }
  rep(done, true);
  res.verifySeconds = (performance.now() - t0) / 1000;
  res.readRate = done / Math.max(res.verifySeconds, 1e-6);
  res.maybeCached = done >= 512 * 2 ** 20 && res.readRate > CACHE_SUSPECT_RATE;
  return res;
}

export const badTotal = (res) => Object.values(res.bad).reduce((a, b) => a + b, 0);

export function wrapPeriod(res) {
  return res.minAliasGap ?? null;
}

/** PASS, PASS_PARTIAL, FAKE, FAIL or INCOMPLETE. Same rules as the desktop app. */
export function verdict(res) {
  if (res.aliasPairs.length) return "FAKE";
  if (res.firstBad === null) {
    if (res.verifyCancelled || res.written === 0 || res.testedEnd < res.written) return "INCOMPLETE";
    if (res.writeDisconnected) return "FAIL";
    return res.limited || res.cancelled ? "PASS_PARTIAL" : "PASS";
  }
  // Fake capacity: good data up to a point, then (almost) nothing survives.
  const span = Math.max(1, res.testedEnd - res.firstBad);
  return badTotal(res) / span >= 0.9 && res.firstBad > 0 ? "FAKE" : "FAIL";
}

/** Bytes of test data the drive really held, when it looks fake. */
export function realCapacityEstimate(res) {
  if (verdict(res) !== "FAKE") return null;
  return wrapPeriod(res) ?? res.firstBad;
}

/** Delete the test folder. */
export async function cleanUp(root) {
  await root.removeEntry(TEST_DIR, { recursive: true });
}
