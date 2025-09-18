"""
Single entrypoint for Sierra Patcher.
- If any CLI arguments are provided → delegate to CLI.
- If no arguments → launch GUI.
"""
from __future__ import annotations
import sys


from . import cli, gui


def main(argv: list[str] | None = None) -> None:
    argv = sys.argv[1:] if argv is None else argv
    if argv:
        cli.run_cli(argv)
    else:
        gui.main()


if __name__ == "__main__":
    main()