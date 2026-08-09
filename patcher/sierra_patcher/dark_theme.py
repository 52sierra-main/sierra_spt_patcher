from __future__ import annotations

import ctypes
import sys
import tkinter as tk
from tkinter import ttk
from tkinter.scrolledtext import ScrolledText


# Browser-like neutral dark palette. Kept in one module so the visual layer can
# evolve without touching installer/generator behavior.
WINDOW_BG = "#202124"
PANEL_BG = "#292a2d"
INPUT_BG = "#303134"
HOVER_BG = "#3c4043"
BORDER = "#5f6368"
TEXT = "#e8eaed"
SECONDARY_TEXT = "#9aa0a6"
DISABLED_TEXT = "#6f7378"
LINK = "#8ab4f8"
ERROR = "#f28b82"
ACCENT = "#8ab4f8"
ACCENT_PRESSED = "#669df6"
ACCENT_TEXT = "#202124"
SELECTION_BG = "#3f6593"


_LEGACY_FOREGROUNDS = {
    "#666": SECONDARY_TEXT,
    "#666666": SECONDARY_TEXT,
    "#444": SECONDARY_TEXT,
    "#444444": SECONDARY_TEXT,
    "#555": SECONDARY_TEXT,
    "#555555": SECONDARY_TEXT,
    "#777": SECONDARY_TEXT,
    "#777777": SECONDARY_TEXT,
    "#0b62d6": LINK,
    "#b00020": ERROR,
}


def _configure_ttk_styles(root: tk.Misc) -> None:
    style = ttk.Style(root)
    try:
        style.theme_use("clam")
    except tk.TclError:
        pass

    style.configure(
        ".",
        background=WINDOW_BG,
        foreground=TEXT,
        bordercolor=BORDER,
        darkcolor=PANEL_BG,
        lightcolor=PANEL_BG,
        troughcolor=INPUT_BG,
        selectbackground=SELECTION_BG,
        selectforeground=TEXT,
        font=("Segoe UI", 9),
    )

    style.configure("TFrame", background=WINDOW_BG)
    style.configure("TLabel", background=WINDOW_BG, foreground=TEXT)
    style.configure("Hint.TLabel", background=WINDOW_BG, foreground=ERROR, font=("Segoe UI", 9))

    style.configure(
        "TLabelframe",
        background=WINDOW_BG,
        foreground=TEXT,
        bordercolor=BORDER,
        relief="solid",
        borderwidth=1,
    )
    style.configure(
        "TLabelframe.Label",
        background=WINDOW_BG,
        foreground=TEXT,
        font=("Segoe UI", 9, "bold"),
    )

    style.configure(
        "TButton",
        background=INPUT_BG,
        foreground=TEXT,
        bordercolor=BORDER,
        focusthickness=1,
        focuscolor=BORDER,
        padding=(8, 4),
    )
    style.map(
        "TButton",
        background=[("pressed", PANEL_BG), ("active", HOVER_BG), ("disabled", PANEL_BG)],
        foreground=[("disabled", DISABLED_TEXT)],
        bordercolor=[("focus", ACCENT), ("active", SECONDARY_TEXT)],
    )

    style.configure(
        "AccentInstall.TButton",
        background=ACCENT,
        foreground=ACCENT_TEXT,
        bordercolor=ACCENT,
        font=("Segoe UI", 10, "bold"),
        padding=(10, 6),
    )
    style.map(
        "AccentInstall.TButton",
        background=[("pressed", ACCENT_PRESSED), ("active", "#9fc2fa"), ("disabled", PANEL_BG)],
        foreground=[("disabled", DISABLED_TEXT)],
        bordercolor=[("pressed", ACCENT_PRESSED), ("active", "#9fc2fa")],
    )

    for entry_style in ("TEntry", "TSpinbox"):
        style.configure(
            entry_style,
            fieldbackground=INPUT_BG,
            background=INPUT_BG,
            foreground=TEXT,
            insertcolor=TEXT,
            bordercolor=BORDER,
            lightcolor=BORDER,
            darkcolor=BORDER,
            arrowcolor=TEXT,
        )
        style.map(
            entry_style,
            fieldbackground=[("readonly", INPUT_BG), ("disabled", PANEL_BG)],
            foreground=[("readonly", TEXT), ("disabled", DISABLED_TEXT)],
            bordercolor=[("focus", ACCENT)],
        )

    style.configure(
        "TCombobox",
        fieldbackground=INPUT_BG,
        background=INPUT_BG,
        foreground=TEXT,
        arrowcolor=TEXT,
        bordercolor=BORDER,
        lightcolor=BORDER,
        darkcolor=BORDER,
    )
    style.map(
        "TCombobox",
        fieldbackground=[("readonly", INPUT_BG), ("disabled", PANEL_BG)],
        background=[("readonly", INPUT_BG), ("active", HOVER_BG)],
        foreground=[("readonly", TEXT), ("disabled", DISABLED_TEXT)],
        arrowcolor=[("disabled", DISABLED_TEXT)],
        bordercolor=[("focus", ACCENT)],
    )

    style.configure("TCheckbutton", background=WINDOW_BG, foreground=TEXT, indicatorcolor=INPUT_BG)
    style.map(
        "TCheckbutton",
        background=[("active", WINDOW_BG)],
        foreground=[("disabled", DISABLED_TEXT)],
        indicatorcolor=[("selected", ACCENT), ("pressed", ACCENT_PRESSED)],
    )

    style.configure("TNotebook", background=WINDOW_BG, bordercolor=BORDER, tabmargins=(4, 4, 4, 0))
    style.configure(
        "TNotebook.Tab",
        background=PANEL_BG,
        foreground=SECONDARY_TEXT,
        bordercolor=BORDER,
        padding=(12, 6),
    )
    style.map(
        "TNotebook.Tab",
        background=[("selected", INPUT_BG), ("active", HOVER_BG)],
        foreground=[("selected", TEXT), ("active", TEXT)],
        expand=[("selected", (0, 0, 0, 1))],
    )

    style.configure(
        "TProgressbar",
        background=ACCENT,
        troughcolor=INPUT_BG,
        bordercolor=INPUT_BG,
        lightcolor=ACCENT,
        darkcolor=ACCENT,
    )
    style.configure("TSeparator", background=BORDER)

    for scrollbar_style in ("Vertical.TScrollbar", "Horizontal.TScrollbar"):
        style.configure(
            scrollbar_style,
            background=HOVER_BG,
            troughcolor=PANEL_BG,
            bordercolor=PANEL_BG,
            arrowcolor=TEXT,
            lightcolor=HOVER_BG,
            darkcolor=HOVER_BG,
        )
        style.map(scrollbar_style, background=[("active", BORDER), ("pressed", SECONDARY_TEXT)])

    style.configure(
        "Treeview",
        background=INPUT_BG,
        fieldbackground=INPUT_BG,
        foreground=TEXT,
        bordercolor=BORDER,
    )
    style.map("Treeview", background=[("selected", SELECTION_BG)], foreground=[("selected", TEXT)])
    style.configure("Treeview.Heading", background=PANEL_BG, foreground=TEXT, bordercolor=BORDER)
    style.map("Treeview.Heading", background=[("active", HOVER_BG)])


