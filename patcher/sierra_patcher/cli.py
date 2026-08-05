from __future__ import annotations

import argparse
import os
import shutil
import threading
import time
from pathlib import Path

from .delete_list import build_delete_list, finalize
from .metadata import Meta, stamp_from_game_exe
from .package_source import LocalPackageSource, WebPackageSource
from .patch_audit import audit_patch_files
from .prereqs import ensure_prereqs
from .registry import exe_version, query_install
from .storage import apply_storage, pack_additional
from .system import check_resources, optimal_threads
from .web_delivery import (
    DEFAULT_CHUNK_SIZE,
    DEFAULT_PUBLISH_WORKERS,
    publish_web_package,
)
from .web_download import DEFAULT_DOWNLOAD_WORKERS, DEFAULT_MATERIALIZE_WORKERS
from .zstd_patch import apply_all_patches, generate_patches
from .paths import (
    OUTPUT_DIR,
    MISSING_out_DIR,
    PATCH_out_DIR,
    STORAGE_out_DIR,
    WORKING_DIR,
)

_DEF_DELETE_LIST_out = str(Path(STORAGE_out_DIR) / "delete_list.txt")
_DEF_INFO_PATH_out = str(Path(STORAGE_out_DIR) / "metadata.info")


# Keep CLI generation behavior aligned with the tested GUI presets. In
# particular, zstd patch-from needs the long window for large Tarkov files.
_DIFF_PRESETS: dict[str, list[str]] = {
    "fast": ["-3", "--long=31"],
    "balanced": ["-10", "--long=31"],
    "aggressive": ["--ultra", "-22", "--long=31"],
}


class _ConsoleProgress:
    """Thread-safe, throttled single-line progress display for CLI workflows."""

    _LABELS = {
        "web:publish": "Publishing files",
        "web:manifest": "Fetching manifest",
        "web:objects": "Downloading objects",
        "web:materialize": "Reconstructing package",
        "audit:patches": "Auditing patches",
    }

    def __init__(self, min_interval: float = 0.10):
        self._min_interval = min_interval
        self._last_time = 0.0
        self._last_phase: str | None = None
        self._last_width = 0
        self._shown = False
        self._lock = threading.Lock()

    @staticmethod
    def _amount(phase: str, current: int, total: int) -> str:
        if phase == "web:objects":
            mib = 1024 * 1024
            return f"{current / mib:,.1f}/{total / mib:,.1f} MiB"
        return f"{current:,}/{total:,}"

    def __call__(self, phase: str, current: int, total: int, message: str = "") -> None:
        now = time.monotonic()
        total = max(int(total), 1)
        current = max(0, min(int(current), total))

        with self._lock:
            phase_changed = phase != self._last_phase
            finished = current >= total
            if not phase_changed and not finished and now - self._last_time < self._min_interval:
                return

            if phase_changed and self._shown:
                print()
                self._last_width = 0

            label = self._LABELS.get(phase, phase)
            percent = current * 100.0 / total
            detail = (message or "").replace("\n", " ")
            if len(detail) > 72:
                detail = "..." + detail[-69:]
            text = (
                f"{label}: {self._amount(phase, current, total)} "
                f"({percent:5.1f}%)"
            )
            if detail:
                text += f"  {detail}"

            print("\r" + text.ljust(self._last_width), end="", flush=True)
            self._last_width = max(self._last_width, len(text))
            self._last_time = now
            self._last_phase = phase
            self._shown = True

    def finish(self) -> None:
        with self._lock:
            if self._shown:
                print()
            self._shown = False
            self._last_width = 0
            self._last_phase = None


def _positive_workers(value: int, name: str) -> int:
    if value <= 0:
        raise SystemExit(f"{name} must be greater than zero")
    return value


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

    print("Auditing produced patch package...")
    if not audit_patch_files(workers=threads):
        raise SystemExit("Generated patch package failed its final audit.")

    if delivery in ("web", "both"):
        repository_root = Path(
            args.web_repo_output or (Path(WORKING_DIR) / "web_repo_output")
        ).resolve()
        chunk_size = int(args.chunk_size_mib) * 1024 * 1024
        if chunk_size <= 0:
            raise SystemExit("--chunk-size-mib must be greater than zero")
        publish_workers = _positive_workers(
            int(args.web_publish_workers),
            "--web-publish-workers",
        )

        print(f"Publishing web package {args.package_id} with {publish_workers} workers...")
        progress = _ConsoleProgress()
        try:
            result = publish_web_package(
                OUTPUT_DIR,
                repository_root,
                args.package_id,
                chunk_size=chunk_size,
                workers=publish_workers,
                on_progress=progress,
            )
        finally:
            progress.finish()

        print(" Web manifest:", result.manifest_path)
        print(" Objects:", result.object_count)
        print(" New objects:", result.new_object_count)
        print(" Existing identical objects reused:", result.reused_object_count)
        print(" Web repository output:", repository_root)

        if delivery == "web":
            # The canonical package is staging for web-only output. Keep the
            # separate repository result and remove staging only after success.
            shutil.rmtree(OUTPUT_DIR, ignore_errors=True)
            print("Removed canonical staging package (web-only delivery).")

    if delivery in ("standalone", "both"):
        print("Standalone package ready →", OUTPUT_DIR)

    print("Generation complete.")


