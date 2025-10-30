from __future__ import annotations
import os, ctypes, datetime as _dt, threading
import tkinter as tk
import os, shutil, platform
import psutil
import cpuinfo
import traceback
from pathlib import Path
import webbrowser
from tkinter import ttk, filedialog, messagebox
from tkinter.scrolledtext import ScrolledText

from .paths import OUTPUT_DIR, PATCH_out_DIR, MISSING_out_DIR, STORAGE_out_DIR, PATCH_read_DIR,MISSING_read_DIR,STORAGE_read_DIR, APP_ROOT, TITLE
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
from . import proc

# ---- console hider (for GUI when console=True) ----

def _hide_console_on_windows():
    try:
        hwnd = ctypes.windll.kernel32.GetConsoleWindow()
        if hwnd:
            ctypes.windll.user32.ShowWindow(hwnd, 0)  # SW_HIDE
    except Exception:
        pass

# ---- helpers----

def open_url(url: str):
    try:
        webbrowser.open(url, new=2)
    except Exception as e:
        messagebox.showerror("Open link", f"Failed to open link:\n{e}")

def copy_to_clipboard(root, text: str, toast: bool = True):
    try:
        root.clipboard_clear()
        root.clipboard_append(text)
        if toast:
            messagebox.showinfo("Copied", "Copied to clipboard.")
    except Exception:
        pass

def _safe_call(widget, func, *args, **kwargs):
    """Run func on Tk main thread."""
    try:
        widget.after(0, lambda: func(*args, **kwargs))
    except Exception:
        # best-effort fallback (e.g., during shutdown)
        try: func(*args, **kwargs)
        except Exception: pass