def _refresh_windows_frame(hwnd: int) -> None:
    """Force Windows to repaint the non-client frame after a DWM change."""
    try:
        user32 = ctypes.windll.user32
        # SWP_NOSIZE | SWP_NOMOVE | SWP_NOZORDER | SWP_NOACTIVATE | SWP_FRAMECHANGED
        user32.SetWindowPos(hwnd, 0, 0, 0, 0, 0, 0x0037)
        # RDW_INVALIDATE | RDW_UPDATENOW | RDW_FRAME
        user32.RedrawWindow(hwnd, None, None, 0x0501)
    except Exception:
        pass


def _set_windows_dark_titlebar(window: tk.Misc) -> None:
    if sys.platform != "win32":
        return
    try:
        window.update_idletasks()
        child_hwnd = int(window.winfo_id())
        parent_hwnd = int(ctypes.windll.user32.GetParent(child_hwnd) or 0)
        handles = [handle for handle in (parent_hwnd, child_hwnd) if handle]
        enabled = ctypes.c_int(1)
        dwm = ctypes.windll.dwmapi

        for hwnd in handles:
            applied = False
            for attribute in (20, 19):  # Win10/11 current value, then older Win10 fallback.
                try:
                    result = dwm.DwmSetWindowAttribute(
                        hwnd,
                        attribute,
                        ctypes.byref(enabled),
                        ctypes.sizeof(enabled),
                    )
                    if result == 0:
                        applied = True
                        break
                except Exception:
                    continue
            if applied:
                _refresh_windows_frame(hwnd)
    except Exception:
        pass


def _schedule_windows_dark_titlebar(window: tk.Misc) -> None:
    """Keep later Toplevel windows dark as they are created and mapped."""
    for delay_ms in (0, 40, 160):
        try:
            window.after(delay_ms, lambda w=window: _set_windows_dark_titlebar(w))
        except tk.TclError:
            return


