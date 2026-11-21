// Plain-language results, shared by the page and the downloadable report.
import { KIND_TEXT, badTotal, realCapacityEstimate, verdict, wrapPeriod } from "./engine.js";

export const gib = (n) => (Math.abs(n) < 2 ** 30
  ? `${Math.round(n / 2 ** 20).toLocaleString()} MiB`
  : `${(n / 2 ** 30).toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 })} GiB`);
const gb = (n) => `${(n / 1e9).toLocaleString(undefined, { maximumFractionDigits: 1 })} GB`;
const mbps = (bytes, s) => (s > 0 ? ` (${(bytes / 2 ** 20 / s).toFixed(1)} MB/s)` : "");

export const HEADLINE = {
  PASS: "PASS: every byte written came back intact.",
  PASS_PARTIAL: "PASS (partial): no errors in the part tested, but the test didn't fill the drive, so this doesn't prove the full capacity.",
  FAKE: "FAKE CAPACITY: the drive claims more space than it can actually store.",
  FAIL: "FAIL: data was lost, or the drive dropped out. Don't trust it.",
  INCOMPLETE: "INCOMPLETE: the test didn't finish, so there's no verdict.",
};

export function summary(res) {
  const v = verdict(res);
  const lines = [];
  lines.push(`Written    : ${gib(res.written)} in ${(res.writeSeconds / 60).toFixed(1)} min${mbps(res.written, res.writeSeconds)}`);
  lines.push(`Read OK    : ${gib(res.good)}${mbps(res.good + badTotal(res), res.verifySeconds)}`);
  if (Object.keys(res.bad).length) {
    lines.push(`Bad        : ${gib(badTotal(res))}, first problem ${gib(res.firstBad)} into the test data`);
    for (const [k, n] of Object.entries(res.bad).sort((a, b) => b[1] - a[1])) {
      lines.push(`  - ${gib(n)} ${KIND_TEXT[k] || k}`);
    }
  }
  const est = realCapacityEstimate(res);
  if (est !== null) {
    lines.push("", `Real capacity: about ${gib(est)} (${gb(est)}) of test data survived,`,
      "plus whatever was already on the drive before the test.");
  }
  const period = wrapPeriod(res);
  if (period) lines.push(`Wrap-around every ${gib(period)}: writing past that overwrites the start of the drive.`);

  const notes = [];
  if (res.maybeCached) notes.push(`Reads ran at ${(res.readRate / 2 ** 20).toFixed(0)} MB/s, faster than USB flash can go. They probably came from memory, not the drive. Did you eject and replug? If not, run the test again and do that step.`);
  if (res.endedFull && !res.limited) notes.push("Writing stopped when the drive reported it was full (normal).");
  if (res.writeDisconnected) notes.push(`The drive disconnected while writing, after ${gib(res.written)}. Counterfeit controllers often hang when they reach their real capacity. If you didn't bump the cable, treat ${gib(res.written)} as the most it can hold.`);
  if (res.writeError && !res.endedFull) notes.push(`Write error: ${res.writeError}`);
  for (const e of res.readErrors.slice(0, 5)) notes.push(`Read error: ${e}`);
  if (res.cancelled) notes.push("You stopped writing early.");
  if (res.verifyCancelled) notes.push("You stopped the read-back early.");
  if (notes.length) lines.push("", "Notes:", ...notes.map((n) => `  - ${n}`));
  lines.push("", "What to do:", ...advice(res, v).map((a) => `  - ${a}`));
  return lines.join("\n");
}

export function advice(res, v = verdict(res)) {
  if (v === "PASS") return ["The capacity is real. Still keep a second copy of anything important."];
  if (v === "PASS_PARTIAL") return ["Run it again without a limit (let it fill the drive) to check all of it."];
  if (v === "FAKE") {
    const out = ["Don't trust it with anything you can't lose. Files saved past the real capacity are gone."];
    const est = realCapacityEstimate(res);
    if (est) out.push(`To use it anyway: back up, then shrink its partition to about ${Math.floor((est * 0.95) / 2 ** 20).toLocaleString()} MiB (Disk Management) and leave the rest unallocated.`);
    out.push("Ask the seller for a refund. Download this report as evidence.");
    return out;
  }
  if (v === "FAIL") return ["Copy off anything you need now, then stop using this drive.",
    "If a cable or hub might be loose, plug it straight into the PC and test again."];
  return ["Run the test again and let it finish."];
}

export function fullReport(res, label = "") {
  const v = verdict(res);
  return [
    `Fake Drive Checker (web) report`,
    `Test started: ${res.startedAt}`,
    label ? `Drive: ${label}` : null,
    `Browser: ${typeof navigator !== "undefined" ? navigator.userAgent : "n/a"}`,
    "",
    HEADLINE[v],
    "",
    summary(res),
    "",
  ].filter((x) => x !== null).join("\n");
}
