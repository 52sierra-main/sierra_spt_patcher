from __future__ import annotations

import os
from pathlib import Path
from tkinter import ttk

from . import cli, gui_web
from .game_copy import DEFAULT_COPY_WORKERS, MAX_COPY_WORKERS
from .gui_resilient import ResilientSierraPatcherGUI
from .paths import STORAGE_out_DIR
from .source_integrity import (
    _load_source_hash_manifest,
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
    """Add exact source fingerprints and optimized install-time verification.

    Existing-copy installs keep the established parallel source-hash preflight.
    Automatic-copy installs verify Live first, then copy and verify in parallel.
    The post-copy destination read is shared with the release source-hash check,
    so delta-source files are not hashed a second time before the full download.
    """

    global _ENABLED
    if _ENABLED:
        return
    _ENABLED = True

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

    # Add a conservative copy/verification worker control to the existing
    # Advanced area without changing the user's tuned web worker defaults.
    original_build_install_tab = ResilientSierraPatcherGUI._build_install_tab

    def build_install_tab_with_copy_workers(self, nb):
        root = original_build_install_tab(self, nb)
        if hasattr(self, "_advanced_frame") and not hasattr(self, "i_copy_workers"):
            frame = self._advanced_frame
            for widget in frame.grid_slaves():
                info = widget.grid_info()
                row = int(info.get("row", 0))
                if row >= 3:
                    widget.grid_configure(row=row + 1)

            self.i_copy_workers = ttk.Spinbox(
                frame,
                from_=1,
                to=MAX_COPY_WORKERS,
            )
            self.i_copy_workers.delete(0, "end")
            self.i_copy_workers.insert(0, str(DEFAULT_COPY_WORKERS))
            self._row(
                frame,
                3,
                "Copy / verification workers",
                self.i_copy_workers,
            )
            if hasattr(self, "_web_install_widgets"):
                self._web_install_widgets.append(self.i_copy_workers)
        return root

    ResilientSierraPatcherGUI._build_install_tab = build_install_tab_with_copy_workers

    def _copy_workers_for_gui(self) -> int:
        widget = getattr(self, "i_copy_workers", None)
        raw = widget.get() if widget is not None else DEFAULT_COPY_WORKERS
        try:
            value = int(raw)
        except (TypeError, ValueError) as exc:
            raise ValueError("Copy / verification workers must be a whole number") from exc
        if value < 1 or value > MAX_COPY_WORKERS:
            raise ValueError(
                f"Copy / verification workers must be between 1 and {MAX_COPY_WORKERS}"
            )
        return value

    # Automatic Web installs complete the copy during the early storage-only
    # preflight. gui_web still reaches its historical copy call later, after the
    # full package is ready. Remember completed copies so that later call becomes
    # a no-op instead of rejecting the now-nonempty destination. Legacy packages
    # keep the selected worker count for their deferred normal-stage copy.
    original_copy_live_game = gui_web.copy_live_game
    early_completed_copies: set[tuple[str, str, str]] = set()
    deferred_copy_workers: dict[tuple[str, str, str], int] = {}

    def copy_live_game_once(
        source,
        destination,
        *,
        source_version=None,
        on_progress=None,
        cancel_event=None,
        workers=DEFAULT_COPY_WORKERS,
        release_source_entries=None,
    ):
        key = _copy_key(source, destination, source_version)
        if key in early_completed_copies:
            early_completed_copies.discard(key)
            deferred_copy_workers.pop(key, None)
            if on_progress is not None:
                on_progress(
                    "install:copy",
                    1,
                    1,
                    "Live game copy already completed and verified",
                )
            return

        worker_count = deferred_copy_workers.pop(key, workers)
        return original_copy_live_game(
            source,
            destination,
            source_version=source_version,
            on_progress=on_progress,
            cancel_event=cancel_event,
            workers=worker_count,
            release_source_entries=release_source_entries,
        )

    gui_web.copy_live_game = copy_live_game_once

    def _ensure_run_state(self) -> None:
        # gui_web resets this legacy flag to False at the start of every install.
        # Reuse that reliable per-run boundary, but track the actual verified path
        # rather than treating verification as a global boolean.
        if not getattr(self, "_source_preflight_done", False):
            self._source_preflight_done = True
            self._source_preflight_verified_root = ""
            self._install_mode_logged = False

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

        For Automatic Copy, Live is checked before copying. The copy engine then
        performs a parallel whole-copy verification and compares the same hashes
        against the release source manifest for delta files. No separate second
        destination source-hash pass is needed.
        """

        _ensure_run_state(self)
        automatic_reader = getattr(self, "_automatic_copy_enabled", None)
        automatic_copy = bool(
            automatic_reader() if callable(automatic_reader) else False
        )

        if not automatic_copy:
            if not self._install_mode_logged:
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

        if _path_key(destination) == self._source_preflight_verified_root:
            self._log(
                "[integrity] copied destination already passed merged copy/release verification; "
                "not re-checking"
            )
            return True

        try:
            copy_workers = _copy_workers_for_gui(self)
        except ValueError as exc:
            self._log(f"[copy] invalid worker setting: {exc}")
            self._stop_with_message("Invalid setting", str(exc))
            self._cancel.set()
            return False

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

        try:
            storage_meta = gui_web.Meta.read(storage_root)
            required_version = storage_meta.version
        except Exception:
            required_version = None

        if not self._install_mode_logged:
            self._install_mode_logged = True
            self._log(
                f"[install] install mode=automatic copy live={live_path} "
                f"live_version={source_version or '-'} required_version={required_version or '-'} "
                f"destination={destination} copy_workers={copy_workers}"
            )

        # The storage-only fetch includes metadata.info, so preserve PR3's cheap
        # version guard even when the catalog entry was legacy/unverified. Force
        # may bypass this heuristic, but not the exact SHA-256 check below.
        try:
            force = bool(self.i_force.get())
        except Exception:
            force = False
        if (
            not force
            and required_version
            and (source_version or "-") != required_version
        ):
            message = gui_web.tr(
                "Version mismatch detected.\n\n"
                "Live client: {live_version}\n"
                "Expected: {expected_version}\n\n"
                "If your live version exceeds that of the patch, please wait for an update. Otherwise, please update your live game and try again.",
                live_version=source_version or "-",
                expected_version=required_version,
            )
            self._log("[install] stopped before automatic copy: version mismatch")
            self._stop_with_message("Version mismatch", message)
            return False

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

        key = _copy_key(live_path, destination, source_version)

        # Legacy packages do not provide exact source hashes. Preserve their old
        # behavior: defer the copy to the normal install worker after preparation,
        # but carry the user's selected parallel copy worker count forward.
        if live_report is None:
            deferred_copy_workers[key] = copy_workers
            self._log(
                "[integrity] legacy package: automatic copy remains in the normal install stage "
                f"with {copy_workers} copy worker(s)"
            )
            return True

        release_entries = _load_source_hash_manifest(storage_root) or []
        self._log(
            f"[copy] early verified copy start source={live_path} destination={destination} "
            f"workers={copy_workers}"
        )
        original_copy_live_game(
            live_path,
            destination,
            source_version=source_version,
            on_progress=self._web_progress_callback(),
            cancel_event=cancel_event,
            workers=copy_workers,
            release_source_entries=release_entries,
        )

        # copy_live_game has now read every destination file exactly once and
        # checked that hash against both the bytes read from Live and, where
        # applicable, the release-required source hash.
        self._source_preflight_verified_root = _path_key(destination)
        early_completed_copies.add(key)
        self._log(
            "[copy] merged whole-copy + release delta-source verification passed; "
            "full package download may begin"
        )
        return True

    original_apply = ResilientSierraPatcherGUI._apply_patches_for_gui

    def apply_with_source_preflight(self, *args, **kwargs):
        _ensure_run_state(self)

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

        if _path_key(destination) == self._source_preflight_verified_root:
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
