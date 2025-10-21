# sierra_patcher/main.py
from __future__ import annotations
import sys

# robust imports (work with/without package context)
try:
    from . import cli, gui
    from .flags import is_dev_mode
except ImportError:  # frozen exe starting main.py as a script
    import sierra_patcher.cli as cli
    import sierra_patcher.gui as gui
    from sierra_patcher.flags import is_dev_mode

def main(argv: list[str] | None = None) -> None:
    argv = sys.argv[1:] if argv is None else argv
    dev = is_dev_mode()
    if argv:
        return cli.run_cli(argv, dev=dev)
    else:
        return gui.main(dev=dev)

if __name__ == "__main__":
    main()
