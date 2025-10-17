from __future__ import annotations
import argparse, os
from pathlib import Path
import time

from .paths import OUTPUT_DIR, PATCH_DIR, MISSING_DIR, STORAGE_DIR
from .system import check_resources, optimal_threads
from .registry import query_install, exe_version
from .metadata import Meta, stamp_from_game_exe
from .storage import pack_additional, apply_storage
from .zstd_patch import generate_patches, apply_all_patches, verify_patch_files
from .delete_list import build_delete_list, finalize
from .prereqs import ensure_prereqs

_DEF_DELETE_LIST = str(Path(STORAGE_DIR) / "delete_list.txt")
_DEF_INFO_PATH   = str(Path(STORAGE_DIR) / "metadata.info")


def _cmd_generate(args: argparse.Namespace) -> None:
    source = args.source
    dest   = args.dest
    if not source or not dest:
        raise SystemExit("Missing --source/--dest. Run with --help for usage.")

    os.makedirs(PATCH_DIR, exist_ok=True)
    os.makedirs(MISSING_DIR, exist_ok=True)
    os.makedirs(STORAGE_DIR, exist_ok=True)

    check_resources()
    threads = args.threads or optimal_threads()

    print("Creating ZSTD patches...")
    generate_patches(source, dest, PATCH_DIR, MISSING_DIR, workers=threads)
    pack_additional(MISSING_DIR, STORAGE_DIR)

    print("Building delete list...")
    build_delete_list(source, dest, _DEF_DELETE_LIST)

    if args.title and args.date:
        print("Stamping metadata...")
        stamp_from_game_exe(_DEF_INFO_PATH, source, args.title, args.date)
    else:
        print("Skipping metadata stamp (no --title/--date provided)")

    print("Verifying produced patches...")
    verify_patch_files()
    print("Generation complete →", OUTPUT_DIR)


def _cmd_install(args: argparse.Namespace) -> None:
    meta = Meta.read(STORAGE_DIR)
    print("Patcher metadata:")
    print(" Version     ", meta.version)
    print(" Release     ", meta.title)
    print(" Description ", meta.description)

    inst = query_install()
    if not inst:
        raise SystemExit("Tarkov installation not found.")
    print("Tarkov install:")
    print(" Path     ", inst.install_path)

    if not args.force:
        exe = Path(inst.install_path, "EscapeFromTarkov.exe")
        if exe_version(str(exe)) != meta.version:
            print("Warning, live client status mismatch metadata.")
            time.sleep(1)

    dest = args.dir
    if not dest:
        raise SystemExit("Missing --dir (destination to patch)")

    check_resources()
    threads = args.threads or optimal_threads()

    print("Applying patches...")
    ok = apply_all_patches(dest, workers=threads)
    print("Finalizing...")
    finalize(dest, _DEF_DELETE_LIST)
    apply_storage(STORAGE_DIR, dest)

    if args.prereqs:
        ensure_prereqs(interactive=not args.yes)

    if not ok:
        print("Some patches failed. See logs above.")
    else:
        print("Done. Have fun!")


def build_parser(dev: bool) -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="sierra-patcher", description="Sierra's patch tool")
    sub = p.add_subparsers(dest="cmd", required=False)

    # install is always available
    i = sub.add_parser("install", help="Apply an existing patch package")
    i.add_argument("--dir", type=str, help="Destination game folder to patch")
    i.add_argument("--threads", type=int, help="Worker threads")
    i.add_argument("--force", action="store_true", help="Bypass metadata checks")
    i.add_argument("--prereqs", action="store_true", help="Ensure .NET prerequisites")
    i.add_argument("-y", "--yes", action="store_true", help="Assume yes for prompts")
    i.set_defaults(func=_cmd_install)

    # generate only in dev mode
    if dev:
        g = sub.add_parser("generate", help="(dev) Create a patch package from dest vs source")
        g.add_argument("--source", type=str, help="Clean game folder")
        g.add_argument("--dest",   type=str, help="SPT target folder")
        g.add_argument("--threads", type=int, help="Worker threads")
        g.add_argument("--title", type=str, help="Release title (e.g., SPT 3.10)")
        g.add_argument("--date",  type=str, help="Date string to stamp")
        g.set_defaults(func=_cmd_generate)

    return p

def run_cli(argv: list[str] | None = None, dev: bool = False) -> None:
    parser = build_parser(dev)
    args = parser.parse_args(argv)
    if not getattr(args, "cmd", None):
        # Option B default: show GUI if no args
        from .gui import main as gui_main
        return gui_main(dev=dev)
    args.func(args)