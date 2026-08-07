from __future__ import annotations

import filecmp
import os
import shutil
import subprocess
import tempfile
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

from .hygiene import format_size, is_package_excluded
from .paths import ZSTD_EXE
from .proc import Cancelled, run_quiet
from .zstd_patch import (
    _called_process_detail,
    _decode_zstd_args,
    _external_path_is_long,
    _python_io_path,
    _replace_file,
    _stage_external_input,
)

SMALL_FILE_LIMIT = 8 * 1024 * 1024
SMALL_DELTA_MAX_RATIO = 0.70
SMALL_DELTA_MIN_SAVINGS = 256 * 1024
FULL_PROBE_RAW_RATIO = 0.85
GENERAL_DELTA_MAX_RATIO = 0.95
DEFAULT_PAYLOAD_WORKERS = min(8, max(2, os.cpu_count() or 4))
_STAGED_PAYLOAD_SUFFIX = ".payload.zst"


def _raise_if_cancelled(cancel_event) -> None:
    if cancel_event is not None and cancel_event.is_set():
        raise Cancelled()


def _normalize_args(zstd_args: list[str] | None) -> list[str]:
    return list(zstd_args) if zstd_args else ["-10", "--long=31"]


def _payload_stage_path(root: str | Path, rel: str | Path) -> Path:
    return Path(root) / (os.fspath(rel) + _STAGED_PAYLOAD_SUFFIX)


def _payload_output_path(root: str | Path, rel: str | Path) -> Path:
    return Path(root) / (os.fspath(rel) + ".zst")


def _remove(path: str | Path) -> None:
    try:
        os.unlink(_python_io_path(path))
    except FileNotFoundError:
        pass


def _compress_full(
    source_file: str | Path,
    output_file: str | Path,
    *,
    zstd_args: list[str],
    cancel_event=None,
) -> None:
    _raise_if_cancelled(cancel_event)
    output_file = Path(output_file)
    os.makedirs(_python_io_path(output_file.parent), exist_ok=True)
    _remove(output_file)
    try:
        run_quiet(
            [
                ZSTD_EXE,
                *zstd_args,
                "-T1",
                "-f",
                os.fspath(source_file),
                "-o",
                os.fspath(output_file),
            ],
            check=True,
            capture=True,
            cancel_event=cancel_event,
        )
        run_quiet(
            [
                ZSTD_EXE,
                "-t",
                os.fspath(output_file),
                *_decode_zstd_args(zstd_args),
                "-T1",
            ],
            check=True,
            capture=True,
            cancel_event=cancel_event,
        )
    except subprocess.CalledProcessError as exc:
        _remove(output_file)
        raise RuntimeError(
            f"zstd full-file compression failed: {_called_process_detail(exc)}"
        ) from exc


def _generate_delta(
    source_file: str | Path,
    target_file: str | Path,
    output_file: str | Path,
    verify_file: str | Path,
    *,
    zstd_args: list[str],
    cancel_event=None,
) -> None:
    _raise_if_cancelled(cancel_event)
    try:
        run_quiet(
            [
                ZSTD_EXE,
                "--patch-from",
                os.fspath(source_file),
                os.fspath(target_file),
                "-o",
                os.fspath(output_file),
                *zstd_args,
                "-T1",
            ],
            check=True,
            capture=True,
            cancel_event=cancel_event,
        )
        run_quiet(
            [
                ZSTD_EXE,
                "-d",
                "--patch-from",
                os.fspath(source_file),
                os.fspath(output_file),
                "-o",
                os.fspath(verify_file),
                *_decode_zstd_args(zstd_args),
                "-T1",
            ],
            check=True,
            capture=True,
            cancel_event=cancel_event,
        )
    except subprocess.CalledProcessError as exc:
        raise RuntimeError(
            f"zstd delta generation failed: {_called_process_detail(exc)}"
        ) from exc

    if not filecmp.cmp(
        _python_io_path(target_file),
        _python_io_path(verify_file),
        shallow=False,
    ):
        raise RuntimeError("delta verification output did not match target")


def _prefer_delta(target_size: int, delta_size: int, full_size: int) -> bool:
    if full_size <= 0:
        return True
    savings = full_size - delta_size
    if target_size <= SMALL_FILE_LIMIT:
        return (
            savings >= SMALL_DELTA_MIN_SAVINGS
            and delta_size <= int(full_size * SMALL_DELTA_MAX_RATIO)
        )
    return delta_size <= int(full_size * GENERAL_DELTA_MAX_RATIO)


