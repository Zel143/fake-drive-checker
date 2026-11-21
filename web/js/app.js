// Page wiring: pick -> write -> eject/replug -> verify -> result.
import { cleanUp, verdict, verifyPhase, writePhase } from "./engine.js";
import { HEADLINE, fullReport, gib, summary } from "./report.js";
import { DEMOS, SimDrive } from "./simdrive.js";

const $ = (id) => document.getElementById(id);
const STEPS = ["step-pick", "step-write", "step-replug", "step-verify", "step-result"];
const SAVED = "fakecheck-pending-run";

let root = null;        // directory handle picked in step 1
let run = null;         // what was written (also saved, so a reload can resume)
let result = null;
let abort = null;
let busy = false;
let wakeLock = null;

function show(step) {
  for (const s of STEPS) $(s).hidden = s !== step;
  $("demo").hidden = !(step === "step-pick" || step === "step-result"); // no demos mid-test
  window.scrollTo({ top: 0, behavior: "smooth" });
}

async function keepAwake(on) {
  try {
    if (on && "wakeLock" in navigator) wakeLock = await navigator.wakeLock.request("screen");
    else { await wakeLock?.release(); wakeLock = null; }
  } catch { /* not critical */ }
}

window.addEventListener("beforeunload", (e) => { if (busy) { e.preventDefault(); e.returnValue = ""; } });

async function pickDrive() {
  return window.showDirectoryPicker({ id: "fakecheck", mode: "readwrite" });
}

function looksLikeSystemDrive(name) {
  return /^c:\\?$/i.test(name);
}

function rateLine({ done, total, rate }) {
  const mb = (rate / 2 ** 20).toFixed(1);
  if (!total) return `${gib(done)} written, ${mb} MB/s`;
  const mins = rate ? (total - done) / rate / 60 : 0;
  return `${gib(done)} of ${gib(total)}, ${mb} MB/s, about ${Math.ceil(mins)} min left`;
}

// ---------- step 1 ----------
function initPick() {
  if (!("showDirectoryPicker" in window)) {
    $("unsupported").hidden = false;
    $("pick").disabled = true;
    const why = $("unsupported-why");
    if (navigator.brave) {
      why.innerHTML = "You're using <b>Brave</b>, which turns this feature off. Open this page in Edge or Chrome, " +
        "or go to <code>brave://flags/#file-system-access-api</code>, set it to <b>Enabled</b> and relaunch Brave.";
    } else if (!window.isSecureContext) {
      why.innerHTML = `This page was opened over plain <code>http://</code> from another address (${location.host}). ` +
        "Browsers only allow drive access on <code>https://</code> or on <code>http://127.0.0.1</code> / <code>localhost</code>.";
    } else {
      why.textContent = "This browser doesn't have the File System Access API. Use Edge or Chrome on a computer.";
    }
  }
  const saved = localStorage.getItem(SAVED);
  if (saved) {
    try {
      run = JSON.parse(saved);
      $("resume-info").textContent = `${gib(run.written)} written on ${new Date(run.startedAt).toLocaleString()}` +
        (run.label ? ` to "${run.label}"` : "") + ".";
      $("resume").hidden = false;
    } catch { localStorage.removeItem(SAVED); }
  }
}

$("resume-go").onclick = () => { $("write-summary").textContent = writeSummary(run); show("step-replug"); };
$("resume-drop").onclick = () => { localStorage.removeItem(SAVED); run = null; $("resume").hidden = true; };

$("pick").onclick = async () => {
  try {
    root = await pickDrive();
  } catch { return; } // user closed the picker
  if (looksLikeSystemDrive(root.name)) {
    $("picked").textContent = "That's your system drive (C:). Pick the USB drive or SD card instead.";
    $("start").disabled = true;
    root = null;
    return;
  }
  const isRoot = /^[a-z]:\\?$/i.test(root.name);
  $("picked").textContent = `Selected: ${root.name}` +
    (isRoot ? "" : " (a folder, not the drive itself; that's fine, the test still fills the whole drive)");
  $("start").disabled = false;
};

$("start").onclick = async () => {
  const gb = parseFloat($("limit").value);
  const limitBytes = gb > 0 ? Math.floor(gb * 1e9) : null;
  const what = limitBytes ? `about ${gb} GB` : "test files until the drive is full";
  if (!confirm(`Write ${what} to "${root.name}"?\n\nBack up anything on this drive first: on a fake drive, ` +
    "the test can overwrite files already on it.")) return;
  show("step-write");
  const bar = $("write-bar");
  if (limitBytes) { bar.max = 1; bar.value = 0; } else bar.removeAttribute("value");
  abort = new AbortController();
  busy = true;
  keepAwake(true);
  try {
    run = await writePhase(root, {
      limitBytes, signal: abort.signal,
      onProgress: (p) => { $("write-status").textContent = rateLine(p); if (p.total) bar.value = p.done / p.total; },
    });
  } catch (e) {
    busy = false;
    keepAwake(false);
    alert(`Couldn't write to the drive: ${e.message}`);
    show("step-pick");
    return;
  }
  busy = false;
  keepAwake(false);
  run.label = root.name;
  if (!run.written) {
    alert(`Nothing could be written. ${run.writeError || ""}`);
    show("step-pick");
    return;
  }
  localStorage.setItem(SAVED, JSON.stringify(run));
  $("write-summary").textContent = writeSummary(run);
  show("step-replug");
};

