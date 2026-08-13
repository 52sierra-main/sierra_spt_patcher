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

from . import gui_web
from .gui import _hide_console_on_windows, _safe_call
from .gui_catalog import CatalogSierraPatcherGUI
from .i18n import canonical_choice, tr
from .paths import WORKING_DIR
from .registry import exe_version, query_install
from .version_preflight import (
    VersionPreflightResult,
    VersionPreflightStatus,
    evaluate_version_preflight,
)
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
    _WARNING_BG = "#fde7e9"
    _WARNING_FG = "#b42318"
    _UNKNOWN_BG = "#eef0f2"
    _UNKNOWN_FG = "#475467"
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
            result = super()._refresh_status()
        finally:
            base_gui.cpuinfo.get_cpu_info = original_probe
        self._update_required_field_emphasis()
        return result

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

        # These explicit references keep behavior independent from translated labels.
        self.i_release_label.configure(
            text=tr("Version / Release"),
            font=("Segoe UI", 9, "bold"),
        )
        self.i_destination_label.configure(font=("Segoe UI", 9, "bold"))

        self._release_badge = tk.Label(
            source_frame,
            text=tr("REQUIRED"),
            bg=self._REQUIRED_BG,
            fg=self._REQUIRED_FG,
            font=("Segoe UI", 8, "bold"),
            padx=6,
            pady=2,
        )
        self._release_badge.grid(row=1, column=2, sticky="w", padx=(4, 8), pady=(6, 0))

        self._destination_badge = tk.Label(
            target_frame,
            text=tr("REQUIRED"),
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
        style.configure("Warning.TEntry", fieldbackground="#fff1f2")
        style.configure("Unknown.TEntry", fieldbackground="#f5f6f7")
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

        self._dest_hint.configure(wraplength=420)
        self._dest_hint.grid_configure(columnspan=3)
        self.i_force.trace_add("write", lambda *_: self._validate_install_ready())
        self._update_required_field_emphasis()
        return root

    def _set_badge(self, badge: tk.Label, ready: bool) -> None:
        if ready:
            badge.configure(
                text=tr("READY  ✓"),
                bg=self._READY_BG,
                fg=self._READY_FG,
            )
        else:
            badge.configure(
                text=tr("REQUIRED"),
                bg=self._REQUIRED_BG,
                fg=self._REQUIRED_FG,
            )

    def _destination_for_preflight(self) -> str:
        destination_reader = getattr(self, "_destination_value", None)
        if callable(destination_reader):
            return destination_reader()
        return (self.i_dest_var.get() or "").strip()

    def _version_preflight(self) -> VersionPreflightResult | None:
        if canonical_choice(self.i_source_var.get(), gui_web.PACKAGE_SOURCES) != "Web release":
            return None

        release_id = self.i_web_release_var.get().strip()
        if (
            not release_id
            or canonical_choice(release_id, (CATALOG_PLACEHOLDER,)) == CATALOG_PLACEHOLDER
            or release_id not in tuple(self.i_web_release.cget("values"))
        ):
            return None

        destination = self._destination_for_preflight()
        if not destination or not Path(destination).is_dir():
            return None

        required_version = self._selected_required_live_version()
        if self._selected_release_probe_loading():
            return VersionPreflightResult(
                VersionPreflightStatus.VERSION_CHECKING,
                None,
                None,
                None,
            )
        if not required_version:
            return evaluate_version_preflight(None, None, None)

        live_version = None
        try:
            installation = query_install()
            if installation:
                live_executable = Path(installation["install_path"]) / "EscapeFromTarkov.exe"
                live_version = exe_version(live_executable)
        except Exception:
            pass

        destination_version = None
        try:
            destination_executable = Path(destination) / "EscapeFromTarkov.exe"
            if destination_executable.is_file():
                destination_version = exe_version(destination_executable)
        except Exception:
            pass

        return evaluate_version_preflight(
            required_version,
            live_version,
            destination_version,
        )

    @staticmethod
    def _shown_version(value: str | None) -> str:
        return value or "—"

    def _preflight_hint(self, result: VersionPreflightResult) -> str:
        required = self._shown_version(result.required_version)
        live = self._shown_version(result.live_version)
        destination = self._shown_version(result.destination_version)

        if result.status == VersionPreflightStatus.UPDATE_REQUIRED:
            return f"{live} → {required}"
        if result.status == VersionPreflightStatus.PATCH_UPDATE_REQUIRED:
            return tr(
                "Supported {required} · Current {current}",
                current=live,
                required=required,
            )
        if result.status == VersionPreflightStatus.SOURCE_MISMATCH:
            return tr(
                "Found {destination} · Required {required}",
                destination=destination,
                required=required,
            )
        if result.status == VersionPreflightStatus.VERSION_UNKNOWN:
            return tr("Couldn’t read game version")
        if result.status == VersionPreflightStatus.CATALOG_UNVERIFIED:
            return tr("No version data · Checked after download")
        if result.status == VersionPreflightStatus.VERSION_CHECKING:
            return tr("Checking release compatibility...")
        return ""

    def _apply_version_preflight(self, result: VersionPreflightResult | None) -> None:
        if result is None:
            return
        if result.status == VersionPreflightStatus.READY:
            self._dest_hint.grid_remove()
            return

        if result.status == VersionPreflightStatus.VERSION_CHECKING:
            self._destination_badge.configure(
                text=tr("CHECKING..."),
                bg=self._UNKNOWN_BG,
                fg=self._UNKNOWN_FG,
            )
            entry_style = "Unknown.TEntry"
            hint_color = self._UNKNOWN_FG
        elif result.status == VersionPreflightStatus.CATALOG_UNVERIFIED:
            self._destination_badge.configure(
                text=tr("UNVERIFIED  ⚠"),
                bg=self._UNKNOWN_BG,
                fg=self._UNKNOWN_FG,
            )
            entry_style = "Unknown.TEntry"
            hint_color = self._UNKNOWN_FG
        else:
            badge_text = {
                VersionPreflightStatus.UPDATE_REQUIRED: "UPDATE LIVE  ⚠",
                VersionPreflightStatus.PATCH_UPDATE_REQUIRED: "PATCH UPDATE  ⚠",
                VersionPreflightStatus.SOURCE_MISMATCH: "FOLDER MISMATCH  ⚠",
                VersionPreflightStatus.VERSION_UNKNOWN: "VERSION UNKNOWN  ⚠",
            }[result.status]
            self._destination_badge.configure(
                text=tr(badge_text),
                bg=self._WARNING_BG,
                fg=self._WARNING_FG,
            )
            entry_style = "Warning.TEntry"
            hint_color = self._WARNING_FG

        try:
            self.i_dest.configure(style=entry_style)
        except tk.TclError:
            pass
        if result.status == VersionPreflightStatus.VERSION_CHECKING:
            self._dest_hint.grid_remove()
            return
        self._dest_hint.configure(
            text=self._preflight_hint(result),
            foreground=hint_color,
        )
        self._dest_hint.grid()

    def _update_required_field_emphasis(self) -> VersionPreflightResult | None:
        if not hasattr(self, "_destination_badge"):
            return None

        destination = self._destination_for_preflight()
        destination_ready = bool(destination and Path(destination).is_dir())
        self._set_badge(self._destination_badge, destination_ready)
        if not destination_ready:
            self._dest_hint.configure(foreground=self._REQUIRED_FG)
        try:
            self.i_dest.configure(style="Ready.TEntry" if destination_ready else "Required.TEntry")
        except tk.TclError:
            pass

        preflight = self._version_preflight()
        self._apply_version_preflight(preflight)

        web_mode = canonical_choice(self.i_source_var.get(), gui_web.PACKAGE_SOURCES) == "Web release"
        if not web_mode:
            self._release_badge.grid_remove()
            try:
                self.i_web_release.configure(style="TCombobox")
            except tk.TclError:
                pass
            return preflight

        self._release_badge.grid()
        release = self.i_web_release_var.get().strip()
        release_ready = bool(
            release
            and canonical_choice(release, (CATALOG_PLACEHOLDER,)) != CATALOG_PLACEHOLDER
            and release in tuple(self.i_web_release.cget("values"))
        )
        self._set_badge(self._release_badge, release_ready)
        try:
            self.i_web_release.configure(
                style="Ready.TCombobox" if release_ready else "Required.TCombobox"
            )
        except tk.TclError:
            pass
        return preflight

    def _validate_install_ready(self):
        result = super()._validate_install_ready()
        preflight = self._update_required_field_emphasis()
        force = bool(self.i_force.get())
        if getattr(self, "_install_running", False):
            self.btn_install.state(["disabled"])
        elif preflight is not None and preflight.blocks_download and (
            preflight.status == VersionPreflightStatus.VERSION_CHECKING or not force
        ):
            self.btn_install.state(["disabled"])
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
        preflight = self._version_preflight()
        force = bool(self.i_force.get())
        if preflight is not None and preflight.status == VersionPreflightStatus.VERSION_CHECKING:
            messagebox.showwarning(
                tr("Compatibility check"),
                self._preflight_hint(preflight),
            )
            self._validate_install_ready()
            return
        if preflight is not None and preflight.blocks_download and not force:
            self._log(
                f"[preflight] stopped before download: {preflight.status.value} "
                f"(live={preflight.live_version or '-'}, "
                f"destination={preflight.destination_version or '-'}, "
                f"required={preflight.required_version or '-'})"
            )
            messagebox.showwarning(
                tr("Compatibility check"),
                self._preflight_hint(preflight)
                + "\n\n"
                + tr("No patch data was downloaded."),
            )
            self._validate_install_ready()
            return
        if preflight is not None and preflight.blocks_download and force:
            self._log(f"[preflight] {preflight.status.value} bypassed by Force")

        self._cleanup_web_cache_after_success = (
            canonical_choice(self.i_source_var.get(), gui_web.PACKAGE_SOURCES) == "Web release"
        )
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
                _safe_call(self, self._detail_var.set, tr("Removing downloaded patch data..."))
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
                        tr("Cache cleanup"),
                        tr(
                            "The patch installed successfully, but Sierra Patcher could not remove all downloaded cache files.\n\n"
                            "Cache location:\n{cache_root}\n\n"
                            "You can delete the objects, packages, and manifests folders manually after closing the patcher.",
                            cache_root=cache_root,
                        ),
                    )
        return super()._set_phase(phase)


def main(dev: bool = False):
    _hide_console_on_windows()
    app = PolishedSierraPatcherGUI(dev=dev)
    app.mainloop()
