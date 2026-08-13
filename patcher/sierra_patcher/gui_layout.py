from __future__ import annotations

import shutil
import tkinter as tk
from pathlib import Path
from tkinter import messagebox, ttk

from . import gui_web
from .game_copy import CopyDestinationStatus, inspect_copy_destination, paths_overlap
from .gui import _hide_console_on_windows
from .gui_polished import PolishedSierraPatcherGUI
from .i18n import canonical_choice, tr
from .paths import WORKING_DIR
from .registry import exe_version, query_install


class LayoutSierraPatcherGUI(PolishedSierraPatcherGUI):
    """Polished GUI with a compact package-source layout and destination placeholder."""

    _DEST_PLACEHOLDER = "Select pasted Live folder"
    _AUTO_DEST_PLACEHOLDER = "Select new SPT folder"

    def _build_install_tab(self, nb) -> ttk.Frame:
        self._completed_auto_copy_destination: str | None = None
        root = super()._build_install_tab(nb)
        source_frame = self.i_web_release.master

        # Preserve the user's/current defaults before replacing the three
        # advanced controls with equivalents inside the collapsible section.
        cache_value = self.i_web_cache.get()
        download_workers = self.i_download_workers.get()
        materialize_workers = self.i_materialize_workers.get()

        # Rows 3+ currently contain cache directory, both worker settings, and
        # the cache explanation. Hide those original widgets; the base GUI is
        # intentionally left unchanged so this layout layer stays reversible.
        for widget in source_frame.grid_slaves():
            info = widget.grid_info()
            if int(info.get("row", 0)) >= 3:
                widget.grid_remove()

        self._advanced_open = False
        self._advanced_button = ttk.Button(
            source_frame,
            text=tr("Advanced ▸"),
            command=self._toggle_advanced_section,
        )
        self._advanced_button.grid(
            row=3,
            column=0,
            columnspan=3,
            sticky="ew",
            padx=12,
            pady=(8, 6),
        )

        self._advanced_frame = ttk.Frame(source_frame)
        self._advanced_frame.columnconfigure(1, weight=1)

        self.i_web_cache = ttk.Entry(self._advanced_frame)
        self.i_web_cache.insert(0, cache_value)
        self.i_download_workers = ttk.Spinbox(self._advanced_frame, from_=1, to=64)
        self.i_download_workers.delete(0, tk.END)
        self.i_download_workers.insert(0, download_workers)
        self.i_materialize_workers = ttk.Spinbox(self._advanced_frame, from_=1, to=32)
        self.i_materialize_workers.delete(0, tk.END)
        self.i_materialize_workers.insert(0, materialize_workers)

        self._row(
            self._advanced_frame,
            0,
            "Cache directory",
            self.i_web_cache,
            browse=lambda: self._browse_entry(self.i_web_cache, "Select web package cache"),
        )
        self._row(self._advanced_frame, 1, "Download workers", self.i_download_workers)
        self._row(
            self._advanced_frame,
            2,
            "Reconstruction workers",
            self.i_materialize_workers,
        )
        ttk.Label(
            self._advanced_frame,
            text=tr(
                "These settings normally do not need to be changed. Downloaded cache is "
                "removed automatically after a successful web installation."
            ),
            wraplength=390,
            foreground="#666",
        ).grid(row=3, column=0, columnspan=3, sticky="w", padx=12, pady=(6, 4))

        self._web_install_widgets = [
            self.i_web_release,
            self.i_web_cache,
            self.i_download_workers,
            self.i_materialize_workers,
        ]

        target_frame = self.i_dest.master
        for widget in target_frame.grid_slaves():
            info = widget.grid_info()
            row = int(info.get("row", 0))
            widget.grid_configure(row=row + (1 if row <= 1 else 2))

        mode_frame = ttk.Frame(target_frame)
        mode_frame.grid(
            row=0,
            column=0,
            columnspan=4,
            sticky="w",
            padx=12,
            pady=(6, 2),
        )
        ttk.Label(mode_frame, text=tr("Install mode")).pack(side="left", padx=(0, 16))
        self.i_install_mode_var = tk.StringVar(value="auto")
        ttk.Radiobutton(
            mode_frame,
            text=tr("Automatic copy (recommended)"),
            value="auto",
            variable=self.i_install_mode_var,
            command=self._sync_install_mode_ui,
        ).pack(side="left")
        ttk.Radiobutton(
            mode_frame,
            text=tr("Use existing copy"),
            value="existing",
            variable=self.i_install_mode_var,
            command=self._sync_install_mode_ui,
        ).pack(side="left", padx=(16, 0))

        self._live_source_label = ttk.Label(target_frame, text=tr("Original"))
        self._live_source_label.grid(row=3, column=0, sticky="w", padx=12, pady=(4, 0))
        self._live_source_frame = ttk.Frame(target_frame)
        self._live_source_frame.grid(
            row=3,
            column=1,
            columnspan=3,
            sticky="ew",
            padx=(12, 8),
            pady=(4, 0),
        )
        self._live_source_frame.columnconfigure(0, weight=1)
        ttk.Entry(
            self._live_source_frame,
            textvariable=self._stat["tk_path"],
            state="readonly",
        ).grid(row=0, column=0, sticky="ew")
        ttk.Label(
            self._live_source_frame,
            textvariable=self._stat["tk_version"],
        ).grid(row=0, column=1, sticky="w", padx=(8, 0))
        self._live_source_detection_label = ttk.Label(
            self._live_source_frame,
            text=tr("Auto-detected"),
            foreground="#666",
        )
        self._live_source_detection_label.grid(row=0, column=2, sticky="w", padx=(8, 0))

        # A placeholder is visual guidance only. Validation and installation
        # treat it exactly like an empty destination.
        self._destination_placeholder_ready = True
        style = ttk.Style(self)
        style.configure("Placeholder.TEntry", foreground="#777777")
        self.i_dest.bind("<FocusIn>", self._clear_destination_placeholder, add="+")
        self.i_dest.bind("<FocusOut>", self._restore_destination_placeholder, add="+")
        self.i_dest_var.trace_add("write", lambda *_: self._refresh_destination_status())
        self._sync_install_mode_ui()

        # Re-apply web/local state now that the advanced widgets have replaced
        # the hidden base controls.
        self._toggle_install_web_options()
        return root

    def _automatic_copy_enabled(self) -> bool:
        mode = getattr(self, "i_install_mode_var", None)
        return bool(mode is not None and mode.get() == "auto")

    def _destination_placeholder(self) -> str:
        return (
            self._AUTO_DEST_PLACEHOLDER
            if self._automatic_copy_enabled()
            else self._DEST_PLACEHOLDER
        )

    def _is_destination_placeholder(self, value: str) -> bool:
        return canonical_choice(
            value,
            (self._DEST_PLACEHOLDER, self._AUTO_DEST_PLACEHOLDER),
        ) in (self._DEST_PLACEHOLDER, self._AUTO_DEST_PLACEHOLDER)

    def _sync_install_mode_ui(self) -> None:
        if not hasattr(self, "i_install_mode_var"):
            return
        automatic = self._automatic_copy_enabled()
        self.i_destination_label.configure(
            text=tr("New SPT folder" if automatic else "Destination to patch")
        )
        if automatic:
            self._live_source_label.grid()
            self._live_source_frame.grid()
        else:
            self._live_source_label.grid_remove()
            self._live_source_frame.grid_remove()

        current = (self.i_dest_var.get() or "").strip()
        if not current or self._is_destination_placeholder(current):
            self.i_dest_var.set(tr(self._destination_placeholder()))
        self._refresh_destination_status()
        self._validate_install_ready()

    def _copy_destination_status(self) -> CopyDestinationStatus:
        destination = self._destination_value()
        if not destination:
            return CopyDestinationStatus(False, "destination_missing")
        if self._destination_overlaps_cache(destination):
            return CopyDestinationStatus(False, "cache_overlap")
        try:
            installation = query_install()
        except Exception:
            installation = None
        if not installation:
            return CopyDestinationStatus(False, "source_missing")
        source = Path(installation["install_path"])
        version = exe_version(source / "EscapeFromTarkov.exe")
        return inspect_copy_destination(source, destination, version)

    def _destination_overlaps_cache(self, destination: str) -> bool:
        cache_value = self.i_web_cache.get().strip()
        cache_root = Path(cache_value or (Path(WORKING_DIR) / "web_cache"))
        return paths_overlap(cache_root, destination)

    def _refresh_destination_status(self):
        destination = self._destination_value()
        self._stat["dst_path"].set(destination or "—")
        try:
            executable = Path(destination) / "EscapeFromTarkov.exe"
            destination_version = (
                (exe_version(executable) or "—") if executable.is_file() else "—"
            )
            if destination_version == "—" and self._automatic_copy_enabled():
                destination_version = self._stat["tk_version"].get() or "—"
            self._stat["dst_version"].set(destination_version)
        except Exception:
            self._stat["dst_version"].set("—")

        try:
            if destination:
                usage_root = Path(destination)
                while not usage_root.exists() and usage_root != usage_root.parent:
                    usage_root = usage_root.parent
                free = shutil.disk_usage(usage_root).free
                self._stat["dst_free"].set(self._format_bytes(free))
            else:
                self._stat["dst_free"].set("—")
        except Exception:
            self._stat["dst_free"].set("—")

    def _destination_ready_for_install(self) -> bool:
        destination = self._destination_value()
        if not destination:
            return False
        if self._destination_overlaps_cache(destination):
            return False
        if self._automatic_copy_enabled():
            return self._copy_destination_status().ready
        destination_path = Path(destination)
        if not destination_path.is_dir():
            return False
        try:
            installation = query_install()
        except Exception:
            installation = None
        return not (
            installation
            and paths_overlap(installation["install_path"], destination_path)
        )

    def _destination_validation_text(self) -> str:
        destination = self._destination_value()
        if not destination:
            return tr("Destination folder is required.")
        if self._destination_overlaps_cache(destination):
            return tr("The destination and cache folders must be separate.")
        if not self._automatic_copy_enabled():
            try:
                installation = query_install()
            except Exception:
                installation = None
            if installation and paths_overlap(installation["install_path"], destination):
                return tr("The Live Tarkov folder and SPT folder must be separate.")
            return tr("Select a valid destination folder.")

        reason = self._copy_destination_status().reason
        messages = {
            "source_missing": (
                "Could not detect the Live Tarkov folder. Use an existing copy instead."
            ),
            "destination_missing": "Destination folder is required.",
            "cache_overlap": "The destination and cache folders must be separate.",
            "overlap": "The Live Tarkov folder and SPT folder must be separate.",
            "not_directory": "The destination must be a folder.",
            "not_empty": (
                "This folder already contains files. Use an existing copy or choose an "
                "empty folder."
            ),
            "state_mismatch": (
                "This partial copy belongs to a different Live folder or version. "
                "Choose another empty folder."
            ),
        }
        return tr(messages.get(reason, "Select a valid destination folder."))

    def _set_advanced_open(self, opened: bool) -> None:
        self._advanced_open = bool(opened)
        if self._advanced_open:
            self._advanced_button.configure(text=tr("Advanced ▾"))
            self._advanced_frame.grid(
                row=4,
                column=0,
                columnspan=3,
                sticky="ew",
                padx=4,
                pady=(0, 6),
            )
        else:
            self._advanced_button.configure(text=tr("Advanced ▸"))
            self._advanced_frame.grid_remove()

    def _toggle_advanced_section(self) -> None:
        if canonical_choice(self.i_source_var.get(), gui_web.PACKAGE_SOURCES) != "Web release":
            return
        self._set_advanced_open(not self._advanced_open)

    def _toggle_install_web_options(self):
        result = super()._toggle_install_web_options()
        if not hasattr(self, "_advanced_button"):
            return result

        web_mode = canonical_choice(self.i_source_var.get(), gui_web.PACKAGE_SOURCES) == "Web release"
        self._advanced_button.configure(state="normal" if web_mode else "disabled")
        if not web_mode and self._advanced_open:
            self._set_advanced_open(False)
        return result

    def _destination_value(self) -> str:
        value = (self.i_dest_var.get() or "").strip()
        if self._is_destination_placeholder(value):
            return ""
        return value

    def _clear_destination_placeholder(self, _event=None) -> None:
        if not getattr(self, "_destination_placeholder_ready", False):
            return
        if self._is_destination_placeholder((self.i_dest_var.get() or "").strip()):
            self.i_dest_var.set("")
            try:
                self.i_dest.configure(style="Required.TEntry")
            except tk.TclError:
                pass

    def _restore_destination_placeholder(self, _event=None) -> None:
        if not getattr(self, "_destination_placeholder_ready", False):
            return
        if not (self.i_dest_var.get() or "").strip():
            self.i_dest_var.set(tr(self._destination_placeholder()))
            try:
                self.i_dest.configure(style="Placeholder.TEntry")
            except tk.TclError:
                pass

    def _validate_install_ready(self):
        result = super()._validate_install_ready()
        if not getattr(self, "_destination_placeholder_ready", False):
            return result

        destination = self._destination_value()
        if not destination:
            self._dest_hint.configure(text=tr("Destination folder is required."))
            self._dest_hint.grid()
            if self._is_destination_placeholder((self.i_dest_var.get() or "").strip()):
                try:
                    self.i_dest.configure(style="Placeholder.TEntry")
                except tk.TclError:
                    pass
            return result

        if self._automatic_copy_enabled():
            status = self._copy_destination_status()
            if not status.ready:
                self._destination_badge.configure(
                    text=tr("INVALID"),
                    bg=self._WARNING_BG,
                    fg=self._WARNING_FG,
                )
                self._dest_hint.configure(
                    text=self._destination_validation_text(),
                    foreground=self._WARNING_FG,
                )
                self._dest_hint.grid()
                self.btn_install.state(["disabled"])
            elif status.resumable:
                preflight = self._version_preflight()
                if preflight is None or not preflight.blocks_download:
                    self._destination_badge.configure(
                        text=tr("RESUME"),
                        bg=self._READY_BG,
                        fg=self._READY_FG,
                    )
                    self._dest_hint.configure(
                        text=tr("The interrupted Live game copy will resume."),
                        foreground=self._READY_FG,
                    )
                    self._dest_hint.grid()
        elif not self._destination_ready_for_install():
            self._destination_badge.configure(
                text=tr("INVALID"),
                bg=self._WARNING_BG,
                fg=self._WARNING_FG,
            )
            self._dest_hint.configure(
                text=self._destination_validation_text(),
                foreground=self._WARNING_FG,
            )
            self._dest_hint.grid()
            self.btn_install.state(["disabled"])

        completed_destination = getattr(self, "_completed_auto_copy_destination", None)
        if (
            self._automatic_copy_enabled()
            and destination
            and destination == completed_destination
            and Path(destination).is_dir()
        ):
            self._set_badge(self._destination_badge, True)
            try:
                self.i_dest.configure(style="Ready.TEntry")
            except tk.TclError:
                pass
            self._dest_hint.grid_remove()
            self.btn_install.state(["disabled"])
        return result

    def _finish_install_run(self) -> None:
        if (
            getattr(self, "_install_running", False)
            and self._automatic_copy_enabled()
            and self._phase_var.get() == tr("Done")
        ):
            destination = self._destination_value()
            if destination:
                self._completed_auto_copy_destination = destination
        return super()._finish_install_run()

    def _refresh_status(self):
        result = super()._refresh_status()
        if hasattr(self, "_live_source_detection_label"):
            detected = self._stat["tk_version"].get() not in (
                "—",
                tr("error"),
                tr("not found"),
            )
            self._live_source_detection_label.configure(
                text=tr("Auto-detected" if detected else "Not detected")
            )
            self._refresh_destination_status()
        return result

    def _run_install(self):
        if not self._destination_value():
            messagebox.showerror(
                tr("Destination required"),
                tr(
                    "Select a new SPT folder."
                    if self._automatic_copy_enabled()
                    else "Select the pasted Live folder that needs to be patched."
                ),
            )
            self.i_dest.focus_set()
            return
        if not self._automatic_copy_enabled():
            try:
                installation = query_install()
            except Exception:
                installation = None
            if not installation and not messagebox.askyesno(
                tr("Live folder not detected"),
                tr(
                    "Sierra could not detect the official Live Tarkov folder. "
                    "Confirm that the selected destination is a separate copy before continuing."
                ),
            ):
                return
        return super()._run_install()


def main(dev: bool = False):
    _hide_console_on_windows()
    app = LayoutSierraPatcherGUI(dev=dev)
    app.mainloop()
