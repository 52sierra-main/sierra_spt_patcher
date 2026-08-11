from __future__ import annotations

import os
import unittest
from types import SimpleNamespace
from unittest import mock


GUI_ENVIRONMENT = os.name == "nt" or bool(os.environ.get("DISPLAY"))

if GUI_ENVIRONMENT:
    import tkinter as tk
    from tkinter import ttk

    from sierra_patcher import gui, gui_catalog, gui_repository, i18n


@unittest.skipUnless(GUI_ENVIRONMENT, "a graphical Tk environment is required")
class GuiLanguageSwitchTests(unittest.TestCase):
    def setUp(self) -> None:
        self.original_language = i18n.current_language()
        self.addCleanup(i18n.set_language, self.original_language)
        i18n.set_language("en")

        save_patch = mock.patch.object(i18n, "_save_language")
        self.save_language = save_patch.start()
        self.addCleanup(save_patch.stop)

        catalog_patch = mock.patch.object(
            gui_catalog.CatalogSierraPatcherGUI,
            "_load_release_catalog_async",
            return_value=None,
        )
        catalog_patch.start()
        self.addCleanup(catalog_patch.stop)

        try:
            self.app = gui_repository.RepositorySierraPatcherGUI(dev=False)
        except tk.TclError as exc:
            self.skipTest(f"Tk is unavailable: {exc}")
        self.addCleanup(self._destroy_app)
        self.app.apply_startup_language(present=False)
        self.app.update_idletasks()
        self.assertEqual(self.app.state(), "withdrawn")

    def _destroy_app(self) -> None:
        try:
            self.app.destroy()
        except tk.TclError:
            pass

    def test_switches_in_place_and_preserves_window_state(self) -> None:
        root_id = self.app.winfo_id()
        notebook = next(
            child
            for child in self.app.winfo_children()
            if isinstance(child, ttk.Notebook)
        )
        notebook.select(self.app._log_tab)
        selected_tab = notebook.select()

        destination = r"X:\Friend\Tarkov"
        self.app.i_dest_var.set(destination)
        self.app.i_force.set(True)
        self.app.log_text.insert("end", "language-state-sentinel\n")
        log_before = self.app.log_text.get("1.0", "end-1c")
        self.app._detail_var.set("3/9 objects cached")
        self.app.i_web_release.configure(
            values=("choose version", "Install", "4.0.1")
        )
        self.app.i_web_release_var.set("Install")

        self.app._language_buttons["ko"].invoke()
        self.app.update()

        self.assertEqual(self.app.winfo_id(), root_id)
        self.assertEqual(i18n.current_language(), "ko")
        self.assertEqual(self.app.i_dest_var.get(), destination)
        self.assertTrue(self.app.i_force.get())
        self.assertEqual(notebook.select(), selected_tab)
        self.assertEqual(self.app.log_text.get("1.0", "end-1c"), log_before)
        self.assertEqual(self.app._detail_var.get(), "객체 3/9 캐시 사용")
        self.assertEqual(self.app.i_web_release_var.get(), "Install")
        self.assertEqual(
            tuple(self.app.i_web_release.cget("values")),
            ("버전 선택", "Install", "4.0.1"),
        )
        self.assertEqual(self.app._stat["pat_title"].get(), "웹 릴리스: Install")
        self.assertEqual(notebook.tab(self.app._ins_tab, "text"), "설치")
        self.assertEqual(self.app._language_label.cget("text"), "언어")
        self.assertEqual(
            self.app._language_buttons["ko"].cget("style"),
            "LanguageSelected.TButton",
        )

        self.app._toggle_language()
        self.app.update()

        self.assertEqual(self.app.winfo_id(), root_id)
        self.assertEqual(i18n.current_language(), "en")
        self.assertEqual(self.app._detail_var.get(), "3/9 objects cached")
        self.assertEqual(self.app.i_web_release_var.get(), "Install")
        self.assertEqual(self.app._stat["pat_title"].get(), "Web release: Install")
        self.assertEqual(notebook.tab(self.app._ins_tab, "text"), "Install")

    def test_language_switcher_is_embedded_in_the_app_header(self) -> None:
        notebook = next(
            child
            for child in self.app.winfo_children()
            if isinstance(child, ttk.Notebook)
        )

        self.assertEqual(str(self.app.cget("menu")), "")
        self.assertEqual(int(self.app._language_toolbar.grid_info()["row"]), 0)
        self.assertEqual(int(notebook.grid_info()["row"]), 1)
        self.assertEqual(self.app._language_label.cget("text"), "Language")
        self.assertEqual(
            tuple(button.cget("text") for button in self.app._language_buttons.values()),
            ("English", "한국어"),
        )
        self.assertEqual(
            self.app._language_buttons["en"].cget("style"),
            "LanguageSelected.TButton",
        )

    def test_saved_korean_starts_localized_but_stays_withdrawn(self) -> None:
        self.app.destroy()
        i18n.set_language("ko")
        self.app = gui_repository.RepositorySierraPatcherGUI(dev=False)

        self.assertEqual(self.app.state(), "withdrawn")
        self.assertEqual(i18n.current_language(), "en")
        self.app.apply_startup_language(present=False)
        self.app.update_idletasks()

        notebook = next(
            child
            for child in self.app.winfo_children()
            if isinstance(child, ttk.Notebook)
        )
        self.assertEqual(i18n.current_language(), "ko")
        self.assertEqual(notebook.tab(self.app._ins_tab, "text"), "설치")
        self.assertEqual(self.app.state(), "withdrawn")

    def test_metadata_and_status_placeholders_keep_their_correct_origin(self) -> None:
        self.app.i_source_var.set("Local package")
        self.app.i_dest_var.set("Install")
        self.app._detail_var.set("Install")
        metadata = SimpleNamespace(version="1.0.0", title="Install")
        with (
            mock.patch.object(gui.Meta, "read", return_value=metadata),
            mock.patch.object(gui, "count_patch_files", return_value=0),
            mock.patch.object(gui, "query_install", side_effect=RuntimeError("missing")),
        ):
            self.app._change_language("ko")
            self.app.update()

        self.assertEqual(self.app._stat["pat_title"].get(), "Install")
        self.assertEqual(self.app.i_dest_var.get(), "Install")
        self.assertEqual(self.app._detail_var.get(), "Install")
        self.assertEqual(self.app._stat["tk_path"].get(), "오류")
        path_entry = next(
            widget
            for widget in self.app._widget_tree(self.app)
            if isinstance(widget, ttk.Entry)
            and str(widget.cget("textvariable")) == str(self.app._stat["tk_path"])
        )
        self.assertEqual(path_entry.get(), "오류")

    def test_save_failure_keeps_the_new_session_language(self) -> None:
        root_id = self.app.winfo_id()
        self.save_language.side_effect = OSError("read-only")
        with mock.patch.object(gui.messagebox, "showwarning") as warning:
            self.app._change_language("ko")
            self.app.update()

        notebook = next(
            child
            for child in self.app.winfo_children()
            if isinstance(child, ttk.Notebook)
        )
        self.assertEqual(self.app.winfo_id(), root_id)
        self.assertEqual(i18n.current_language(), "ko")
        self.assertEqual(notebook.tab(self.app._ins_tab, "text"), "설치")
        warning.assert_called_once()

    def test_running_task_blocks_language_change(self) -> None:
        self.app._install_running = True
        with mock.patch.object(gui.messagebox, "showwarning") as warning:
            self.app._language_buttons["ko"].invoke()

        self.assertEqual(i18n.current_language(), "en")
        self.assertEqual(
            self.app._language_buttons["en"].cget("style"),
            "LanguageSelected.TButton",
        )
        warning.assert_called_once()


if __name__ == "__main__":
    unittest.main()
