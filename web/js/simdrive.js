// Simulated drives for the demo (and tests). They speak the same File System Access API
// subset the engine uses, and misbehave the way real counterfeits do:
//   "genuine": stores everything up to its claimed size
//   "zeros":   claims more than it has; past the real size nothing is stored (reads 0x00)
//   "wrap":    past the real size, addresses wrap and overwrite the start of the flash
//   "drop":    the controller hangs and drops off USB when it reaches the real size
import { CHUNK } from "./pattern.js";

const err = (name, message) => Object.assign(new Error(message), { name });

export class SimDrive {
  constructor({ claimed, real = claimed, mode = "genuine", name = "SIM:\\" }) {
    this.claimed = claimed;
    this.real = mode === "genuine" ? claimed : real;
    this.mode = mode;
    this.flash = new Uint8Array(this.real); // the chips that actually exist
    this.cursor = 0;                          // next logical address to allocate
    this.gone = false;
    this.root = new SimDir(this, name);
  }

  check() { if (this.gone) throw err("NotFoundError", "The device is not connected."); }
  replug() { this.gone = false; }

  /** Store one chunk at the next logical address; returns that address. */
  writeChunk(data) {
    this.check();
    const at = this.cursor;
    if (at + data.length > this.claimed) throw err("QuotaExceededError", "There is not enough space on the disk.");
    if (at + data.length > this.real) {
      if (this.mode === "drop") { this.gone = true; throw err("NotReadableError", "The device is not connected."); }
      if (this.mode === "wrap") this.flash.set(data, at % this.real);
      // "zeros": silently discarded
    } else {
      this.flash.set(data, at);
    }
    this.cursor += data.length;
    return at;
  }

  readChunk(at, n) {
    this.check();
    if (at + n <= this.real) return this.flash.slice(at, at + n);
    if (this.mode === "wrap") return this.flash.slice(at % this.real, (at % this.real) + n);
    return new Uint8Array(n); // fake zone reads as zeros
  }
}

class SimFile {
  constructor(drive, name) { this.drive = drive; this.name = name; this.kind = "file"; this.chunks = []; }

  async createWritable() {
    const pending = [];
    return {
      write: async (u8) => { pending.push(this.drive.writeChunk(u8)); },
      close: async () => { this.drive.check(); this.chunks = pending; },
      abort: async () => {},
    };
  }

  async getFile() {
    this.drive.check();
    const drive = this.drive, chunks = this.chunks;
    // Only chunk-aligned slices are needed by the engine.
    return {
      size: chunks.length * CHUNK,
      slice: (a, b) => ({
        arrayBuffer: async () => {
          const i = Math.floor(a / CHUNK);
          if (i >= chunks.length) return new ArrayBuffer(0);
          return drive.readChunk(chunks[i], Math.min(b - a, CHUNK)).buffer;
        },
      }),
    };
  }
}

class SimDir {
  constructor(drive, name) { this.drive = drive; this.name = name; this.kind = "directory"; this.items = new Map(); }

  async getDirectoryHandle(name, { create = false } = {}) {
    this.drive.check();
    if (!this.items.has(name)) {
      if (!create) throw err("NotFoundError", `${name} not found`);
      this.items.set(name, new SimDir(this.drive, name));
    }
    return this.items.get(name);
  }

  async getFileHandle(name, { create = false } = {}) {
    this.drive.check();
    if (!this.items.has(name)) {
      if (!create) throw err("NotFoundError", `${name} not found`);
      this.items.set(name, new SimFile(this.drive, name));
    }
    return this.items.get(name);
  }

  async removeEntry(name) { this.drive.check(); if (!this.items.delete(name)) throw err("NotFoundError", name); }

  async *keys() { this.drive.check(); yield* this.items.keys(); }
}

const MiB = 2 ** 20;
export const DEMOS = {
  genuine: { label: "Genuine 256 MB drive", claimed: 256 * MiB, mode: "genuine" },
  zeros: { label: "Fake: claims 512 MB, really 128 MB (extra writes vanish)", claimed: 512 * MiB, real: 128 * MiB, mode: "zeros" },
  wrap: { label: "Fake: claims 512 MB, really 128 MB (wraps around and overwrites itself)", claimed: 512 * MiB, real: 128 * MiB, mode: "wrap" },
  drop: { label: "Fake: disconnects when it hits its real size (like many counterfeit SSDs)", claimed: 512 * MiB, real: 128 * MiB, mode: "drop" },
};
