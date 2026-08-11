from __future__ import annotations

import hashlib
import json
import os
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from pathlib import Path, PurePosixPath

from .i18n import tr
from .proc import Cancelled
from .zstd_patch import _python_io_path


SOURCE_HASHES_FILENAME = "source_hashes.json"
SOURCE_HASHES_FORMAT_VERSION = 1
_HASH_CHUNK_SIZE = 4 * 1024 * 1024


@dataclass(frozen=True)
class SourceHashMismatch:
    path: str
    reason: str
    expected_sha256: str
    actual_sha256: str | None = None
    expected_size: int | None = None
    actual_size: int | None = None


@dataclass(frozen=True)
class SourceIntegrityReport:
    total: int
    matched: int
    mismatches: tuple[SourceHashMismatch, ...]

    @property
    def failed(self) -> int:
        return len(self.mismatches)


def _raise_if_cancelled(cancel_event) -> None:
    if cancel_event is not None and cancel_event.is_set():
        raise Cancelled()


def _sha256_file(path: str | Path, cancel_event=None) -> str:
    digest = hashlib.sha256()
    with open(_python_io_path(path), "rb") as handle:
        while True:
            _raise_if_cancelled(cancel_event)
            chunk = handle.read(_HASH_CHUNK_SIZE)
            if not chunk:
                break
            digest.update(chunk)
    return digest.hexdigest()


def _safe_relative_path(value: str) -> PurePosixPath:
    path = PurePosixPath(str(value).replace("\\", "/"))
    if path.is_absolute() or not path.parts or ".." in path.parts:
        raise ValueError(f"unsafe source integrity path: {value!r}")
    return path


def build_source_hash_manifest(
    source_root: str | Path,
    patch_root: str | Path,
    storage_root: str | Path,
    *,
    workers: int = 8,
    on_progress=None,
    cancel_event=None,
) -> Path:
    """Record exact source hashes for every file that receives a delta patch."""

    source_root_path = Path(source_root)
    patch_root_path = Path(patch_root)
    storage_root_path = Path(storage_root)
    patch_files = sorted(patch_root_path.rglob("*.zst"))
    total = len(patch_files)

    if on_progress is not None:
        on_progress("source-hash:build", 0, max(total, 1), f"hashed 0/{total} delta sources")

    def hash_source(patch_file: Path) -> dict:
        _raise_if_cancelled(cancel_event)
        relative = patch_file.relative_to(patch_root_path).with_suffix("")
        relative_text = relative.as_posix()
        source_file = source_root_path / relative
        if not os.path.isfile(_python_io_path(source_file)):
            raise RuntimeError(
                f"delta source disappeared while building integrity data: {relative_text}"
            )
        return {
            "path": relative_text,
            "size": int(os.path.getsize(_python_io_path(source_file))),
            "sha256": _sha256_file(source_file, cancel_event),
        }

    entries: list[dict] = []
    if patch_files:
        max_workers = max(1, min(int(workers), len(patch_files), 64))
        completed = 0
        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            futures = {executor.submit(hash_source, patch): patch for patch in patch_files}
            for future in as_completed(futures):
                _raise_if_cancelled(cancel_event)
                try:
                    entry = future.result()
                except Exception:
                    for pending in futures:
                        pending.cancel()
                    raise
                entries.append(entry)
                completed += 1
                if on_progress is not None:
                    on_progress(
                        "source-hash:build",
                        completed,
                        total,
                        f"hashed {completed}/{total} delta sources",
                    )

    entries.sort(key=lambda item: item["path"])
    payload = {
        "format_version": SOURCE_HASHES_FORMAT_VERSION,
        "algorithm": "sha256",
        "files": entries,
    }

    storage_root_path.mkdir(parents=True, exist_ok=True)
    output = storage_root_path / SOURCE_HASHES_FILENAME
    temp = output.with_name(output.name + ".tmp")
    try:
        temp.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
        os.replace(_python_io_path(temp), _python_io_path(output))
    finally:
        try:
            os.unlink(_python_io_path(temp))
        except FileNotFoundError:
            pass
    return output


def _load_source_hash_manifest(storage_root: str | Path) -> list[dict] | None:
    manifest_path = Path(storage_root) / SOURCE_HASHES_FILENAME
    if not os.path.isfile(_python_io_path(manifest_path)):
        return None

    try:
        data = json.loads(Path(_python_io_path(manifest_path)).read_text(encoding="utf-8"))
    except Exception as exc:
        raise RuntimeError("source integrity manifest is not valid JSON") from exc

    if data.get("format_version") != SOURCE_HASHES_FORMAT_VERSION:
        raise RuntimeError(
            f"unsupported source integrity manifest version: {data.get('format_version')!r}"
        )
    if data.get("algorithm") != "sha256":
        raise RuntimeError(
            f"unsupported source integrity algorithm: {data.get('algorithm')!r}"
        )
    files = data.get("files")
    if not isinstance(files, list):
        raise RuntimeError("source integrity manifest files must be a list")

    normalized: list[dict] = []
    seen: set[str] = set()
    for item in files:
        if not isinstance(item, dict):
            raise RuntimeError("source integrity manifest contains an invalid file entry")
        relative = _safe_relative_path(str(item.get("path", ""))).as_posix()
        if relative in seen:
            raise RuntimeError(f"source integrity manifest contains duplicate path: {relative}")
        seen.add(relative)
        sha256 = str(item.get("sha256", "")).strip().lower()
        if len(sha256) != 64 or any(ch not in "0123456789abcdef" for ch in sha256):
            raise RuntimeError(f"source integrity manifest has invalid SHA-256 for: {relative}")
        try:
            size = int(item.get("size"))
        except (TypeError, ValueError) as exc:
            raise RuntimeError(f"source integrity manifest has invalid size for: {relative}") from exc
        if size < 0:
            raise RuntimeError(f"source integrity manifest has invalid size for: {relative}")
        normalized.append({"path": relative, "size": size, "sha256": sha256})

    normalized.sort(key=lambda item: item["path"])
    return normalized


