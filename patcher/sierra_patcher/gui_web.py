from __future__ import annotations

import datetime as _dt
import os
import shutil
import threading
import time
from pathlib import Path
import tkinter as tk
from tkinter import filedialog, messagebox, ttk

from . import proc
from .delete_list import build_delete_list, finalize
from .gui import SierraPatcherGUI, _hide_console_on_windows, _safe_call
from .i18n import canonical_choice, localized_choices, tr, tr_progress
from .metadata import Meta, stamp_from_game_exe
from .package_source import LocalPackageSource, WebPackageSource
from .patch_audit import audit_patch_files
from .paths import (
    MISSING_out_DIR,
    OUTPUT_DIR,
    PATCH_out_DIR,
    PATCH_read_DIR,
    STORAGE_out_DIR,
    STORAGE_read_DIR,
    WORKING_DIR,
)
from .prereqs import missing_requirements_for_metadata
from .registry import exe_version, query_install
from .storage import apply_storage, pack_additional
from .system import check_resources, optimal_threads
from .utils import copy_self_to_output, folder_size, rename_output_folder, summarize_integrity_list
from .web_delivery import DEFAULT_CHUNK_SIZE, DEFAULT_PUBLISH_WORKERS, publish_web_package
from .web_download import DEFAULT_DOWNLOAD_WORKERS, DEFAULT_MATERIALIZE_WORKERS
from .zstd_patch import apply_all_patches, count_dest_files, count_patch_files, generate_patches


DELIVERY_MODES = ("Standalone", "Web delivery", "Both")
PACKAGE_SOURCES = ("Local package", "Web release")