def _process_target_file(
    source_root: str,
    target_root: str,
    target_file: str,
    patch_root: str,
    payload_stage_root: str,
    zstd_args: list[str],
    cancel_event=None,
) -> tuple[str, int, int]:
    _raise_if_cancelled(cancel_event)
    if is_package_excluded(target_file, target_root):
        return "excluded", 0, 0

    rel = os.path.relpath(target_file, target_root)
    source_file = os.path.join(source_root, rel)
    patch_file = Path(patch_root) / (rel + ".zst")
    payload_file = _payload_stage_path(payload_stage_root, rel)
    target_size = os.path.getsize(_python_io_path(target_file))

    stage_parent = str(Path(patch_root).parent)
    os.makedirs(_python_io_path(stage_parent), exist_ok=True)
    stage_dir = tempfile.mkdtemp(prefix="sierra_hybrid_", dir=stage_parent)
    delta_tmp = Path(stage_dir) / "delta.zst"
    full_tmp = Path(stage_dir) / "full.zst"
    verify_tmp = Path(stage_dir) / "verify.out"

    try:
        target_for_zstd = _stage_external_input(target_file, stage_dir, "target.bin")

        if not os.path.exists(_python_io_path(source_file)):
            _compress_full(
                target_for_zstd,
                full_tmp,
                zstd_args=zstd_args,
                cancel_event=cancel_event,
            )
            os.makedirs(_python_io_path(payload_file.parent), exist_ok=True)
            _replace_file(full_tmp, payload_file)
            return "additional", os.path.getsize(_python_io_path(payload_file)), target_size

        if filecmp.cmp(
            _python_io_path(source_file),
            _python_io_path(target_file),
            shallow=False,
        ):
            return "identical", 0, target_size

        source_for_zstd = _stage_external_input(source_file, stage_dir, "source.bin")
        _generate_delta(
            source_for_zstd,
            target_for_zstd,
            delta_tmp,
            verify_tmp,
            zstd_args=zstd_args,
            cancel_event=cancel_event,
        )
        delta_size = delta_tmp.stat().st_size

        should_probe_full = (
            target_size <= SMALL_FILE_LIMIT
            or delta_size >= int(max(target_size, 1) * FULL_PROBE_RAW_RATIO)
        )

        if should_probe_full:
            _compress_full(
                target_for_zstd,
                full_tmp,
                zstd_args=zstd_args,
                cancel_event=cancel_event,
            )
            full_size = full_tmp.stat().st_size
            if not _prefer_delta(target_size, delta_size, full_size):
                os.makedirs(_python_io_path(payload_file.parent), exist_ok=True)
                _replace_file(full_tmp, payload_file)
                return "full", os.path.getsize(_python_io_path(payload_file)), target_size

        os.makedirs(_python_io_path(patch_file.parent), exist_ok=True)
        _replace_file(delta_tmp, patch_file)
        return "delta", os.path.getsize(_python_io_path(patch_file)), target_size
    finally:
        shutil.rmtree(stage_dir, ignore_errors=True)


