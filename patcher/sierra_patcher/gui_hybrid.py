from __future__ import annotations

import json
import shutil
import threading
import tkinter as tk
from pathlib import Path
from tkinter import filedialog, messagebox, ttk

from . import cli, gui_web, web_download
from .archived_snapshot import (
    ARCHIVED_SNAPSHOT_MARKER,
    archive_web_release,
    read_archived_snapshot,
)
from .delete_list import finalize as _finalize_now
from .gui import _hide_console_on_windows, _safe_call
from .gui_resilient import ResilientSierraPatcherGUI
from .hybrid_payload import generate_patches as generate_hybrid_patches
from .package_format import enable_hybrid_package_format
from .package_source import ArchivedSnapshotSource, LocalPackageSource as _RealLocalPackageSource
from .paths import WORKING_DIR
from .storage import apply_storage as _apply_payloads_now
from .web_catalog import CATALOG_PLACEHOLDER


# One canonical package format: deltas + ordinary-Zstd payloads + metadata.
enable_hybrid_package_format()
gui_web.generate_patches = generate_hybrid_patches
cli.generate_patches = generate_hybrid_patches

# New public workflow: publish one web release, then optionally save that exact
# release as an object-only Archived snapshot for portable/offline use.
gui_web.DELIVERY_MODES = ("Web delivery",)
gui_web.PACKAGE_SOURCES = ("Web release", "Archived snapshot")


