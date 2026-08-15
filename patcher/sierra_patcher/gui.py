from __future__ import annotations
import os, ctypes, datetime as _dt, threading
import tkinter as tk
import os, shutil, platform, sys
import psutil
import cpuinfo
import traceback
from pathlib import Path
import webbrowser
from tkinter import ttk, filedialog, messagebox
from tkinter.scrolledtext import ScrolledText

from .utils import  rename_output_folder, copy_self_to_output, open_url, copy_to_clipboard, folder_size, format_bytes, summarize_integrity_list
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
from .i18n import (
    DEFAULT_LANGUAGE,
    SUPPORTED_LANGUAGES,
    alternate_language,
    canonical_exact_text,
    canonical_text,
    canonical_choice,
    current_language,
    retranslate,
    retranslate_exact,
    set_language,
    tr,
    tr_progress,
)
from .prereqs import format_missing_requirements, missing_requirements_for_metadata
from .session_log import free_space, session_log
from . import proc

DIFF_PRESETS = {
  "Fast (bigger patches)": ["-3", "--long=31"],
  "Balanced": ["-10", "--long=31"],
  "Aggressive (smallest patches)": ["--ultra", "-22", "--long=31"],
  "MAX (experimental)": ["--max", "--long=31"],
}

# ---- console hider (for GUI when console=True) ----

def _hide_console_on_windows():
    try:
        hwnd = ctypes.windll.kernel32.GetConsoleWindow()
        if hwnd:
            ctypes.windll.user32.ShowWindow(hwnd, 0)  # SW_HIDE
    except Exception:
        pass