def generate_patches(
    source_root: str,
    dest_root: str,
    out_root: str,
    missing_root: str,
    workers: int = 8,
    on_progress=None,
    cancel_event=None,
    use_tqdm: bool = True,
    zstd_args: list[str] | None = None,
) -> int:
    """Generate a hybrid delta/full Zstd package.

    ``out_root`` receives patch-from deltas. ``missing_root`` is used as a
    temporary staging tree for ordinary Zstd payloads; ``pack_additional`` later
    promotes that tree to top-level ``payloads/``.
    """

    del use_tqdm
    args = _normalize_args(zstd_args)

    shutil.rmtree(_python_io_path(out_root), ignore_errors=True)
    shutil.rmtree(_python_io_path(missing_root), ignore_errors=True)
    os.makedirs(_python_io_path(out_root), exist_ok=True)
    os.makedirs(_python_io_path(missing_root), exist_ok=True)

    files: list[str] = []
    excluded = 0
    for root, _, names in os.walk(dest_root):
        for name in names:
            path = os.path.join(root, name)
            if is_package_excluded(path, dest_root):
                excluded += 1
            else:
                files.append(path)

    total = len(files)
    stats = {
        "delta": 0,
        "full": 0,
        "additional": 0,
        "identical": 0,
        "excluded": excluded,
    }
    bytes_by_kind = {"delta": 0, "full": 0, "additional": 0}
    target_bytes_by_kind = {"delta": 0, "full": 0, "additional": 0}
    completed = 0
    lock = threading.Lock()
    max_workers = max(1, min(int(workers), 64))

    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        futures = {
            executor.submit(
                _process_target_file,
                source_root,
                dest_root,
                path,
                out_root,
                missing_root,
                args,
                cancel_event,
            ): path
            for path in files
        }
        for future in as_completed(futures):
            _raise_if_cancelled(cancel_event)
            try:
                kind, packed_bytes, target_bytes = future.result()
            except Exception as exc:
                for pending in futures:
                    pending.cancel()
                failed = futures[future]
                rel = os.path.relpath(failed, dest_root)
                raise RuntimeError(f"hybrid package generation failed for {rel}: {exc}") from exc

            with lock:
                completed += 1
                if kind in stats:
                    stats[kind] += 1
                if kind in bytes_by_kind:
                    bytes_by_kind[kind] += packed_bytes
                    target_bytes_by_kind[kind] += target_bytes

            if on_progress:
                on_progress(
                    "generate:patch",
                    completed,
                    total,
                    f"processed {completed}/{total} ({kind})",
                )

    packed_total = sum(bytes_by_kind.values())
    raw_total = sum(target_bytes_by_kind.values())
    print(
        "hybrid generation summary: "
        f"delta={stats['delta']}, full={stats['full']}, additional={stats['additional']}, "
        f"identical={stats['identical']}, hygiene_skipped={stats['excluded']}"
    )
    print(
        "hybrid payload sizes: "
        f"delta={format_size(bytes_by_kind['delta'])}, "
        f"full={format_size(bytes_by_kind['full'])}, "
        f"additional={format_size(bytes_by_kind['additional'])}, "
        f"packed_total={format_size(packed_total)}, target_bytes={format_size(raw_total)}"
    )
    return total


def _compress_raw_payload(source: Path, destination: Path, cancel_event=None) -> None:
    os.makedirs(_python_io_path(destination.parent), exist_ok=True)
    stage_parent = destination.parent
    stage_dir = tempfile.mkdtemp(prefix="sierra_payload_pack_", dir=os.fspath(stage_parent))
    temp_output = Path(stage_dir) / "payload.zst"
    try:
        source_for_zstd = _stage_external_input(source, stage_dir, "source.bin")
        _compress_full(
            source_for_zstd,
            temp_output,
            zstd_args=["-10", "--long=31"],
            cancel_event=cancel_event,
        )
        _replace_file(temp_output, destination)
    finally:
        shutil.rmtree(stage_dir, ignore_errors=True)


def finalize_payloads(
    additional_dir: str | Path,
    storage_dir: str | Path,
    cancel_event=None,
    on_progress=None,
) -> None:
    """Promote staged full/add files into top-level ``payloads/``."""

    stage_root = Path(additional_dir)
    package_root = Path(storage_dir).parent
    payload_root = package_root / "payloads"
    shutil.rmtree(_python_io_path(payload_root), ignore_errors=True)
    payload_root.mkdir(parents=True, exist_ok=True)

    storage_root = Path(storage_dir)
    storage_root.mkdir(parents=True, exist_ok=True)
    for legacy_name in ("storage.sierra", ".af.key"):
        _remove(storage_root / legacy_name)

    if not stage_root.is_dir():
        return

    files = [path for path in stage_root.rglob("*") if path.is_file()]
    total = len(files)
    done = 0
    for source in files:
        _raise_if_cancelled(cancel_event)
        rel = source.relative_to(stage_root)
        rel_text = rel.as_posix()
        if rel_text.endswith(_STAGED_PAYLOAD_SUFFIX):
            logical = rel_text[: -len(_STAGED_PAYLOAD_SUFFIX)]
            destination = payload_root / (logical + ".zst")
            os.makedirs(_python_io_path(destination.parent), exist_ok=True)
            _replace_file(source, destination)
        else:
            destination = payload_root / (rel_text + ".zst")
            _compress_raw_payload(source, destination, cancel_event)

        done += 1
        if on_progress:
            on_progress("payload:pack", done, max(total, 1), f"compressed {done}/{total}")

    shutil.rmtree(_python_io_path(stage_root), ignore_errors=True)
    print(f"payloads ready: {total} file(s)")