def present_main_window(app: tk.Tk) -> None:
    """Present the main Tk window only after its native frame is dark.

    Tk/Windows can paint the non-client frame once in the system light theme
    before DWM dark-mode attributes visibly take effect. A later minimize/restore
    then fixes it because Windows recreates/repaints that frame. Keeping the root
    withdrawn during initial DWM setup gives Windows the correct state before the
    first visible presentation instead of relying on a later repaint.
    """
    if sys.platform != "win32":
        app.deiconify()
        return

    try:
        app.withdraw()
        # Realize Tk's HWND hierarchy while it is still invisible.
        app.update_idletasks()
        _set_windows_dark_titlebar(app)

        def reveal() -> None:
            try:
                # Re-apply immediately before the visibility transition.
                _set_windows_dark_titlebar(app)
                app.deiconify()
                app.update_idletasks()
                # One final application targets any wrapper/frame state Tk/Windows
                # updates as part of deiconify().
                _set_windows_dark_titlebar(app)
            except tk.TclError:
                pass

        app.after(0, reveal)
    except tk.TclError:
        try:
            app.deiconify()
        except tk.TclError:
            pass


def _normalized_color(value: object) -> str:
    return str(value or "").strip().lower()


def _theme_classic_widget(widget: tk.Misc) -> None:
    if isinstance(widget, (tk.Tk, tk.Toplevel)):
        try:
            widget.configure(background=WINDOW_BG)
        except tk.TclError:
            pass
        _schedule_windows_dark_titlebar(widget)
        return

    if isinstance(widget, (ScrolledText, tk.Text)):
        try:
            widget.configure(
                background=INPUT_BG,
                foreground=TEXT,
                insertbackground=TEXT,
                selectbackground=SELECTION_BG,
                selectforeground=TEXT,
                relief="flat",
                borderwidth=1,
                highlightthickness=1,
                highlightbackground=BORDER,
                highlightcolor=ACCENT,
            )
        except tk.TclError:
            pass
        return

    if isinstance(widget, (tk.Entry, tk.Spinbox)):
        try:
            widget.configure(
                background=INPUT_BG,
                foreground=TEXT,
                insertbackground=TEXT,
                selectbackground=SELECTION_BG,
                selectforeground=TEXT,
                highlightbackground=BORDER,
                highlightcolor=ACCENT,
            )
        except tk.TclError:
            pass
        return

    if isinstance(widget, tk.Listbox):
        try:
            widget.configure(
                background=INPUT_BG,
                foreground=TEXT,
                selectbackground=SELECTION_BG,
                selectforeground=TEXT,
                highlightbackground=BORDER,
                highlightcolor=ACCENT,
            )
        except tk.TclError:
            pass
        return

    if isinstance(widget, tk.Label):
        try:
            current_bg = _normalized_color(widget.cget("background"))
            default_backgrounds = {
                "",
                "systembuttonface",
                "#f0f0f0",
                "#ececec",
                "white",
                "#ffffff",
            }
            if current_bg in default_backgrounds:
                widget.configure(background=WINDOW_BG)

            current_fg = _normalized_color(widget.cget("foreground"))
            if current_fg in _LEGACY_FOREGROUNDS:
                widget.configure(foreground=_LEGACY_FOREGROUNDS[current_fg])
            elif current_fg in {"", "systembuttontext", "black", "#000000"}:
                widget.configure(foreground=TEXT)
        except tk.TclError:
            pass


def _theme_ttk_widget(widget: tk.Misc) -> None:
    if not isinstance(widget, ttk.Label):
        return
    try:
        current_fg = _normalized_color(widget.cget("foreground"))
    except tk.TclError:
        return
    replacement = _LEGACY_FOREGROUNDS.get(current_fg)
    if replacement:
        try:
            widget.configure(foreground=replacement)
        except tk.TclError:
            pass


def _theme_widget(widget: tk.Misc) -> None:
    _theme_classic_widget(widget)
    _theme_ttk_widget(widget)


def _theme_tree(widget: tk.Misc) -> None:
    _theme_widget(widget)
    try:
        children = widget.winfo_children()
    except tk.TclError:
        return
    for child in children:
        _theme_tree(child)


def install_dark_theme(app: tk.Tk) -> None:
    """Apply Sierra's dark UI without changing any application behavior."""
    _configure_ttk_styles(app)

    # Native Tk popups used by ttk.Combobox inherit these through the option DB.
    app.option_add("*TCombobox*Listbox.background", INPUT_BG)
    app.option_add("*TCombobox*Listbox.foreground", TEXT)
    app.option_add("*TCombobox*Listbox.selectBackground", SELECTION_BG)
    app.option_add("*TCombobox*Listbox.selectForeground", TEXT)
    app.option_add("*Menu.background", INPUT_BG)
    app.option_add("*Menu.foreground", TEXT)
    app.option_add("*Menu.activeBackground", HOVER_BG)
    app.option_add("*Menu.activeForeground", TEXT)

    _theme_tree(app)

    # Dependency prompts and other Toplevels are created later. Theme widgets as
    # they are mapped so those windows match the main application automatically.
    def on_map(event) -> None:
        try:
            _theme_widget(event.widget)
        except Exception:
            pass

    app.bind_all("<Map>", on_map, add="+")
    _schedule_windows_dark_titlebar(app)
