from __future__ import annotations
import os, ctypes, datetime as _dt, threading
import tkinter as tk
from tkinter import ttk, filedialog, messagebox
from tkinter.scrolledtext import ScrolledText

from .paths import OUTPUT_DIR, PATCH_DIR, MISSING_DIR, STORAGE_DIR
from .system import check_resources, optimal_threads
from .registry import query_install, exe_version
from .metadata import Meta, stamp_from_game_exe
from .storage import pack_additional, apply_storage
from .zstd_patch import (
    generate_patches, apply_all_patches, verify_patch_files,
    count_dest_files, count_patch_files,
)
from .delete_list import build_delete_list, finalize
from .prereqs import ensure_prereqs

# ---- console hider (for GUI when console=True) ----

def _hide_console_on_windows():
    try:
        hwnd = ctypes.windll.kernel32.GetConsoleWindow()
        if hwnd:
            ctypes.windll.user32.ShowWindow(hwnd, 0)  # SW_HIDE
    except Exception:
        pass

# -----------------------------
# GUI
# -----------------------------
class SierraPatcherGUI(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("Sierra Patcher – GUI")
        self.geometry("700x500")
        self.minsize(500, 200)

        self.grid_rowconfigure(0, weight=0)   # notebook row: no vertical stretch
        self.grid_rowconfigure(1, weight=0)   # progress row: no vertical stretch
        self.grid_rowconfigure(2, weight=1)   # spacer row: absorbs extra height
        self.grid_columnconfigure(0, weight=1)

        nb = ttk.Notebook(self)
        nb.grid(row=0, column=0, sticky="ew", padx=0, pady=(0,2))

        self._phase_var = tk.StringVar(value="Idle")
        self._detail_var = tk.StringVar(value="")
        self._total_var = 1
        self._done_var = 0

        self._gen_tab = self._build_generate_tab(nb)
        self._ins_tab = self._build_install_tab(nb)
        self._log = self._build_log_tab(nb)

        nb.add(self._gen_tab, text="Generate")
        nb.add(self._ins_tab, text="Install")
        nb.add(self._log, text="Logs")

        # Shared progress widgets below notebook
        pframe = ttk.LabelFrame(self, text="Progress")
        pframe.grid(row=1, column=0, sticky="ew", padx=10, pady=(0,0))
        self._prog_bar = ttk.Progressbar(pframe, mode="determinate")
        self._prog_bar.pack(fill=tk.X, padx=12, pady=1)
        ttk.Label(pframe, textvariable=self._phase_var).pack(anchor="w", padx=12)
        ttk.Label(pframe, textvariable=self._detail_var, foreground="#666").pack(anchor="w", padx=12, pady=(0,1))

    # ---------- Shared progress helpers ----------
    def _reset_prog(self, total: int, phase: str):
        self._total_var = max(1, total)
        self._done_var = 0
        self._phase_var.set(phase)
        self._detail_var.set("")
        self._prog_bar.configure(mode="determinate", maximum=self._total_var, value=0)

    def _step_prog(self, message: str | None = None):
        self._done_var += 1
        self._prog_bar['value'] = self._done_var
        if message:
            self._detail_var.set(message)
        self._prog_bar.update_idletasks()

    def _set_phase(self, phase: str):
        self._phase_var.set(phase)

    # ---------- UI builders ----------
    def _build_generate_tab(self, nb) -> ttk.Frame:
        f = ttk.Frame(nb)
        f.columnconfigure(1, weight=1)

        self.g_source = ttk.Entry(f)
        self.g_dest = ttk.Entry(f)
        self.g_title = ttk.Entry(f)
        self.g_date = ttk.Entry(f)
        self.g_date.insert(0, _dt.date.today().isoformat())
        self.g_threads = ttk.Spinbox(f, from_=1, to=64)
        self.g_threads.delete(0, tk.END)
        self.g_threads.insert(0, str(optimal_threads()))

        self._row(f, 0, "Source (clean game)", self.g_source, browse=lambda: self._browse(self.g_source))
        self._row(f, 1, "Target (SPT build)", self.g_dest, browse=lambda: self._browse(self.g_dest))
        self._row(f, 2, "Release title", self.g_title)
        self._row(f, 3, "Date", self.g_date)
        self._row(f, 4, "Threads", self.g_threads)

        # Generate button inside Generate tab
        ttk.Button(f, text="Generate patch package", command=self._run_generate).grid(row=5, column=0, columnspan=3, pady=(6, 8), padx=12, sticky="w")

        return f

    def _build_install_tab(self, nb) -> ttk.Frame:
        f = ttk.Frame(nb)
        f.columnconfigure(1, weight=1)

        self.i_dest = ttk.Entry(f)
        self.i_threads = ttk.Spinbox(f, from_=1, to=64)
        self.i_threads.delete(0, tk.END)
        self.i_threads.insert(0, str(optimal_threads()))
        self.i_force = tk.BooleanVar(value=False)
        self.i_prereq = tk.BooleanVar(value=False)

        self._row(f, 0, "Destination to patch", self.i_dest, browse=lambda: self._browse(self.i_dest))
        self._row(f, 1, "Threads", self.i_threads)

        ttk.Checkbutton(f, text="Force (bypass metadata checks)", variable=self.i_force).grid(row=2, column=0, columnspan=2, sticky="w", padx=12)
        ttk.Checkbutton(f, text=".NET prerequisites", variable=self.i_prereq).grid(row=3, column=0, columnspan=2, sticky="w", padx=12)

        # Install button inside Install tab
        ttk.Button(f, text="Install patch package", command=self._run_install).grid(row=4, column=0, columnspan=3, pady=(6, 8), padx=12, sticky="w")

        return f

    def _build_log_tab(self, nb) -> ttk.Frame:
        f = ttk.Frame(nb)
        f.rowconfigure(0, weight=1)
        f.columnconfigure(0, weight=1)
        self.log_text = ScrolledText(f, state="normal", wrap="word")
        self.log_text.grid(row=0, column=0, sticky="nsew", padx=8, pady=2)
        return f

    def _row(self, parent, r, label, entry, browse=None):
        ttk.Label(parent, text=label).grid(row=r, column=0, sticky="w", padx=12, pady=6)
        entry.grid(row=r, column=1, sticky="ew", padx=12, pady=6)
        if browse:
            ttk.Button(parent, text="Browse", command=browse).grid(row=r, column=2, padx=6)

    def _browse(self, entry: ttk.Entry):
        d = filedialog.askdirectory(title="Select folder")
        if d:
            entry.delete(0, tk.END)
            entry.insert(0, d)

    # ---------- Action handlers ----------
    def _run_generate(self):
        src = self.g_source.get().strip()
        dst = self.g_dest.get().strip()
        title = self.g_title.get().strip() or ""
        date = self.g_date.get().strip() or _dt.date.today().isoformat()
        threads = int(self.g_threads.get())
        if not src or not dst:
            messagebox.showerror("Missing folders", "Please set Source and Target folders.")
            return
        check_resources()

        # Pre-compute totals: files in dest + 4 post steps
        total_files = count_dest_files(dst)
        extra_steps = 4  # pack_additional, build_delete_list, stamp_metadata, verify
        self._reset_prog(total_files + extra_steps, "Generating patches")

        def on_progress(phase, current, total, message):
            self._detail_var.set(message)
            # We treat each processed file as one progress step
            self._prog_bar['value'] = min(self._total_var, self._done_var + current)

        def worker():
            # Patching phase
            generate_patches(src, dst, PATCH_DIR, MISSING_DIR, workers=threads,
                              on_progress=on_progress)
            self._done_var = total_files

            # Post steps (each +1)
            self._set_phase("Packing additional files")
            pack_additional(MISSING_DIR, STORAGE_DIR)
            self._step_prog("additional files packed")

            self._set_phase("Building delete list")
            build_delete_list(src, dst, os.path.join(STORAGE_DIR, "delete_list.txt"))
            self._step_prog("delete list written")

            self._set_phase("Stamping metadata")
            stamp_from_game_exe(os.path.join(STORAGE_DIR, "metadata.info"), src, title, date)
            self._step_prog("metadata stamped")

            self._set_phase("Verifying patches")
            verify_patch_files()
            self._step_prog("verification complete")

            self._set_phase("Done")
            messagebox.showinfo("Generate", f"Patch package ready in:\n{OUTPUT_DIR}")

        threading.Thread(target=worker, daemon=True).start()

    def _run_install(self):
        dst = self.i_dest.get().strip()
        threads = int(self.i_threads.get())
        force = self.i_force.get()
        prereq = self.i_prereq.get()
        if not dst:
            messagebox.showerror("Missing folder", "Please set Destination folder.")
            return
        check_resources()

        total_patches = count_patch_files()
        # extra: finalize + apply_storage (+1 each) (+1 prereqs optional)
        extra_steps = 2 + (1 if prereq else 0)
        self._reset_prog(total_patches + extra_steps, "Applying patches")

        def on_progress(phase, current, total, message):
            self._detail_var.set(message)
            self._prog_bar['value'] = min(self._total_var, self._done_var + current)

        def worker():
            # Metadata + guards
            meta = Meta.read(STORAGE_DIR)
            inst = query_install()
            if not inst:
                messagebox.showerror("Install", "Tarkov installation not found in registry.")
                return
            if not force:
                exe = os.path.join(inst.install_path, "EscapeFromTarkov.exe")
                if exe_version(exe) != meta.version:
                    messagebox.showerror("Install", "Client version mismatch vs metadata.")
                    return
                if inst.publisher != "Battlestate Games":
                    messagebox.showerror("Install", "Publisher mismatch. Aborting.")
                    return

            # Apply patches
            apply_all_patches(dst, workers=threads, on_progress=on_progress)
            self._done_var = total_patches

            self._set_phase("Finalizing (delete list)")
            finalize(dst, os.path.join(STORAGE_DIR, "delete_list.txt"))
            self._step_prog("cleanup done")

            self._set_phase("Applying storage")
            apply_storage(STORAGE_DIR, dst)
            self._step_prog("storage applied")

            if prereq:
                self._set_phase("Installing .NET prerequisites")
                ensure_prereqs(interactive=False)
                self._step_prog("prereqs step done")

            self._set_phase("Done")
            messagebox.showinfo("Install", "Patch applied successfully.")

        threading.Thread(target=worker, daemon=True).start()

def main():
    _hide_console_on_windows()
    app = SierraPatcherGUI()
    app.mainloop()