def _decode_payload_once(
    payload_file: Path,
    payload_root: Path,
    dest_root: Path,
    cancel_event=None,
) -> None:
    _raise_if_cancelled(cancel_event)
    rel = payload_file.relative_to(payload_root).with_suffix("")
    destination = dest_root / rel
    os.makedirs(_python_io_path(destination.parent), exist_ok=True)

    needs_stage = _external_path_is_long(payload_file) or _external_path_is_long(destination)
    stage_dir: str | None = None
    if needs_stage:
        stage_dir = tempfile.mkdtemp(prefix="sierra_payload_apply_", dir=os.fspath(dest_root))
        payload_for_zstd = _stage_external_input(payload_file, stage_dir, "payload.zst")
        output = Path(stage_dir) / "decoded.out"
    else:
        payload_for_zstd = os.fspath(payload_file)
        output = destination.with_name(destination.name + ".sierra_new")
        _remove(output)

    try:
        run_quiet(
            [
                ZSTD_EXE,
                "-d",
                "-f",
                payload_for_zstd,
                "-o",
                os.fspath(output),
                "-T1",
                "--long=31",
            ],
            check=True,
            capture=True,
            cancel_event=cancel_event,
        )
        if not os.path.exists(_python_io_path(output)):
            raise RuntimeError("zstd returned success but produced no output file")
        _replace_file(output, destination)
    finally:
        if stage_dir:
            shutil.rmtree(stage_dir, ignore_errors=True)
        else:
            _remove(output)


def _decode_payload_with_retry(
    payload_file: Path,
    payload_root: Path,
    dest_root: Path,
    cancel_event=None,
    retries: int = 2,
) -> None:
    last_error: Exception | None = None
    for attempt in range(retries + 1):
        _raise_if_cancelled(cancel_event)
        try:
            _decode_payload_once(payload_file, payload_root, dest_root, cancel_event)
            return
        except Cancelled:
            raise
        except Exception as exc:
            last_error = exc
            if attempt < retries:
                delay = 0.5 * (attempt + 1)
                if cancel_event is not None:
                    if cancel_event.wait(delay):
                        raise Cancelled()
                else:
                    time.sleep(delay)
    rel = payload_file.relative_to(payload_root).with_suffix("").as_posix()
    raise RuntimeError(f"full payload failed after {retries + 1} attempts: {rel}: {last_error}")


def apply_payloads(
    storage_dir: str | Path,
    dest_dir: str | Path,
    cancel_event=None,
    on_progress=None,
    workers: int = DEFAULT_PAYLOAD_WORKERS,
) -> None:
    """Apply ordinary-Zstd full/add payloads."""

    package_root = Path(storage_dir).parent
    payload_root = package_root / "payloads"
    legacy_archive = Path(storage_dir) / "storage.sierra"
    if legacy_archive.is_file():
        raise RuntimeError(
            "This release uses the retired storage.sierra/7-Zip package format. "
            "Regenerate the release with the current Sierra Patcher."
        )
    if not payload_root.is_dir():
        print("No payloads found - skipping full-file stage.")
        return

    payloads = sorted(path for path in payload_root.rglob("*.zst") if path.is_file())
    if not payloads:
        print("No payloads found - skipping full-file stage.")
        return

    destination = Path(dest_dir)
    max_workers = max(1, min(int(workers), 32, len(payloads)))
    completed = 0
    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        futures = {
            executor.submit(
                _decode_payload_with_retry,
                payload,
                payload_root,
                destination,
                cancel_event,
            ): payload
            for payload in payloads
        }
        for future in as_completed(futures):
            _raise_if_cancelled(cancel_event)
            try:
                future.result()
            except Exception:
                for pending in futures:
                    pending.cancel()
                raise
            completed += 1
            if on_progress:
                rel = futures[future].relative_to(payload_root).with_suffix("").as_posix()
                on_progress("payload:apply", completed, len(payloads), f"applied {rel}")

    print(f"payloads applied: {completed}/{len(payloads)}")
