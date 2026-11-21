// Run: node --test web/test
// A fake in-memory "drive" that speaks the File System Access API subset the engine uses.
import assert from "node:assert/strict";
import { test } from "node:test";

import { CHUNK, SECTOR, classify, makeChunk } from "../js/pattern.js";
import { TEST_DIR, cleanUp, realCapacityEstimate, verdict, verifyPhase, wrapPeriod, writePhase } from "../js/engine.js";
import { summary } from "../js/report.js";

const err = (name, message = name) => Object.assign(new Error(message), { name });

class Drive {
  constructor(capacity = Infinity) { this.capacity = capacity; this.used = 0; this.gone = false; this.failAfter = Infinity; }
  check() { if (this.gone) throw err("NotFoundError", "drive gone"); }
}

class FileH {
  constructor(drive) { this.drive = drive; this.data = new Uint8Array(0); this.kind = "file"; }
  async createWritable() {
    const d = this.drive, parts = [];
    let size = 0;
    return {
      write: async (u8) => {
        d.check();
        if (d.used + u8.length > d.failAfter) { d.gone = true; throw err("NotReadableError", "device not connected"); }
        if (d.used + u8.length > d.capacity) throw err("QuotaExceededError", "disk full");
        parts.push(u8.slice()); size += u8.length; d.used += u8.length;
      },
      close: async () => {
        d.check();
        const out = new Uint8Array(size);
        let o = 0;
        for (const p of parts) { out.set(p, o); o += p.length; }
        this.data = out;
      },
      abort: async () => { d.used -= size; },
    };
  }
  async getFile() { this.drive.check(); return new Blob([this.data]); }
}

class DirH {
  constructor(drive) { this.drive = drive; this.items = new Map(); this.kind = "directory"; }
  async getDirectoryHandle(name, { create = false } = {}) {
    this.drive.check();
    if (!this.items.has(name)) { if (!create) throw err("NotFoundError"); this.items.set(name, new DirH(this.drive)); }
    return this.items.get(name);
  }
  async getFileHandle(name, { create = false } = {}) {
    this.drive.check();
    if (!this.items.has(name)) { if (!create) throw err("NotFoundError"); this.items.set(name, new FileH(this.drive)); }
    return this.items.get(name);
  }
  async removeEntry(name) { this.drive.check(); if (!this.items.delete(name)) throw err("NotFoundError"); }
  async *keys() { this.drive.check(); yield* this.items.keys(); }
}

const newDrive = (capacity) => { const d = new Drive(capacity); return [d, new DirH(d)]; };
const testFiles = (root) => [...root.items.get(TEST_DIR).items.values()];

test("chunks are deterministic and distinct", () => {
  assert.deepEqual(makeChunk(1, 0), makeChunk(1, 0));
  assert.notDeepEqual(makeChunk(1, 0), makeChunk(1, 1));
  assert.notDeepEqual(makeChunk(1, 0), makeChunk(2, 0));
});

test("classify tells failures apart", () => {
  const chunk = makeChunk(7, 3);
  const idx = 3 * (CHUNK / SECTOR) + 5;
  const s5 = chunk.slice(5 * SECTOR, 6 * SECTOR);
  assert.deepEqual(classify(new Uint8Array(SECTOR), 7, 0), ["zeros", null]);
  assert.deepEqual(classify(new Uint8Array(SECTOR).fill(255), 7, 0), ["erased", null]);
  assert.deepEqual(classify(s5, 7, 0), ["aliased", idx]);
  const flipped = s5.slice(); flipped[100] ^= 1;
  assert.deepEqual(classify(flipped, 7, idx), ["corrupt", null]);
  assert.deepEqual(classify(new Uint8Array(10), 7, 0), ["missing", null]);
});

test("genuine drive: fills until full, then passes", async () => {
  const [, root] = newDrive(10 * CHUNK);
  const run = await writePhase(root, { chunksPerFile: 3 });
  assert.equal(run.written, 9 * CHUNK); // the 4th file hit "full" and was dropped
  assert.ok(run.endedFull);
  const res = await verifyPhase(root, run);
  assert.equal(verdict(res), "PASS");
  await cleanUp(root);
  assert.equal(root.items.size, 0);
});

