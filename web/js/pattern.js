// Deterministic test data. Same idea as fakecheck/pattern.py (the bytes differ: JS uses
// its own PRNG). Every 4 KiB sector starts with a 16-byte stamp: magic, run id, sector
// index. When a sector reads back wrong, the stamp says how: blank, or another sector's data.

export const SECTOR = 4096;
export const CHUNK = 4 << 20; // 4 MiB
export const SECTORS_PER_CHUNK = CHUNK / SECTOR;
const WORDS_PER_SECTOR = SECTOR / 4;
const MAGIC = 0x31434446; // "FDC1" as little-endian bytes

function mix(a, b, c) {
  let h = (a ^ Math.imul(b + 1, 0x9e3779b1) ^ Math.imul(c + 1, 0x85ebca77)) >>> 0;
  h ^= h >>> 16; h = Math.imul(h, 0x7feb352d);
  h ^= h >>> 15; h = Math.imul(h, 0x846ca68b);
  h ^= h >>> 16;
  return h >>> 0 || 1;
}

/** The exact bytes chunk `index` of run `runId` must contain. */
export function makeChunk(runId, index) {
  const w = new Uint32Array(CHUNK / 4);
  // xorshift128: fast, and plenty random for "did these bytes survive?"
  let x = mix(runId, index, 0), y = mix(runId, index, 1), z = mix(runId, index, 2), v = mix(runId, index, 3);
  for (let i = 0; i < w.length; i++) {
    const t = x ^ (x << 11);
    x = y; y = z; z = v;
    v = (v ^ (v >>> 19) ^ t ^ (t >>> 8)) >>> 0;
    w[i] = v;
  }
  const first = index * SECTORS_PER_CHUNK;
  for (let s = 0; s < SECTORS_PER_CHUNK; s++) {
    const o = s * WORDS_PER_SECTOR, g = first + s;
    w[o] = MAGIC;
    w[o + 1] = runId >>> 0;
    w[o + 2] = g >>> 0;
    w[o + 3] = Math.floor(g / 2 ** 32);
  }
  return new Uint8Array(w.buffer);
}

/** Explain a sector that did NOT match. Returns [kind, otherSectorIndex|null]. */
export function classify(sector, runId, expectedIndex) {
  if (sector.length < SECTOR) return ["missing", null];
  const w = new Uint32Array(sector.buffer, sector.byteOffset, WORDS_PER_SECTOR);
  if (w.every((x) => x === 0)) return ["zeros", null];
  if (w.every((x) => x === 0xffffffff)) return ["erased", null];
  if (w[0] === MAGIC && w[1] === runId >>> 0) {
    const idx = w[2] + w[3] * 2 ** 32;
    if (idx !== expectedIndex) return ["aliased", idx];
  }
  return ["corrupt", null];
}
