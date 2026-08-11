from __future__ import annotations

from pathlib import Path

from . import cli, gui_web
from .gui_resilient import ResilientSierraPatcherGUI
from .paths import STORAGE_out_DIR
from .source_integrity import (
    build_source_hash_manifest,
    describe_source_mismatch,
    format_source_integrity_summary,
    verify_destination_sources,
)


_ENABLED = False


def _argument(args, kwargs, position: int, name: str, default=None):
    if len(args) > position:
        return args[position]
    return kwargs.get(name, default)


def enable_source_integrity_hooks() -> None:
    """Add exact per-delta source fingerprints without changing package schema.

    Generation records ``storage/source_hashes.json`` after the final delta/full
    selection. Installation verifies that file immediately before the resilient
    patch engine is allowed to mutate the selected destination.
    """

    global _ENABLED
    if _ENABLED:
        return
    _ENABLED = True

    # gui_hybrid has already replaced gui_web.generate_patches with the current
    # hybrid generator by the time main imports this module. Wrap that final
    # callable so hashes describe only files that really remained as deltas.
    original_generate = gui_web.generate_patches

    def generate_with_source_hashes(*args, **kwargs):
        result = original_generate(*args, **kwargs)

        source_root = _argument(args, kwargs, 0, "source_root")
        patch_root = _argument(args, kwargs, 2, "out_root")
        workers = int(kwargs.get("workers", 8))
        on_progress = kwargs.get("on_progress")
        cancel_event = kwargs.get("cancel_event")

        if source_root is None or patch_root is None:
            raise RuntimeError("could not determine source/patch roots for integrity manifest")

        manifest_path = build_source_hash_manifest(
            source_root,
            patch_root,
            STORAGE_out_DIR,
            workers=workers,
            on_progress=on_progress,
            cancel_event=cancel_event,
        )
        print(f"source integrity manifest ready: {manifest_path}")
        return result

    gui_web.generate_patches = generate_with_source_hashes
    cli.generate_patches = generate_with_source_hashes

    original_apply = ResilientSierraPatcherGUI._apply_patches_for_gui

    def apply_with_source_preflight(self, *args, **kwargs):
        # Force has always meant bypassing metadata compatibility checks. Keep
        # that behavior available for deliberate recovery/testing scenarios.
        try:
            force = bool(self.i_force.get())
        except Exception:
            force = False
        if force:
            self._log("[integrity] exact source-file preflight bypassed by Force")
            return original_apply(self, *args, **kwargs)

        destination = _argument(args, kwargs, 0, "dest_dir")
        patch_root = kwargs.get("patch_root")
        workers = int(kwargs.get("workers", 8))
        cancel_event = kwargs.get("cancel_event")

        if destination is None or patch_root is None:
            raise RuntimeError("could not determine destination/package roots for source preflight")

        storage_root = Path(patch_root).parent / "storage"

        def progress(_phase, current, total, message):
            self._set_phase("Verifying source files")
            self._phase_progress(current, total, message)

        report = verify_destination_sources(
            storage_root,
            destination,
            workers=workers,
            on_progress=progress,
            cancel_event=cancel_event,
        )

        if report is None:
            self._log(
                "[integrity] source_hashes.json not present; exact source preflight skipped "
                "for legacy package"
            )
            return original_apply(self, *args, **kwargs)

        if report.failed:
            self._log(
                f"[integrity] source preflight FAILED: {report.matched}/{report.total} matched, "
                f"{report.failed} mismatched"
            )
            for mismatch in report.mismatches:
                self._log(f"[integrity] {describe_source_mismatch(mismatch)}")

            # Use the same thread-safe stop dialog path as the existing version
            # and folder checks. Mark the operation cancelled only after showing
            # the compatibility result; gui_web will then return before delete,
            # payload, or patch mutation stages can run.
            self._stop_with_message(
                "Source files mismatch",
                format_source_integrity_summary(report),
            )
            self._cancel.set()
            return report.total, 0, 0

        self._log(f"[integrity] source preflight passed: {report.total}/{report.total} matched")
        return original_apply(self, *args, **kwargs)

    ResilientSierraPatcherGUI._apply_patches_for_gui = apply_with_source_preflight
