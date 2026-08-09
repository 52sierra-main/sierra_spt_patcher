# sierra_patcher/main.py
from __future__ import annotations
import sys

# robust imports (work with/without package context)
try:
    from . import cli, gui_repository as gui
    from .dark_theme import install_dark_theme
    from .flags import is_dev_mode
    from .gui import _hide_console_on_windows
except ImportError:  # frozen exe starting main.py as a script
    import sierra_patcher.cli as cli
    import sierra_patcher.gui_repository as gui
    from sierra_patcher.dark_theme import install_dark_theme
    from sierra_patcher.flags import is_dev_mode
    from sierra_patcher.gui import _hide_console_on_windows


def main(argv: list[str] | None = None) -> None:
    argv = sys.argv[1:] if argv is None else argv
    dev = is_dev_mode()
    if argv:
        return cli.run_cli(argv, dev=dev)

    _hide_console_on_windows()
    app = gui.RepositorySierraPatcherGUI(dev=dev)
    install_dark_theme(app)
    app.mainloop()


if __name__ == "__main__":
    main()
