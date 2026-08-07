from __future__ import annotations

from pathlib import Path

from . import gui_web, patch_apply
from .gui import _hide_console_on_windows
from .gui_layout import LayoutSierraPatcherGUI
from .hygiene import is_volatile_runtime_file
from .patch_apply import PatchApplyError, apply_patches_resilient


class ResilientSierraPatcherGUI(LayoutSierraPatcherGUI):
    """Current GUI with isolated patch retries and verbose failure diagnostics."""

    def __init__(self, dev: bool = False):
        super().__init__(dev=dev)

        # gui_web owns the established install workflow. Replace only its patch
        # application callable for this GUI instance so generation, web delivery,
        # layout, and the user's tuned download/reassembly defaults stay intact.
        gui_web.apply_all_patches = self._apply_patches_for_gui

        # Compatibility for already-published packages that accidentally contain
        # runtime-generated Vuplex Chromium logs. Those files are not real game
        # assets and may legitimately be absent or different on every machine.
        self._base_apply_single_detailed = patch_apply._apply_single_detailed
        patch_apply._apply_single_detailed = self._apply_single_with_volatile_skip

    def _apply_single_with_volatile_skip(
        self,
        patch_file: Path,
        dest_dir: Path,
        patch_root: Path,
        cancel_event=None,
    ):
        relative = patch_file.relative_to(patch_root).with_suffix("")
        destination_file = Path(dest_dir) / relative
        if is_volatile_runtime_file(destination_file, dest_dir):
            self._log(
                f"[patch] skipped volatile runtime file: {relative.as_posix()} "
                "(Vuplex Chromium debug log)"
            )
            return patch_apply.PatchAttemptResult(
                patch_file=patch_file,
                relative_path=relative.as_posix(),
                ok=True,
                code="SKIPPED_VOLATILE",
                detail="runtime-generated Vuplex Chromium debug log",
            )

        return self._base_apply_single_detailed(
            patch_file,
            Path(dest_dir),
            Path(patch_root),
            cancel_event,
        )

    def _apply_patches_for_gui(self, *args, **kwargs):
        # The legacy apply callable accepted use_tqdm. The resilient GUI engine
        # does not use tqdm, but accepts every other established argument.
        kwargs.pop("use_tqdm", None)
        original_progress = kwargs.pop("on_progress", None)

        def progress(phase, current, total, message):
            if phase == "install:retry":
                self._set_phase("Retrying failed patches")
            if original_progress is not None:
                original_progress(phase, current, total, message)

        report = apply_patches_resilient(
            *args,
            **kwargs,
            retry_attempts=2,
            retry_delay_seconds=0.75,
            on_progress=progress,
            on_log=self._log,
        )
        self._last_patch_apply_report = report

        # Raising here is deliberate: gui_web's worker stops immediately, before
        # delete-list finalization or storage extraction. The generic install
        # exception handler then preserves the cache and directs the user to Logs,
        # which now contain file paths, reason codes, retry history, and zstd stderr.
        if report.failed:
            raise PatchApplyError(report)

        return report.total, report.succeeded, report.failed


def main(dev: bool = False):
    _hide_console_on_windows()
    app = ResilientSierraPatcherGUI(dev=dev)
    app.mainloop()
