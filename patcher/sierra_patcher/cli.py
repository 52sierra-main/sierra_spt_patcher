from __future__ import annotations

import argparse
import os
import shutil
import time
from pathlib import Path

from .delete_list import build_delete_list, finalize
from .metadata import Meta, stamp_from_game_exe
from .package_source import LocalPackageSource, WebPackageSource
from .prereqs import ensure_prereqs
from .registry import exe_version, query_install
from .storage import apply_storage, pack_additional
from .system import check_resources, optimal_threads
from .web_delivery import DEFAULT_CHUNK_SIZE, publish_web_package
from .zstd_patch import apply_all_patches, generate_patches, verify_patch_files
from .paths import (
    OUTPUT_DIR,
    MISSING_out_DIR,
    MISSING_read_DIR,
    PATCH_out_DIR,
    PATCH_read_DIR,
    STORAGE_out_DIR,
    STORAGE_read_DIR,
    WORKING_DIR,
)

_DEF_DELETE_LIST_out = str(Path(STORAGE_out_DIR) / "delete_list.txt")
_DEF_INFO_PATH_out = str(Path(STORAGE_out_DIR) / "metadata.info")
_DEF_DELETE_LIST_read = str(Path(STORAGE_read_DIR) / "delete_list.txt")
_DEF_INFO_PATH_read = str(Path(STORAGE_read_DIR) / "metadata.info")


# Keep CLI generation behavior aligned with the tested GUI presets. In
# particular, zstd patch-from needs the long window for large Tarkov files.
_DIFF_PRESETS: dict[str, list[str]] = {
    "fast": ["-3", "--long=31"],
    "balanced": ["-10", "--long=31"],
    "aggressive": ["--ultra", "-22", "--long=31"],
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

    delivery = getattr(args, "delivery", "standalone")
    if delivery in ("web", "both") and not args.package_id:
        raise SystemExit("--package-id is required for web/both delivery (example: --package-id 4.0.13).")

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

    if delivery in ("web", "both"):
        repository_root = Path(
            args.web_repo_output or (Path(WORKING_DIR) / "web_repo_output")
        ).resolve()
        chunk_size = int(args.chunk_size_mib) * 1024 * 1024
        if chunk_size <= 0:
            raise SystemExit("--chunk-size-mib must be greater than zero")

        print(f"Publishing web package {args.package_id}...")
        result = publish_web_package(
            OUTPUT_DIR,
            repository_root,
            args.package_id,
            chunk_size=chunk_size,
        )
        print(" Web manifest:", result.manifest_path)
        print(" Objects:", result.object_count)
        print(" New objects:", result.new_object_count)
        print(" Existing identical objects reused:", result.reused_object_count)
        print(" Web repository output:", repository_root)

        if delivery == "web":
            # The canonical package is staging for web-only output. Keep the
            # separate repository result and remove the staging tree only after
            # a successful publish.
            shutil.rmtree(OUTPUT_DIR, ignore_errors=True)
            print("Removed canonical staging package (web-only delivery).")

    if delivery in ("standalone", "both"):
        print("Standalone package ready →", OUTPUT_DIR)

    print("Generation complete.")


def _cmd_install(args: argparse.Namespace) -> None:
    if args.web_release:
        cache_root = Path(args.web_cache or (Path(WORKING_DIR) / "web_cache"))
        print(f"Preparing web package {args.web_release}...")
        source = WebPackageSource(args.web_release, cache_root)
    else:
        source = LocalPackageSource()

    layout = source.prepare()
    meta = Meta.read(layout.storage_root)

    print("Package source:", layout.source_type)
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
    total, succeeded, failed = apply_all_patches(
        dest,
        workers=threads,
        patch_root=layout.patch_root,
    )

    print("Finalizing...")
    finalize(dest, str(layout.storage_root / "delete_list.txt"))

    apply_storage(layout.storage_root, dest)

    if failed:
        print(f"Some patches failed ({failed}/{total}). See logs above.")
    else:
        print(f"Done. Applied {succeeded}/{total} patches. Have fun!")


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
    i.add_argument("--web-release", type=str, help="Fetch this package ID from the trusted Sierra web repository")
    i.add_argument("--web-cache", type=str, help="Web object/package cache directory (default: ./web_cache beside the patcher)")
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
        g.add_argument(
            "--delivery",
            choices=("standalone", "web", "both"),
            default="standalone",
            help="Package delivery output. Web/both publish manifest + content-addressed objects.",
        )
        g.add_argument("--package-id", type=str, help="Machine-safe web release ID, e.g. 4.0.13")
        g.add_argument("--web-repo-output", type=str, help="Directory to receive releases/ and objects/ for HFS upload")
        g.add_argument(
            "--chunk-size-mib",
            type=int,
            default=DEFAULT_CHUNK_SIZE // (1024 * 1024),
            help="Web object chunk size in MiB (default: 256)",
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
