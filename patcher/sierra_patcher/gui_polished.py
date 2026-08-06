from __future__ import annotations

import os
import shutil
import tkinter as tk
from functools import lru_cache
from pathlib import Path
from tkinter import messagebox, ttk

try:
    import winreg
except ImportError:  # pragma: no cover - Sierra Patcher is Windows-targeted
    winreg = None

from .gui import _hide_console_on_windows, _safe_call
from .gui_catalog import CatalogSierraPatcherGUI
from .paths import WORKING_DIR
from .web_catalog import CATALOG_PLACEHOLDER
from .web_download import _io_path


@lru_cache(maxsize=1)
def _native_cpu_info() -> dict[str, str]:
    """Return CPU branding without spawning WMIC/PowerShell/helper consoles.

    py-cpuinfo may invoke console utilities internally on Windows. The base GUI
    refreshed that probe whenever status changed, which could briefly flash
    console windows even though Sierra's own child processes are hidden.
    """

    if os.name == "nt" and winreg is not None:
        try:
            key_path = r"HARDWARE\DESCRIPTION\System\CentralProcessor\0"
            with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, key_path) as key:
                value, _ = winreg.QueryValueEx(key, "ProcessorNameString")
            brand = str(value).strip()
            if brand:
                return {"brand_raw": brand}
        except Exception:
            pass

    # Environment fallback is intentionally subprocess-free as well.
    brand = (os.environ.get("PROCESSOR_IDENTIFIER") or "CPU").strip() or "CPU"
    return {"brand_raw": brand}


class PolishedSierraPatcherGUI(CatalogSierraPatcherGUI):
    """Catalog GUI with stronger required-field guidance and success cleanup."""

    _REQUIRED_BG = "#fff0c2"
    _REQUIRED_FG = "#7a4d00"
    _READY_BG = "#e6f4ea"
    _READY_FG = "#216e39"
    _MANAGED_CACHE_DIRS = ("objects", "packages", "manifests")

    def _refresh_status(self):
        """Refresh status without allowing py-cpuinfo to spawn helper consoles."""

        # Keep the existing status implementation centralized in gui.py for
        # now, but replace only its CPU probe for the duration of this refresh.
        # Tk status refreshes run on the GUI thread, so this temporary swap does
        # not race Sierra's worker threads.
        from . import gui as base_gui

        original_probe = base_gui.cpuinfo.get_cpu_info
        base_gui.cpuinfo.get_cpu_info = _native_cpu_info
        try:
            return super()._refresh_status()
        finally:
            base_gui.cpuinfo.get_cpu_info = original_probe

    def _build_install_tab(self, nb) -> ttk.Frame:
        root = super()._build_install_tab(nb)

        source_frame = self.i_web_release.master
        target_frame = self.i_dest.master

        # Give the version hint its own row instead of overlapping the cache row.
        for widget in source_frame.grid_slaves():
            if widget is self._release_hint:
                continue
            info = widget.grid_info()
            row = int(info.get("row", 0))
            if row >= 2:
                widget.grid_configure(row=row + 1)
        self._release_hint.grid_configure(row=2, column=1, columnspan=2)

        # Rename/bolden the two values a normal web install actually requires.
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
        # partially ignore fieldbackground, so badges remain the reliable cue.
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
        # Generation also uses the shared phase name "Done". Never allow it to
        # inherit a stale install-cleanup flag.
        self._cleanup_web_cache_after_success = False
        return super()._run_generate()

    def _run_install(self):
        self._cleanup_web_cache_after_success = self.i_source_var.get() == "Web release"
        cache_text = self.i_web_cache.get().strip()
        self._cleanup_web_cache_root = Path(cache_text or (Path(WORKING_DIR) / "web_cache"))
        return super()._run_install()

    def _clear_managed_web_cache(self, cache_root: Path) -> None:
        """Delete all Sierra-managed web cache data without deleting unrelated files."""
        cache_root = cache_root.resolve()
        for dirname in self._MANAGED_CACHE_DIRS:
            managed = cache_root / dirname
            if os.path.exists(_io_path(managed)):
                shutil.rmtree(_io_path(managed), ignore_errors=False)

        # Remove the root itself only when nothing else is stored there.
        try:
            os.rmdir(_io_path(cache_root))
        except FileNotFoundError:
            pass
        except OSError:
            # Non-empty is expected if the user deliberately chose a directory
            # that also contains unrelated data.
            pass

    def _set_phase(self, phase: str):
        if phase == "Done" and getattr(self, "_cleanup_web_cache_after_success", False):
            self._cleanup_web_cache_after_success = False
            cache_root = getattr(self, "_cleanup_web_cache_root", None)
            if cache_root:
                super()._set_phase("Cleaning download cache")
                _safe_call(self, self._detail_var.set, "Removing downloaded patch data...")
                try:
                    self._clear_managed_web_cache(cache_root)
                    self._log(f"[cache] cleared after successful install: {cache_root}")
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
                        "You can delete the objects, packages, and manifests folders manually after closing the patcher.",
                    )
        return super()._set_phase(phase)


def main(dev: bool = False):
    _hide_console_on_windows()
    app = PolishedSierraPatcherGUI(dev=dev)
    app.mainloop()
