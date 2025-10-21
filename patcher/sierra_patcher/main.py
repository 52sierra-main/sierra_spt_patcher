"""
Single entrypoint for Sierra Patcher.
- If any CLI arguments are provided → delegate to CLI.
- If no arguments → launch GUI.
"""
from __future__ import annotations
import sys
from .flags import is_dev_mode
from . import cli, gui

def main(argv: list[str] | None = None) -> None:
    argv = sys.argv[1:] if argv is None else argv
    dev = is_dev_mode()
    #dev = True
    if argv:
        return cli.run_cli(argv, dev=dev)
    else:
        return gui.main(dev=dev)

if __name__ == "__main__":
    main()