class HybridSierraPatcherGUI(ResilientSierraPatcherGUI):
    """Hybrid Zstd package GUI with portable object-only Archived snapshots."""

    def __init__(self, dev: bool = False):
        # gui_web's established worker treats every non-web source as its local
        # source class. Route that existing hook to the selected offline source
        # without duplicating the long install workflow.
        gui_web.LocalPackageSource = lambda: self._selected_offline_source()
        gui_web.finalize = self._defer_delete_finalize
        gui_web.apply_storage = self._apply_payloads_then_finalize
        self._pending_delete_finalize: tuple[str, str] | None = None
        self._offline_source_config: tuple[str, str, int] | None = None
        self._archived_cleanup_pending = False
        self._archived_cleanup_package_id: str | None = None
        self._archived_cleanup_cache: Path | None = None
        super().__init__(dev=dev)

    def _defer_delete_finalize(self, dest_dir: str, delete_list_path: str) -> None:
        self._pending_delete_finalize = (dest_dir, delete_list_path)
        self._log("[install] delete-list finalization deferred until full payloads succeed")

    def _apply_payloads_then_finalize(
        self,
        storage_dir,
        dest_dir,
        cancel_event=None,
        on_progress=None,
    ) -> None:
        _apply_payloads_now(
            storage_dir,
            dest_dir,
            cancel_event=cancel_event,
            on_progress=on_progress,
        )
        pending = self._pending_delete_finalize
        if pending is not None:
            pending_dest, delete_list_path = pending
            _finalize_now(pending_dest, delete_list_path)
            self._pending_delete_finalize = None
            self._log("[install] delete-list finalization completed after payloads")

    def _has_local_package(self) -> bool:
        # Prevent an Archived snapshot launched from its own folder from trying
        # to contact the catalog during initial GUI construction. Catalog's
        # temporary "Local package" selection is replaced with Archived snapshot
        # as soon as the hybrid controls have been created.
        return (Path(WORKING_DIR) / ARCHIVED_SNAPSHOT_MARKER).is_file()

    def _build_generate_tab(self, nb) -> ttk.Frame:
        root = super()._build_generate_tab(nb)
        self.g_delivery_var.set("Web delivery")
        self._toggle_generate_web_options()

        # Web delivery is now the only generation output. Keep the internal
        # value for the established generation worker, but remove the redundant
        # selector from the Delivery section and compact the remaining rows.
        delivery_frame = next(
            (
                widget
                for widget in root.winfo_children()
                if isinstance(widget, ttk.LabelFrame)
                and widget.cget("text") == "Delivery"
            ),
            None,
        )
        if delivery_frame is not None:
            for child in delivery_frame.grid_slaves(row=0):
                child.grid_remove()
            remaining = list(delivery_frame.grid_slaves())
            for child in remaining:
                info = child.grid_info()
                if not info:
                    continue
                row = int(info.get("row", 0))
                if row >= 1:
                    child.grid_configure(row=row - 1)

        for widget in root.winfo_children():
            for child in widget.winfo_children():
                if isinstance(child, ttk.Button) and child.cget("text") == "Generate patch package":
                    child.configure(text="Generate web release")
        return root

    def _build_install_tab(self, nb) -> ttk.Frame:
        root = super()._build_install_tab(nb)
        source_frame = self.i_web_release.master

        self.btn_archive_snapshot = ttk.Button(
            source_frame,
            text="Save selected release as Archived snapshot...",
            command=self._run_archive_snapshot,
        )
        self.btn_archive_snapshot.grid(
            row=5,
            column=0,
            columnspan=3,
            sticky="ew",
            padx=12,
            pady=(4, 8),
        )

        self._archive_path_frame = ttk.Frame(source_frame)
        self._archive_path_frame.columnconfigure(1, weight=1)
        self.i_archive_path_var = tk.StringVar()
        self.i_archive_path = ttk.Entry(
            self._archive_path_frame,
            textvariable=self.i_archive_path_var,
        )
        ttk.Label(
            self._archive_path_frame,
            text="Archived snapshot",
            font=("Segoe UI", 9, "bold"),
        ).grid(row=0, column=0, sticky="w", padx=(0, 8), pady=(2, 2))
        self.i_archive_path.grid(row=0, column=1, sticky="ew", pady=(2, 2))
        ttk.Button(
            self._archive_path_frame,
            text="Browse...",
            command=self._browse_archived_snapshot,
        ).grid(row=0, column=2, sticky="e", padx=(8, 0), pady=(2, 2))
        self._archive_hint = ttk.Label(
            self._archive_path_frame,
            text="Select a Sierra Archived snapshot folder.",
            style="Hint.TLabel",
        )
        self._archive_hint.grid(row=1, column=1, columnspan=2, sticky="w", pady=(2, 0))
        self._archive_badge = tk.Label(
            self._archive_path_frame,
            text="REQUIRED",
            bg=self._REQUIRED_BG,
            fg=self._REQUIRED_FG,
            font=("Segoe UI", 8, "bold"),
            padx=6,
            pady=2,
        )
        self._archive_badge.grid(row=0, column=3, sticky="w", padx=(6, 0))

        self.i_archive_path_var.trace_add("write", lambda *_: self._validate_install_ready())

        local_marker = Path(WORKING_DIR) / ARCHIVED_SNAPSHOT_MARKER
        if local_marker.is_file():
            self.i_archive_path_var.set(str(Path(WORKING_DIR)))
            self.i_source_var.set("Archived snapshot")

        self._toggle_install_web_options()
        self._validate_install_ready()
        return root

    def _selected_offline_source(self):
        config = self._offline_source_config
        if config is not None and config[0] == "Archived snapshot":
            _, snapshot_path, workers = config
            cache_root = self._archived_cleanup_cache or (Path(WORKING_DIR) / "web_cache")
            return ArchivedSnapshotSource(
                snapshot_path,
                cache_root,
                materialize_workers=workers,
            )
        return _RealLocalPackageSource()

    def _browse_archived_snapshot(self) -> None:
        selected = filedialog.askdirectory(title="Select Sierra Archived snapshot")
        if selected:
            self.i_archive_path_var.set(selected)

    def _snapshot_ready(self) -> bool:
        if not hasattr(self, "i_archive_path_var"):
            return False
        value = self.i_archive_path_var.get().strip()
        if not value:
            return False
        try:
            read_archived_snapshot(value)
            return True
        except Exception:
            return False

    def _toggle_install_web_options(self):
        result = super()._toggle_install_web_options()
        if not hasattr(self, "btn_archive_snapshot"):
            return result

        source = self.i_source_var.get()
        if source == "Web release":
            self._archive_path_frame.grid_remove()
            self.btn_archive_snapshot.grid()
        elif source == "Archived snapshot":
            self.btn_archive_snapshot.grid_remove()
            self._archive_path_frame.grid(
                row=5,
                column=0,
                columnspan=3,
                sticky="ew",
                padx=12,
                pady=(4, 8),
            )
        else:
            self.btn_archive_snapshot.grid_remove()
            self._archive_path_frame.grid_remove()

        self._validate_install_ready()
        return result

    def _validate_install_ready(self):
        result = super()._validate_install_ready()
        if not hasattr(self, "i_archive_path_var"):
            return result

        source = self.i_source_var.get()
        if source == "Archived snapshot":
            ready = self._snapshot_ready()
            self._set_badge(self._archive_badge, ready)
            if ready:
                self._archive_hint.configure(text="Archived snapshot is ready.")
            else:
                self._archive_hint.configure(text="Select a valid Sierra Archived snapshot folder.")

            destination = self._destination_value()
            destination_ready = bool(destination and Path(destination).is_dir())
            if ready and destination_ready:
                self.btn_install.state(["!disabled"])
            else:
                self.btn_install.state(["disabled"])
        else:
            self._archive_hint.configure(text="Select a Sierra Archived snapshot folder.")

        if source == "Web release":
            release = self.i_web_release_var.get().strip()
            valid_release = bool(
                release
                and release != CATALOG_PLACEHOLDER
                and release in tuple(self.i_web_release.cget("values"))
            )
            self.btn_archive_snapshot.configure(state="normal" if valid_release else "disabled")
        return result

    def _refresh_status(self):
        result = super()._refresh_status()
        if not hasattr(self, "i_archive_path_var"):
            return result
        if self.i_source_var.get() != "Archived snapshot":
            return result
        try:
            info = read_archived_snapshot(self.i_archive_path_var.get().strip())
            manifest = json.loads(info.manifest_path.read_text(encoding="utf-8"))
            patch_count = sum(
                1
                for entry in manifest.get("files", [])
                if isinstance(entry, dict) and str(entry.get("path", "")).startswith("patchfiles/")
            )
            self._stat["pat_title"].set(f"Archived: {info.package_id}")
            self._stat["pat_patches"].set(str(patch_count))
        except Exception:
            self._stat["pat_title"].set("Archived snapshot")
            self._stat["pat_patches"].set("Not prepared")
        return result

    def _run_archive_snapshot(self) -> None:
        release = self.i_web_release_var.get().strip()
        if not release or release == CATALOG_PLACEHOLDER:
            messagebox.showerror("Version required", "Choose a web release to archive first.")
            return

        selected = filedialog.askdirectory(title="Choose where to store the Archived snapshot")
        if not selected:
            return
        selected_path = Path(selected)
        if (selected_path / ARCHIVED_SNAPSHOT_MARKER).is_file():
            snapshot_root = selected_path
        else:
            snapshot_root = selected_path / f"Sierra-Archived-{release}"

        try:
            download_workers = self._positive_int(self.i_download_workers.get(), "Download workers")
        except ValueError as exc:
            messagebox.showerror("Invalid setting", str(exc))
            return

        cache_text = self.i_web_cache.get().strip()
        cache_root = Path(cache_text or (Path(WORKING_DIR) / "web_cache"))
        self._cleanup_web_cache_after_success = False
        self._cancel = threading.Event()
        self.btn_abort_ins.state(["!disabled"])
        self.btn_archive_snapshot.configure(state="disabled")
        self.btn_install.state(["disabled"])

        def worker():
            try:
                self._log(f"[archive] start release={release} destination={snapshot_root}")
                info = archive_web_release(
                    release,
                    snapshot_root,
                    cache_root,
                    download_workers=download_workers,
                    on_progress=self._web_progress_callback(),
                    cancel_event=self._cancel,
                    include_patcher=True,
                )
                if self._cancel.is_set():
                    return
                self._set_phase("Done")
                self._log(f"[archive] ready: {info.root}")
                _safe_call(
                    self,
                    messagebox.showinfo,
                    "Archived snapshot",
                    "Archived snapshot created successfully.\n\n"
                    f"Release: {info.package_id}\n"
                    f"Location:\n{info.root}\n\n"
                    "The snapshot keeps the package in manifest/object form and reconstructs it only when installed.",
                )
            except Exception:
                if self._cancel.is_set():
                    self._set_phase("Cancelled")
                    self._log("[archive] cancelled")
                else:
                    self._log_exc("[archive] failed")
                    _safe_call(
                        self,
                        messagebox.showerror,
                        "Archived snapshot",
                        "Could not create the Archived snapshot. See Logs for details.",
                    )
            finally:
                _safe_call(self, self.btn_abort_ins.state, ["disabled"])
                _safe_call(self, self._validate_install_ready)

        threading.Thread(target=worker, daemon=True).start()

    def _run_install(self):
        self._pending_delete_finalize = None
        if self.i_source_var.get() == "Archived snapshot":
            if not self._snapshot_ready():
                messagebox.showerror(
                    "Archived snapshot required",
                    "Select a valid Sierra Archived snapshot folder first.",
                )
                return
            info = read_archived_snapshot(self.i_archive_path_var.get().strip())
            cache_text = self.i_web_cache.get().strip()
            self._archived_cleanup_pending = True
            self._archived_cleanup_package_id = info.package_id
            self._archived_cleanup_cache = Path(
                cache_text or (Path(WORKING_DIR) / "web_cache")
            )
            try:
                materialize_workers = self._positive_int(
                    self.i_materialize_workers.get(),
                    "Reconstruction workers",
                    32,
                )
            except ValueError as exc:
                messagebox.showerror("Invalid setting", str(exc))
                return
            self._offline_source_config = (
                "Archived snapshot",
                self.i_archive_path_var.get().strip(),
                materialize_workers,
            )
        else:
            self._offline_source_config = None
            self._archived_cleanup_pending = False
            self._archived_cleanup_package_id = None
            self._archived_cleanup_cache = None
        return super()._run_install()

    def _set_phase(self, phase: str):
        if phase == "Done" and self._archived_cleanup_pending:
            self._archived_cleanup_pending = False
            package_id = self._archived_cleanup_package_id
            cache_root = self._archived_cleanup_cache
            if package_id and cache_root:
                try:
                    root = cache_root / "packages" / package_id
                    shutil.rmtree(web_download._io_path(root), ignore_errors=False)
                    self._log(f"[archive] removed reconstructed install cache: {root}")
                except FileNotFoundError:
                    pass
                except Exception as exc:
                    self._log(f"[archive] reconstructed cache cleanup failed: {exc}")
        return super()._set_phase(phase)


def main(dev: bool = False):
    _hide_console_on_windows()
    app = HybridSierraPatcherGUI(dev=dev)
    app.mainloop()
