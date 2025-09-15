"""
GUI wrapper for Sierra Patcher

- Two tabs: Generate (dev-side) and Install (user-side)
- Non-blocking (runs work in threads)
- Simple log window that captures stdout/stderr from underlying modules
- Minimal progress indication (indeterminate bar while job runs)

Usage (run as module):
    python -m sierra_patcher.gui_app

Or freeze with PyInstaller and set entry to: -m sierra_patcher.gui_app
"""
from __future__ import annotations

import os
import sys
import threading
import queue
import datetime as _dt
from dataclasses import dataclass
from typing import Optional

import tkinter as tk
from tkinter import ttk, filedialog, messagebox
from tkinter.scrolledtext import ScrolledText

# Internal modules
from .paths import OUTPUT_DIR, PATCH_DIR, MISSING_DIR, STORAGE_DIR
from .system import check_resources, optimal_threads
from .registry import query_install, exe_version
from .metadata import Meta, stamp_from_game_exe
from .storage import pack_additional, apply_storage
from .zstd_patch import generate_patches, apply_all_patches, verify_patch_files
from .delete_list import build_delete_list, finalize
from .prereqs import ensure_prereqs


# -----------------------------
# Utilities for log capturing
# -----------------------------
class _TeeStream:
    """A file-like that tees writes to both the real stream and a Tk queue."""
    def __init__(self, real, q: queue.Queue[str], tag: str):
        self._real = real
        self._q = q
        self._tag = tag

    def write(self, s: str):
        if s:
            try:
                self._q.put((self._tag, s))
            except Exception:
                pass
            try:
                self._real.write(s)
            except Exception:
                pass

    def flush(self):
        try:
            self._real.flush()
        except Exception:
            pass


@dataclass
class Job:
    name: str
    thread: Optional[threading.Thread] = None
    running: bool = False


