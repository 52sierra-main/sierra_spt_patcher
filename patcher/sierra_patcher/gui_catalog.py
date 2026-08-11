from __future__ import annotations

import threading
import tkinter as tk
from pathlib import Path
from tkinter import messagebox, ttk

from . import gui_web
from .gui_web import IntegratedSierraPatcherGUI
from .i18n import canonical_choice, tr
from .metadata import Meta
from .paths import STORAGE_read_DIR
from .web_catalog import CATALOG_PLACEHOLDER, fetch_release_catalog


class CatalogSierraPatcherGUI(IntegratedSierraPatcherGUI):
    """Integrated GUI with automatic local detection and online release catalog."""

    def _has_local_package(self) -> bool:
        metadata_path = Path(STORAGE_read_DIR) / "metadata.info"
        if not metadata_path.is_file():
            return False
        try:
            Meta.read(STORAGE_read_DIR)
            return True
        except Exception:
            return False

    def _build_install_tab(self, nb) -> ttk.Frame:
        root = super()._build_install_tab(nb)

        source_frame = self.i_web_release.master
        old_release_entry = self.i_web_release
        old_release_entry.grid_remove()

        self.i_web_release_var = tk.StringVar(value=tr(CATALOG_PLACEHOLDER))
        self.i_web_release = ttk.Combobox(
            source_frame,
            textvariable=self.i_web_release_var,
            state="disabled",
            values=(tr(CATALOG_PLACEHOLDER),),
        )
        self.i_web_release.grid(row=1, column=1, sticky="ew", padx=12, pady=(6, 0))
        self.i_web_release.bind(
            "<<ComboboxSelected>>",
            lambda _event: (self._validate_install_ready(), self._refresh_status()),
        )

        # Match the destination field's required marker.
        tk.Label(source_frame, text=" *", fg="#b00020").grid(
            row=1,
            column=0,
            sticky="e",
            padx=(0, 0),
            pady=(6, 0),
        )
        self._release_hint = ttk.Label(
            source_frame,
            text=tr("Version selection is required."),
            style="Hint.TLabel",
        )
        self._release_hint.grid(row=2, column=1, sticky="w", padx=12, pady=(2, 0))
        self._release_hint.grid_remove()

        self._web_install_widgets = [
            self.i_web_release,
            self.i_web_cache,
            self.i_download_workers,
            self.i_materialize_workers,
        ]

        # Prefer web delivery for normal public builds. A valid local package
        # beside the executable takes priority so standalone packages remain
        # zero-configuration/offline capable.
        if self._has_local_package():
            self.i_source_var.set(tr("Local package"))
        else:
            self.i_source_var.set(tr("Web release"))

        self._catalog_loading = False
        self._catalog_loaded = False
        self._catalog_error: str | None = None
        self._toggle_install_web_options()
        if canonical_choice(self.i_source_var.get(), gui_web.PACKAGE_SOURCES) == "Web release":
            self._load_release_catalog_async()
        return root

    def _toggle_install_web_options(self):
        # During the parent builder's first call, the replacement combobox has
        # not been created yet. Preserve its normal behavior for that moment.
        if not hasattr(self, "i_web_release_var"):
            return super()._toggle_install_web_options()

        enabled = canonical_choice(self.i_source_var.get(), gui_web.PACKAGE_SOURCES) == "Web release"
        self.i_web_cache.configure(state="normal" if enabled else "disabled")
        self.i_download_workers.configure(state="normal" if enabled else "disabled")
        self.i_materialize_workers.configure(state="normal" if enabled else "disabled")
        self.i_web_release.configure(state="readonly" if enabled else "disabled")

        if enabled and not self._catalog_loaded and not self._catalog_loading:
            self._load_release_catalog_async()

        self._refresh_status()
        self._validate_install_ready()

    def _load_release_catalog_async(self, force: bool = False) -> None:
        if self._catalog_loading:
            return
        if self._catalog_loaded and not force:
            return

        self._catalog_loading = True
        self._catalog_error = None
        self.i_web_release_var.set(tr(CATALOG_PLACEHOLDER))
        self.i_web_release.configure(values=(tr(CATALOG_PLACEHOLDER),))
        self._release_hint.configure(text=tr("Loading available versions..."))
        self._release_hint.grid()

        def worker():
            try:
                releases = fetch_release_catalog()
                error = None
            except Exception as exc:
                releases = []
                error = str(exc)

            def finish():
                self._catalog_loading = False
                self._catalog_loaded = error is None
                self._catalog_error = error

                values = (tr(CATALOG_PLACEHOLDER), *releases)
                self.i_web_release.configure(values=values)
                self.i_web_release_var.set(tr(CATALOG_PLACEHOLDER))

                if error:
                    self._release_hint.configure(
                        text=tr("Could not load versions. Check repository catalog.json.")
                    )
                    self._log(f"[catalog] load failed: {error}")
                elif not releases:
                    self._release_hint.configure(text=tr("No web releases are currently listed."))
                    self._log("[catalog] loaded: no releases")
                else:
                    self._release_hint.configure(text=tr("Version selection is required."))
                    self._log(f"[catalog] loaded {len(releases)} release(s)")

                if canonical_choice(self.i_source_var.get(), gui_web.PACKAGE_SOURCES) != "Web release":
                    self._release_hint.grid_remove()
                self._validate_install_ready()
                self._refresh_status()

            self.after(0, finish)

        threading.Thread(target=worker, daemon=True).start()

    def _validate_install_ready(self):
        # Parent __init__ can call this before the catalog widgets exist.
        if not hasattr(self, "i_web_release_var"):
            return super()._validate_install_ready()

        destination = (self.i_dest_var.get() or "").strip()
        valid_destination = bool(destination and Path(destination).is_dir())
        web_mode = canonical_choice(self.i_source_var.get(), gui_web.PACKAGE_SOURCES) == "Web release"
        release = self.i_web_release_var.get().strip()
        valid_release = not web_mode or bool(
            release
            and canonical_choice(release, (CATALOG_PLACEHOLDER,)) != CATALOG_PLACEHOLDER
            and release in tuple(self.i_web_release.cget("values"))
        )

        if valid_destination:
            self._dest_hint.grid_remove()
        else:
            self._dest_hint.configure(
                text=tr("Destination folder is required.")
                if not destination
                else tr("Folder does not exist.")
            )
            self._dest_hint.grid()

        if web_mode:
            if valid_release:
                self._release_hint.grid_remove()
            else:
                if self._catalog_loading:
                    self._release_hint.configure(text=tr("Loading available versions..."))
                elif self._catalog_error:
                    self._release_hint.configure(
                        text=tr("Could not load versions. Check repository catalog.json.")
                    )
                else:
                    self._release_hint.configure(text=tr("Version selection is required."))
                self._release_hint.grid()
        else:
            self._release_hint.grid_remove()

        if valid_destination and valid_release:
            self.btn_install.state(["!disabled"])
        else:
            self.btn_install.state(["disabled"])

    def _refresh_status(self):
        if not hasattr(self, "i_web_release_var"):
            return super()._refresh_status()
        if (
            canonical_choice(self.i_source_var.get(), gui_web.PACKAGE_SOURCES) == "Web release"
            and canonical_choice(self.i_web_release_var.get(), (CATALOG_PLACEHOLDER,)) == CATALOG_PLACEHOLDER
        ):
            # Let the normal status helper populate System/Tarkov/Destination,
            # then override only package information for the placeholder state.
            super()._refresh_status()
            self._stat["pat_version"].set("—")
            self._stat["pat_title"].set(tr("Choose version"))
            self._stat["pat_patches"].set(tr("Not prepared"))
            return
        super()._refresh_status()

    def _run_install(self):
        if (
            canonical_choice(self.i_source_var.get(), gui_web.PACKAGE_SOURCES) == "Web release"
            and canonical_choice(
                self.i_web_release_var.get().strip(),
                (CATALOG_PLACEHOLDER,),
            ) == CATALOG_PLACEHOLDER
        ):
            messagebox.showerror(
                tr("Version required"),
                tr("Choose a web release version first."),
            )
            self._validate_install_ready()
            return
        return super()._run_install()


def main(dev: bool = False):
    from .gui import _hide_console_on_windows

    _hide_console_on_windows()
    app = CatalogSierraPatcherGUI(dev=dev)
    app.mainloop()
