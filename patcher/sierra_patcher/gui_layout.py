from __future__ import annotations

import tkinter as tk
from pathlib import Path
from tkinter import messagebox, ttk

from . import gui_web
from .gui import _hide_console_on_windows
from .gui_polished import PolishedSierraPatcherGUI
from .i18n import canonical_choice, tr


class LayoutSierraPatcherGUI(PolishedSierraPatcherGUI):
    """Polished GUI with a compact package-source layout and destination placeholder."""

    _DEST_PLACEHOLDER = "Select pasted Live folder"

    def _build_install_tab(self, nb) -> ttk.Frame:
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

        # A placeholder is visual guidance only. Validation and installation
        # treat it exactly like an empty destination.
        self._destination_placeholder_ready = True
        style = ttk.Style(self)
        style.configure("Placeholder.TEntry", foreground="#777777")
        self.i_dest.bind("<FocusIn>", self._clear_destination_placeholder, add="+")
        self.i_dest.bind("<FocusOut>", self._restore_destination_placeholder, add="+")
        self._restore_destination_placeholder()

        # Re-apply web/local state now that the advanced widgets have replaced
        # the hidden base controls.
        self._toggle_install_web_options()
        return root

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
        if canonical_choice(value, (self._DEST_PLACEHOLDER,)) == self._DEST_PLACEHOLDER:
            return ""
        return value

    def _clear_destination_placeholder(self, _event=None) -> None:
        if not getattr(self, "_destination_placeholder_ready", False):
            return
        if canonical_choice(
            (self.i_dest_var.get() or "").strip(),
            (self._DEST_PLACEHOLDER,),
        ) == self._DEST_PLACEHOLDER:
            self.i_dest_var.set("")
            try:
                self.i_dest.configure(style="Required.TEntry")
            except tk.TclError:
                pass

    def _restore_destination_placeholder(self, _event=None) -> None:
        if not getattr(self, "_destination_placeholder_ready", False):
            return
        if not (self.i_dest_var.get() or "").strip():
            self.i_dest_var.set(tr(self._DEST_PLACEHOLDER))
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
            if canonical_choice(
                (self.i_dest_var.get() or "").strip(),
                (self._DEST_PLACEHOLDER,),
            ) == self._DEST_PLACEHOLDER:
                try:
                    self.i_dest.configure(style="Placeholder.TEntry")
                except tk.TclError:
                    pass
        return result

    def _run_install(self):
        if not self._destination_value():
            messagebox.showerror(
                tr("Destination required"),
                tr("Select the pasted Live folder that Sierra Patcher should modify."),
            )
            self.i_dest.focus_set()
            return
        return super()._run_install()


def main(dev: bool = False):
    _hide_console_on_windows()
    app = LayoutSierraPatcherGUI(dev=dev)
    app.mainloop()