# -----------------------------
# Main App
# -----------------------------
class SierraPatcherGUI(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("Sierra Patcher – GUI")
        self.geometry("980x680")
        self.minsize(880, 560)

        # Queue for log lines
        self._log_q: queue.Queue[str] = queue.Queue()
        self._orig_out, self._orig_err = sys.stdout, sys.stderr
        sys.stdout = _TeeStream(sys.stdout, self._log_q, "stdout")
        sys.stderr = _TeeStream(sys.stderr, self._log_q, "stderr")

        # Job tracker
        self._job = Job(name="idle")

        # Notebook
        nb = ttk.Notebook(self)
        nb.pack(fill=tk.BOTH, expand=True)

        self._gen_tab = self._build_generate_tab(nb)
        self._ins_tab = self._build_install_tab(nb)
        self._log = self._build_log_frame()

        nb.add(self._gen_tab, text="Generate")
        nb.add(self._ins_tab, text="Install")
        nb.add(self._log, text="Logs")

        # start polling for logs
        self.after(60, self._drain_logs)

    # ---------- UI builders ----------
    def _row(self, parent, r, label, widget, pad=(8, 6)):
        ttk.Label(parent, text=label).grid(row=r, column=0, sticky="w", padx=12, pady=pad)
        widget.grid(row=r, column=1, sticky="ew", padx=12, pady=pad)

    def _browse_dir(self, entry: ttk.Entry, title: str):
        d = filedialog.askdirectory(title=title)
        if d:
            entry.delete(0, tk.END)
            entry.insert(0, d)

    def _build_generate_tab(self, nb) -> ttk.Frame:
        f = ttk.Frame(nb)
        f.columnconfigure(1, weight=1)

        e_source = ttk.Entry(f)
        e_dest = ttk.Entry(f)
        e_title = ttk.Entry(f)
        e_date = ttk.Entry(f)
        e_date.insert(0, _dt.date.today().isoformat())
        e_threads = ttk.Spinbox(f, from_=1, to=64)
        e_threads.delete(0, tk.END)
        e_threads.insert(0, str(optimal_threads()))

        self._row(f, 0, "Source (clean game)", e_source)
        ttk.Button(f, text="Browse", command=lambda: self._browse_dir(e_source, "Select CLEAN game folder (source)")).grid(row=0, column=2, padx=6)

        self._row(f, 1, "Target (SPT build)", e_dest)
        ttk.Button(f, text="Browse", command=lambda: self._browse_dir(e_dest, "Select TARGET folder (SPT build)")).grid(row=1, column=2, padx=6)

        self._row(f, 2, "Release title", e_title)
        self._row(f, 3, "Date", e_date)
        self._row(f, 4, "Threads", e_threads)

        p = ttk.Progressbar(f, mode="indeterminate")
        p.grid(row=5, column=0, columnspan=3, sticky="ew", padx=12, pady=(10, 6))

        def run_generate():
            src = e_source.get().strip()
            dst = e_dest.get().strip()
            title = e_title.get().strip() or ""
            date = e_date.get().strip() or ""
            threads = int(e_threads.get())
            if not src or not dst:
                messagebox.showerror("Missing folders", "Please choose Source and Target directories.")
                return
            self._start_job("generate", p, lambda: self._do_generate(src, dst, title, date, threads))

        btn = ttk.Button(f, text="Generate patch package", command=run_generate)
        btn.grid(row=6, column=0, columnspan=3, pady=(4, 10))

        self._g_entries = (e_source, e_dest, e_title, e_date, e_threads)
        self._g_prog = p
        return f

    def _build_install_tab(self, nb) -> ttk.Frame:
        f = ttk.Frame(nb)
        f.columnconfigure(1, weight=1)

        e_dest = ttk.Entry(f)
        e_threads = ttk.Spinbox(f, from_=1, to=64)
        e_threads.delete(0, tk.END)
        e_threads.insert(0, str(optimal_threads()))

        v_force = tk.BooleanVar(value=False)
        v_prereq = tk.BooleanVar(value=False)

        self._row(f, 0, "Destination to patch", e_dest)
        ttk.Button(f, text="Browse", command=lambda: self._browse_dir(e_dest, "Select destination Tarkov folder to patch")).grid(row=0, column=2, padx=6)

        self._row(f, 1, "Threads", e_threads)

        c1 = ttk.Checkbutton(f, text="Force (bypass metadata checks)", variable=v_force)
        c1.grid(row=2, column=0, columnspan=2, sticky="w", padx=12, pady=(0,6))
        c2 = ttk.Checkbutton(f, text="Install .NET prerequisites", variable=v_prereq)
        c2.grid(row=3, column=0, columnspan=2, sticky="w", padx=12, pady=(0,6))

        p = ttk.Progressbar(f, mode="indeterminate")
        p.grid(row=4, column=0, columnspan=3, sticky="ew", padx=12, pady=(10, 6))

        def run_install():
            dst = e_dest.get().strip()
            threads = int(e_threads.get())
            if not dst:
                messagebox.showerror("Missing folder", "Please choose the Destination directory.")
                return
            self._start_job("install", p, lambda: self._do_install(dst, threads, v_force.get(), v_prereq.get()))

        btn = ttk.Button(f, text="Install patch package", command=run_install)
        btn.grid(row=5, column=0, columnspan=3, pady=(4, 10))

        self._i_entries = (e_dest, e_threads, v_force, v_prereq)
        self._i_prog = p
        return f

    def _build_log_frame(self) -> ttk.Frame:
        f = ttk.Frame(self)
        f.rowconfigure(0, weight=1)
        f.columnconfigure(0, weight=1)
        txt = ScrolledText(f, state="disabled", wrap="word", height=20)
        txt.grid(row=0, column=0, sticky="nsew", padx=8, pady=8)
        self._log_text = txt

        btns = ttk.Frame(f)
        btns.grid(row=1, column=0, sticky="ew", padx=8, pady=(0,8))
        ttk.Button(btns, text="Copy all", command=self._copy_logs).pack(side=tk.LEFT)
        ttk.Button(btns, text="Clear", command=self._clear_logs).pack(side=tk.LEFT, padx=(8,0))
        return f

    # ---------- Job orchestration ----------
    def _set_inputs_state(self, enabled: bool):
        state = ("!disabled" if enabled else "disabled")
        for e in getattr(self, "_g_entries", ()):  # type: ignore
            try:
                if isinstance(e, tuple):
                    continue
                e.state([state])
            except Exception:
                pass
        for e in getattr(self, "_i_entries", ()):  # type: ignore
            try:
                if isinstance(e, tuple):
                    continue
                if isinstance(e, tk.BooleanVar):
                    continue
                e.state([state])
            except Exception:
                pass

    def _start_job(self, name: str, prog: ttk.Progressbar, target):
        if self._job.running:
            messagebox.showwarning("Busy", "Another task is currently running.")
            return
        self._job = Job(name=name, running=True)
        self._set_inputs_state(False)
        prog.start(18)

        def run():
            try:
                target()
                print(f"\n[{name}] completed.")
            except SystemExit as e:
                print(f"[{name}] aborted: {e}")
            except Exception as e:
                print(f"[{name}] error: {e}")
            finally:
                self.after(0, self._finish_job, prog)

        t = threading.Thread(target=run, daemon=True)
        self._job.thread = t
        t.start()

    def _finish_job(self, prog: ttk.Progressbar):
        prog.stop()
        self._job.running = False
        self._set_inputs_state(True)

    # ---------- Actions ----------
    def _do_generate(self, source: str, dest: str, title: str, date: str, threads: int):
        print("=== GENERATE ===")
        print("Source:", source)
        print("Target:", dest)
        check_resources()

        # ensure dirs
        os.makedirs(PATCH_DIR, exist_ok=True)
        os.makedirs(MISSING_DIR, exist_ok=True)
        os.makedirs(STORAGE_DIR, exist_ok=True)

        generate_patches(source, dest, PATCH_DIR, MISSING_DIR, workers=threads)
        pack_additional(MISSING_DIR, STORAGE_DIR)
        build_delete_list(source, dest, os.path.join(STORAGE_DIR, "delete_list.txt"))

        if title and date:
            stamp_from_game_exe(os.path.join(STORAGE_DIR, "metadata.info"), source, title, date)
        else:
            today = _dt.date.today().isoformat()
            stamp_from_game_exe(os.path.join(STORAGE_DIR, "metadata.info"), source, title or "", date or today)

        verify_patch_files()
        print("Output:", OUTPUT_DIR)

    def _do_install(self, dest: str, threads: int, force: bool, prereqs: bool):
        print("=== INSTALL ===")
        meta = Meta.read(STORAGE_DIR)
        print("Patcher metadata:")
        print(" version:", meta.version)
        print(" title:", meta.title)

        inst = query_install()
        if not inst:
            raise SystemExit("Tarkov installation not found in registry.")

        print("Detected installation:")
        print(" path:", inst.install_path)
        print(" version:", inst.version)
        print(" publisher:", inst.publisher)

        if not force:
            exe = os.path.join(inst.install_path, "EscapeFromTarkov.exe")
            if exe_version(exe) != meta.version:
                raise SystemExit("Client version mismatch vs metadata. Use Force to override.")
            if inst.publisher != "Battlestate Games":
                raise SystemExit("Publisher mismatch. Aborting.")

        check_resources()
        ok = apply_all_patches(dest, workers=threads)
        finalize(dest, os.path.join(STORAGE_DIR, "delete_list.txt"))
        apply_storage(STORAGE_DIR, dest)
        if prereqs:
            ensure_prereqs(interactive=False)
        if not ok:
            print("Some patches failed. Review logs.")

    # ---------- Log pane helpers ----------
    def _drain_logs(self):
        drained = 0
        try:
            while True:
                tag, s = self._log_q.get_nowait()
                self._append_log(s, tag)
                drained += 1
                if drained > 500:
                    break
        except queue.Empty:
            pass
        self.after(60, self._drain_logs)

    def _append_log(self, s: str, tag: str = "stdout"):
        txt = self._log_text
        txt.configure(state="normal")
        txt.insert(tk.END, s)
        txt.see(tk.END)
        txt.configure(state="disabled")

    def _copy_logs(self):
        data = self._log_text.get("1.0", tk.END)
        self.clipboard_clear()
        self.clipboard_append(data)
        messagebox.showinfo("Logs", "Copied to clipboard.")

    def _clear_logs(self):
        self._log_text.configure(state="normal")
        self._log_text.delete("1.0", tk.END)
        self._log_text.configure(state="disabled")


# -----------------------------
# Entrypoint
# -----------------------------

def main():
    app = SierraPatcherGUI()
    app.mainloop()

if __name__ == "__main__":
    main()
