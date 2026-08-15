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

    def verify_source_files(self, storage_root, destination, workers=8, cancel_event=None) -> bool:
        """Verify the destination against the package's source hashes.

        Returns True when the install may proceed, False when it was stopped.
        Shared by the early pre-download check and the patch-stage preflight so
        the 5,000+ files are only ever hashed once per run.
        """

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
                "[integrity] WARNING: source_hashes.json is not present in this package "
                f"(looked in {storage_root}). The exact source preflight cannot run, so a "
                "wrong destination will only be detected once patches start failing. "
                "Regenerate this release with the current Sierra Patcher to restore the check."
            )
            return True

        if report.failed:
            self._log(
                f"[integrity] source preflight FAILED: {report.matched}/{report.total} matched, "
                f"{report.failed} mismatched"
            )
            for mismatch in report.mismatches:
                self._log(f"[integrity] {describe_source_mismatch(mismatch)}")

            # Same thread-safe stop path as the version and folder checks. The
            # operation is marked cancelled only after the result is shown, so
            # the caller returns before any patch, payload or delete stage runs.
            self._stop_with_message(
                "Source files mismatch",
                format_source_integrity_summary(report),
            )
            self._cancel.set()
            return False

        self._log(f"[integrity] source preflight passed: {report.total}/{report.total} matched")
        self._source_preflight_done = True
        return True

    original_apply = ResilientSierraPatcherGUI._apply_patches_for_gui

    def apply_with_source_preflight(self, *args, **kwargs):
        # Force bypasses the *heuristic* compatibility checks (version string,
        # aggregate folder sizes) because those can raise false alarms. It must
        # never bypass this one. A source-hash mismatch is not a warning sign,
        # it is proof that the deltas cannot decode: every patch reconstructs
        # its target from the exact bytes recorded here. Skipping the check does
        # not let the install succeed, it only delays the failure until after
        # thousands of the user's files have been rewritten into garbage.
        try:
            force = bool(self.i_force.get())
        except Exception:
            force = False
        if force:
            self._log(
                "[integrity] Force is enabled: version and folder-size checks are "
                "bypassed, but the exact source-file check always runs"
            )

        # Web installs verify before downloading the package. Hashing thousands
        # of multi-GB files a second time would roughly double the install time
        # for no additional safety.
        if getattr(self, "_source_preflight_done", False):
            self._log(
                "[integrity] source files already verified before download; not re-checking"
            )
            return original_apply(self, *args, **kwargs)

        destination = _argument(args, kwargs, 0, "dest_dir")
        patch_root = kwargs.get("patch_root")
        workers = int(kwargs.get("workers", 8))
        cancel_event = kwargs.get("cancel_event")

        if destination is None or patch_root is None:
            raise RuntimeError("could not determine destination/package roots for source preflight")

        storage_root = Path(patch_root).parent / "storage"
        if not verify_source_files(self, storage_root, destination, workers, cancel_event):
            return 0, 0, 0

        return original_apply(self, *args, **kwargs)

    ResilientSierraPatcherGUI._verify_source_files = verify_source_files
    ResilientSierraPatcherGUI._apply_patches_for_gui = apply_with_source_preflight