$("write-stop").onclick = () => { abort?.abort(); $("write-status").textContent += " (stopping…)"; };

function writeSummary(r) {
  let s = `Wrote ${gib(r.written)}`;
  if (r.writeSeconds) s += ` in ${Math.round(r.writeSeconds / 60)} min (${(r.written / 2 ** 20 / r.writeSeconds).toFixed(1)} MB/s)`;
  if (r.writeDisconnected) s += ". The drive disconnected while writing, which fakes often do at their real capacity";
  else if (r.endedFull && !r.limited) s += ", until the drive said it was full";
  return s + ".";
}

// ---------- steps 3-4 ----------
$("repick").onclick = async () => {
  $("repick-error").textContent = "";
  let picked;
  try { picked = await pickDrive(); } catch { return; }
  root = picked;
  show("step-verify");
  abort = new AbortController();
  busy = true;
  keepAwake(true);
  try {
    result = await verifyPhase(root, run, {
      signal: abort.signal,
      onProgress: (p) => { $("verify-status").textContent = rateLine(p); $("verify-bar").value = p.total ? p.done / p.total : 1; },
    });
  } catch (e) {
    busy = false;
    keepAwake(false);
    show("step-replug");
    $("repick-error").textContent = e.message;
    return;
  } finally {
    busy = false;
    keepAwake(false);
  }
  if (!result.verifyCancelled) localStorage.removeItem(SAVED); // a stopped read-back can be resumed
  showResult();
};

$("verify-stop").onclick = () => abort?.abort();

// ---------- step 5 ----------
function showResult() {
  const v = verdict(result);
  $("verdict").textContent = HEADLINE[v];
  $("verdict").className = v;
  $("summary").textContent = summary(result);
  $("cleanup-status").textContent = "";
  show("step-result");
}

$("download").onclick = () => {
  const blob = new Blob([fullReport(result, run.label)], { type: "text/plain" });
  const a = Object.assign(document.createElement("a"), {
    href: URL.createObjectURL(blob),
    download: `fakecheck-${verdict(result)}-${new Date().toISOString().slice(0, 10)}.txt`,
  });
  a.click();
  URL.revokeObjectURL(a.href);
};

$("cleanup").onclick = async () => {
  try {
    await cleanUp(root);
    $("cleanup-status").textContent = "Test files deleted.";
  } catch (e) {
    $("cleanup-status").textContent = `Couldn't delete them (${e.message}). Delete the "fakecheck-test" folder by hand.`;
  }
};

$("again").onclick = () => { root = run = result = null; $("picked").textContent = ""; $("start").disabled = true; $("resume").hidden = true; show("step-pick"); };

// ---------- demo: the same engine against a simulated drive ----------
async function runDemo(key) {
  const spec = DEMOS[key];
  const drive = new SimDrive(spec);
  show("step-write");
  $("write-bar").max = 1;
  $("write-bar").value = 0;
  const progress = (bar, status) => (p) => {
    $(status).textContent = `${rateLine(p)} (simulated)`;
    $(bar).value = p.total ? p.done / p.total : Math.min(1, p.done / spec.claimed);
  };
  const pause = (ms) => new Promise((r) => setTimeout(r, ms));
  abort = new AbortController();
  run = await writePhase(drive.root, { signal: abort.signal, onProgress: progress("write-bar", "write-status") });
  run.label = `DEMO: ${spec.label}`;
  $("write-status").textContent = `${writeSummary(run)} In a real test you'd now eject and replug the drive.`;
  await pause(1500);
  drive.replug();
  root = drive.root;
  show("step-verify");
  result = await verifyPhase(drive.root, run, { onProgress: progress("verify-bar", "verify-status") });
  result.maybeCached = false; // a simulated drive lives in memory by design
  showResult();
  $("verdict").insertAdjacentHTML("beforeend", '<span class="demo-tag">DEMO</span>');
}

for (const [key, spec] of Object.entries(DEMOS)) {
  const b = Object.assign(document.createElement("button"), { textContent: spec.label });
  b.onclick = () => runDemo(key);
  $("demo-buttons").append(b);
}

initPick();