def verify_destination_sources(
    storage_root: str | Path,
    destination_root: str | Path,
    *,
    workers: int = 8,
    on_progress=None,
    cancel_event=None,
) -> SourceIntegrityReport | None:
    """Verify every delta reference before any patch is applied.

    Returns ``None`` for legacy packages that do not contain source_hashes.json.
    """

    entries = _load_source_hash_manifest(storage_root)
    if entries is None:
        return None

    destination_root_path = Path(destination_root)
    total = len(entries)
    if on_progress is not None:
        on_progress("source-hash:verify", 0, max(total, 1), f"verified 0/{total} source files")

    def verify(entry: dict) -> SourceHashMismatch | None:
        _raise_if_cancelled(cancel_event)
        relative = entry["path"]
        destination_file = destination_root_path.joinpath(*PurePosixPath(relative).parts)
        expected_size = int(entry["size"])
        expected_sha = str(entry["sha256"])

        if not os.path.isfile(_python_io_path(destination_file)):
            return SourceHashMismatch(
                path=relative,
                reason="missing",
                expected_sha256=expected_sha,
                expected_size=expected_size,
            )

        actual_size = int(os.path.getsize(_python_io_path(destination_file)))
        if actual_size != expected_size:
            return SourceHashMismatch(
                path=relative,
                reason="size",
                expected_sha256=expected_sha,
                expected_size=expected_size,
                actual_size=actual_size,
            )

        actual_sha = _sha256_file(destination_file, cancel_event)
        if actual_sha != expected_sha:
            return SourceHashMismatch(
                path=relative,
                reason="sha256",
                expected_sha256=expected_sha,
                actual_sha256=actual_sha,
                expected_size=expected_size,
                actual_size=actual_size,
            )
        return None

    mismatches: list[SourceHashMismatch] = []
    if entries:
        max_workers = max(1, min(int(workers), len(entries), 64))
        completed = 0
        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            futures = {executor.submit(verify, entry): entry for entry in entries}
            for future in as_completed(futures):
                _raise_if_cancelled(cancel_event)
                try:
                    mismatch = future.result()
                except Exception:
                    for pending in futures:
                        pending.cancel()
                    raise
                if mismatch is not None:
                    mismatches.append(mismatch)
                completed += 1
                if on_progress is not None:
                    on_progress(
                        "source-hash:verify",
                        completed,
                        total,
                        f"verified {completed}/{total} source files",
                    )

    mismatches.sort(key=lambda item: item.path)
    return SourceIntegrityReport(
        total=total,
        matched=total - len(mismatches),
        mismatches=tuple(mismatches),
    )


def describe_source_mismatch(mismatch: SourceHashMismatch) -> str:
    """Return canonical English detail for logs and support diagnostics."""

    if mismatch.reason == "missing":
        return f"{mismatch.path}: missing"
    if mismatch.reason == "size":
        return (
            f"{mismatch.path}: size mismatch "
            f"(expected {mismatch.expected_size:,}, found {mismatch.actual_size:,} bytes)"
        )
    return (
        f"{mismatch.path}: SHA-256 mismatch "
        f"(expected {mismatch.expected_sha256}, found {mismatch.actual_sha256})"
    )


def _localized_source_mismatch(mismatch: SourceHashMismatch) -> str:
    """Return a localized mismatch detail for the user-facing stop dialog."""

    if mismatch.reason == "missing":
        return tr("{path}: missing", path=mismatch.path)
    if mismatch.reason == "size":
        return tr(
            "{path}: size mismatch (expected {expected:,}, found {actual:,} bytes)",
            path=mismatch.path,
            expected=mismatch.expected_size,
            actual=mismatch.actual_size,
        )
    return tr(
        "{path}: SHA-256 mismatch (expected {expected_sha}, found {actual_sha})",
        path=mismatch.path,
        expected_sha=mismatch.expected_sha256,
        actual_sha=mismatch.actual_sha256,
    )


def format_source_integrity_summary(
    report: SourceIntegrityReport,
    *,
    max_items: int = 8,
) -> str:
    lines = [
        tr("The selected Tarkov copy does not match the source files required by this release."),
        "",
        tr("Checked: {count}", count=report.total),
        tr("Matched: {count}", count=report.matched),
        tr("Mismatched: {count}", count=report.failed),
        "",
        tr("No game files were modified."),
    ]
    if report.mismatches:
        lines.extend(["", tr("Examples:")])
        for mismatch in report.mismatches[:max_items]:
            lines.append(f"- {_localized_source_mismatch(mismatch)}")
        if report.failed > max_items:
            lines.append(
                tr(
                    "... and {count} more. See Logs for details.",
                    count=report.failed - max_items,
                )
            )
    return "\n".join(lines)
