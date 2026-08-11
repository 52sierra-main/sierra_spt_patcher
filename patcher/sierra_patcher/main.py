# sierra_patcher/main.py
from __future__ import annotations
import sys

# robust imports (work with/without package context)
try:
    from . import cli, gui_repository as gui
    from .dark_theme import install_dark_theme, present_main_window
    from .flags import is_dev_mode
    from .generation_guard import enable_generation_guard
    from .gui import _hide_console_on_windows
    from .runtime_requirement_hooks import enable_runtime_requirement_hooks
    from .source_integrity_hooks import enable_source_integrity_hooks
except ImportError:  # frozen exe starting main.py as a script
    import sierra_patcher.cli as cli
    import sierra_patcher.gui_repository as gui
    from sierra_patcher.dark_theme import install_dark_theme, present_main_window
    from sierra_patcher.flags import is_dev_mode
    from sierra_patcher.generation_guard import enable_generation_guard
    from sierra_patcher.gui import _hide_console_on_windows
    from sierra_patcher.runtime_requirement_hooks import enable_runtime_requirement_hooks
    from sierra_patcher.source_integrity_hooks import enable_source_integrity_hooks


def main(argv: list[str] | None = None) -> None:
    argv = sys.argv[1:] if argv is None else argv
    dev = is_dev_mode()

    # Install generation post-processing hooks before either CLI or GUI dispatch.
    # Source integrity wraps the hybrid generator first; runtime discovery then
    # wraps that result so both manifests are produced for every new release.
    enable_source_integrity_hooks()
    enable_runtime_requirement_hooks()

    if argv:
        return cli.run_cli(argv, dev=dev)

    _hide_console_on_windows()
    enable_generation_guard()
    app = gui.RepositorySierraPatcherGUI(dev=dev)

    # Keep the first visible native frame from being painted with Windows' light
    # title bar before the DWM dark-mode attribute is ready.
    app.withdraw()
    install_dark_theme(app)
    present_main_window(app)
    app.mainloop()


if __name__ == "__main__":
    main()
