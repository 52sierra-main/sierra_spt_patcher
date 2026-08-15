from __future__ import annotations

import os
import tempfile
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock


GUI_ENVIRONMENT = os.name == "nt" or bool(os.environ.get("DISPLAY"))

if GUI_ENVIRONMENT:
    import tkinter as tk
    from tkinter import ttk

    from sierra_patcher import (
        dark_theme,
        gui,
        gui_catalog,
        gui_hybrid,
        gui_layout,
        gui_polished,
        gui_repository,
        gui_web,
        i18n,
    )
    from sierra_patcher.version_preflight import (
        VersionPreflightResult,
        VersionPreflightStatus,
    )
    from sierra_patcher.web_catalog import CatalogRelease


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
        # The log widget is read-only now; go through the normal append path.
        self.app._append_log("language-state-sentinel")
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

    def test_install_mode_defaults_to_automatic_and_preserves_destination(self) -> None:
        self.assertEqual(self.app.i_install_mode_var.get(), "auto")
        self.assertEqual(self.app.i_destination_label.cget("text"), "New SPT folder")
        self.assertTrue(self.app._live_source_frame.grid_info())

        destination = r"X:\SPT\3.11.4"
        self.app.i_dest_var.set(destination)
        self.app.i_install_mode_var.set("existing")
        self.app._sync_install_mode_ui()

        self.assertEqual(self.app.i_dest_var.get(), destination)
        self.assertEqual(self.app.i_destination_label.cget("text"), "Destination to patch")
        self.assertFalse(self.app._live_source_frame.grid_info())

    def test_install_mode_radio_hover_keeps_dark_background(self) -> None:
        dark_theme._configure_ttk_styles(self.app)
        style = ttk.Style(self.app)

        self.assertEqual(style.lookup("TRadiobutton", "background"), dark_theme.WINDOW_BG)
        self.assertEqual(
            style.lookup("TRadiobutton", "background", ("active",)),
            dark_theme.WINDOW_BG,
        )
        self.assertEqual(
            style.lookup("TRadiobutton", "background", ("selected", "active")),
            dark_theme.WINDOW_BG,
        )

    def test_empty_automatic_copy_destination_is_ready(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            live = root / "Live"
            destination = root / "SPT"
            live.mkdir()
            destination.mkdir()
            (live / "EscapeFromTarkov.exe").touch()
            installation = {"install_path": str(live)}
            self.app.i_source_var.set("Web release")
            self.app._catalog_release_details = {
                "3.11.4": CatalogRelease("3.11.4", "1.1.0.46699")
            }
            self.app.i_web_release.configure(values=("choose version", "3.11.4"))
            self.app.i_web_release_var.set("3.11.4")

            with (
                mock.patch.object(gui, "query_install", return_value=installation),
                mock.patch.object(gui, "exe_version", return_value="1.1.0.46699"),
                mock.patch.object(gui_layout, "query_install", return_value=installation),
                mock.patch.object(gui_layout, "exe_version", return_value="1.1.0.46699"),
                mock.patch.object(gui_polished, "query_install", return_value=installation),
                mock.patch.object(gui_polished, "exe_version", return_value="1.1.0.46699"),
            ):
                self.app.i_dest_var.set(str(destination))
                self.app._validate_install_ready()

            self.assertEqual(self.app._destination_badge.cget("text"), "READY  ✓")
            self.assertEqual(self.app.i_dest.cget("style"), "Ready.TEntry")
            self.assertNotIn("disabled", self.app.btn_install.state())

    def test_live_folder_is_never_a_valid_destination(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            live = Path(temporary) / "Live"
            live.mkdir()
            (live / "EscapeFromTarkov.exe").touch()
            installation = {"install_path": str(live)}
            with mock.patch.object(gui_layout, "query_install", return_value=installation):
                self.app.i_install_mode_var.set("existing")
                self.app.i_dest_var.set(str(live))
                self.app.i_force.set(True)
                self.app._sync_install_mode_ui()

                self.assertFalse(self.app._destination_ready_for_install())
                self.assertIn("disabled", self.app.btn_install.state())
                self.assertEqual(self.app._destination_badge.cget("text"), "INVALID")

    def test_destination_cannot_overlap_download_cache(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            live = root / "Live"
            cache = root / "cache"
            destination = cache / "packages" / "3.11.4" / "SPT"
            live.mkdir()
            (live / "EscapeFromTarkov.exe").touch()
            installation = {"install_path": str(live)}
            with (
                mock.patch.object(gui, "query_install", return_value=installation),
                mock.patch.object(gui, "exe_version", return_value="1.1.0.46699"),
                mock.patch.object(gui_layout, "query_install", return_value=installation),
                mock.patch.object(gui_layout, "exe_version", return_value="1.1.0.46699"),
                mock.patch.object(gui_polished, "query_install", return_value=installation),
                mock.patch.object(gui_polished, "exe_version", return_value="1.1.0.46699"),
            ):
                self.app.i_web_cache.delete(0, "end")
                self.app.i_web_cache.insert(0, str(cache))
                self.app.i_dest_var.set(str(destination))
                self.app._validate_install_ready()

                self.assertFalse(self.app._destination_ready_for_install())
                self.assertIn("disabled", self.app.btn_install.state())
                self.assertEqual(
                    self.app._destination_validation_text(),
                    "The destination and cache folders must be separate.",
                )

    def test_existing_copy_preflight_uses_destination_version(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            live = root / "Live"
            destination = root / "Copy"
            live.mkdir()
            destination.mkdir()
            (live / "EscapeFromTarkov.exe").touch()
            (destination / "EscapeFromTarkov.exe").touch()
            installation = {"install_path": str(live)}
            self.app.i_source_var.set("Web release")
            self.app.i_install_mode_var.set("existing")
            self.app._catalog_release_details = {
                "3.11.4": CatalogRelease("3.11.4", "1.1.0.46699")
            }
            self.app.i_web_release.configure(values=("choose version", "3.11.4"))
            self.app.i_web_release_var.set("3.11.4")
            self.app.i_dest_var.set(str(destination))

            def version(path):
                return "1.1.0.47000" if Path(path).parent == live else "1.1.0.46699"

            with (
                mock.patch.object(gui_polished, "query_install", return_value=installation),
                mock.patch.object(gui_polished, "exe_version", side_effect=version),
            ):
                result = self.app._version_preflight()

            self.assertIsNotNone(result)
            self.assertEqual(result.status, VersionPreflightStatus.READY)

    def test_existing_copy_requires_confirmation_when_live_detection_fails(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            destination = Path(temporary) / "Copy"
            destination.mkdir()
            self.app.i_install_mode_var.set("existing")
            self.app.i_dest_var.set(str(destination))
            with (
                mock.patch.object(gui_layout, "query_install", return_value=None),
                mock.patch.object(gui_layout.messagebox, "askyesno", return_value=False) as confirm,
                mock.patch.object(
                    gui_polished.PolishedSierraPatcherGUI,
                    "_run_install",
                ) as install,
            ):
                gui_layout.LayoutSierraPatcherGUI._run_install(self.app)

            confirm.assert_called_once()
            install.assert_not_called()

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

    def test_version_mismatch_is_red_localized_and_stops_before_download(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            live = root / "live"
            destination = root / "destination"
            live.mkdir()
            destination.mkdir()
            (live / "EscapeFromTarkov.exe").touch()

            self.app.i_source_var.set("Web release")
            self.app._catalog_release_details = {
                "3.11.4": CatalogRelease("3.11.4", "1.1.0.46699")
            }
            self.app.i_web_release.configure(values=("choose version", "3.11.4"))
            self.app.i_web_release_var.set("3.11.4")
            self.app.i_dest_var.set(str(destination))

            installation = {"install_path": str(live)}
            with (
                mock.patch.object(gui, "query_install", return_value=installation),
                mock.patch.object(gui, "exe_version", return_value="1.1.0.46657"),
                mock.patch.object(gui_layout, "query_install", return_value=installation),
                mock.patch.object(gui_layout, "exe_version", return_value="1.1.0.46657"),
                mock.patch.object(gui_polished, "query_install", return_value=installation),
                mock.patch.object(
                    gui_polished,
                    "exe_version",
                    return_value="1.1.0.46657",
                ),
            ):
                self.app._validate_install_ready()

                self.assertEqual(
                    self.app._destination_badge.cget("text"),
                    "UPDATE LIVE  ⚠",
                )
                self.assertEqual(
                    self.app._destination_badge.cget("bg"),
                    self.app._WARNING_BG,
                )
                self.assertIn("disabled", self.app.btn_install.state())
                self.assertEqual(
                    self.app._dest_hint.cget("text"),
                    "1.1.0.46657 → 1.1.0.46699",
                )

                with (
                    mock.patch.object(gui_polished.messagebox, "showwarning") as warning,
                    mock.patch.object(
                        gui_catalog.CatalogSierraPatcherGUI,
                        "_run_install",
                    ) as download,
                ):
                    gui_polished.PolishedSierraPatcherGUI._run_install(self.app)

                warning.assert_called_once()
                download.assert_not_called()

                self.app._change_language("ko")
                self.app.update()

                self.assertEqual(
                    self.app._destination_badge.cget("text"),
                    "본섭 업데이트  ⚠",
                )
                self.assertEqual(
                    self.app._dest_hint.cget("text"),
                    "1.1.0.46657 → 1.1.0.46699",
                )

                self.app.i_force.set(True)
                self.assertNotIn("disabled", self.app.btn_install.state())
                self.assertEqual(
                    self.app._destination_badge.cget("text"),
                    "본섭 업데이트  ⚠",
                )

    def test_download_failure_does_not_start_automatic_copy(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            live = root / "Live"
            destination = root / "SPT"
            live.mkdir()
            (live / "EscapeFromTarkov.exe").touch()
            installation = {"install_path": str(live)}
            self.app.i_source_var.set("Web release")
            self.app._catalog_release_details = {
                "3.11.4": CatalogRelease("3.11.4", "1.1.0.46699")
            }
            self.app.i_web_release.configure(values=("choose version", "3.11.4"))
            self.app.i_web_release_var.set("3.11.4")

            class FailingSource:
                def __init__(self, *_args, **_kwargs):
                    pass

                def prepare(self, **_kwargs):
                    raise RuntimeError("download failed")

            with (
                mock.patch.object(gui, "query_install", return_value=installation),
                mock.patch.object(gui, "exe_version", return_value="1.1.0.46699"),
                mock.patch.object(gui_layout, "query_install", return_value=installation),
                mock.patch.object(gui_layout, "exe_version", return_value="1.1.0.46699"),
                mock.patch.object(gui_polished, "query_install", return_value=installation),
                mock.patch.object(gui_polished, "exe_version", return_value="1.1.0.46699"),
                mock.patch.object(gui_web, "WebPackageSource", FailingSource),
                mock.patch.object(gui_web, "copy_live_game") as copy_live_game,
                mock.patch.object(gui_web.messagebox, "showerror"),
            ):
                self.app.i_dest_var.set(str(destination))
                self.app._run_install()
                deadline = time.monotonic() + 2

                def wait_for_install():
                    if not self.app._install_running or time.monotonic() >= deadline:
                        self.app.quit()
                    else:
                        self.app.after(10, wait_for_install)

                self.app.after(10, wait_for_install)
                self.app.mainloop()

            self.assertFalse(self.app._install_running)
            copy_live_game.assert_not_called()
            self.assertFalse(destination.exists())

    def test_archived_preparation_failure_does_not_start_automatic_copy(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            live = root / "Live"
            destination = root / "SPT"
            live.mkdir()
            (live / "EscapeFromTarkov.exe").touch()
            installation = {"install_path": str(live)}
            self.app.i_source_var.set("Archived snapshot")

            class FailingSource:
                def prepare(self, **_kwargs):
                    raise RuntimeError("archived preparation failed")

            with (
                mock.patch.object(gui, "query_install", return_value=installation),
                mock.patch.object(gui, "exe_version", return_value="1.1.0.46699"),
                mock.patch.object(gui_layout, "query_install", return_value=installation),
                mock.patch.object(gui_layout, "exe_version", return_value="1.1.0.46699"),
                mock.patch.object(gui_web, "LocalPackageSource", FailingSource),
                mock.patch.object(gui_web, "copy_live_game") as copy_live_game,
                mock.patch.object(gui_web, "check_resources"),
                mock.patch.object(gui_web.messagebox, "showerror"),
            ):
                self.app.i_dest_var.set(str(destination))
                gui_web.IntegratedSierraPatcherGUI._run_install(self.app)
                deadline = time.monotonic() + 2

                def wait_for_install():
                    if not self.app._install_running or time.monotonic() >= deadline:
                        self.app.quit()
                    else:
                        self.app.after(10, wait_for_install)

                self.app.after(10, wait_for_install)
                self.app.mainloop()

            self.assertFalse(self.app._install_running)
            copy_live_game.assert_not_called()
            self.assertFalse(destination.exists())

    def test_archived_preflight_stop_clears_pending_cleanup(self) -> None:
        with (
            mock.patch.object(self.app, "_snapshot_ready", return_value=True),
            mock.patch.object(
                gui_hybrid,
                "read_archived_snapshot",
                return_value=SimpleNamespace(package_id="3.11.4"),
            ),
            mock.patch.object(
                gui_layout.LayoutSierraPatcherGUI,
                "_run_install",
                return_value=None,
            ),
        ):
            self.app.i_source_var.set("Archived snapshot")
            self.app.i_archive_path_var.set("snapshot")
            gui_hybrid.HybridSierraPatcherGUI._run_install(self.app)

        self.assertIsNone(self.app._offline_source_config)
        self.assertFalse(self.app._archived_cleanup_pending)
        self.assertIsNone(self.app._archived_cleanup_package_id)
        self.assertIsNone(self.app._archived_cleanup_cache)

    def test_automatic_copy_runs_after_package_preparation(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            live = root / "Live"
            destination = root / "SPT"
            storage = root / "package" / "storage"
            patch_root = root / "package" / "patchfiles"
            live.mkdir()
            storage.mkdir(parents=True)
            patch_root.mkdir(parents=True)
            (live / "EscapeFromTarkov.exe").touch()
            installation = {"install_path": str(live)}
            order = []
            self.app.i_source_var.set("Web release")
            self.app._catalog_release_details = {
                "3.11.4": CatalogRelease("3.11.4", "1.1.0.46699")
            }
            self.app.i_web_release.configure(values=("choose version", "3.11.4"))
            self.app.i_web_release_var.set("3.11.4")
            self.app.i_web_cache.delete(0, "end")
            self.app.i_web_cache.insert(0, str(root / "cache"))

            class PreparedSource:
                def __init__(self, *_args, **_kwargs):
                    pass

                def prepare(self, **_kwargs):
                    order.append("package")
                    return SimpleNamespace(storage_root=storage, patch_root=patch_root)

            def copy_game(*_args, **_kwargs):
                order.append("copy")
                destination.mkdir()
                (destination / "EscapeFromTarkov.exe").touch()
                (destination / "SPT.Launcher.exe").touch()

            def apply_patches(*_args, **_kwargs):
                order.append("patch")
                return 0, 0, 0

            def finalize_install(*_args, **_kwargs):
                order.append("finalize")

            def apply_storage(*_args, **_kwargs):
                order.append("storage")

            metadata = SimpleNamespace(
                version="1.1.0.46699",
                integrity_folders={},
                runtime_requirements=None,
                dependencies=None,
                title="3.11.4",
            )
            with (
                mock.patch.object(gui, "query_install", return_value=installation),
                mock.patch.object(gui, "exe_version", return_value="1.1.0.46699"),
                mock.patch.object(gui_layout, "query_install", return_value=installation),
                mock.patch.object(gui_layout, "exe_version", return_value="1.1.0.46699"),
                mock.patch.object(gui_polished, "query_install", return_value=installation),
                mock.patch.object(gui_polished, "exe_version", return_value="1.1.0.46699"),
                mock.patch.object(gui_web, "query_install", return_value=installation),
                mock.patch.object(gui_web, "exe_version", return_value="1.1.0.46699"),
                mock.patch.object(gui_web, "WebPackageSource", PreparedSource),
                mock.patch.object(gui_web, "copy_live_game", side_effect=copy_game),
                mock.patch.object(gui_web, "apply_all_patches", side_effect=apply_patches),
                mock.patch.object(gui_web.Meta, "read", return_value=metadata),
                mock.patch.object(gui_web, "missing_requirements_for_metadata", return_value=[]),
                mock.patch.object(gui_web, "count_patch_files", return_value=0),
                mock.patch.object(
                    gui_web,
                    "finalize",
                    side_effect=finalize_install,
                ) as finalize_call,
                mock.patch.object(
                    gui_web,
                    "apply_storage",
                    side_effect=apply_storage,
                ) as apply_storage_call,
                mock.patch.object(gui_web.messagebox, "showinfo"),
            ):
                self.app.i_dest_var.set(str(destination))
                self.app._run_install()
                deadline = time.monotonic() + 2

                def wait_for_install():
                    if not self.app._install_running or time.monotonic() >= deadline:
                        self.app.quit()
                    else:
                        self.app.after(10, wait_for_install)

                self.app.after(10, wait_for_install)
                self.app.mainloop()

                other_destination = root / "Other"
                other_destination.mkdir()
                (other_destination / "unrelated.txt").touch()
                self.app.i_dest_var.set(str(other_destination))
                self.app._validate_install_ready()
                self.assertEqual(self.app._destination_badge.cget("text"), "INVALID")
                self.assertIn("disabled", self.app.btn_install.state())

                self.app.i_dest_var.set(str(destination))
                self.app._validate_install_ready()

            self.assertFalse(self.app._install_running)
            self.assertEqual(self.app._completed_auto_copy_destination, str(destination))
            self.assertEqual(self.app._destination_badge.cget("text"), "READY  ✓")
            self.assertEqual(self.app.i_dest.cget("style"), "Ready.TEntry")
            self.assertFalse(self.app._dest_hint.grid_info())
            self.assertIn("disabled", self.app.btn_install.state())
            self.assertEqual(self.app._phase_var.get(), "Done")
            self.assertEqual(self.app._detail_var.get(), "")
            self.assertEqual(order, ["package", "copy", "patch", "finalize", "storage"])
            finalize_call.assert_called_once_with(
                str(destination),
                str(storage / "delete_list.txt"),
            )
            self.assertEqual(apply_storage_call.call_args.args[:2], (storage, str(destination)))

    def test_failed_cache_cleanup_clears_completed_detail(self) -> None:
        self.app._cleanup_web_cache_after_success = True
        self.app._cleanup_web_cache_root = Path("missing-cache")
        self.app._detail_var.set("stale detail")

        with (
            mock.patch.object(
                self.app,
                "_clear_managed_web_cache",
                side_effect=OSError("locked"),
            ),
            mock.patch.object(gui_polished.messagebox, "showwarning") as warning,
        ):
            self.app._set_phase("Done")
            self.app.update()

        self.assertEqual(self.app._phase_var.get(), "Done")
        self.assertEqual(self.app._detail_var.get(), "")
        warning.assert_called_once()

    def test_preflight_statuses_use_compact_display(self) -> None:
        self.assertEqual(int(self.app._dest_hint.grid_info()["columnspan"]), 3)
        cases = (
            (VersionPreflightStatus.READY, "READY  ✓", "", False),
            (VersionPreflightStatus.VERSION_CHECKING, "CHECKING...", "", False),
            (
                VersionPreflightStatus.UPDATE_REQUIRED,
                "UPDATE LIVE  ⚠",
                "1.1.0.46657 → 1.1.0.46699",
                True,
            ),
            (
                VersionPreflightStatus.PATCH_UPDATE_REQUIRED,
                "PATCH UPDATE  ⚠",
                "Supported 1.1.0.46699 · Current 1.1.0.47000",
                True,
            ),
            (
                VersionPreflightStatus.SOURCE_MISMATCH,
                "FOLDER MISMATCH  ⚠",
                "Found 1.1.0.46657 · Required 1.1.0.46699",
                True,
            ),
            (
                VersionPreflightStatus.VERSION_UNKNOWN,
                "VERSION UNKNOWN  ⚠",
                "Couldn’t read game version",
                True,
            ),
            (
                VersionPreflightStatus.CATALOG_UNVERIFIED,
                "UNVERIFIED  ⚠",
                "No version data · Checked after download",
                True,
            ),
        )
        color_by_status = {
            VersionPreflightStatus.READY: (self.app._READY_BG, self.app._READY_FG),
            VersionPreflightStatus.VERSION_CHECKING: (
                self.app._UNKNOWN_BG,
                self.app._UNKNOWN_FG,
            ),
            VersionPreflightStatus.CATALOG_UNVERIFIED: (
                self.app._UNKNOWN_BG,
                self.app._UNKNOWN_FG,
            ),
        }

        for status, badge_text, hint_text, hint_visible in cases:
            with self.subTest(status=status):
                self.app._set_badge(self.app._destination_badge, ready=True)
                self.app._dest_hint.configure(text="stale")
                self.app._dest_hint.grid()
                result = VersionPreflightResult(
                    status,
                    "1.1.0.46699",
                    "1.1.0.47000"
                    if status == VersionPreflightStatus.PATCH_UPDATE_REQUIRED
                    else "1.1.0.46657",
                    "1.1.0.46657",
                )

                self.app._apply_version_preflight(result)

                expected_bg, expected_fg = color_by_status.get(
                    status,
                    (self.app._WARNING_BG, self.app._WARNING_FG),
                )
                self.assertEqual(self.app._destination_badge.cget("text"), badge_text)
                self.assertEqual(self.app._destination_badge.cget("bg"), expected_bg)
                self.assertEqual(self.app._destination_badge.cget("fg"), expected_fg)
                self.assertEqual(bool(self.app._dest_hint.grid_info()), hint_visible)
                if hint_visible:
                    self.assertEqual(self.app._dest_hint.cget("text"), hint_text)
                    self.assertEqual(
                        str(self.app._dest_hint.cget("foreground")),
                        expected_fg,
                    )
                    self.assertNotIn("\n", hint_text)

    def test_legacy_catalog_release_is_probed_automatically(self) -> None:
        self.app._catalog_release_details = {
            "3.11.4": CatalogRelease("3.11.4")
        }
        self.app.i_web_release.configure(values=("choose version", "3.11.4"))
        self.app.i_web_release_var.set("3.11.4")

        with mock.patch.object(
            gui_catalog,
            "probe_release_live_version",
            return_value="1.1.0.46699",
        ) as probe:
            self.app._on_release_selected()
            deadline = time.monotonic() + 2

            def wait_for_probe():
                if (
                    not self.app._selected_release_probe_loading()
                    or time.monotonic() >= deadline
                ):
                    self.app.quit()
                else:
                    self.app.after(10, wait_for_probe)

            self.app.after(10, wait_for_probe)
            self.app.mainloop()

        probe.assert_called_once()
        self.assertFalse(self.app._selected_release_probe_loading())
        self.assertEqual(
            self.app._selected_required_live_version(),
            "1.1.0.46699",
        )


if __name__ == "__main__":
    unittest.main()
