from __future__ import annotations

import shutil
import tkinter as tk
from pathlib import Path
from tkinter import messagebox, ttk

from .gui import _hide_console_on_windows, _safe_call
from .gui_catalog import CatalogSierraPatcherGUI
from .web_catalog import CATALOG_PLACEHOLDER
from .web_download import _io_path


class PolishedSierraPatcherGUI(CatalogSierraPatcherGUI):
    """Catalog GUI with stronger required-field guidance and success cleanup."""

    _REQUIRED_BG = "#fff0c2"
    _REQUIRED_FG = "#7a4d00"
    _READY_BG = "#e6f4ea"
    _READY_FG = "#216e39"

    def _build_install_tab(self, nb) -> ttk.Frame:
        root = super()._build_install_tab(nb)

        source_frame = self.i_web_release.master
        target_frame = self.i_dest.master

        # The catalog layer inserts a release hint beneath the release selector.
        # Move the lower source controls down by one row so the hint has its own
        # dedicated line instead of overlapping the cache-directory row.
        for widget in source_frame.grid_slaves():
            if widget is self._release_hint:
                continue
            info = widget.grid_info()
            row = int(info.get("row", 0))
            if row >= 2:
                widget.grid_configure(row=row + 1)
        self._release_hint.grid_configure(row=2, column=1, columnspan=2)

        # Rename/bolden the two values a public web install actually requires.
        for widget in source_frame.winfo_children():
            if isinstance(widget, ttk.Label) and widget.cget("text") == "Release ID":
                widget.configure(text="Version / Release", font=("Segoe UI", 9, "bold"))
                break
        for widget in target_frame.winfo_children():
            if isinstance(widget, ttk.Label) and widget.cget("text") == "Destination to patch":
                widget.configure(font=("Segoe UI", 9, "bold"))
                break

        self._release_badge = tk.Label(
            source_frame,
            text="REQUIRED",
            bg=self._REQUIRED_BG,
            fg=self._REQUIRED_FG,
            font=("Segoe UI", 8, "bold"),
            padx=6,
            pady=2,
        )
        self._release_badge.grid(row=1, column=2, sticky="w", padx=(4, 8), pady=(6, 0))

        self._destination_badge = tk.Label(
            target_frame,
            text="REQUIRED",
            bg=self._REQUIRED_BG,
            fg=self._REQUIRED_FG,
            font=("Segoe UI", 8, "bold"),
            padx=6,
            pady=2,
        )
        self._destination_badge.grid(row=0, column=3, sticky="w", padx=(4, 8), pady=(6, 0))

        # Field backgrounds are a secondary cue. Some native Windows ttk themes
        # partially ignore fieldbackground, so the badges remain the primary,
        # theme-independent signal.
        style = ttk.Style(self)
        style.configure("Required.TEntry", fieldbackground="#fff8dc")
        style.configure("Ready.TEntry", fieldbackground="#f1fbf3")
        style.configure("Required.TCombobox", fieldbackground="#fff8dc")
        style.configure("Ready.TCombobox", fieldbackground="#f1fbf3")
        style.map(
            "Required.TCombobox",
            fieldbackground=[("readonly", "#fff8dc")],
            selectbackground=[("readonly", "#fff8dc")],
            selectforeground=[("readonly", "#000000")],
        )
        style.map(
            "Ready.TCombobox",
            fieldbackground=[("readonly", "#f1fbf3")],
            selectbackground=[("readonly", "#f1fbf3")],
            selectforeground=[("readonly", "#000000")],
        )

        self._update_required_field_emphasis()
        return root

    def _set_badge(self, badge: tk.Label, ready: bool) -> None:
        if ready:
            badge.configure(
                text="READY  ✓",
                bg=self._READY_BG,
                fg=self._READY_FG,
            )
        else:
            badge.configure(
                text="REQUIRED",
                bg=self._REQUIRED_BG,
                fg=self._REQUIRED_FG,
            )

    def _update_required_field_emphasis(self) -> None:
        if not hasattr(self, "_destination_badge"):
            return

        destination = (self.i_dest_var.get() or "").strip()
        destination_ready = bool(destination and Path(destination).is_dir())
        self._set_badge(self._destination_badge, destination_ready)
        try:
            self.i_dest.configure(style="Ready.TEntry" if destination_ready else "Required.TEntry")
        except tk.TclError:
            pass

        web_mode = self.i_source_var.get() == "Web release"
        if not web_mode:
            self._release_badge.grid_remove()
            try:
                self.i_web_release.configure(style="TCombobox")
            except tk.TclError:
                pass
            return

        self._release_badge.grid()
        release = self.i_web_release_var.get().strip()
        release_ready = bool(
            release
            and release != CATALOG_PLACEHOLDER
            and release in tuple(self.i_web_release.cget("values"))
        )
        self._set_badge(self._release_badge, release_ready)
        try:
            self.i_web_release.configure(
                style="Ready.TCombobox" if release_ready else "Required.TCombobox"
            )
        except tk.TclError:
            pass

    def _validate_install_ready(self):
        result = super()._validate_install_ready()
        self._update_required_field_emphasis()
        return result

    def _toggle_install_web_options(self):
        result = super()._toggle_install_web_options()
        self._update_required_field_emphasis()
        return result

    def _run_generate(self):
        # Generation also uses the shared progress phase name "Done". Ensure a
        # later successful generation can never trigger an install cleanup flag.
        self._cleanup_web_cache_after_success = False
        return super()._run_generate()

    def _run_install(self):
        self._cleanup_web_cache_after_success = self.i_source_var.get() == "Web release"
        self._cleanup_web_cache_root = Path(self.i_web_cache.get().strip())
        return super()._run_install()

    def _set_phase(self, phase: str):
        if phase == "Done" and getattr(self, "_cleanup_web_cache_after_success", False):
            self._cleanup_web_cache_after_success = False
            cache_root = getattr(self, "_cleanup_web_cache_root", None)
            if cache_root:
                super()._set_phase("Cleaning download cache")
                self._detail_var.set("Removing downloaded patch data...")
                try:
                    shutil.rmtree(_io_path(cache_root), ignore_errors=False)
                    self._log(f"[cache] removed after successful install: {cache_root}")
                except FileNotFoundError:
                    pass
                except Exception as exc:
                    # The patch itself has already succeeded. Cleanup failure is
                    # reported separately and must not turn it into a failed install.
                    self._log(f"[cache] cleanup failed: {exc}")
                    _safe_call(
                        self,
                        messagebox.showwarning,
                        "Cache cleanup",
                        "The patch installed successfully, but Sierra Patcher could not remove all downloaded cache files.\n\n"
                        f"Cache location:\n{cache_root}\n\n"
                        "You can delete this folder manually after closing the patcher.",
                    )
        return super()._set_phase(phase)


def main(dev: bool = False):
    _hide_console_on_windows()
    app = PolishedSierraPatcherGUI(dev=dev)
    app.mainloop()