test("a limit gives a partial pass", async () => {
  const [, root] = newDrive();
  const run = await writePhase(root, { limitBytes: 3 * CHUNK });
  assert.equal(verdict(await verifyPhase(root, run)), "PASS_PARTIAL");
});

test("fake zone reading zeros is FAKE, with a capacity estimate", async () => {
  const [, root] = newDrive(8 * CHUNK);
  const run = await writePhase(root, { chunksPerFile: 2 });
  for (const f of testFiles(root).slice(2)) f.data = new Uint8Array(f.data.length);
  const res = await verifyPhase(root, run);
  assert.equal(verdict(res), "FAKE");
  assert.equal(res.firstBad, 4 * CHUNK);
  assert.deepEqual(res.bad, { zeros: 4 * CHUNK });
  assert.equal(realCapacityEstimate(res), 4 * CHUNK);
  assert.match(summary(res), /Real capacity: about/);
});

test("discarded writes (0xFF) are FAKE", async () => {
  const [, root] = newDrive(8 * CHUNK);
  const run = await writePhase(root, { chunksPerFile: 2 });
  testFiles(root).at(-1).data.fill(255);
  assert.equal(verdict(await verifyPhase(root, run)), "FAKE");
});

test("wrap-around is FAKE and measures the period", async () => {
  const [, root] = newDrive(8 * CHUNK);
  const run = await writePhase(root, { chunksPerFile: 2 });
  const f = testFiles(root);
  f[0].data = f[2].data.slice();
  f[1].data = f[3].data.slice();
  const res = await verifyPhase(root, run);
  assert.equal(verdict(res), "FAKE");
  assert.equal(wrapPeriod(res), 4 * CHUNK);
});

test("scattered bit errors are FAIL, not FAKE", async () => {
  const [, root] = newDrive(8 * CHUNK);
  const run = await writePhase(root, { chunksPerFile: 2 });
  for (const f of [testFiles(root)[0], testFiles(root).at(-1)]) f.data[SECTOR + 50] ^= 255;
  const res = await verifyPhase(root, run);
  assert.equal(verdict(res), "FAIL");
  assert.deepEqual(res.bad, { corrupt: 2 * SECTOR });
});

test("drive dropping out while writing: verify after replug, verdict FAIL", async () => {
  const [drive, root] = newDrive();
  drive.failAfter = 5 * CHUNK;
  const run = await writePhase(root, { chunksPerFile: 2 });
  assert.ok(run.writeDisconnected);
  assert.equal(run.written, 4 * CHUNK); // the half-written file is dropped
  drive.gone = false; // replugged
  const res = await verifyPhase(root, run);
  assert.equal(verdict(res), "FAIL");
  assert.match(summary(res), /disconnected while writing/);
});

test("a vanished test file counts as missing", async () => {
  const [, root] = newDrive(6 * CHUNK);
  const run = await writePhase(root, { chunksPerFile: 2 });
  root.items.get(TEST_DIR).items.delete(run.files[2].name);
  const res = await verifyPhase(root, run);
  assert.deepEqual(res.bad, { missing: 2 * CHUNK });
  assert.equal(verdict(res), "FAKE");
});

test("picking the wrong folder for verify is a clear error", async () => {
  const [, root] = newDrive(4 * CHUNK);
  const run = await writePhase(root);
  const [, other] = newDrive();
  await assert.rejects(verifyPhase(other, run), /Pick the same drive/);
});

test("stopping the write early is a partial pass", async () => {
  const [, root] = newDrive();
  const ac = new AbortController();
  let n = 0;
  const run = await writePhase(root, { chunksPerFile: 2, signal: ac.signal,
    onProgress: () => { if (++n === 1) ac.abort(); } });
  assert.ok(run.cancelled);
  assert.equal(verdict(await verifyPhase(root, run)), "PASS_PARTIAL");
});
