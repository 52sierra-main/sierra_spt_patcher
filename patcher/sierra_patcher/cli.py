from __future__ import annotations

import argparse
import os
import time
from pathlib import Path

from .delete_list import build_delete_list, finalize
from .metadata import Meta, stamp_from_game_exe
from .prereqs import ensure_prereqs
from .registry import exe_version, query_install
from .storage import apply_storage, pack_additional
from .system import check_resources, optimal_threads
from .zstd_patch import apply_all_patches, generate_patches, verify_patch_files
from .paths import (
    OUTPUT_DIR,
    MISSING_out_DIR,
    MISSING_read_DIR,
    PATCH_out_DIR,
    PATCH_read_DIR,
    STORAGE_out_DIR,
    STORAGE_read_DIR,
)

_DEF_DELETE_LIST_out = str(Path(STORAGE_out_DIR) / "delete_list.txt")
_DEF_INFO_PATH_out = str(Path(STORAGE_out_DIR) / "metadata.info")
_DEF_DELETE_LIST_read = str(Path(STORAGE_read_DIR) / "delete_list.txt")
_DEF_INFO_PATH_read = str(Path(STORAGE_read_DIR) / "metadata.info")


_DIFF_PRESETS: dict[str, list[str]] = {
    "fast": ["-3"],
    "balanced": ["-10", "--long=27"],
    "aggressive": ["--ultra", "-19", "--long=31"],
}


def _resolve_diff(args: argparse.Namespace) -> tuple[str, list[str]]:
    prof = (getattr(args, "diff", None) or "balanced").strip().lower()
    if prof not in _DIFF_PRESETS:
        raise SystemExit(f"Invalid --diff '{prof}'. Choose one of: {', '.join(_DIFF_PRESETS)}")
    return prof, _DIFF_PRESETS[prof]


def _cmd_generate(args: argparse.Namespace) -> None:
    source = args.source
    dest = args.dest
    if not source or not dest:
        raise SystemExit("Missing --source/--dest. Run with --help for usage.")

    os.makedirs(PATCH_out_DIR, exist_ok=True)
    os.makedirs(MISSING_out_DIR, exist_ok=True)
    os.makedirs(STORAGE_out_DIR, exist_ok=True)

    check_resources()
    threads = args.threads or optimal_threads()

    diff_profile, zstd_args = _resolve_diff(args)

    print(f"Creating ZSTD patches (diff={diff_profile})...")
    generate_patches(
        source,
        dest,
        PATCH_out_DIR,
        MISSING_out_DIR,
        workers=threads,
        zstd_args=zstd_args,
    )

    pack_additional(MISSING_out_DIR, STORAGE_out_DIR)

    print("Building delete list...")
    build_delete_list(source, dest, _DEF_DELETE_LIST_out)

    if args.title and args.date:
        print("Stamping metadata...")
        stamp_from_game_exe(
            _DEF_INFO_PATH_out,
            source,
            args.title,
            args.date,
            diff_profile=diff_profile,
            zstd_patch_args=zstd_args,
        )
    else:
        print("Skipping metadata stamp (no --title/--date provided)")

    print("Verifying produced patches...")
    verify_patch_files()

    print("Generation complete →", OUTPUT_DIR)


def _cmd_install(args: argparse.Namespace) -> None:
    meta = Meta.read(STORAGE_read_DIR)

    print("Patcher metadata:")
    print(" Version ", meta.version)
    print(" Release ", meta.title)
    print(" Description ", meta.description)

    inst = query_install()
    if not inst:
        raise SystemExit("Tarkov installation not found.")

    print("Tarkov install:")
    print(" Path ", inst["install_path"])

    if not args.force:
        exe = Path(inst["install_path"], "EscapeFromTarkov.exe")
        if exe_version(str(exe)) != meta.version:
            print("Warning, live client status mismatch metadata.")
            time.sleep(1)

    dest = args.dir
    if not dest:
        raise SystemExit("Missing --dir (destination to patch)")

    check_resources()
    threads = args.threads or optimal_threads()

    if not args.skip_prereq_check:
        missing = ensure_prereqs(meta, interactive=False)
        if missing:
            raise SystemExit("Missing required .NET dependencies. Install them from the links above, then run again.")

    print("Applying patches...")
    ok = apply_all_patches(dest, workers=threads)

    print("Finalizing...")
    finalize(dest, _DEF_DELETE_LIST_read)

    apply_storage(STORAGE_read_DIR, dest)

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
    i.add_argument("--prereqs", action="store_true", help="Deprecated: dependencies are checked automatically and never auto-installed")
    i.add_argument("--skip-prereq-check", action="store_true", help="Skip .NET dependency preflight")
    i.add_argument("-y", "--yes", action="store_true", help="Assume yes for prompts")
    i.set_defaults(func=_cmd_install)

    # generate only in dev mode
    if dev:
        g = sub.add_parser("generate", help="(dev) Create a patch package from dest vs source")
        g.add_argument("--source", type=str, help="Clean game folder")
        g.add_argument("--dest", type=str, help="SPT target folder")
        g.add_argument("--threads", type=int, help="Worker threads")
        g.add_argument("--title", type=str, help="Release title (e.g., SPT 3.10)")
        g.add_argument("--date", type=str, help="Date string to stamp")
        g.add_argument(
            "--diff",
            choices=sorted(_DIFF_PRESETS.keys()),
            default="balanced",
            help="Diff aggressiveness: smaller patches cost more time/CPU/RAM",
        )
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