def _cmd_install(args: argparse.Namespace) -> None:
    if args.web_release:
        cache_root = Path(args.web_cache or (Path(WORKING_DIR) / "web_cache"))
        download_workers = _positive_workers(
            int(args.web_download_workers),
            "--web-download-workers",
        )
        materialize_workers = _positive_workers(
            int(args.web_materialize_workers),
            "--web-materialize-workers",
        )
        print(
            f"Preparing web package {args.web_release} "
            f"({download_workers} download / {materialize_workers} reconstruction workers)..."
        )
        source = WebPackageSource(
            args.web_release,
            cache_root,
            download_workers=download_workers,
            materialize_workers=materialize_workers,
        )
        progress = _ConsoleProgress()
        try:
            layout = source.prepare(on_progress=progress)
        finally:
            progress.finish()
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
    parser = argparse.ArgumentParser(prog="sierra-patcher", description="Sierra's patch tool")
    sub = parser.add_subparsers(dest="cmd", required=False)

    install = sub.add_parser("install", help="Apply an existing patch package")
    install.add_argument("--dir", type=str, help="Destination game folder to patch")
    install.add_argument("--threads", type=int, help="Patch-application worker threads")
    install.add_argument("--force", action="store_true", help="Bypass metadata checks")
    install.add_argument("--prereqs", action="store_true", help="Deprecated: dependencies are checked automatically and never auto-installed")
    install.add_argument("--skip-prereq-check", action="store_true", help="Skip .NET dependency preflight")
    install.add_argument("--web-release", type=str, help="Fetch this package ID from the trusted Sierra web repository")
    install.add_argument("--web-cache", type=str, help="Web object/package cache directory (default: ./web_cache beside the patcher)")
    install.add_argument(
        "--web-download-workers",
        type=int,
        default=DEFAULT_DOWNLOAD_WORKERS,
        help=f"Concurrent object downloads (default: {DEFAULT_DOWNLOAD_WORKERS})",
    )
    install.add_argument(
        "--web-materialize-workers",
        type=int,
        default=DEFAULT_MATERIALIZE_WORKERS,
        help=f"Concurrent file reconstruction workers (default: {DEFAULT_MATERIALIZE_WORKERS})",
    )
    install.add_argument("-y", "--yes", action="store_true", help="Assume yes for prompts")
    install.set_defaults(func=_cmd_install)

    if dev:
        generate = sub.add_parser("generate", help="(dev) Create a patch package from dest vs source")
        generate.add_argument("--source", type=str, help="Clean game folder")
        generate.add_argument("--dest", type=str, help="SPT target folder")
        generate.add_argument("--threads", type=int, help="Patch-generation worker threads")
        generate.add_argument("--title", type=str, help="Release title (e.g., SPT 3.10)")
        generate.add_argument("--date", type=str, help="Date string to stamp")
        generate.add_argument(
            "--diff",
            choices=sorted(_DIFF_PRESETS.keys()),
            default="balanced",
            help="Diff aggressiveness: smaller patches cost more time/CPU/RAM",
        )
        generate.add_argument(
            "--delivery",
            choices=("standalone", "web", "both"),
            default="standalone",
            help="Package delivery output. Web/both publish manifest + content-addressed objects.",
        )
        generate.add_argument("--package-id", type=str, help="Machine-safe web release ID, e.g. 4.0.13")
        generate.add_argument("--web-repo-output", type=str, help="Directory to receive releases/ and objects/ for HFS upload")
        generate.add_argument(
            "--chunk-size-mib",
            type=int,
            default=DEFAULT_CHUNK_SIZE // (1024 * 1024),
            help="Web object chunk size in MiB (default: 256)",
        )
        generate.add_argument(
            "--web-publish-workers",
            type=int,
            default=DEFAULT_PUBLISH_WORKERS,
            help=f"Concurrent web publishing workers (default: {DEFAULT_PUBLISH_WORKERS})",
        )
        generate.set_defaults(func=_cmd_generate)

    return parser


def run_cli(argv: list[str] | None = None, dev: bool = False) -> None:
    parser = build_parser(dev)
    args = parser.parse_args(argv)

    if not getattr(args, "cmd", None):
        from .gui import main as gui_main

        return gui_main(dev=dev)

    args.func(args)
