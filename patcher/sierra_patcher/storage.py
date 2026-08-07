from __future__ import annotations

from pathlib import Path

from .hybrid_payload import apply_payloads, finalize_payloads


def pack_additional(
    additional_dir: str | Path,
    storage_dir: str | Path,
    cancel_event=None,
    on_progress=None,
) -> None:
    """Compatibility name for the new Zstd payload finalization stage."""

    finalize_payloads(
        additional_dir,
        storage_dir,
        cancel_event=cancel_event,
        on_progress=on_progress,
    )


def apply_storage(
    storage_dir: str | Path,
    dest_dir: str | Path,
    cancel_event=None,
    on_progress=None,
) -> None:
    """Compatibility name for applying ordinary-Zstd full/add payloads."""

    apply_payloads(
        storage_dir,
        dest_dir,
        cancel_event=cancel_event,
        on_progress=on_progress,
    )


def recover_password(storage_dir: str | Path) -> str:
    raise RuntimeError(
        "The legacy storage.sierra password format has been retired. "
        "Current packages use ordinary Zstd payloads instead."
    )
