from __future__ import annotations

import os
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


def _path_key(path: str | os.PathLike | None) -> str:
    if not path:
        return ""
    try:
        return os.path.normcase(os.path.realpath(os.path.abspath(os.fspath(path))))
    except Exception:
        return os.path.normcase(os.fspath(path))


def _copy_key(source, destination, source_version) -> tuple[str, str, str]:
    return (
        _path_key(source),
        _path_key(destination),
        str(source_version or "").strip(),
    )


def enable_source_integrity_hooks() -> None:
    """Add exact per-delta source fingerprints and install-time verification.

    New Web releases fetch only ``storage/`` first. Existing-copy installs verify
    that destination before the full package download. Automatic-copy installs
    verify the detected Live folder, copy it, verify the copied destination, and
    only then allow the full package download to continue.
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

    # Automatic Web installs now complete the copy during the early storage-only
    # preflight. gui_web still reaches its historical copy call later, after the
    # full package is ready. Remember those completed copies so that call becomes
    # a no-op instead of rejecting the now-nonempty destination.
    original_copy_live_game = gui_web.copy_live_game
    early_completed_copies: set[tuple[str, str, str]] = set()

    def copy_live_game_once(
        source,
        destination,
        *,
        source_version=None,
        on_progress=None,
        cancel_event=None,
    ):
        key = _copy_key(source, destination, source_version)
        if key in early_completed_copies:
            early_completed_copies.discard(key)
            if on_progress is not None:
                on_progress(
                    "install:copy",
                    1,
                    1,
                    "Live game copy already completed and verified",
                )
            return
        return original_copy_live_game(
            source,
            destination,
            source_version=source_version,
            on_progress=on_progress,
            cancel_event=cancel_event,
        )

    gui_web.copy_live_game = copy_live_game_once

    def _verify_root(
        self,
        storage_root,
        root,
        workers=8,
        cancel_event=None,
        *,
        mark_verified: bool = True,
    ):
        def progress(_phase, current, total, message):
            self._set_phase("Verifying source files")
            self._phase_progress(current, total, message)

        report = verify_destination_sources(
            storage_root,
            root,
            workers=workers,
            on_progress=progress,
            cancel_event=cancel_event,
        )

        if report is None:
            self._log(
                "[integrity] WARNING: source_hashes.json is not present in this package "
                f"(looked in {storage_root}). The exact source preflight cannot run; "
                "legacy patch-stage failure handling remains the safety fallback."
            )
            return True, None

        if report.failed:
            self._log(
                f"[integrity] source preflight FAILED for {root}: "
                f"{report.matched}/{report.total} matched, {report.failed} mismatched"
            )
            for mismatch in report.mismatches:
                self._log(f"[integrity] {describe_source_mismatch(mismatch)}")
            self._stop_with_message(
                "Source files mismatch",
                format_source_integrity_summary(report),
            )
            self._cancel.set()
            return False, report

        self._log(
            f"[integrity] source preflight passed for {root}: "
            f"{report.total}/{report.total} matched"
        )
        if mark_verified:
            self._source_preflight_verified_root = _path_key(root)
        return True, report

    def verify_source_files(self, storage_root, destination, workers=8, cancel_event=None) -> bool:
        """Verify the source that will actually feed the delta patches.

        For Automatic Copy, the detected Live install is verified first, copied,
        then the newly-created destination is independently verified. This second
        pass intentionally catches copy-time interference or corruption before
        the multi-GB package download and before any patch is applied.
        """

        automatic_reader = getattr(self, "_automatic_copy_enabled", None)
        automatic_copy = bool(
            automatic_reader() if callable(automatic_reader) else False
        )

        if not automatic_copy:
            if not getattr(self, "_install_mode_logged", False):
                self._install_mode_logged = True
                self._log(f"[install] install mode=existing copy destination={destination}")
            ok, _report = _verify_root(
                self,
                storage_root,
                destination,
                workers,
                cancel_event,
            )
            return ok

        # If this exact destination was already verified after the automatic copy,
        # a later patch-stage call has nothing useful to repeat.
        if _path_key(destination) == getattr(self, "_source_preflight_verified_root", ""):
            self._log(
                "[integrity] copied destination already passed exact source verification; "
                "not re-checking"
            )
            return True

        try:
            installation = gui_web.query_install()
        except Exception:
            installation = None
        if not installation:
            self._log("[integrity] automatic-copy preflight could not locate Live Tarkov")
            ok, _report = _verify_root(
                self,
                storage_root,
                destination,
                workers,
                cancel_event,
            )
            return ok

        live_path = installation["install_path"]
        live_executable = Path(live_path) / "EscapeFromTarkov.exe"
        source_version = gui_web.exe_version(live_executable)
        if not getattr(self, "_install_mode_logged", False):
            self._install_mode_logged = True
            self._log(
                f"[install] install mode=automatic copy live={live_path} "
                f"live_version={source_version or '-'} destination={destination}"
            )

        self._log("[integrity] verifying detected Live Tarkov before automatic copy")
        live_ok, live_report = _verify_root(
            self,
            storage_root,
            live_path,
            workers,
            cancel_event,
            mark_verified=False,
        )
        if not live_ok:
            return False

        # Legacy packages do not provide exact source hashes. Preserve their old
        # behavior: defer the copy to the normal install worker after preparation.
        if live_report is None:
            self._log(
                "[integrity] legacy package: automatic copy remains in the normal install stage"
            )
            return True

        self._log(f"[copy] early verified copy start source={live_path} destination={destination}")
        original_copy_live_game(
            live_path,
            destination,
            source_version=source_version,
            on_progress=self._web_progress_callback(),
            cancel_event=cancel_event,
        )
        self._log("[copy] early Live game copy completed; verifying copied destination")

        destination_ok, destination_report = _verify_root(
            self,
            storage_root,
            destination,
            workers,
            cancel_event,
        )
        if not destination_ok:
            self._log(
                "[copy] copied destination failed exact verification; no patches were applied. "
                "Delete the destination before retrying."
            )
            return False

        if destination_report is not None:
            early_completed_copies.add(
                _copy_key(live_path, destination, source_version)
            )
            self._log(
                "[copy] copied destination verified successfully; full package download may begin"
            )
        return True

    original_apply = ResilientSierraPatcherGUI._apply_patches_for_gui

    def apply_with_source_preflight(self, *args, **kwargs):
        # Force can bypass heuristic version/folder-size checks, but exact source
        # hashes are proof of whether a delta can decode and are never bypassed.
        try:
            force = bool(self.i_force.get())
        except Exception:
            force = False
        if force:
            self._log(
                "[integrity] Force is enabled: heuristic checks may be bypassed, "
                "but exact source-file verification remains mandatory"
            )

        destination = _argument(args, kwargs, 0, "dest_dir")
        patch_root = kwargs.get("patch_root")
        workers = int(kwargs.get("workers", 8))
        cancel_event = kwargs.get("cancel_event")

        if destination is None or patch_root is None:
            raise RuntimeError("could not determine destination/package roots for source preflight")

        if _path_key(destination) == getattr(self, "_source_preflight_verified_root", ""):
            self._log(
                "[integrity] this exact destination was already verified before download; "
                "not re-checking"
            )
            return original_apply(self, *args, **kwargs)

        storage_root = Path(patch_root).parent / "storage"
        if not verify_source_files(self, storage_root, destination, workers, cancel_event):
            return 0, 0, 0

        return original_apply(self, *args, **kwargs)

    ResilientSierraPatcherGUI._verify_source_files = verify_source_files
    ResilientSierraPatcherGUI._apply_patches_for_gui = apply_with_source_preflight