# ---- helpers----

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
    # The Logs tab keeps only the most recent lines; the on-disk session log is
    # complete and is what the Save/Copy buttons hand to support.
    _LOG_WIDGET_MAX_LINES = 4000

    def __init__(self, dev: bool = False):
        startup_language = current_language()
        if startup_language != DEFAULT_LANGUAGE:
            set_language(DEFAULT_LANGUAGE)
        super().__init__()
        self.withdraw()
        self._startup_language = startup_language
        self._language_change_in_progress = False
        self.title("Sierra Installer")
        self.geometry("800x520")
        self.resizable(False, False)

        self.grid_rowconfigure(0, weight=0)   # app header
        self.grid_rowconfigure(1, weight=0)   # notebook row: no vertical stretch
        self.grid_rowconfigure(2, weight=0)   # progress row: no vertical stretch
        self.grid_rowconfigure(3, weight=1)   # spacer row: absorbs extra height
        self.grid_columnconfigure(0, weight=1)
        self._build_language_switcher()

        nb = ttk.Notebook(self, height=340)
        nb.grid(row=1, column=0, sticky="ew", padx=0, pady=(0,2))

        self._phase_var = tk.StringVar(value=tr("Idle"))
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
            nb.add(self._gen_tab, text=tr("Generate"))

        self._ins_tab = self._build_install_tab(nb)
        self._log_tab = self._build_log_tab(nb)
        self._information = self._build_information_tab(nb)

        nb.add(self._ins_tab, text=tr("Install"))
        nb.add(self._log_tab, text=tr("Logs"))
        nb.add(self._information, text=tr("Info"))

        # Shared progress widgets below notebook
        pframe = ttk.LabelFrame(self, text=tr("Progress"))
        pframe.grid(row=2, column=0, sticky="ew", padx=10, pady=(0,0))
        self._prog_bar = ttk.Progressbar(pframe, mode="determinate")
        self._prog_bar.pack(fill=tk.X, padx=12, pady=1)
        ttk.Label(pframe, textvariable=self._phase_var).pack(anchor="w", padx=12)
        ttk.Label(pframe, textvariable=self._detail_var, foreground="#666").pack(anchor="w", padx=12, pady=(0,1))

        icon_path =os.path.join(os.path.dirname(__file__), "assets", "title.ico")
        if os.path.exists(icon_path):
            self.iconbitmap(icon_path)

        self._startup_language_callback = self.after_idle(
            self.apply_startup_language
        )

    def apply_startup_language(self, *, present: bool = True) -> None:
        callback = self._startup_language_callback
        self._startup_language_callback = None
        if callback is not None:
            try:
                self.after_cancel(callback)
            except tk.TclError:
                pass
        language = self._startup_language
        self._startup_language = None
        if language is not None and language != current_language():
            set_language(language)
            self._refresh_language()
        if present:
            self.deiconify()

    def _build_language_switcher(self) -> None:
        toolbar = ttk.Frame(self, style="LanguageBar.TFrame")
        toolbar.grid(row=0, column=0, sticky="ew", padx=12, pady=(8, 5))
        toolbar.columnconfigure(0, weight=1)

        ttk.Label(
            toolbar,
            text="Sierra Installer",
            style="AppTitle.TLabel",
        ).grid(row=0, column=0, sticky="w")

        controls = ttk.Frame(toolbar, style="LanguageBar.TFrame")
        controls.grid(row=0, column=1, sticky="e")
        self._language_label = ttk.Label(
            controls,
            text=tr("Language"),
            style="LanguageLabel.TLabel",
        )
        self._language_label.grid(row=0, column=0, padx=(0, 7))

        self._language_buttons = {}
        for code, label in SUPPORTED_LANGUAGES.items():
            button = ttk.Button(
                controls,
                text=label,
                width=8,
                command=lambda selected=code: self._change_language(selected),
            )
            button.grid(
                row=0,
                column=len(self._language_buttons) + 1,
                padx=(0, 3) if self._language_buttons else 0,
            )
            self._language_buttons[code] = button

        ttk.Separator(toolbar).grid(
            row=1,
            column=0,
            columnspan=2,
            sticky="ew",
            pady=(7, 0),
        )
        self._language_toolbar = toolbar
        self._sync_language_switcher()
        self.bind("<Control-Shift-KeyPress-L>", self._toggle_language_event)
        self.bind("<Control-Shift-KeyPress-l>", self._toggle_language_event)

    def _sync_language_switcher(self) -> None:
        self._language_label.configure(text=tr("Language"))
        active_language = current_language()
        for code, button in self._language_buttons.items():
            style = (
                "LanguageSelected.TButton"
                if code == active_language
                else "Language.TButton"
            )
            button.configure(style=style)

    def _toggle_language(self) -> None:
        self._change_language(alternate_language())

    def _toggle_language_event(self, _event=None):
        self._toggle_language()
        return "break"

    def _task_running(self) -> bool:
        if (
            getattr(self, "_install_running", False)
            or getattr(self, "_generation_running", False)
            or getattr(self, "_repository_running", False)
        ):
            return True
        for name in ("btn_abort_ins", "btn_abort_gen"):
            button = getattr(self, name, None)
            if button is not None:
                try:
                    if "disabled" not in button.state():
                        return True
                except tk.TclError:
                    pass
        return False

    @staticmethod
    def _widget_tree(root):
        pending = list(root.winfo_children())
        while pending:
            widget = pending.pop()
            yield widget
            try:
                pending.extend(widget.winfo_children())
            except tk.TclError:
                pass

    @staticmethod
    def _translated_value(
        current: str,
        sources: dict[str, str],
        *,
        formatted: bool = True,
    ) -> str:
        source = sources.get(current)
        if source is None:
            source = (
                canonical_text(current)
                if formatted
                else canonical_exact_text(current)
            )
        translated = retranslate(source) if formatted else retranslate_exact(source)
        if source != current or translated != current:
            sources[current] = source
            sources[translated] = source
        return translated

    @staticmethod
    def _translated_choice_value(
        current: str,
        sources: dict[str, str],
        choices: tuple[str, ...],
    ) -> str:
        source = sources.get(current)
        if source is None:
            source = canonical_choice(current, choices)
            if source not in choices:
                return current
        translated = tr(source)
        sources[current] = source
        sources[translated] = source
        return translated

    @staticmethod
    def _retranslate_widget_text(widget) -> None:
        try:
            current = str(widget.cget("text"))
        except (AttributeError, tk.TclError):
            return
        sources = getattr(widget, "_language_text_sources", None)
        if sources is None:
            sources = {}
            widget._language_text_sources = sources
        translated = SierraPatcherGUI._translated_value(current, sources)
        if translated != current:
            try:
                widget.configure(text=translated)
            except tk.TclError:
                pass

    @staticmethod
    def _retranslate_notebook(notebook: ttk.Notebook) -> None:
        try:
            tabs = notebook.tabs()
        except tk.TclError:
            return
        tab_sources = getattr(notebook, "_language_tab_sources", None)
        if tab_sources is None:
            tab_sources = {}
            notebook._language_tab_sources = tab_sources
        for tab_id in tabs:
            try:
                current = str(notebook.tab(tab_id, "text"))
                sources = tab_sources.setdefault(str(tab_id), {})
                translated = SierraPatcherGUI._translated_value(
                    current,
                    sources,
                    formatted=False,
                )
                if translated != current:
                    notebook.tab(tab_id, text=translated)
            except tk.TclError:
                continue

    def _retranslate_combobox(self, combobox: ttk.Combobox) -> str | None:
        try:
            raw_values = combobox.cget("values")
            if isinstance(raw_values, str):
                raw_values = combobox.tk.splitlist(raw_values)
            values = tuple(str(value) for value in raw_values)
            sources = getattr(combobox, "_language_value_sources", None)
            if sources is None:
                sources = {}
                combobox._language_value_sources = sources

            fixed_choices: tuple[str, ...] | None = None
            if combobox is getattr(self, "i_web_release", None):
                fixed_choices = ("choose version",)
            elif combobox is getattr(self, "r_release", None):
                fixed_choices = ("choose release",)

            def translate(value: str) -> str:
                if fixed_choices is not None:
                    return self._translated_choice_value(
                        value,
                        sources,
                        fixed_choices,
                    )
                return self._translated_value(
                    value,
                    sources,
                    formatted=False,
                )

            translated_values = tuple(
                translate(value)
                for value in values
            )
            if translated_values != values:
                combobox.configure(values=translated_values)

            current = combobox.get()
            translated = translate(current)
            if translated != current:
                combobox.set(translated)
            variable_name = str(combobox.cget("textvariable"))
            return variable_name or None
        except tk.TclError:
            return None

    def _retranslate_textvariable(
        self,
        widget,
        translated_variables: set[str],
        formatted_variables: set[str],
    ) -> None:
        if not isinstance(widget, (tk.Label, tk.Message, ttk.Label)):
            return
        try:
            variable_name = str(widget.cget("textvariable"))
        except (AttributeError, tk.TclError):
            return
        if not variable_name or variable_name in translated_variables:
            return
        translated_variables.add(variable_name)
        variable_sources = getattr(self, "_language_variable_sources", None)
        if variable_sources is None:
            variable_sources = {}
            self._language_variable_sources = variable_sources
        sources = variable_sources.setdefault(variable_name, {})
        try:
            current = str(self.getvar(variable_name))
            detail_variable = getattr(self, "_detail_var", None)
            if detail_variable is not None and variable_name == str(detail_variable):
                translated = tr_progress(current)
            else:
                translated = self._translated_value(
                    current,
                    sources,
                    formatted=variable_name in formatted_variables,
                )
            if translated != current:
                self.setvar(variable_name, translated)
        except (KeyError, tk.TclError):
            pass

    def _refresh_language(self) -> None:
        status_variables = getattr(self, "_stat", {})
        translated_variables = {
            str(variable)
            for variable in status_variables.values()
        }
        formatted_variables = {
            str(variable)
            for variable in (
                getattr(self, "g_integrity_var", None),
                getattr(self, "r_status_var", None),
            )
            if variable is not None
        }
        for widget in self._widget_tree(self):
            self._retranslate_widget_text(widget)
            if isinstance(widget, ttk.Notebook):
                self._retranslate_notebook(widget)
            if isinstance(widget, ttk.Combobox):
                variable_name = self._retranslate_combobox(widget)
                if variable_name:
                    translated_variables.add(variable_name)
            else:
                self._retranslate_textvariable(
                    widget,
                    translated_variables,
                    formatted_variables,
                )

        destination_var = getattr(self, "i_dest_var", None)
        if destination_var is not None:
            variable_sources = getattr(self, "_language_variable_sources", None)
            if variable_sources is None:
                variable_sources = {}
                self._language_variable_sources = variable_sources
            sources = variable_sources.setdefault(str(destination_var), {})
            try:
                current = destination_var.get()
                translated = self._translated_choice_value(
                    current,
                    sources,
                    ("Select pasted Live folder",),
                )
                if translated != current:
                    destination_var.set(translated)
            except tk.TclError:
                pass

        if status_variables:
            self._refresh_status()

        self._sync_language_switcher()
        self.update_idletasks()

    def _change_language(self, language: str) -> None:
        if language == current_language() or self._language_change_in_progress:
            return
        if self._task_running():
            self._sync_language_switcher()
            messagebox.showwarning(
                tr("Language"),
                tr("A task is running. Wait for it to finish or cancel it before changing the language."),
            )
            return

        previous_language = current_language()
        self._language_change_in_progress = True
        try:
            set_language(language)
            self._refresh_language()
        except Exception as exc:
            set_language(previous_language)
            try:
                self._refresh_language()
            except Exception:
                pass
            messagebox.showerror(
                tr("Language"),
                tr("The language could not be changed:\n{error}", error=exc),
            )
            return
        finally:
            self._language_change_in_progress = False

        try:
            set_language(language, persist=True)
        except OSError as exc:
            messagebox.showwarning(
                tr("Language"),
                tr(
                    "The language changed for this session, but the preference could not be saved:\n{error}",
                    error=exc,
                ),
            )

    # ---------- Shared progress helpers ----------
    def _phase_progress(self, current: int | float, total: int | float, message: str = ""):
        """Set progress bar to an absolute position (current/total) and update detail text."""
        def _do():
            tot = max(1, int(total))
            cur = max(0, min(int(current), tot))
            self._prog_bar.configure(mode="determinate", maximum=tot, value=cur)
            if message:
                self._detail_var.set(tr_progress(message))
            self._prog_bar.update_idletasks()
        _safe_call(self, _do)

    
    def _reset_prog(self, total: int, phase: str):
        def _do():
            self._total_var = max(1, total)
            self._done_var = 0
            self._phase_var.set(tr(phase))
            self._detail_var.set("")
            self._prog_bar.configure(mode="determinate", maximum=self._total_var, value=0)
        _safe_call(self, _do)

    def _step_prog(self, message: str | None = None):
        def _do():
            self._done_var += 1
            self._prog_bar['value'] = self._done_var
            if message:
                self._detail_var.set(tr_progress(message))
            self._prog_bar.update_idletasks()
        _safe_call(self, _do)

    def _set_phase(self, phase: str):
        _safe_call(self, self._phase_var.set, tr(phase))

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

    def _stop_with_message(self, title: str, text: str):
        """Stop the current worker gracefully and show a custom message."""
        self._cancel.set()                         # co-operative stop
        self._set_phase("Stopped")
        self._phase_progress(0, 1, "")             # empty the bar
        _safe_call(self, messagebox.showwarning, tr(title), tr(text))


    # ---------- tab helpers ----------

    def _validate_install_ready(self):
        dst = (self.i_dest_var.get() or "").strip()
        valid = bool(dst and os.path.isdir(dst))
        # Show/hide hint
        if valid:
            self._dest_hint.grid_remove()
        else:
            self._dest_hint.configure(
                text=tr("Destination folder is required.") if not dst else tr("Folder does not exist.")
            )
            self._dest_hint.grid()

        # Button state
        if valid:
            self.btn_install.state(["!disabled"])
        else:
            self.btn_install.state(["disabled"])

    def _status_row(self, parent, row: int, col: int, label: str,
                var: tk.StringVar, kind: str = "text"):
        ttk.Label(parent, text=tr(label), foreground="#666").grid(row=row, column=col, sticky="w", padx=8)

        if kind == "path":
            # Read-only single-line field that can horizontally scroll with caret
            e = ttk.Entry(parent, textvariable=var, state="readonly", takefocus=True, justify="left")
            e.grid(row=row, column=col, sticky="ew", padx=(80, 8))  # leave space after the label
            parent.grid_columnconfigure(col, weight=1)

            # Let users set caret with mouse and scroll with wheel (nice UX)
            def _wheel(ev):
                # Shift+Wheel scrolls faster
                step = -5 if ev.delta > 0 else 5
                e.xview_scroll(step if ev.state & 0x0001 else (1 if step > 0 else -1), "units")
                return "break"
            e.bind("<MouseWheel>", _wheel)            # Windows
            e.bind("<Button-1>", lambda ev: e.icursor("@%d" % ev.x))

            # Quick copy: Ctrl+C (even though it's readonly)
            e.bind("<Control-c>", lambda _e: (self.clipboard_clear(), self.clipboard_append(var.get())))
            # Optional: select all on focus for quick copy
            e.bind("<FocusIn>", lambda _e: e.select_range(0, "end"))
            return

        # default text (wrap short fields if needed)
        ttk.Label(parent, textvariable=var, anchor="w", wraplength=240)\
            .grid(row=row, column=col, sticky="w", padx=(80, 8))


    def _browse_and_refresh(self, entry: ttk.Entry):
        d = filedialog.askdirectory(title=tr("Select folder"))
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
    
    def _update_integrity_label(self):
        self.g_integrity_var.set(
            summarize_integrity_list(self.g_integrity_folders)
        )

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
            cores = tr("{physical} cores / {logical} threads", physical=phys, logical=logi)
        except Exception:
            cores = "—"
        try:
            vm = psutil.virtual_memory()
            ram = tr(
                "{total} total, {available} free",
                total=self._format_bytes(vm.total),
                available=self._format_bytes(vm.available),
            )
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
                self._stat["tk_path"].set(tr("Not found"))
                self._stat["tk_version"].set(tr("not found"))
                self._stat["tk_publisher"].set(tr("not found"))
        except Exception:
            self._stat["tk_path"].set(tr("error"))
            self._stat["tk_version"].set(tr("error"))
            self._stat["tk_publisher"].set(tr("error"))

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
        self.g_diff_profile = tk.StringVar(value=tr("Balanced"))
        diff_box = ttk.Combobox(
            f,
            textvariable=self.g_diff_profile,
            state="readonly",
            values=[tr(label) for label in DIFF_PRESETS],
        )

        self._row(f, 0, "Source (clean game)", self.g_source,
                    browse=lambda: self._browse(self.g_source))
        self._row(f, 1, "Target (SPT installation)", self.g_dest,
                    browse=lambda: self._browse(self.g_dest))
        self._row(f, 2, "Release title", self.g_title)
        self._row(f, 3, "Date", self.g_date)
        self._row(f, 4, "Threads", self.g_threads)
        self._row(f, 5, "Diff aggressiveness", diff_box)

        # --- Integrity check folders ----------------------------------------
        self.g_integrity_folders: list[str] = [] # type: ignore
        self.g_integrity_var = tk.StringVar(value=tr("Tracked folders: (none)"))

        card = ttk.LabelFrame(f, text=tr("Integrity check folders"))
        card.grid(row=6, column=0, columnspan=3, sticky="ew",
                      padx=12, pady=(6, 4))
        card.columnconfigure(0, weight=1)

        ttk.Label(card, textvariable=self.g_integrity_var, anchor="w")\
                .grid(row=0, column=0, columnspan=2, sticky="ew",
                      padx=4, pady=(2, 4))

        def add_folder():
            src = Path(self.g_source.get().strip())
            if not src.is_dir():
                messagebox.showwarning(
                    tr("Source required"),
                    tr("Select a valid Source (clean game) folder first."),
                )
                return
            folder = filedialog.askdirectory(
                initialdir=src,
                title=tr("Choose folder to track (inside Source)"),
            )
            if not folder:
                return
            folder = Path(folder)
            try:
                rel = folder.relative_to(src)
            except ValueError:
                messagebox.showwarning(
                    tr("Invalid folder"),
                    tr("Please choose a folder inside the Source directory."),
                )
                return
            rel_str = str(rel).replace("\\", "/")
            if rel_str not in self.g_integrity_folders:
                self.g_integrity_folders.append(rel_str)
            self._update_integrity_label()

        def clear_folders():
            self.g_integrity_folders.clear()
            self._update_integrity_label()

        ttk.Button(card, text=tr("Add folder..."), command=add_folder)\
            .grid(row=1, column=0, sticky="w", padx=4, pady=(0, 4))
        ttk.Button(card, text=tr("Clear"), command=clear_folders)\
            .grid(row=1, column=1, sticky="w", padx=4, pady=(0, 4))

        # Generate button inside Generate tab
        self.btn_generate = ttk.Button(f, text=tr("Generate patch package"), command=self._run_generate)
        self.btn_generate\
            .grid(row=7, column=0, columnspan=3, pady=(6, 8), padx=12, sticky="w")
        self.btn_abort_gen = ttk.Button(
            f, text=tr("Abort"), command=self._abort_generate, state="disabled"
        )
        self.btn_abort_gen.grid(row=7, column=1, padx=6, pady=(6, 8), sticky="w")
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

        self._row(
            f, 0, "Destination to patch",
            self.i_dest,
            browse=lambda: self._browse_and_refresh(self.i_dest),
            required=True,
        )

        # Small validation hint under destination
        self._dest_hint = ttk.Label(f, text=tr("Destination folder is required."), style="Hint.TLabel")
        self._dest_hint.grid(row=1, column=1, sticky="w", padx=12, pady=(2, 0))
        self._dest_hint.grid_remove()  # start hidden
        self._row(f, 1, "Threads", self.i_threads)

        ttk.Checkbutton(f, text=tr("Force (bypass metadata checks)"), variable=self.i_force)\
            .grid(row=2, column=0, columnspan=2, sticky="w", padx=12)

        
        # Install button: highlighted, disabled until valid
        self.btn_install = ttk.Button(f, text=tr("Install SPT"), style="AccentInstall.TButton", command=self._run_install)
        self.btn_install.state(["!disabled"])
        self.btn_install.grid(row=3, column=0, columnspan=3, pady=(8, 8), padx=12, sticky="w")

        self.btn_abort_ins = ttk.Button(f, text=tr("Abort"), command=self._abort_install, state="disabled")
        self.btn_abort_ins.grid(row=3, column=1, padx=6, pady=(6,8), sticky="w")

        # ---- Status panel -------------------------------------------------------
        card = ttk.LabelFrame(f, text=tr("Status"))
        card.grid(row=4, column=0, columnspan=3, sticky="ew", padx=10, pady=(8, 0))
        card.columnconfigure(0, weight=1)   # System
        card.columnconfigure(1, weight=2)   # Patcher
        card.columnconfigure(2, weight=1)   # Tarkov (wider)
        card.columnconfigure(3, weight=1)   # Destination

        # Section headers
        ttk.Label(card, text=tr("System"), font=("Segoe UI", 10, "bold")).grid(row=0, column=0, sticky="w", padx=8, pady=(8,2))
        ttk.Label(card, text=tr("Patcher"), font=("Segoe UI", 10, "bold")).grid(row=0, column=1, sticky="w", padx=8, pady=(8,2))
        ttk.Label(card, text=tr("Tarkov"),  font=("Segoe UI", 10, "bold")).grid(row=0, column=2, sticky="w", padx=8, pady=(8,2))
        ttk.Label(card, text=tr("Destination"), font=("Segoe UI", 10, "bold")).grid(row=0, column=3, sticky="w", padx=8, pady=(8,2))

        # StringVars
        self._stat = {k: tk.StringVar(value="—") for k in [
            "sys_cpu", "sys_cores", "sys_ram",
            "pat_version", "pat_title", "pat_patches",
            "tk_path", "tk_version", "tk_publisher",
            "dst_free",
        ]}

        # System
        self._status_row(card, 1, 0, "CPU",       self._stat["sys_cpu"], kind="path")
        self._status_row(card, 2, 0, "Cores",     self._stat["sys_cores"])
        self._status_row(card, 3, 0, "Memory",    self._stat["sys_ram"])

        # Patcher
        self._status_row(card, 1, 1, "target client",  self._stat["pat_version"])
        self._status_row(card, 2, 1, "target SPT",   self._stat["pat_title"])
        self._status_row(card, 3, 1, "Patch files", self._stat["pat_patches"])

        # Tarkov
        self._status_row(card, 1, 2, "Path",      self._stat["tk_path"],kind="path")
        self._status_row(card, 2, 2, "Version",   self._stat["tk_version"])
        self._status_row(card, 3, 2, "Publisher", self._stat["tk_publisher"])

        # Destination
        self._status_row(card, 1, 3, "Free",      self._stat["dst_free"])

        # Controls
        btns = ttk.Frame(card)
        btns.grid(row=4, column=0, columnspan=4, sticky="ew", padx=8, pady=(6,8))
        ttk.Button(btns, text=tr("Refresh"), command=self._refresh_status).pack(side="left")
        ttk.Button(btns, text=tr("Open destination"), command=self._open_destination).pack(side="left", padx=6)

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
                with Image.open(logo_path) as source_image:
                    img = source_image.resize((96, 96))
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
            links, text=tr("Patchers"),
            command=lambda: open_url("https://52sierra.net/patcher/")
        ).grid(row=0, column=0, padx=(0, 8))
        ttk.Button(
            links, text="Discord",
            command=lambda: open_url("https://discord.gg/uKMW8PxE8s")
        ).grid(row=0, column=1, padx=(0, 8))
        ttk.Button(
            links, text=tr("Docs"),
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
        site_l = ttk.Label(right_links, text=tr("Homepage"))
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
        about = ttk.LabelFrame(cards, text=tr("About"), padding=12)
        about.grid(row=0, column=0, sticky="nsew", padx=(0, 8), pady=0)

        try:
            from sierra_patcher import __version__ as _VER
        except Exception:
            _VER = "0.1.0"

        ttk.Label(about, text=tr("Version: {version}", version=_VER), foreground="#444").grid(row=0, column=0, sticky="w")
        ttk.Label(about, text=tr("Sierra Installer provides patch generation/application for SPT installations."),
                  foreground="#555").grid(row=1, column=0, sticky="w", pady=(6, 0))

        # Support card (right)
        support = ttk.LabelFrame(cards, text=tr("Support"), padding=12)
        support.grid(row=0, column=1, sticky="nsew", padx=(8, 0), pady=0)

        support_email = "sierra@52sierra.net"
        row = 0
        ttk.Label(support, text=tr("mail address:"), foreground="#444").grid(row=row, column=0, sticky="w"); row += 1

        # clickable email
        mail_l = ttk.Label(support, text=support_email, foreground="#0b62d6", cursor="hand2")
        mail_l.grid(row=row, column=0, sticky="w", pady=(2, 0)); row += 1
        mail_l.bind("<Button-1>", lambda _e: open_url(f"mailto:{support_email}"))
        mail_l.bind("<Enter>",   lambda _e: mail_l.configure(underline=True))
        mail_l.bind("<Leave>",   lambda _e: mail_l.configure(underline=False))

        btns = ttk.Frame(support)
        btns.grid(row=row, column=0, sticky="w", pady=(8, 0)); row += 1
        ttk.Button(btns, text=tr("Copy email"),
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
        # Read-only: users were typing into the log widget and sending those
        # edits to support. A disabled Text still allows selection and Ctrl+C.
        self.log_text = ScrolledText(f, state="disabled", wrap="word")
        self.log_text.grid(row=0, column=0, sticky="nsew", padx=8, pady=2)

        actions = ttk.Frame(f)
        actions.grid(row=1, column=0, sticky="ew", padx=8, pady=(2, 4))
        ttk.Button(actions, text=tr("Save log to file..."), command=self._save_log).pack(side="left")
        ttk.Button(actions, text=tr("Copy log"), command=self._copy_log).pack(side="left", padx=6)
        ttk.Button(actions, text=tr("Open log folder"), command=self._open_log_folder).pack(side="left")

        log_path = session_log().path
        self._log_path_var = tk.StringVar(
            value=str(log_path) if log_path else tr("Log file unavailable (this session only)")
        )
        ttk.Label(f, textvariable=self._log_path_var, foreground="#666", wraplength=900).grid(
            row=2, column=0, sticky="w", padx=8, pady=(0, 6)
        )

        # The on-disk log keeps everything; the widget keeps only the tail so a
        # very large failure list cannot make the Logs tab unusable.
        session_log().add_sink(self._log_sink)
        return f

    def _log_sink(self, line: str) -> None:
        _safe_call(self, self._append_log, line)

    def destroy(self):
        # Each window registers its own bound sink; drop it so a replaced or
        # closed window cannot keep receiving lines.
        try:
            session_log().remove_sink(self._log_sink)
        except Exception:
            pass
        return super().destroy()

    def _append_log(self, msg: str):
        try:
            self.log_text.configure(state="normal")
            self.log_text.insert("end", msg + "\n")

            line_count = int(self.log_text.index("end-1c").split(".")[0])
            if line_count > self._LOG_WIDGET_MAX_LINES:
                self.log_text.delete("1.0", f"{line_count - self._LOG_WIDGET_MAX_LINES}.0")

            self.log_text.see("end")
        except Exception:
            pass
        finally:
            try:
                self.log_text.configure(state="disabled")
            except Exception:
                pass

    def _log(self, *parts):
        session_log().write(" ".join(str(p) for p in parts))

    def _log_exc(self, prefix="Error"):
        tb = "".join(traceback.format_exc())
        session_log().write(f"{prefix}:\n{tb}")

    # ---------- log export ----------

    def _log_contents(self) -> str:
        """Prefer the on-disk log; it retains lines trimmed from the widget."""
        path = session_log().path
        if path is not None:
            try:
                return Path(path).read_text(encoding="utf-8", errors="replace")
            except Exception:
                pass
        try:
            return self.log_text.get("1.0", "end-1c")
        except Exception:
            return ""

    def _save_log(self):
        contents = self._log_contents()
        if not contents.strip():
            messagebox.showinfo(tr("Logs"), tr("There is nothing to save yet."))
            return
        target = filedialog.asksaveasfilename(
            title=tr("Save log to file"),
            defaultextension=".txt",
            initialfile=f"sierra-log-{_dt.datetime.now().strftime('%Y%m%d-%H%M%S')}.txt",
            filetypes=[("Text files", "*.txt"), ("Log files", "*.log"), ("All files", "*.*")],
        )
        if not target:
            return
        try:
            Path(target).write_text(contents, encoding="utf-8")
        except Exception as exc:
            messagebox.showerror(tr("Logs"), tr("Could not save the log:\n{error}", error=exc))
            return
        messagebox.showinfo(tr("Logs"), tr("Log saved to:\n{path}", path=target))

    def _copy_log(self):
        contents = self._log_contents()
        if not contents.strip():
            messagebox.showinfo(tr("Logs"), tr("There is nothing to copy yet."))
            return
        copy_to_clipboard(self, contents)

    def _open_log_folder(self):
        path = session_log().path
        if path is None:
            messagebox.showinfo(tr("Logs"), tr("No log file could be created for this session."))
            return
        try:
            os.startfile(str(Path(path).parent))  # Windows
        except Exception:
            pass

    def _log_install_header(self, **overrides) -> None:
        """Record everything support currently has to ask the user for by hand."""

        def value(name, default="—"):
            try:
                attribute = getattr(self, name, None)
                return default if attribute is None else attribute.get()
            except Exception:
                return default

        destination = overrides.pop("destination", None) or value("i_dest_var", "")
        fields = {
            "Source mode": value("i_source_var"),
            "Release": value("i_web_release_var", "—"),
            "Destination": destination or "—",
            "Destination free": free_space(destination) if destination else "—",
            "Archived snapshot": value("i_archive_path_var", "—") or "—",
            "Cache directory": value("i_web_cache"),
            "Force enabled": value("i_force", False),
            "Patch workers": value("i_threads"),
            "Download workers": value("i_download_workers"),
            "Reconstruction workers": value("i_materialize_workers"),
        }
        fields.update(overrides)
        session_log().write_section("Install run", fields)


    def _row(self, parent, r, label, entry_widget, browse=None, required=False):
        # label with optional red asterisk
        lbl_text = tr(f"{label}")
        lbl = ttk.Label(parent, text=lbl_text)
        lbl.grid(row=r, column=0, sticky="w", padx=12, pady=(6, 0))
        if required:
            # add a red asterisk right next to the label
            tk.Label(parent, text=" *", fg="#b00020").grid(row=r, column=0, sticky="e", padx=(0, 0), pady=(6, 0))

        entry_widget.grid(row=r, column=1, sticky="ew", padx=12, pady=(6, 0))
        if browse:
            ttk.Button(parent, text=tr("Browse"), command=browse).grid(row=r, column=2, padx=6, pady=(6, 0))
        return lbl

    def _browse(self, entry: ttk.Entry):
        d = filedialog.askdirectory(title=tr("Select folder"))
        if d:
            entry.delete(0, tk.END)
            entry.insert(0, d)

    def _show_dependency_prompt(self, meta: Meta, missing) -> bool:
        result = {"continue": False}
        win = tk.Toplevel(self)
        win.title(tr(".NET Dependencies"))
        win.resizable(False, False)
        win.transient(self)
        win.grab_set()

        release = meta.title or tr("this patch")
        ttk.Label(
            win,
            text=tr("{release} needs additional Microsoft .NET components.", release=release),
            font=("Segoe UI", 10, "bold"),
        ).grid(row=0, column=0, columnspan=4, sticky="w", padx=12, pady=(12, 4))
        ttk.Label(
            win,
            text=tr("Install these from Microsoft, then press Install again. You can continue if you have already installed them elsewhere."),
            wraplength=560,
        ).grid(row=1, column=0, columnspan=4, sticky="w", padx=12, pady=(0, 8))

        text = ScrolledText(win, height=9, width=78, wrap="word")
        text.grid(row=2, column=0, columnspan=4, sticky="ew", padx=12, pady=(0, 8))
        text.insert("1.0", format_missing_requirements(missing))
        text.configure(state="disabled")

        def open_all():
            for req in missing:
                webbrowser.open(req.download_url)

        def copy_links():
            links = "\n".join(req.download_url for req in missing)
            copy_to_clipboard(self, links, toast=False)

        def continue_install():
            result["continue"] = True
            win.destroy()

        ttk.Button(win, text=tr("Open links"), command=open_all).grid(row=3, column=0, padx=(12, 6), pady=(0, 12), sticky="w")
        ttk.Button(win, text=tr("Copy links"), command=copy_links).grid(row=3, column=1, padx=6, pady=(0, 12), sticky="w")
        ttk.Button(win, text=tr("Continue anyway"), command=continue_install).grid(row=3, column=2, padx=6, pady=(0, 12), sticky="e")
        ttk.Button(win, text=tr("Cancel"), command=win.destroy).grid(row=3, column=3, padx=(6, 12), pady=(0, 12), sticky="e")

        win.update_idletasks()
        x = self.winfo_rootx() + max(0, (self.winfo_width() - win.winfo_width()) // 2)
        y = self.winfo_rooty() + max(0, (self.winfo_height() - win.winfo_height()) // 2)
        win.geometry(f"+{x}+{y}")
        self.wait_window(win)
        return result["continue"]

    # ---------- Action handlers ----------
    def _run_generate(self):
        self._cancel = threading.Event()
        self.btn_abort_gen.state(["!disabled"])
        src = self.g_source.get().strip()
        dst = self.g_dest.get().strip()
        title = self.g_title.get().strip() or ""
        date = self.g_date.get().strip() or _dt.date.today().isoformat()
        threads = int(self.g_threads.get())
        profile_label = canonical_choice(
            getattr(self, "g_diff_profile", tk.StringVar(value=tr("Balanced"))).get(),
            DIFF_PRESETS,
        )
        diff_args = DIFF_PRESETS.get(profile_label, DIFF_PRESETS["Balanced"])
        if not src or not dst:
            messagebox.showerror(
                tr("Missing folders"),
                tr("Please set Source and Target folders."),
            )
            return
        check_resources()

        # Pre-compute totals: files in dest + 4 post steps
        total_files = count_dest_files(dst)
        extra_steps = 4  # pack_additional, build_delete_list, stamp_metadata, verify
        self._reset_prog(total_files + extra_steps, "Generating patches")

        def on_progress(phase, current, total, message):
            _safe_call(self, self._detail_var.set, tr_progress(message))
            # keep progress determinate by mapping current → value without touching Tk from worker
            def _do():
                self._prog_bar['value'] = min(self._total_var, self._done_var + current)
            _safe_call(self, _do)

        def worker():
            try:
                for d in (OUTPUT_DIR, PATCH_out_DIR, MISSING_out_DIR, STORAGE_out_DIR):
                    os.makedirs(d, exist_ok=True)

                self._log("[generate] start")
                from .paths import ZSTD_EXE
                if not os.path.isfile(ZSTD_EXE):
                    raise RuntimeError(f"zstd not found at: {ZSTD_EXE}")

                # Phase 1: generate patches (zstd)
                total_files = count_dest_files(dst)
                self._set_phase("Generating patches")
                self._reset_prog(total_files, "Generating patches")
                generate_patches(
                    src, dst, PATCH_out_DIR, MISSING_out_DIR,
                    workers=threads,
                    zstd_args=diff_args,
                    on_progress=lambda _p, i, n, msg: self._phase_progress(i, n, msg),
                    cancel_event=self._cancel,
                    use_tqdm=False,
                )
                if self._cancel.is_set():
                    self._set_phase("Cancelled"); self._log("[generate] cancelled"); return

                # Phase 2: pack additional files (7-Zip %)
                self._set_phase("Packing additional files")
                self._reset_prog(100, "Packing additional files")
                pack_additional(
                    MISSING_out_DIR, STORAGE_out_DIR,
                    cancel_event=self._cancel,
                    on_progress=lambda _p, cur, tot, msg: self._phase_progress(cur, tot, msg),
                )

                # Phase 3: build delete list (single step)
                self._set_phase("Building delete list")
                self._reset_prog(1, "Building delete list")
                build_delete_list(src, dst, os.path.join(STORAGE_out_DIR, "delete_list.txt"))
                self._phase_progress(1, 1, "delete list written")

                # Phase 4: stamp metadata (single step)
                self._set_phase("Stamping metadata")
                self._reset_prog(1, "Stamping metadata")

                # Build integrity_folders mapping: { "relative/path": size_in_bytes }
                src_path = Path(src)
                integrity: dict[str, int] = {}
                for rel in getattr(self, "g_integrity_folders", []):
                    integrity[rel] = folder_size(src_path / rel)

                stamp_from_game_exe(
                    os.path.join(STORAGE_out_DIR, "metadata.info"),
                    src,
                    title,
                    date,
                    integrity_folders=integrity,
                    diff_profile=profile_label,
                    zstd_patch_args=diff_args,
                )
                self._phase_progress(1, 1, "metadata stamped")

                # Phase 5: verify patches (absolute count)
                total_patches = count_patch_files()
                self._set_phase("Verifying patches")
                self._reset_prog(total_patches, "Verifying patches")
                verify_patch_files(
                    cancel_event=self._cancel,
                    on_progress=lambda _p, i, n, msg: self._phase_progress(i, n, msg),
                )

                # Finalize: copy self & rename
                copy_self_to_output(OUTPUT_DIR, self._log)
                live_exe = os.path.join(src, "EscapeFromTarkov.exe")
                final_dir = rename_output_folder(OUTPUT_DIR, spt_version=title, live_client_exe=live_exe, log=self._log) or OUTPUT_DIR

                self._set_phase("Done")
                self._log("[generate] done")
                _safe_call(
                    self,
                    messagebox.showinfo,
                    tr("Generate"),
                    tr("Patch package ready in:\n{path}", path=final_dir),
                )

            except proc.Cancelled:
                self._phase_progress(1, 1, "Cancelled")
                self._log("[generate] cancelled by user")
            except Exception:
                if self._cancel.is_set():
                    self._set_phase("Cancelled"); self._log("[generate] cancelled during operation"); return
                self._log_exc("[generate] failed")
                _safe_call(
                    self,
                    messagebox.showerror,
                    tr("Generate"),
                    tr("Generation failed. See Logs for details."),
                )
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
        if not dst:
            messagebox.showerror(
                tr("Missing folder"),
                tr("Please set Destination folder."),
            )
            self.btn_abort_ins.state(["disabled"])
            return
        try:
            meta_for_deps = Meta.read(STORAGE_read_DIR)
            missing_deps = missing_requirements_for_metadata(meta_for_deps)
        except Exception as e:
            messagebox.showerror(
                tr("Metadata error"),
                tr("Could not read patch metadata:\n{error}", error=e),
            )
            self.btn_abort_ins.state(["disabled"])
            return
        if missing_deps and not self._show_dependency_prompt(meta_for_deps, missing_deps):
            self._set_phase("Stopped")
            self.btn_abort_ins.state(["disabled"])
            return
        check_resources()

        total_patches = count_patch_files()
        # extra: finalize + apply_storage (+1 each)
        extra_steps = 2
        self._reset_prog(total_patches + extra_steps, "Applying patches")

        def on_progress(phase, current, total, message):
            _safe_call(self, self._detail_var.set, tr_progress(message))
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
                inst = query_install()
                if not inst:
                    raise RuntimeError("Tarkov installation not found (registry).")

                if not force:
                    exe = os.path.join(inst["install_path"], "EscapeFromTarkov.exe")
                    ver_now = exe_version(exe) or "-"
                    if meta.version and ver_now != meta.version:
                        msg = (
                            "Version mismatch detected.\n\n"
                            f"Live client: {ver_now}\n"
                            f"Expected:    {meta.version}\n\n"
                            "Please select the correct Tarkov folder or enable "
                            "'Force (bypass metadata checks)' if you know what you're doing."
                        )
                        self._log(f"[install] stopped: version mismatch (live={ver_now}, expected={meta.version})")
                        self._stop_with_message("Version mismatch", msg)
                        return  # ← important: exit worker without throwing

                # --- Integrity folders size check (if present in metadata) ---
                integrity = getattr(meta, "integrity_folders", None) or {}
                if integrity and not force:
                    dst_path = Path(dst)
                    mismatches: list[tuple[str, int, int]] = []

                    for rel, expected_size in integrity.items():
                        actual_size = folder_size(dst_path / rel)
                        if actual_size != expected_size:
                            mismatches.append((rel, expected_size, actual_size))

                    if mismatches:
                        lines = []
                        for rel, exp, act in mismatches:
                            def _fmt(n: int) -> str:
                                return f"{n / (1024**3):.2f} GiB ({n:,} bytes)"
                            lines.append(
                                f"{rel}:\n"
                                f"  expected: {_fmt(exp)}\n"
                                f"  found:    {_fmt(act)}"
                            )
                        msg = (
                            "The selected folder does not match the original source used to "
                            "build this patch.\n\n"
                            "One or more tracked subfolders differ in size:\n\n"
                            + "\n\n".join(lines)
                            + "\n\nThis usually means the live client version or contents are "
                            "different from what this patch expects. Please repair/update your "
                            "client and try again, or use Force only if you know what you're doing."
                        )
                        self._log("[install] stopped: integrity folder mismatch")
                        self._stop_with_message("Folder contents mismatch", msg)
                        return


                # Phase 1: apply patches (absolute count)
                total_patches = count_patch_files()
                self._set_phase("Applying patches")
                self._reset_prog(total_patches, "Applying patches")
                apply_all_patches(
                    dst,
                    workers=threads,
                    on_progress=lambda _p, i, n, msg: self._phase_progress(i, n, msg),
                    cancel_event=self._cancel,
                    use_tqdm=False,
                )
                if self._cancel.is_set():
                    self._set_phase("Cancelled"); self._log("[install] cancelled"); return

                # Phase 2: finalize (delete list, single step)
                self._set_phase("Finalizing (delete list)")
                self._reset_prog(1, "Finalizing")
                finalize(dst, os.path.join(STORAGE_read_DIR, "delete_list.txt"))
                self._phase_progress(1, 1, "cleanup done")

                # Phase 3: apply storage (7-Zip %)
                self._set_phase("Applying storage")
                self._reset_prog(100, "Applying storage")
                apply_storage(
                    STORAGE_read_DIR, dst,
                    cancel_event=self._cancel,
                    on_progress=lambda _p, cur, tot, msg: self._phase_progress(cur, tot, msg),
                )

                self._set_phase("Done")
                self._log("[install] done")
                _safe_call(
                    self,
                    messagebox.showinfo,
                    tr("Install"),
                    tr("Patch applied successfully."),
                )
            except proc.Cancelled:
                self._phase_progress(1, 1, "Cancelled")
                self._log("[install] cancelled by user")
            except Exception:
                if self._cancel.is_set():
                    self._set_phase("Cancelled"); self._log("[install] cancelled during operation"); return
                self._log_exc("[install] failed")
                _safe_call(
                    self,
                    messagebox.showerror,
                    tr("Install"),
                    tr("Install failed. See Logs for details."),
                )
            finally:
                proc.kill_all()
                _safe_call(self, self.btn_abort_ins.state, ["disabled"])


        threading.Thread(target=worker, daemon=True).start()

def main(dev: bool = False):
    _hide_console_on_windows()
    app = SierraPatcherGUI(dev=dev)
    app.mainloop()
