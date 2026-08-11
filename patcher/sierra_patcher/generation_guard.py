from __future__ import annotations

import tkinter as tk
from tkinter import ttk

from . import gui_web
from .i18n import tr


_ENABLED = False
_GENERATE_BUTTON_LABELS = {"Generate patch package", "Generate web release"}


def _find_generate_button(app) -> ttk.Button | None:
    explicit = getattr(app, "btn_generate", None)
    if isinstance(explicit, ttk.Button):
        try:
            if explicit.winfo_exists():
                app._guarded_generate_button = explicit
                return explicit
        except tk.TclError:
            pass

    cached = getattr(app, "_guarded_generate_button", None)
    if isinstance(cached, ttk.Button):
        try:
            if cached.winfo_exists():
                return cached
        except tk.TclError:
            pass

    try:
        stack = list(app.winfo_children())
    except tk.TclError:
        return None

    while stack:
        widget = stack.pop()
        try:
            if isinstance(widget, ttk.Button) and str(widget.cget("text")) in {
                label
                for english in _GENERATE_BUTTON_LABELS
                for label in (english, tr(english))
            }:
                app._guarded_generate_button = widget
                return widget
            stack.extend(widget.winfo_children())
        except tk.TclError:
            continue
    return None


def _set_generate_enabled(app, enabled: bool) -> None:
    button = _find_generate_button(app)
    if button is None:
        return
    try:
        button.state(["!disabled"] if enabled else ["disabled"])
    except tk.TclError:
        pass


def _finish_generation_run(app) -> None:
    app._generation_running = False
    try:
        app.btn_abort_gen.state(["disabled"])
    except (AttributeError, tk.TclError):
        pass
    _set_generate_enabled(app, True)


def _restore_previous_progress(app, previous_ui) -> None:
    if previous_ui is None:
        return
    try:
        phase, detail, maximum, value = previous_ui
        app._phase_var.set(phase)
        app._detail_var.set(detail)
        app._prog_bar.configure(maximum=maximum, value=value)
    except Exception:
        pass


def _watch_generation_completion(app) -> None:
    if not getattr(app, "_generation_running", False):
        return

    try:
        abort_disabled = "disabled" in app.btn_abort_gen.state()
    except (AttributeError, tk.TclError):
        _finish_generation_run(app)
        return

    if abort_disabled:
        _finish_generation_run(app)
        return

    try:
        app.after(100, lambda: _watch_generation_completion(app))
    except tk.TclError:
        _finish_generation_run(app)


def enable_generation_guard() -> None:
    """Prevent overlapping Generate workers and provide immediate startup feedback.

    The established generation worker already enables Abort when it accepts a
    request and disables Abort in its finally block. This wrapper uses that
    lifecycle signal without changing generation, publishing, cancellation, or
    any worker-count settings.
    """
    global _ENABLED
    if _ENABLED:
        return
    _ENABLED = True

    original = gui_web.IntegratedSierraPatcherGUI._run_generate

    def guarded_run_generate(self, *args, **kwargs):
        if getattr(self, "_generation_running", False):
            try:
                self._log("[generate] duplicate start ignored: generation already running")
            except Exception:
                pass
            return None

        previous_ui = None
        try:
            previous_ui = (
                self._phase_var.get(),
                self._detail_var.get(),
                self._prog_bar.cget("maximum"),
                self._prog_bar.cget("value"),
            )
        except Exception:
            pass

        self._generation_running = True
        _set_generate_enabled(self, False)
        try:
            self._phase_var.set(tr("Preparing generation"))
            self._detail_var.set(tr("Validating resources..."))
            self._prog_bar.configure(mode="determinate", maximum=1, value=0)
            self.update_idletasks()
        except tk.TclError:
            _finish_generation_run(self)
            return None

        try:
            result = original(self, *args, **kwargs)
        except Exception:
            _finish_generation_run(self)
            _restore_previous_progress(self, previous_ui)
            raise

        # Synchronous validation failures return before the original method
        # enables Abort. A real generation run leaves Abort enabled until its
        # worker's finally block, which gives us a reliable completion signal.
        try:
            started = "disabled" not in self.btn_abort_gen.state()
        except (AttributeError, tk.TclError):
            started = False

        if not started:
            _finish_generation_run(self)
            _restore_previous_progress(self, previous_ui)
            return result

        try:
            self.after(100, lambda: _watch_generation_completion(self))
        except tk.TclError:
            _finish_generation_run(self)
        return result

    gui_web.IntegratedSierraPatcherGUI._run_generate = guarded_run_generate
