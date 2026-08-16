# sierra_patcher/main.py
from __future__ import annotations
import sys
import traceback

# robust imports (work with/without package context)
try:
    from . import cli, gui_repository as gui
    from .dark_theme import install_dark_theme, present_main_window
    from .flags import is_dev_mode
    from .generation_guard import enable_generation_guard
    from .gui import _hide_console_on_windows
    from .patch_failure_hooks import enable_patch_failure_hooks
    from .runtime_requirement_hooks import enable_runtime_requirement_hooks
    from .session_log import session_log, start_session_logging
    from .source_integrity_hooks import enable_source_integrity_hooks
except ImportError:  # frozen exe starting main.py as a script
    import sierra_patcher.cli as cli
    import sierra_patcher.gui_repository as gui
    from sierra_patcher.dark_theme import install_dark_theme, present_main_window
    from sierra_patcher.flags import is_dev_mode
    from sierra_patcher.generation_guard import enable_generation_guard
    from sierra_patcher.gui import _hide_console_on_windows
    from sierra_patcher.patch_failure_hooks import enable_patch_failure_hooks
    from sierra_patcher.runtime_requirement_hooks import enable_runtime_requirement_hooks
    from sierra_patcher.session_log import session_log, start_session_logging
    from sierra_patcher.source_integrity_hooks import enable_source_integrity_hooks


def main(argv: list[str] | None = None) -> None:
    argv = sys.argv[1:] if argv is None else argv
    dev = is_dev_mode()

    # Open the session log first so the header, every print() in the engine
    # modules, and any startup failure all land in the same file.
    start_session_logging()

    # Install generation/runtime and patch-safety hooks before either CLI or GUI
    # dispatch. Source integrity wraps the hybrid generator; runtime discovery
    # then wraps that result so both manifests are produced for new releases.
    enable_patch_failure_hooks()
    enable_source_integrity_hooks()
    enable_runtime_requirement_hooks()

    try:
        if argv:
            return cli.run_cli(argv, dev=dev)

        _hide_console_on_windows()
        enable_generation_guard()
        app = gui.RepositorySierraPatcherGUI(dev=dev)
        app.apply_startup_language(present=False)

        # Tk swallows worker-callback exceptions by printing them to a stderr
        # that does not exist in a windowed build. Route them to the log.
        def report_tk_exception(exc_type, exc_value, exc_traceback):
            session_log().write(
                "Unhandled Tk callback exception:\n"
                + "".join(traceback.format_exception(exc_type, exc_value, exc_traceback))
            )

        app.report_callback_exception = report_tk_exception

        # Keep the first visible native frame from being painted with Windows'
        # light title bar before the DWM dark-mode attribute is ready.
        app.withdraw()
        install_dark_theme(app)
        present_main_window(app)
        app.mainloop()
    except BaseException:
        session_log().write("Fatal error:\n" + traceback.format_exc())
        raise
    finally:
        session_log().write("Session ended.")
        session_log().close()


if __name__ == "__main__":
    main()