class IntegratedSierraPatcherGUI(SierraPatcherGUI):
    """Existing Sierra GUI with modular local/web package delivery."""

    def __init__(self, dev: bool = False):
        # This guard is independent from the button state. It prevents queued
        # double-clicks or future programmatic calls from starting overlapping
        # workers that would mutate the same destination/cache concurrently.
        self._install_running = False
        super().__init__(dev=dev)
        self.geometry("980x680")
        for child in self.winfo_children():
            if isinstance(child, ttk.Notebook):
                child.configure(height=500)

    @staticmethod
    def _positive_int(value: str, label: str, maximum: int = 64) -> int:
        try:
            result = int(value)
        except (TypeError, ValueError) as exc:
            raise ValueError(
                tr("{label} must be a whole number", label=tr(label))
            ) from exc
        if result < 1 or result > maximum:
            raise ValueError(
                tr(
                    "{label} must be between 1 and {maximum}",
                    label=tr(label),
                    maximum=maximum,
                )
            )
        return result

    def _begin_install_run(self) -> bool:
        """Atomically claim the install action and show immediate feedback."""
        if self._install_running:
            self._log("[install] duplicate start ignored: installation already running")
            return False

        self._install_running = True
        self.btn_install.state(["disabled"])
        self.btn_abort_ins.state(["!disabled"])

        # _run_install executes on Tk's main thread, so update these directly.
        # This removes the visible Idle gap before slower package preparation.
        self._phase_var.set(tr("Preparing installation"))
        self._detail_var.set(tr("Validating package..."))
        self._prog_bar.configure(mode="determinate", maximum=1, value=0)
        self.update_idletasks()
        return True

    def _finish_install_run(self) -> None:
        """Release the install guard and restore controls from current inputs."""
        self._install_running = False
        self.btn_abort_ins.state(["disabled"])
        self._validate_install_ready()

    def _threadsafe_dialog(self, callback, *args):
        done = threading.Event()
        result = {"value": None, "error": None}

        def invoke():
            try:
                result["value"] = callback(*args)
            except Exception as exc:
                result["error"] = exc
            finally:
                done.set()

        self.after(0, invoke)
        while not done.wait(0.1):
            if self._cancel.is_set():
                return False
        if result["error"] is not None:
            raise result["error"]
        return result["value"]

    def _web_progress_callback(self):
        phase_labels = {
            "web:manifest": "Fetching manifest",
            "web:objects": "Downloading objects",
            "web:materialize": "Reconstructing package",
            "web:publish": "Publishing web package",
            "archive:objects": "Verifying archived objects",
            "archive:resume": "Resuming archived snapshot",
        }
        lock = threading.Lock()
        state = {"phase": None, "time": 0.0}

        def callback(phase, current, total, message):
            now = time.monotonic()
            with lock:
                phase_changed = phase != state["phase"]
                finished = int(current) >= max(1, int(total))
                if not phase_changed and not finished and now - state["time"] < 0.10:
                    return
                state["phase"] = phase
                state["time"] = now

            label = phase_labels.get(phase, phase)
            detail = tr_progress(message or "")
            if phase == "web:objects":
                mib = 1024 * 1024
                detail = f"{current / mib:,.1f} / {max(total, 1) / mib:,.1f} MiB — {detail}"
            self._set_phase(label)
            self._phase_progress(current, total, detail)

        return callback

    def _browse_entry(self, entry: ttk.Entry, title: str = "Select folder"):
        chosen = filedialog.askdirectory(title=tr(title))
        if chosen:
            entry.configure(state="normal")
            entry.delete(0, tk.END)
            entry.insert(0, chosen)

    def _build_generate_tab(self, nb) -> ttk.Frame:
        root = ttk.Frame(nb)
        root.columnconfigure(0, weight=1)
        root.columnconfigure(1, weight=1)

        package = ttk.LabelFrame(root, text=tr("Patch package"))
        package.grid(row=0, column=0, sticky="nsew", padx=(10, 5), pady=8)
        package.columnconfigure(1, weight=1)

        self.g_source = ttk.Entry(package)
        self.g_dest = ttk.Entry(package)
        self.g_title = ttk.Entry(package)
        self.g_date = ttk.Entry(package)
        self.g_date.insert(0, _dt.date.today().isoformat())
        self.g_threads = ttk.Spinbox(package, from_=1, to=64)
        self.g_threads.delete(0, tk.END)
        self.g_threads.insert(0, str(optimal_threads()))
        self.g_diff_profile = tk.StringVar(value=tr("Balanced"))
        diff_box = ttk.Combobox(
            package,
            textvariable=self.g_diff_profile,
            state="readonly",
            values=localized_choices(self._diff_presets().keys()),
        )

        self._row(package, 0, "Source (clean game)", self.g_source, browse=lambda: self._browse_entry(self.g_source))
        self._row(package, 1, "Target (SPT)", self.g_dest, browse=lambda: self._browse_entry(self.g_dest))
        self._row(package, 2, "Release title", self.g_title)
        self._row(package, 3, "Date", self.g_date)
        self._row(package, 4, "Patch threads", self.g_threads)
        self._row(package, 5, "Diff aggressiveness", diff_box)

        integrity = ttk.LabelFrame(package, text=tr("Integrity check folders"))
        integrity.grid(row=6, column=0, columnspan=3, sticky="ew", padx=12, pady=(8, 6))
        integrity.columnconfigure(0, weight=1)
        self.g_integrity_folders: list[str] = []
        self.g_integrity_var = tk.StringVar(value=tr("Tracked folders: (none)"))
        ttk.Label(integrity, textvariable=self.g_integrity_var).grid(
            row=0, column=0, columnspan=2, sticky="ew", padx=6, pady=(4, 2)
        )

        def add_integrity_folder():
            source = Path(self.g_source.get().strip())
            if not source.is_dir():
                messagebox.showwarning(
                    tr("Source required"),
                    tr("Select a valid clean Source folder first."),
                )
                return
            folder = filedialog.askdirectory(initialdir=source, title=tr("Choose folder inside Source"))
            if not folder:
                return
            try:
                relative = Path(folder).relative_to(source).as_posix()
            except ValueError:
                messagebox.showwarning(
                    tr("Invalid folder"),
                    tr("Choose a folder inside the Source directory."),
                )
                return
            if relative not in self.g_integrity_folders:
                self.g_integrity_folders.append(relative)
            self.g_integrity_var.set(summarize_integrity_list(self.g_integrity_folders))

        ttk.Button(integrity, text=tr("Add folder..."), command=add_integrity_folder).grid(
            row=1, column=0, sticky="w", padx=6, pady=(2, 6)
        )
        ttk.Button(
            integrity,
            text=tr("Clear"),
            command=lambda: (
                self.g_integrity_folders.clear(),
                self.g_integrity_var.set(tr("Tracked folders: (none)")),
            ),
        ).grid(row=1, column=1, sticky="w", padx=6, pady=(2, 6))

        delivery = ttk.LabelFrame(root, text=tr("Delivery"))
        self.g_delivery_frame = delivery
        delivery.grid(row=0, column=1, sticky="nsew", padx=(5, 10), pady=8)
        delivery.columnconfigure(1, weight=1)

        self.g_delivery_var = tk.StringVar(value=tr("Standalone"))
        delivery_box = ttk.Combobox(
            delivery,
            textvariable=self.g_delivery_var,
            state="readonly",
            values=localized_choices(DELIVERY_MODES),
        )
        self._row(delivery, 0, "Delivery mode", delivery_box)

        self.g_package_id = ttk.Entry(delivery)
        self.g_web_repo = ttk.Entry(delivery)
        self.g_web_repo.insert(0, str(Path(WORKING_DIR) / "web_repo_output"))
        self.g_chunk_size = ttk.Spinbox(delivery, from_=1, to=512)
        self.g_chunk_size.delete(0, tk.END)
        self.g_chunk_size.insert(0, str(DEFAULT_CHUNK_SIZE // (1024 * 1024)))
        self.g_publish_workers = ttk.Spinbox(delivery, from_=1, to=32)
        self.g_publish_workers.delete(0, tk.END)
        self.g_publish_workers.insert(0, str(DEFAULT_PUBLISH_WORKERS))

        self._row(delivery, 1, "Package ID", self.g_package_id)
        self._row(
            delivery,
            2,
            "Repository output",
            self.g_web_repo,
            browse=lambda: self._browse_entry(self.g_web_repo, "Select web repository output"),
        )
        self._row(delivery, 3, "Chunk size (MiB)", self.g_chunk_size)
        self._row(delivery, 4, "Publishing workers", self.g_publish_workers)

        ttk.Label(
            delivery,
            text=tr(
                "Web output uses releases/<ID>/manifest.json and a shared objects/ tree. "
                "Upload objects first and the manifest last."
            ),
            wraplength=390,
            foreground="#555",
        ).grid(row=5, column=0, columnspan=3, sticky="w", padx=12, pady=(10, 6))

        self._web_generate_widgets = [
            self.g_package_id,
            self.g_web_repo,
            self.g_chunk_size,
            self.g_publish_workers,
        ]
        delivery_box.bind("<<ComboboxSelected>>", lambda _event: self._toggle_generate_web_options())
        self._toggle_generate_web_options()

        actions = ttk.Frame(root)
        actions.grid(row=1, column=0, columnspan=2, sticky="ew", padx=10, pady=(0, 8))
        self.btn_generate = ttk.Button(
            actions,
            text=tr("Generate patch package"),
            command=self._run_generate,
        )
        self.btn_generate.pack(side="left")
        self.btn_abort_gen = ttk.Button(
            actions,
            text=tr("Abort"),
            command=self._abort_generate,
            state="disabled",
        )
        self.btn_abort_gen.pack(side="left", padx=8)
        return root

    @staticmethod
    def _diff_presets():
        from .gui import DIFF_PRESETS
        return DIFF_PRESETS

    def _toggle_generate_web_options(self):
        enabled = canonical_choice(self.g_delivery_var.get(), DELIVERY_MODES) in (
            "Web delivery",
            "Both",
        )
        for widget in self._web_generate_widgets:
            widget.configure(state="normal" if enabled else "disabled")
        if enabled and not self.g_package_id.get().strip() and self.g_title.get().strip():
            self.g_package_id.insert(0, self.g_title.get().strip())

    def _build_install_tab(self, nb) -> ttk.Frame:
        root = ttk.Frame(nb)
        root.columnconfigure(0, weight=1)
        root.columnconfigure(1, weight=1)

        source = ttk.LabelFrame(root, text=tr("Package source"))
        source.grid(row=0, column=0, sticky="nsew", padx=(10, 5), pady=8)
        source.columnconfigure(1, weight=1)

        self.i_source_var = tk.StringVar(value=tr("Local package"))
        source_box = ttk.Combobox(
            source,
            textvariable=self.i_source_var,
            state="readonly",
            values=localized_choices(PACKAGE_SOURCES),
        )
        self.i_source_label = self._row(source, 0, "Source", source_box)

        self.i_web_release = ttk.Entry(source)
        self.i_web_cache = ttk.Entry(source)
        self.i_web_cache.insert(0, str(Path(WORKING_DIR) / "web_cache"))
        self.i_download_workers = ttk.Spinbox(source, from_=1, to=64)
        self.i_download_workers.delete(0, tk.END)
        self.i_download_workers.insert(0, str(DEFAULT_DOWNLOAD_WORKERS))
        self.i_materialize_workers = ttk.Spinbox(source, from_=1, to=32)
        self.i_materialize_workers.delete(0, tk.END)
        self.i_materialize_workers.insert(0, str(DEFAULT_MATERIALIZE_WORKERS))

        self.i_release_label = self._row(source, 1, "Release ID", self.i_web_release)
        self._row(
            source,
            2,
            "Cache directory",
            self.i_web_cache,
            browse=lambda: self._browse_entry(self.i_web_cache, "Select web package cache"),
        )
        self._row(source, 3, "Download workers", self.i_download_workers)
        self._row(source, 4, "Reconstruction workers", self.i_materialize_workers)
        ttk.Label(
            source,
            text=tr("Verified objects and completed package files are retained for resume/reuse."),
            wraplength=390,
            foreground="#555",
        ).grid(row=5, column=0, columnspan=3, sticky="w", padx=12, pady=(8, 6))

        self._web_install_widgets = [
            self.i_web_release,
            self.i_web_cache,
            self.i_download_workers,
            self.i_materialize_workers,
        ]
        source_box.bind("<<ComboboxSelected>>", lambda _event: self._toggle_install_web_options())

        target = ttk.LabelFrame(root, text=tr("Installation"))
        target.grid(row=0, column=1, sticky="nsew", padx=(5, 10), pady=8)
        target.columnconfigure(1, weight=1)

        self.i_dest_var = tk.StringVar()
        self.i_dest = ttk.Entry(target, textvariable=self.i_dest_var)
        self.i_threads = ttk.Spinbox(target, from_=1, to=64)
        self.i_threads.delete(0, tk.END)
        self.i_threads.insert(0, str(optimal_threads()))
        self.i_force = tk.BooleanVar(value=False)

        self.i_destination_label = self._row(
            target,
            0,
            "Destination to patch",
            self.i_dest,
            browse=lambda: self._browse_and_refresh(self.i_dest),
            required=True,
        )
        self._dest_hint = ttk.Label(
            target,
            text=tr("Destination folder is required."),
            style="Hint.TLabel",
        )
        self._dest_hint.grid(row=1, column=1, sticky="w", padx=12)
        self._row(target, 2, "Patch threads", self.i_threads)
        ttk.Checkbutton(
            target,
            text=tr("Force (bypass metadata checks)"),
            variable=self.i_force,
        ).grid(row=3, column=0, columnspan=3, sticky="w", padx=12, pady=(8, 0))

        actions = ttk.Frame(target)
        actions.grid(row=4, column=0, columnspan=3, sticky="w", padx=12, pady=(14, 8))
        self.btn_install = ttk.Button(
            actions,
            text=tr("Install SPT"),
            style="AccentInstall.TButton",
            command=self._run_install,
        )
        self.btn_install.pack(side="left")
        self.btn_abort_ins = ttk.Button(
            actions,
            text=tr("Abort"),
            command=self._abort_install,
            state="disabled",
        )
        self.btn_abort_ins.pack(side="left", padx=8)

        card = ttk.LabelFrame(root, text=tr("Status"))
        card.grid(row=1, column=0, columnspan=2, sticky="ew", padx=10, pady=(0, 8))
        for column in range(4):
            card.columnconfigure(column, weight=1)
        for column, title in enumerate(("System", "Patcher", "Live Tarkov Folder", "Destination")):
            ttk.Label(card, text=tr(title), font=("Segoe UI", 10, "bold")).grid(
                row=0, column=column, sticky="w", padx=8, pady=(8, 2)
            )

        self._stat = {key: tk.StringVar(value="—") for key in [
            "sys_cpu", "sys_cores", "sys_ram", "pat_version", "pat_title",
            "pat_patches", "tk_path", "tk_version", "tk_publisher", "dst_free",
        ]}
        self._status_row(card, 1, 0, "CPU", self._stat["sys_cpu"], kind="path")
        self._status_row(card, 2, 0, "Cores", self._stat["sys_cores"])
        self._status_row(card, 3, 0, "Memory", self._stat["sys_ram"])
        self._status_row(card, 1, 1, "Client", self._stat["pat_version"])
        self._status_row(card, 2, 1, "Release", self._stat["pat_title"])
        self._status_row(card, 3, 1, "Patches", self._stat["pat_patches"])
        self._status_row(card, 1, 2, "Path", self._stat["tk_path"], kind="path")
        self._status_row(card, 2, 2, "Version", self._stat["tk_version"])
        self._status_row(card, 3, 2, "Publisher", self._stat["tk_publisher"])
        self._status_row(card, 1, 3, "Free", self._stat["dst_free"])
        ttk.Button(card, text=tr("Refresh"), command=self._refresh_status).grid(
            row=4, column=0, sticky="w", padx=8, pady=(6, 8)
        )
        ttk.Button(card, text=tr("Open destination"), command=self._open_destination).grid(
            row=4, column=1, sticky="w", padx=8, pady=(6, 8)
        )

        self.i_dest_var.trace_add("write", lambda *_: self._validate_install_ready())
        self.i_web_release.bind("<KeyRelease>", lambda _event: self._validate_install_ready())
        self._toggle_install_web_options()
        self._refresh_status()
        self._validate_install_ready()
        return root

    def _toggle_install_web_options(self):
        enabled = canonical_choice(self.i_source_var.get(), PACKAGE_SOURCES) == "Web release"
        for widget in self._web_install_widgets:
            widget.configure(state="normal" if enabled else "disabled")
        self._refresh_status()
        self._validate_install_ready()

    def _validate_install_ready(self):
        destination = (self.i_dest_var.get() or "").strip()
        valid_destination = bool(destination and os.path.isdir(destination))
        valid_source = True
        source_value = getattr(
            self,
            "i_source_var",
            tk.StringVar(value=tr("Local package")),
        ).get()
        if canonical_choice(source_value, PACKAGE_SOURCES) == "Web release":
            valid_source = bool(self.i_web_release.get().strip())

        if valid_destination:
            self._dest_hint.grid_remove()
        else:
            self._dest_hint.configure(
                text=(
                    tr("Destination folder is required.")
                    if not destination
                    else tr("Folder does not exist.")
                )
            )
            self._dest_hint.grid()

        if getattr(self, "_install_running", False):
            self.btn_install.state(["disabled"])
        elif valid_destination and valid_source:
            self.btn_install.state(["!disabled"])
        else:
            self.btn_install.state(["disabled"])

    def _refresh_status(self):
        super()._refresh_status()
        if (
            getattr(self, "i_source_var", None)
            and canonical_choice(self.i_source_var.get(), PACKAGE_SOURCES) == "Web release"
        ):
            release = self.i_web_release.get().strip()
            cache = Path(self.i_web_cache.get().strip() or (Path(WORKING_DIR) / "web_cache"))
            package_root = cache / "packages" / release if release else None
            storage_root = package_root / "storage" if package_root else None
            patch_root = package_root / "patchfiles" if package_root else None
            if storage_root and storage_root.is_dir():
                try:
                    meta = Meta.read(storage_root)
                    self._stat["pat_version"].set(meta.version or "—")
                    self._stat["pat_title"].set(meta.title or release)
                    self._stat["pat_patches"].set(str(count_patch_files(patch_root)))
                    return
                except Exception:
                    pass
            self._stat["pat_version"].set("—")
            self._stat["pat_title"].set(
                tr("Web release: {release}", release=release)
                if release
                else tr("Select release")
            )
            self._stat["pat_patches"].set(tr("Not prepared"))

    def _run_generate(self):
        src = self.g_source.get().strip()
        dst = self.g_dest.get().strip()
        title = self.g_title.get().strip()
        date = self.g_date.get().strip() or _dt.date.today().isoformat()
        delivery = canonical_choice(self.g_delivery_var.get(), DELIVERY_MODES)
        package_id = self.g_package_id.get().strip() or title

        if not src or not dst:
            messagebox.showerror(tr("Missing folders"), tr("Set both Source and Target folders."))
            return
        if delivery in ("Web delivery", "Both") and not package_id:
            messagebox.showerror(
                tr("Package ID required"),
                tr("Enter a machine-safe Package ID, such as 3.9.8."),
            )
            return

        try:
            threads = self._positive_int(self.g_threads.get(), "Patch threads")
            chunk_mib = self._positive_int(self.g_chunk_size.get(), "Chunk size", 512)
            publish_workers = self._positive_int(self.g_publish_workers.get(), "Publishing workers", 32)
        except ValueError as exc:
            messagebox.showerror(tr("Invalid setting"), str(exc))
            return

        repository_root = Path(self.g_web_repo.get().strip() or (Path(WORKING_DIR) / "web_repo_output"))
        profile_label = canonical_choice(self.g_diff_profile.get(), self._diff_presets().keys())
        diff_args = self._diff_presets().get(profile_label, self._diff_presets()["Balanced"])
        self._cancel = threading.Event()
        self.btn_abort_gen.state(["!disabled"])
        check_resources()

        def worker():
            try:
                for directory in (OUTPUT_DIR, PATCH_out_DIR, MISSING_out_DIR, STORAGE_out_DIR):
                    os.makedirs(directory, exist_ok=True)
                self._log(f"[generate] start delivery={delivery}")

                total_files = count_dest_files(dst)
                self._reset_prog(total_files, "Generating patches")
                generate_patches(
                    src,
                    dst,
                    PATCH_out_DIR,
                    MISSING_out_DIR,
                    workers=threads,
                    zstd_args=diff_args,
                    on_progress=lambda _phase, current, total, message: self._phase_progress(current, total, message),
                    cancel_event=self._cancel,
                    use_tqdm=False,
                )
                if self._cancel.is_set():
                    return

                self._reset_prog(100, "Packing additional files")
                pack_additional(
                    MISSING_out_DIR,
                    STORAGE_out_DIR,
                    cancel_event=self._cancel,
                    on_progress=lambda _phase, current, total, message: self._phase_progress(current, total, message),
                )
                if self._cancel.is_set():
                    return

                self._reset_prog(1, "Building delete list")
                build_delete_list(src, dst, str(Path(STORAGE_out_DIR) / "delete_list.txt"))
                self._phase_progress(1, 1, tr("delete list written"))

                self._reset_prog(1, "Stamping metadata")
                source_path = Path(src)
                integrity = {
                    relative: folder_size(source_path / relative)
                    for relative in self.g_integrity_folders
                }
                stamp_from_game_exe(
                    str(Path(STORAGE_out_DIR) / "metadata.info"),
                    src,
                    title,
                    date,
                    integrity_folders=integrity,
                    diff_profile=profile_label,
                    zstd_patch_args=diff_args,
                )
                self._phase_progress(1, 1, tr("metadata stamped"))

                patch_count = count_patch_files(PATCH_out_DIR)
                self._reset_prog(max(patch_count, 1), "Auditing patch package")
                if not audit_patch_files(
                    PATCH_out_DIR,
                    workers=threads,
                    cancel_event=self._cancel,
                    on_progress=lambda _phase, current, total, message: self._phase_progress(current, total, message),
                ):
                    raise RuntimeError("Generated patch package failed its final audit")
                if self._cancel.is_set():
                    return

                web_result = None
                if delivery in ("Web delivery", "Both"):
                    callback = self._web_progress_callback()
                    web_result = publish_web_package(
                        OUTPUT_DIR,
                        repository_root,
                        package_id,
                        chunk_size=chunk_mib * 1024 * 1024,
                        workers=publish_workers,
                        on_progress=callback,
                        cancel_event=self._cancel,
                    )
                    self._log(
                        f"[generate] web manifest={web_result.manifest_path} "
                        f"objects={web_result.object_count} new={web_result.new_object_count}"
                    )
                if self._cancel.is_set():
                    return

                standalone_dir = None
                if delivery in ("Standalone", "Both"):
                    self._set_phase("Finalizing standalone package")
                    copy_self_to_output(OUTPUT_DIR, self._log)
                    standalone_dir = rename_output_folder(
                        OUTPUT_DIR,
                        spt_version=title,
                        live_client_exe=os.path.join(src, "EscapeFromTarkov.exe"),
                        log=self._log,
                    ) or OUTPUT_DIR
                elif delivery == "Web delivery":
                    shutil.rmtree(OUTPUT_DIR, ignore_errors=True)

                self._set_phase("Done")
                lines = [tr("Generation completed successfully.")]
                if standalone_dir:
                    lines.append(tr("Standalone:\n{path}", path=standalone_dir))
                if web_result:
                    lines.append(tr("Web repository:\n{path}", path=repository_root))
                _safe_call(self, messagebox.showinfo, tr("Generate"), "\n\n".join(lines))
                self._log("[generate] done")
            except proc.Cancelled:
                self._set_phase("Cancelled")
                self._log("[generate] cancelled")
            except Exception:
                if self._cancel.is_set():
                    self._set_phase("Cancelled")
                    self._log("[generate] cancelled during operation")
                else:
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
        if getattr(self, "_install_running", False):
            self._log("[install] duplicate start ignored: installation already running")
            return

        destination = self.i_dest.get().strip()
        source_mode = canonical_choice(self.i_source_var.get(), PACKAGE_SOURCES)
        release_id = self.i_web_release.get().strip()
        force = self.i_force.get()

        if not destination or not os.path.isdir(destination):
            messagebox.showerror(tr("Missing folder"), tr("Select a valid destination folder."))
            return
        if source_mode == "Web release" and not release_id:
            messagebox.showerror(tr("Release required"), tr("Enter the web Release ID to install."))
            return

        try:
            patch_workers = self._positive_int(self.i_threads.get(), "Patch threads")
            download_workers = self._positive_int(self.i_download_workers.get(), "Download workers")
            materialize_workers = self._positive_int(
                self.i_materialize_workers.get(), "Reconstruction workers", 32
            )
        except ValueError as exc:
            messagebox.showerror(tr("Invalid setting"), str(exc))
            return

        cache_root = Path(self.i_web_cache.get().strip() or (Path(WORKING_DIR) / "web_cache"))
        self._cancel = threading.Event()
        if not self._begin_install_run():
            return

        try:
            check_resources()
        except Exception:
            self._finish_install_run()
            raise

        def worker():
            try:
                self._log(f"[install] start source={source_mode}")
                if source_mode == "Web release":
                    source = WebPackageSource(
                        release_id,
                        cache_root,
                        download_workers=download_workers,
                        materialize_workers=materialize_workers,
                    )
                    layout = source.prepare(
                        on_progress=self._web_progress_callback(),
                        cancel_event=self._cancel,
                    )
                else:
                    layout = LocalPackageSource().prepare(
                        on_progress=self._web_progress_callback(),
                        cancel_event=self._cancel,
                    )

                if self._cancel.is_set():
                    return
                meta = Meta.read(layout.storage_root)
                _safe_call(self, self._refresh_status)

                missing = missing_requirements_for_metadata(meta)
                if missing and not self._threadsafe_dialog(self._show_dependency_prompt, meta, missing):
                    self._set_phase("Stopped")
                    self._log("[install] stopped for missing dependencies")
                    return

                installation = query_install()
                if not installation:
                    raise RuntimeError("Tarkov installation not found (registry)")

                if not force:
                    executable = os.path.join(installation["install_path"], "EscapeFromTarkov.exe")
                    live_version = exe_version(executable) or "-"
                    if meta.version and live_version != meta.version:
                        message = tr(
                            "Version mismatch detected.\n\n"
                            "Live client: {live_version}\n"
                            "Expected: {expected_version}\n\n"
                            "If your live version exceeds that of the patch, please wait for an update. Otherwise, please update your live game and try again.",
                            live_version=live_version,
                            expected_version=meta.version,
                        )
                        self._log("[install] stopped: version mismatch")
                        self._stop_with_message("Version mismatch", message)
                        return

                integrity = getattr(meta, "integrity_folders", None) or {}
                if integrity and not force:
                    mismatches = []
                    destination_path = Path(destination)
                    for relative, expected_size in integrity.items():
                        actual_size = folder_size(destination_path / relative)
                        if actual_size != expected_size:
                            mismatches.append((relative, expected_size, actual_size))
                    if mismatches:
                        details = "\n\n".join(
                            tr(
                                "{relative}: expected {expected:,} bytes, found {actual:,} bytes",
                                relative=relative,
                                expected=expected,
                                actual=actual,
                            )
                            for relative, expected, actual in mismatches
                        )
                        self._stop_with_message(
                            "Folder contents mismatch",
                            tr(
                                "The destination differs from the source used to build this patch.\n\n{details}",
                                details=details,
                            ),
                        )
                        return

                total_patches = count_patch_files(layout.patch_root)
                self._reset_prog(max(total_patches, 1), "Applying patches")
                total, succeeded, failed = apply_all_patches(
                    destination,
                    workers=patch_workers,
                    patch_root=layout.patch_root,
                    on_progress=lambda _phase, current, total_count, message: self._phase_progress(
                        current, total_count, message
                    ),
                    cancel_event=self._cancel,
                    use_tqdm=False,
                )
                if self._cancel.is_set():
                    return

                self._reset_prog(1, "Finalizing")
                finalize(destination, str(layout.storage_root / "delete_list.txt"))
                self._phase_progress(1, 1, tr("cleanup done"))

                self._reset_prog(100, "Applying storage")
                apply_storage(
                    layout.storage_root,
                    destination,
                    cancel_event=self._cancel,
                    on_progress=lambda _phase, current, total_count, message: self._phase_progress(
                        current, total_count, message
                    ),
                )
                if failed:
                    raise RuntimeError(f"Some patches failed ({failed}/{total})")

                self._set_phase("Done")
                self._log(f"[install] done applied={succeeded}/{total}")
                _safe_call(
                    self,
                    messagebox.showinfo,
                    tr("Install"),
                    tr("Patch applied successfully."),
                )
            except proc.Cancelled:
                self._set_phase("Cancelled")
                self._log("[install] cancelled")
            except Exception:
                if self._cancel.is_set():
                    self._set_phase("Cancelled")
                    self._log("[install] cancelled during operation")
                else:
                    self._log_exc("[install] failed")
                    _safe_call(
                        self,
                        messagebox.showerror,
                        tr("Install"),
                        tr("Install failed. See Logs for details."),
                    )
            finally:
                proc.kill_all()
                _safe_call(self, self._finish_install_run)

        try:
            threading.Thread(target=worker, daemon=True).start()
        except Exception:
            self._finish_install_run()
            raise


def main(dev: bool = False):
    _hide_console_on_windows()
    app = IntegratedSierraPatcherGUI(dev=dev)
    app.mainloop()