# -----------------------------
# GUI
# -----------------------------
class SierraPatcherGUI(tk.Tk):
    def __init__(self, dev: bool = False):
        super().__init__()
        self.title("Sierra Installer")
        self.geometry("800x460")
        self.resizable(False, False)

        self.grid_rowconfigure(0, weight=0)   # notebook row: no vertical stretch
        self.grid_rowconfigure(1, weight=0)   # progress row: no vertical stretch
        self.grid_rowconfigure(2, weight=1)   # spacer row: absorbs extra height
        self.grid_columnconfigure(0, weight=1)

        nb = ttk.Notebook(self, height=340)
        nb.grid(row=0, column=0, sticky="ew", padx=0, pady=(0,2))

        self._phase_var = tk.StringVar(value="Idle")
        self._detail_var = tk.StringVar(value="")
        self._total_var = 1
        self._done_var = 0

        style = ttk.Style(self)
        # Bold, slightly larger button for emphasis
        style.configure("AccentInstall.TButton", font=("Segoe UI", 10, "bold"), padding=(10, 6))
        # (Optional) make validation hint red & small
        style.configure("Hint.TLabel", foreground="#b00020", font=("Segoe UI", 9))
        
        if dev:
            self._gen_tab = self._build_generate_tab(nb)
            nb.add(self._gen_tab, text="Generate")

        self._ins_tab = self._build_install_tab(nb)
        self._log_tab = self._build_log_tab(nb)
        self._information = self._build_information_tab(nb)

        nb.add(self._ins_tab, text="Install")
        nb.add(self._log_tab, text="Logs")
        nb.add(self._information, text="info")

        # Shared progress widgets below notebook
        pframe = ttk.LabelFrame(self, text="Progress")
        pframe.grid(row=1, column=0, sticky="ew", padx=10, pady=(0,0))
        self._prog_bar = ttk.Progressbar(pframe, mode="determinate")
        self._prog_bar.pack(fill=tk.X, padx=12, pady=1)
        ttk.Label(pframe, textvariable=self._phase_var).pack(anchor="w", padx=12)
        ttk.Label(pframe, textvariable=self._detail_var, foreground="#666").pack(anchor="w", padx=12, pady=(0,1))

        icon_path =os.path.join(os.path.dirname(__file__), "assets", "title.ico")
        if os.path.exists(icon_path):
            self.iconbitmap(icon_path)

    # ---------- Shared progress helpers ----------
    def _reset_prog(self, total: int, phase: str):
        def _do():
            self._total_var = max(1, total)
            self._done_var = 0
            self._phase_var.set(phase)
            self._detail_var.set("")
            self._prog_bar.configure(mode="determinate", maximum=self._total_var, value=0)
        _safe_call(self, _do)

    def _step_prog(self, message: str | None = None):
        def _do():
            self._done_var += 1
            self._prog_bar['value'] = self._done_var
            if message:
                self._detail_var.set(message)
            self._prog_bar.update_idletasks()
        _safe_call(self, _do)

    def _set_phase(self, phase: str):
        _safe_call(self, self._phase_var.set, phase)

    def _abort_generate(self):
        try:
            self._log("[generate] abort requested")
            self._cancel.set()
            from . import proc
            proc.kill_all()
        except Exception:
            pass

    def _abort_install(self):
        try:
            self._log("[install] abort requested")
            self._cancel.set()
            from . import proc
            proc.kill_all()
        except Exception:
            pass


    # ---------- tab helpers ----------

    def _validate_install_ready(self):
        dst = (self.i_dest_var.get() or "").strip()
        valid = bool(dst and os.path.isdir(dst))
        # Show/hide hint
        if valid:
            self._dest_hint.grid_remove()
        else:
            self._dest_hint.configure(text="Destination folder is required." if not dst else "Folder does not exist.")
            self._dest_hint.grid()

        # Button state
        if valid:
            self.btn_install.state(["!disabled"])
        else:
            self.btn_install.state(["disabled"])

    
    def _status_row(self, parent, r, c, label, var: tk.StringVar):
        frm = ttk.Frame(parent)
        frm.grid(row=r, column=c, sticky="ew", padx=8, pady=2)
        ttk.Label(frm, text=f"{label}: ", foreground="#666").pack(side="left")
        ttk.Label(frm, textvariable=var).pack(side="left")

    def _browse_and_refresh(self, entry: ttk.Entry):
        d = filedialog.askdirectory(title="Select folder")
        if d:
            entry.delete(0, tk.END)
            entry.insert(0, d)
            # keep StringVar in sync
            try:
                self.i_dest_var.set(d)
            except Exception:
                pass
            self._refresh_status()
            self._validate_install_ready()

    def _open_destination(self):
        path = self.i_dest.get().strip()
        if not path:
            return
        try:
            os.startfile(path)  # Windows
        except Exception:
            pass

    def _format_bytes(self, n: int) -> str:
        # GiB with one decimal
        return f"{n / (1024**3):.1f} GiB"

    def _refresh_status(self):
        # --- System ---
        try:
            info = cpuinfo.get_cpu_info()
            cpu = info['brand_raw']
        except Exception:
            cpu = "CPU"
        try:
            phys = psutil.cpu_count(logical=False) or 1
            logi = psutil.cpu_count(logical=True) or phys
            cores = f"{phys} cores / {logi} threads"
        except Exception:
            cores = "—"
        try:
            vm = psutil.virtual_memory()
            ram = f"{self._format_bytes(vm.total)} total, {self._format_bytes(vm.available)} free"
        except Exception:
            ram = "—"

        self._stat["sys_cpu"].set(cpu)
        self._stat["sys_cores"].set(cores)
        self._stat["sys_ram"].set(ram)

        # --- Patcher (metadata + patch count) ---
        try:
            meta = Meta.read(STORAGE_read_DIR)
            self._stat["pat_version"].set(meta.version or "—")
            self._stat["pat_title"].set(meta.title or "—")
        except Exception:
            self._stat["pat_version"].set("—")
            self._stat["pat_title"].set("—")
        try:
            from .zstd_patch import count_patch_files
            self._stat["pat_patches"].set(str(count_patch_files()))
        except Exception:
            self._stat["pat_patches"].set("—")

        # --- Tarkov install ---
        try:
            inst = query_install()
            exe = os.path.join(inst["install_path"], "EscapeFromTarkov.exe")
            if inst:
                self._stat["tk_path"].set(str(inst["install_path"]))
                self._stat["tk_version"].set(exe_version(exe) or "—") #inst["display_version"] or 
                self._stat["tk_publisher"].set("—")#inst["publisher"] or 
            else:
                self._stat["tk_path"].set("Not found")
                self._stat["tk_version"].set("not found")
                self._stat["tk_publisher"].set("not found")
        except Exception:
            self._stat["tk_path"].set("error")
            self._stat["tk_version"].set("error")
            self._stat["tk_publisher"].set("error")

        # --- Destination (chosen folder) ---
        dst = self.i_dest.get().strip()
        try:
            if dst and os.path.isdir(dst):
                free = shutil.disk_usage(dst).free
                self._stat["dst_free"].set(self._format_bytes(free))
            else:
                self._stat["dst_free"].set("—")
        except Exception:
            self._stat["dst_free"].set("—")


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
        self._row(f, 1, "Target (SPT installation)", self.g_dest, browse=lambda: self._browse(self.g_dest))
        self._row(f, 2, "Release title", self.g_title)
        self._row(f, 3, "Date", self.g_date)
        self._row(f, 4, "Threads", self.g_threads)

        # Generate button inside Generate tab
        ttk.Button(f, text="Generate patch package", command=self._run_generate).grid(row=5, column=0, columnspan=3, pady=(6, 8), padx=12, sticky="w")
        self.btn_abort_gen = ttk.Button(f, text="Abort", command=self._abort_generate, state="disabled")
        self.btn_abort_gen.grid(row=5, column=1, padx=6, pady=(6,8), sticky="w")
        return f

    def _build_install_tab(self, nb) -> ttk.Frame:
        f = ttk.Frame(nb)
        f.columnconfigure(1, weight=1)

        # Inputs
        self.i_dest_var = tk.StringVar()
        self.i_dest = ttk.Entry(f)
        self.i_threads = ttk.Spinbox(f, from_=1, to=64)  # cap to 64; we’ll suggest optimal below
        self.i_threads.delete(0, tk.END)
        self.i_threads.insert(0, str(optimal_threads()))
        self.i_force = tk.BooleanVar(value=False)
        self.i_prereq = tk.BooleanVar(value=False)

        self._row(
            f, 0, "Destination to patch",
            self.i_dest,
            browse=lambda: self._browse_and_refresh(self.i_dest),
            required=True,
        )

        # Small validation hint under destination
        self._dest_hint = ttk.Label(f, text="Destination folder is required.", style="Hint.TLabel")
        self._dest_hint.grid(row=1, column=1, sticky="w", padx=12, pady=(2, 0))
        self._dest_hint.grid_remove()  # start hidden
        self._row(f, 1, "Threads", self.i_threads)

        ttk.Checkbutton(f, text="Force (bypass metadata checks)", variable=self.i_force)\
            .grid(row=2, column=0, columnspan=2, sticky="w", padx=12)
        ttk.Checkbutton(f, text="install .NET prerequisites", variable=self.i_prereq)\
            .grid(row=3, column=0, columnspan=2, sticky="w", padx=12)

        
        # Install button: highlighted, disabled until valid
        self.btn_install = ttk.Button(f, text="Install SPT", style="AccentInstall.TButton", command=self._run_install)
        self.btn_install.state(["!disabled"])
        self.btn_install.grid(row=4, column=0, columnspan=3, pady=(8, 8), padx=12, sticky="w")

        self.btn_abort_ins = ttk.Button(f, text="Abort", command=self._abort_install, state="disabled")
        self.btn_abort_ins.grid(row=4, column=1, padx=6, pady=(6,8), sticky="w")

        # ---- Status panel -------------------------------------------------------
        card = ttk.LabelFrame(f, text="Status")
        card.grid(row=5, column=0, columnspan=3, sticky="ew", padx=10, pady=(8, 0))
        for c in range(4):
            card.columnconfigure(c, weight=1)

        # Section headers
        ttk.Label(card, text="System", font=("Segoe UI", 10, "bold")).grid(row=0, column=0, sticky="w", padx=8, pady=(8,2))
        ttk.Label(card, text="Patcher", font=("Segoe UI", 10, "bold")).grid(row=0, column=1, sticky="w", padx=8, pady=(8,2))
        ttk.Label(card, text="Tarkov",  font=("Segoe UI", 10, "bold")).grid(row=0, column=2, sticky="w", padx=8, pady=(8,2))
        ttk.Label(card, text="Destination", font=("Segoe UI", 10, "bold")).grid(row=0, column=3, sticky="w", padx=8, pady=(8,2))

        # StringVars
        self._stat = {k: tk.StringVar(value="—") for k in [
            "sys_cpu", "sys_cores", "sys_ram",
            "pat_version", "pat_title", "pat_patches",
            "tk_path", "tk_version", "tk_publisher",
            "dst_free",
        ]}

        # System
        self._status_row(card, 1, 0, "CPU",       self._stat["sys_cpu"])
        self._status_row(card, 2, 0, "Cores",     self._stat["sys_cores"])
        self._status_row(card, 3, 0, "Memory",    self._stat["sys_ram"])

        # Patcher
        self._status_row(card, 1, 1, "target client",  self._stat["pat_version"])
        self._status_row(card, 2, 1, "target SPT",   self._stat["pat_title"])
        self._status_row(card, 3, 1, "Patch files", self._stat["pat_patches"])

        # Tarkov
        self._status_row(card, 1, 2, "Path",      self._stat["tk_path"])
        self._status_row(card, 2, 2, "Version",   self._stat["tk_version"])
        self._status_row(card, 3, 2, "Publisher", self._stat["tk_publisher"])

        # Destination
        self._status_row(card, 1, 3, "Free",      self._stat["dst_free"])

        # Controls
        btns = ttk.Frame(card)
        btns.grid(row=4, column=0, columnspan=4, sticky="ew", padx=8, pady=(6,8))
        ttk.Button(btns, text="Refresh", command=self._refresh_status).pack(side="left")
        ttk.Button(btns, text="Open destination", command=self._open_destination).pack(side="left", padx=6)

        # Initial fill
        self._refresh_status()
        self.i_dest_var.trace_add("write", lambda *_: self._validate_install_ready())
        
        self._validate_install_ready()
    
        return f

    
    def _build_information_tab(self, nb) -> ttk.Frame:
        f = ttk.Frame(nb)
        f.columnconfigure(0, weight=1)

        # === Header (logo + title) ===
        header = ttk.Frame(f)
        header.grid(row=0, column=0, sticky="ew", padx=16, pady=(14, 10))
        header.columnconfigure(1, weight=1)

        # Logo
        logo_path = os.path.join(os.path.dirname(__file__), "assets", "title.ico")
        logo_lbl = ttk.Label(header)
        logo_lbl.grid(row=0, column=0, rowspan=2, sticky="w", padx=(0, 12))
        if os.path.exists(logo_path):
            try:
                from PIL import Image, ImageTk
                img = Image.open(logo_path).resize((96, 96))
                self._info_logo_img = ImageTk.PhotoImage(img)  # keep reference!
                logo_lbl.configure(image=self._info_logo_img)
            except Exception:
                pass

        # Title + tagline
        title_lbl = ttk.Label(
            header,
            text="Sierra Installer",
            font=("Segoe UI", 16, "bold")
        )
        title_lbl.grid(row=0, column=1, sticky="w")
        tagline_lbl = ttk.Label(
            header,
            text="We have your six",
            font=("Segoe UI", 11),
            foreground="#666"
        )
        tagline_lbl.grid(row=1, column=1, sticky="w", pady=(4, 0))

        # Separator
        ttk.Separator(f).grid(row=1, column=0, sticky="ew", padx=16, pady=(0, 8))

        # === Links row (buttons + hyperlink labels) ===
        links = ttk.Frame(f)
        links.grid(row=2, column=0, sticky="ew", padx=16, pady=(4, 12))
        links.columnconfigure(3, weight=1)  # push right-side filler

        # Primary call-to-action buttons
        ttk.Button(
            links, text="Patchers",
            command=lambda: open_url("https://52sierra.net/patcher/")
        ).grid(row=0, column=0, padx=(0, 8))
        ttk.Button(
            links, text="Discord",
            command=lambda: open_url("https://discord.gg/uKMW8PxE8s")
        ).grid(row=0, column=1, padx=(0, 8))
        ttk.Button(
            links, text="Docs",
            command=lambda: open_url("https://52sierra.net/patcher/readme.txt")
        ).grid(row=0, column=2, padx=(0, 8))

        # Link-style labels
        def link(lbl: ttk.Label, url: str):
            lbl.configure(foreground="#0b62d6", cursor="hand2")
            lbl.bind("<Button-1>", lambda _e: open_url(url))
            lbl.bind("<Enter>", lambda _e: lbl.configure(underline=True))
            lbl.bind("<Leave>", lambda _e: lbl.configure(underline=False))

        right_links = ttk.Frame(links)
        right_links.grid(row=0, column=4, sticky="e")
        site_l = ttk.Label(right_links, text="Homepage")
        repo_l = ttk.Label(right_links, text="GitHub")
        site_l.grid(row=0, column=0, padx=8)
        repo_l.grid(row=0, column=1, padx=8)
        link(site_l, "https://52sierra.net/")
        link(repo_l, "https://github.com/52sierra-main/spt-downpatcher")

        # === About / Support cards (side-by-side) ===
        cards = ttk.Frame(f)
        cards.grid(row=3, column=0, sticky="ew", padx=16, pady=(4, 12))
        cards.columnconfigure(0, weight=1)
        cards.columnconfigure(1, weight=1)

        # About card (left)
        about = ttk.LabelFrame(cards, text="About", padding=12)
        about.grid(row=0, column=0, sticky="nsew", padx=(0, 8), pady=0)

        try:
            from sierra_patcher import __version__ as _VER
        except Exception:
            _VER = "0.1.0"

        ttk.Label(about, text=f"Version: {_VER}", foreground="#444").grid(row=0, column=0, sticky="w")
        ttk.Label(about, text="Sierra Installer provides patch generation/application for SPT installations.",
                  foreground="#555").grid(row=1, column=0, sticky="w", pady=(6, 0))

        # Support card (right)
        support = ttk.LabelFrame(cards, text="Support", padding=12)
        support.grid(row=0, column=1, sticky="nsew", padx=(8, 0), pady=0)

        support_email = "sierra@52sierra.net"
        row = 0
        ttk.Label(support, text="mail address:", foreground="#444").grid(row=row, column=0, sticky="w"); row += 1

        # clickable email
        mail_l = ttk.Label(support, text=support_email, foreground="#0b62d6", cursor="hand2")
        mail_l.grid(row=row, column=0, sticky="w", pady=(2, 0)); row += 1
        mail_l.bind("<Button-1>", lambda _e: open_url(f"mailto:{support_email}"))
        mail_l.bind("<Enter>",   lambda _e: mail_l.configure(underline=True))
        mail_l.bind("<Leave>",   lambda _e: mail_l.configure(underline=False))

        btns = ttk.Frame(support)
        btns.grid(row=row, column=0, sticky="w", pady=(8, 0)); row += 1
        ttk.Button(btns, text="Copy email",
                   command=lambda: copy_to_clipboard(self, support_email)).pack(side="left")

        # --- Footer ---
        ttk.Separator(f).grid(row=4, column=0, sticky="ew", padx=16, pady=(4, 8))
        ttk.Label(f, text="© 2025 Sierra. All rights reserved.", foreground="#777")\
            .grid(row=5, column=0, sticky="w", padx=16, pady=(0, 10))

        return f


    def _build_log_tab(self, nb) -> ttk.Frame:
        f = ttk.Frame(nb)
        f.rowconfigure(0, weight=1)
        f.columnconfigure(0, weight=1)
        self.log_text = ScrolledText(f, state="normal", wrap="word")
        self.log_text.grid(row=0, column=0, sticky="nsew", padx=8, pady=2)
        return f
    
    def _append_log(self, msg: str):
        try:
            self.log_text.insert("end", msg + "\n")
            self.log_text.see("end")
        except Exception:
            pass

    def _log(self, *parts):
        _safe_call(self, self._append_log, " ".join(str(p) for p in parts))

    def _log_exc(self, prefix="Error"):
        tb = "".join(traceback.format_exc())
        _safe_call(self, self._append_log, f"{prefix}:\n{tb}")


    def _row(self, parent, r, label, entry_widget, browse=None, required=False):
        # label with optional red asterisk
        lbl_text = f"{label}"
        lbl = ttk.Label(parent, text=lbl_text)
        lbl.grid(row=r, column=0, sticky="w", padx=12, pady=(6, 0))
        if required:
            # add a red asterisk right next to the label
            tk.Label(parent, text=" *", fg="#b00020").grid(row=r, column=0, sticky="e", padx=(0, 0), pady=(6, 0))

        entry_widget.grid(row=r, column=1, sticky="ew", padx=12, pady=(6, 0))
        if browse:
            ttk.Button(parent, text="Browse", command=browse).grid(row=r, column=2, padx=6, pady=(6, 0))

    def _browse(self, entry: ttk.Entry):
        d = filedialog.askdirectory(title="Select folder")
        if d:
            entry.delete(0, tk.END)
            entry.insert(0, d)

    # ---------- Action handlers ----------
    def _run_generate(self):
        self._cancel = threading.Event()
        self.btn_abort_gen.state(["!disabled"])
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
            _safe_call(self, self._detail_var.set, message)
            # keep progress determinate by mapping current → value without touching Tk from worker
            def _do():
                self._prog_bar['value'] = min(self._total_var, self._done_var + current)
            _safe_call(self, _do)

        def worker():
            try:
                for d in (OUTPUT_DIR, PATCH_out_DIR, MISSING_out_DIR, STORAGE_out_DIR):
                    os.makedirs(d, exist_ok=True)

                self._log("[generate] start")
                # basic sanity: tools present?
                from .paths import ZSTD_EXE
                if not os.path.isfile(ZSTD_EXE):
                    raise RuntimeError(f"zstd not found at: {ZSTD_EXE}")

                # Patching phase
                generate_patches(src, dst, PATCH_out_DIR, MISSING_out_DIR, workers=threads,
                                on_progress=on_progress, cancel_event=self._cancel, use_tqdm=False)
                if self._cancel.is_set():
                    self._set_phase("Cancelled")
                    self._log("[generate] cancelled")
                    return
                self._done_var = total_files

                self._set_phase("Packing additional files")
                pack_additional(MISSING_out_DIR, STORAGE_out_DIR, cancel_event=self._cancel)
                self._step_prog("additional files packed")

                self._set_phase("Building delete list")
                build_delete_list(src, dst, os.path.join(STORAGE_out_DIR, "delete_list.txt"))
                self._step_prog("delete list written")

                self._set_phase("Stamping metadata")
                stamp_from_game_exe(os.path.join(STORAGE_out_DIR, "metadata.info"), src, title, date)
                self._step_prog("metadata stamped")

                self._set_phase("Verifying patches")
                verify_patch_files(cancel_event=self._cancel)
                self._step_prog("verification complete")

                self._set_phase("Done")
                self._log("[generate] done")
                _safe_call(self, messagebox.showinfo, "Generate", f"Patch package ready in:\n{OUTPUT_DIR}")
            except proc.Cancelled:
                self._set_phase("Cancelled")
                self._log("[generate] cancelled by user")
                return
            except Exception:
                self._log_exc("[generate] failed")
                _safe_call(self, messagebox.showerror, "Generate", "Generation failed. See Logs tab for details.")
            finally:
                proc.kill_all()
                _safe_call(self, self.btn_abort_gen.state, ["disabled"])

        threading.Thread(target=worker, daemon=True).start()

    def _run_install(self):
        self._cancel = threading.Event()
        self.btn_abort_ins.state(["!disabled"])
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
            _safe_call(self, self._detail_var.set, message)
            # keep progress determinate by mapping current → value without touching Tk from worker
            def _do():
                self._prog_bar['value'] = min(self._total_var, self._done_var + current)
            _safe_call(self, _do)

        def worker():
            try:
                self._log("[install] start")
                from .paths import ZSTD_EXE
                if not os.path.isfile(ZSTD_EXE):
                    raise RuntimeError(f"zstd not found at: {ZSTD_EXE}")

                meta = Meta.read(STORAGE_read_DIR)
                inst = query_install()  # now dict; keep it that way consistently
                if not inst:
                    raise RuntimeError("Tarkov installation not found (registry).")

                if not force:
                    exe = os.path.join(inst["install_path"], "EscapeFromTarkov.exe")
                    ver_now = exe_version(exe) or "-"
                    if meta.version and ver_now != meta.version:
                        raise RuntimeError(f"Live Tarkov version {ver_now} != target {meta.version}")

                apply_all_patches(dst, workers=threads, on_progress=on_progress,
                              cancel_event=self._cancel, use_tqdm=False)
                if self._cancel.is_set():
                    self._set_phase("Cancelled")
                    self._log("[install] cancelled")
                    return
                self._done_var = total_patches

                self._set_phase("Finalizing (delete list)")
                finalize(dst, os.path.join(STORAGE_read_DIR, "delete_list.txt"))
                self._step_prog("cleanup done")

                self._set_phase("Applying storage")
                apply_storage(STORAGE_read_DIR, dst, cancel_event=self._cancel)
                self._step_prog("storage applied")

                if prereq:
                    self._set_phase("Installing .NET prerequisites")
                    ensure_prereqs(interactive=False)
                    self._step_prog("prereqs installed")

                self._set_phase("Done")
                self._log("[install] done")
                _safe_call(self, messagebox.showinfo, "Install", "Patch applied successfully.")
            except proc.Cancelled:
                self._set_phase("Cancelled")
                self._log("[install] cancelled by user")
                return
            except Exception:
                self._log_exc("[install] failed")
                _safe_call(self, messagebox.showerror, "Install", "Install failed. See Logs tab for details.")
            finally:
                proc.kill_all()
                _safe_call(self, self.btn_abort_ins.state, ["disabled"])

        threading.Thread(target=worker, daemon=True).start()

def main(dev: bool = False):
    _hide_console_on_windows()
    app = SierraPatcherGUI(dev=dev)
    app.mainloop()
