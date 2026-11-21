"""Tkinter window: pick a drive, see its red flags, run the test."""
from __future__ import annotations

import os
import queue
import threading
import time
import tkinter as tk
from tkinter import messagebox, ttk

from . import drives, hints, report, verify
from .hints import gib

STALL_SECONDS = 60
VERDICT_COLORS = {"PASS": "#1a7f37", "PASS_PARTIAL": "#9a6700", "FAKE": "#cf222e",
                  "FAIL": "#cf222e", "INCOMPLETE": "#57606a"}


class App:
    def __init__(self, root: tk.Tk):
        self.root = root
        root.title("Fake Drive Checker")
        root.geometry("860x700")
        root.minsize(700, 560)
        self.q: queue.Queue = queue.Queue()
        self.disks: list[drives.Disk] = []
        self.rows: dict[str, tuple[drives.Disk, drives.Volume]] = {}
        self.cancel: threading.Event | None = None
        self.running = False
        self.last_progress = 0.0
        self._build()
        self.refresh()
        root.after(200, self._poll)

    # ---- layout ----
    def _build(self):
        pad = {"padx": 8, "pady": 4}
        top = ttk.Frame(self.root)
        top.pack(fill="x", **pad)
        ttk.Label(top, text="Drives", font=("Segoe UI", 11, "bold")).pack(side="left")
        self.refresh_btn = ttk.Button(top, text="Refresh", command=self.refresh)
        self.refresh_btn.pack(side="right")

        cols = ("drive", "name", "claimed", "bus", "flags")
        self.tree = ttk.Treeview(self.root, columns=cols, show="headings", height=6, selectmode="browse")
        for c, w, label in (("drive", 60, "Drive"), ("name", 280, "Name"), ("claimed", 120, "Claims"),
                            ("bus", 70, "Bus"), ("flags", 220, "Red flags")):
            self.tree.heading(c, text=label)
            self.tree.column(c, width=w, anchor="w")
        self.tree.tag_configure("red", foreground="#cf222e")
        self.tree.tag_configure("blocked", foreground="#8c959f")
        self.tree.pack(fill="x", **pad)
        self.tree.bind("<<TreeviewSelect>>", lambda e: self.show_selected())

        self.details = tk.Text(self.root, height=13, wrap="word", font=("Consolas", 9),
                               relief="flat", background="#f6f8fa")
        self.details.pack(fill="both", expand=False, **pad)
        self.details.configure(state="disabled")

        ctl = ttk.Frame(self.root)
        ctl.pack(fill="x", **pad)
        ttk.Label(ctl, text="Limit (GB, blank = whole drive):").pack(side="left")
        self.limit = ttk.Entry(ctl, width=8)
        self.limit.pack(side="left", padx=4)
        self.start_btn = ttk.Button(ctl, text="Start test", command=self.start, state="disabled")
        self.start_btn.pack(side="left", padx=8)
        self.stop_btn = ttk.Button(ctl, text="Stop", command=self.stop, state="disabled")
        self.stop_btn.pack(side="left")
        self.open_btn = ttk.Button(ctl, text="Open reports folder", command=self.open_reports)
        self.open_btn.pack(side="right")

        self.bar = ttk.Progressbar(self.root, maximum=1000)
        self.bar.pack(fill="x", **pad)
        self.status = ttk.Label(self.root, text="")
        self.status.pack(fill="x", **pad)

        self.verdict = tk.Label(self.root, text="", font=("Segoe UI", 13, "bold"), anchor="w")
        self.verdict.pack(fill="x", **pad)
        self.result = tk.Text(self.root, height=12, wrap="word", font=("Consolas", 9), relief="flat")
        self.result.pack(fill="both", expand=True, **pad)
        self.result.configure(state="disabled")

    @staticmethod
    def _set_text(widget: tk.Text, text: str):
        widget.configure(state="normal")
        widget.delete("1.0", "end")
        widget.insert("1.0", text)
        widget.configure(state="disabled")

    # ---- drive list ----
    def refresh(self):
        self.refresh_btn.configure(state="disabled")
        self.status.configure(text="Looking for drives...")

        def work():
            try:
                self.q.put(("disks", drives.list_disks()))
            except Exception as e:  # shown to the user, not fatal
                self.q.put(("error", f"Couldn't list drives: {e}"))
        threading.Thread(target=work, daemon=True).start()

    def _fill_tree(self):
        self.tree.delete(*self.tree.get_children())
        self.rows.clear()
        for d in self.disks:
            flagged = [h for h in hints.hints(d) if h.level in ("red", "warn")]
            flag_text = f"{len(flagged)} flag(s)" if flagged else ""
            if any(h.level == "red" for h in flagged):
                flag_text = "KNOWN FAKE ID, " + flag_text
            for v in d.volumes:
                if not v.letter:
                    continue
                blocked = hints.blockers(d, v)
                tag = "blocked" if blocked else ("red" if flagged else "")
                iid = f"{d.number}:{v.letter}"
                self.rows[iid] = (d, v)
                self.tree.insert("", "end", iid=iid, tags=(tag,), values=(
                    f"{v.letter}:", d.model, gib(d.size), d.bus,
                    "not testable" if blocked else flag_text))
        self.status.configure(text=f"{len(self.rows)} volume(s). Grey ones can't be tested "
                                   "(system or internal disks).")

    def selected(self):
        sel = self.tree.selection()
        return self.rows.get(sel[0]) if sel else None

    def show_selected(self):
        s = self.selected()
        if not s:
            return
        d, v = s
        text = report.describe_disk(d)
        blocked = hints.blockers(d, v)
        if blocked:
            text += "\n\nCan't test this one: " + "; ".join(blocked) + "."
        self._set_text(self.details, text)
        self.start_btn.configure(state="disabled" if blocked or self.running else "normal")

    # ---- running the test ----
    def start(self):
        s = self.selected()
        if not s:
            return
        d, v = s
        try:
            limit = float(self.limit.get()) * 2**30 if self.limit.get().strip() else None
        except ValueError:
            messagebox.showerror("Limit", "The limit must be a number of GB, or blank.")
            return
        size = min(v.free, limit) if limit else v.free
        ok = messagebox.askokcancel(
            "Start test?",
            f"This writes about {gib(size)} of test files to {v.letter}:\\, reads them back and deletes them.\n\n"
            "On a fake drive, writing past the real capacity can overwrite files that are ALREADY on it. "
            "Back them up first.\n\nCan take hours on slow drives. Start?", icon="warning")
        if not ok:
            return
        self.running = True
        self.cancel = threading.Event()
        for b in (self.start_btn, self.refresh_btn):
            b.configure(state="disabled")
        self.stop_btn.configure(state="normal")
        self.verdict.configure(text="Testing...", fg="#57606a")
        self._set_text(self.result, "")
        self.last_progress = time.monotonic()

        def work():
            try:
                res = verify.run_test(f"{v.letter}:\\", limit_bytes=int(limit) if limit else None,
                                      cancel=self.cancel,
                                      progress=lambda *p: self.q.put(("progress", p)))
                self.q.put(("done", (d, res)))
            except Exception as e:
                self.q.put(("error", f"Test crashed: {e}"))
        threading.Thread(target=work, daemon=True).start()

    def stop(self):
        if self.cancel:
            self.cancel.set()
            self.status.configure(text="Stopping... (waiting for the drive to finish the current block)")

    def open_reports(self):
        os.makedirs(report.REPORTS_DIR, exist_ok=True)
        os.startfile(report.REPORTS_DIR)

    # ---- worker -> UI ----
    def _poll(self):
        try:
            while True:
                kind, payload = self.q.get_nowait()
                getattr(self, f"_on_{kind}")(payload)
        except queue.Empty:
            pass
        if self.running and time.monotonic() - self.last_progress > STALL_SECONDS:
            self.status.configure(text=f"No progress for {int(time.monotonic() - self.last_progress)} s. "
                                       "The drive may have hung (fakes often do at the fake zone). "
                                       "Wait a bit, or unplug and replug it.")
        self.root.after(200, self._poll)

    def _on_disks(self, disks):
        self.disks = disks
        self.refresh_btn.configure(state="normal")
        self._fill_tree()

    def _on_progress(self, p):
        phase, done, total, rate = p
        self.last_progress = time.monotonic()
        frac = done / total if total else 1
        # writing is the first half of the bar, verifying the second
        self.bar["value"] = 500 * frac + (500 if phase == "verify" else 0)
        eta = (total - done) / rate / 60 if rate else 0
        verb = "Writing" if phase == "write" else "Reading back"
        self.status.configure(text=f"{verb}: {gib(done)} of {gib(total)}, {rate / 2**20:.1f} MB/s, "
                                   f"about {eta:.0f} min left in this phase")

    def _on_done(self, payload):
        disk, res = payload
        self.running = False
        self.stop_btn.configure(state="disabled")
        self.refresh_btn.configure(state="normal")
        self.show_selected()
        self.bar["value"] = 1000
        path = report.save_report(disk, res)
        self.verdict.configure(text=report.HEADLINE[res.verdict], fg=VERDICT_COLORS[res.verdict])
        self._set_text(self.result, report.describe_result(res) + f"\n\nReport saved: {path}")
        self.status.configure(text="Done.")

    def _on_error(self, msg):
        self.running = False
        self.stop_btn.configure(state="disabled")
        self.refresh_btn.configure(state="normal")
        self.status.configure(text=msg)
        messagebox.showerror("Fake Drive Checker", msg)


def main():
    root = tk.Tk()
    try:
        ttk.Style().theme_use("vista")
    except tk.TclError:
        pass
    App(root)
    root.mainloop()
