// The demo drives must produce the verdicts they advertise.
import assert from "node:assert/strict";
import { test } from "node:test";

import { realCapacityEstimate, verdict, verifyPhase, wrapPeriod, writePhase } from "../js/engine.js";
import { DEMOS, SimDrive } from "../js/simdrive.js";

async function runDemo(key) {
  const drive = new SimDrive(DEMOS[key]);
  const run = await writePhase(drive.root);
  drive.replug();
  return { drive, run, res: await verifyPhase(drive.root, run) };
}

const MiB = 2 ** 20;

test("genuine demo passes", async () => {
  const { res } = await runDemo("genuine");
  assert.equal(verdict(res), "PASS");
  assert.ok(res.endedFull);
});

test("zeros demo is FAKE at 128 MiB", async () => {
  const { res } = await runDemo("zeros");
  assert.equal(verdict(res), "FAKE");
  assert.equal(res.firstBad, 128 * MiB);
  assert.deepEqual(Object.keys(res.bad), ["zeros"]);
  assert.equal(realCapacityEstimate(res), 128 * MiB);
});

test("wrap demo is FAKE with a 128 MiB period", async () => {
  const { res } = await runDemo("wrap");
  assert.equal(verdict(res), "FAKE");
  assert.equal(wrapPeriod(res), 128 * MiB);
  assert.equal(realCapacityEstimate(res), 128 * MiB);
});

test("drop demo reports the disconnect", async () => {
  const { run, res } = await runDemo("drop");
  assert.ok(run.writeDisconnected);
  assert.equal(run.written, 128 * MiB);
  assert.equal(verdict(res), "FAIL");
});
