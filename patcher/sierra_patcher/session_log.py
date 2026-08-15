"""Session logging for Sierra Installer.

The GUI is built with ``console=False``, so ``sys.stdout``/``sys.stderr`` are
``None`` and every ``print()`` in the codebase is a silent no-op. This module
gives the application one durable sink:

- a timestamped log file on disk that survives a crash or force-close,
- a tee for stdout/stderr so existing ``print()`` diagnostics are captured,
- a session header describing the machine and build,
- fan-out to the GUI Logs tab through registered sinks.

Nothing here raises. Logging must never be able to fail an installation.
"""

from __future__ import annotations

import datetime as _dt
import os
import platform
import shutil
import sys
import tempfile
import threading
from pathlib import Path

LOG_DIR_NAME = "logs"
LOG_FILE_PREFIX = "sierra"
MAX_SESSIONS_KEPT = 10

try:
    import winreg
except ImportError:  # pragma: no cover - Sierra Installer is Windows-targeted
    winreg = None


def _cpu_brand() -> str:
    """Read the CPU name without spawning WMIC/PowerShell helper consoles."""

    if os.name == "nt" and winreg is not None:
        try:
            key_path = r"HARDWARE\DESCRIPTION\System\CentralProcessor\0"
            with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, key_path) as key:
                value, _ = winreg.QueryValueEx(key, "ProcessorNameString")
            brand = str(value).strip()
            if brand:
                return brand
        except Exception:
            pass
    return (os.environ.get("PROCESSOR_IDENTIFIER") or "unknown").strip() or "unknown"


def _candidate_log_dirs() -> list[Path]:
    """Preferred log locations, most discoverable first.

    Beside the executable is easiest for users to find and matches the portable
    nature of Archived snapshots. It can be read-only (Program Files, a network
    share, a mounted ISO), so LOCALAPPDATA and the temp directory follow.
    """

    candidates: list[Path] = []
    try:
        from .paths import WORKING_DIR

        candidates.append(Path(WORKING_DIR) / LOG_DIR_NAME)
    except Exception:
        pass

    local_appdata = os.environ.get("LOCALAPPDATA")
    if local_appdata:
        candidates.append(Path(local_appdata) / "SierraInstaller" / LOG_DIR_NAME)

    try:
        candidates.append(Path(tempfile.gettempdir()) / "SierraInstaller" / LOG_DIR_NAME)
    except Exception:
        pass
    return candidates


def _format_bytes(value: int | float | None) -> str:
    if value is None:
        return "unknown"
    size = float(value)
    for unit in ("B", "KiB", "MiB", "GiB", "TiB"):
        if size < 1024 or unit == "TiB":
            return f"{size:.1f} {unit}" if unit != "B" else f"{int(size)} B"
        size /= 1024
    return f"{size:.1f} TiB"


def free_space(path: str | Path | None) -> str:
    if not path:
        return "unknown"
    try:
        return _format_bytes(shutil.disk_usage(str(path)).free)
    except Exception:
        return "unknown"


class SessionLog:
    """Thread-safe line logger writing to a file plus any registered sinks."""

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._handle = None
        self._path: Path | None = None
        self._sinks: list = []
        self._opened = False

    @property
    def path(self) -> Path | None:
        return self._path

    def open(self) -> Path | None:
        with self._lock:
            if self._opened:
                return self._path
            self._opened = True

            stamp = _dt.datetime.now().strftime("%Y%m%d-%H%M%S")
            filename = f"{LOG_FILE_PREFIX}-{stamp}.log"
            for directory in _candidate_log_dirs():
                try:
                    directory.mkdir(parents=True, exist_ok=True)
                    handle = open(
                        directory / filename,
                        "a",
                        encoding="utf-8",
                        errors="replace",
                    )
                except Exception:
                    continue
                self._handle = handle
                self._path = directory / filename
                break

            self._prune()
            return self._path

    def _prune(self) -> None:
        """Keep only the newest MAX_SESSIONS_KEPT logs beside the current one."""

        if self._path is None:
            return
        try:
            existing = sorted(
                self._path.parent.glob(f"{LOG_FILE_PREFIX}-*.log"),
                key=lambda item: item.name,
                reverse=True,
            )
        except Exception:
            return
        for stale in existing[MAX_SESSIONS_KEPT:]:
            try:
                stale.unlink()
            except Exception:
                pass

    def add_sink(self, sink) -> None:
        with self._lock:
            if sink not in self._sinks:
                self._sinks.append(sink)

    def remove_sink(self, sink) -> None:
        with self._lock:
            if sink in self._sinks:
                self._sinks.remove(sink)

    def write(self, message, *, source: str = "") -> None:
        """Write one (possibly multi-line) message to the file and every sink."""

        try:
            text = str(message)
        except Exception:
            return

        stamp = _dt.datetime.now().strftime("%H:%M:%S.%f")[:-3]
        padding = " " * (len(stamp) + 2)
        prefix = f"[{source}] " if source else ""

        lines = text.replace("\r\n", "\n").replace("\r", "\n").split("\n")
        rendered = [f"{stamp}  {prefix}{lines[0]}"]
        rendered.extend(f"{padding}{line}" for line in lines[1:])
        formatted = "\n".join(rendered)

        with self._lock:
            handle = self._handle
            sinks = list(self._sinks)

        if handle is not None:
            try:
                handle.write(formatted + "\n")
                handle.flush()
            except Exception:
                pass

        for sink in sinks:
            try:
                sink(formatted)
            except Exception:
                pass

    def write_section(self, title: str, fields: dict) -> None:
        """Write a labelled block of key/value diagnostics."""

        width = max((len(str(key)) for key in fields), default=0)
        lines = [f"===== {title} ====="]
        for key, value in fields.items():
            lines.append(f"  {str(key).ljust(width)} : {value}")
        self.write("\n".join(lines))

    def close(self) -> None:
        with self._lock:
            handle = self._handle
            self._handle = None
        if handle is not None:
            try:
                handle.flush()
                handle.close()
            except Exception:
                pass


class _StreamTee:
    """File-like object forwarding writes to the session log and the real stream."""

    def __init__(self, log: SessionLog, source: str, passthrough) -> None:
        self._log = log
        self._source = source
        self._passthrough = passthrough
        self._buffer = ""
        self._lock = threading.Lock()

    def write(self, text) -> int:
        try:
            value = str(text)
        except Exception:
            return 0

        if self._passthrough is not None:
            try:
                self._passthrough.write(value)
            except Exception:
                pass

        if not value:
            return 0

        # Carriage returns are treated as line breaks so a stray progress
        # renderer cannot smear the log file into one enormous line.
        normalized = value.replace("\r\n", "\n").replace("\r", "\n")
        with self._lock:
            self._buffer += normalized
            parts = self._buffer.split("\n")
            self._buffer = parts.pop()
            complete = parts

        for line in complete:
            if line.strip():
                self._log.write(line, source=self._source)
        return len(value)

    def flush(self) -> None:
        if self._passthrough is not None:
            try:
                self._passthrough.flush()
            except Exception:
                pass
        with self._lock:
            pending = self._buffer
            self._buffer = ""
        if pending.strip():
            self._log.write(pending, source=self._source)

    def isatty(self) -> bool:
        return False

    def writable(self) -> bool:
        return True

    def readable(self) -> bool:
        return False

    def seekable(self) -> bool:
        return False

    @property
    def encoding(self) -> str:
        return "utf-8"

    def fileno(self):
        # Sierra always passes explicit PIPE/DEVNULL handles to subprocesses, so
        # nothing should ask for a descriptor. Fail loudly if something does.
        raise OSError("Sierra Installer log stream has no file descriptor")


_SESSION_LOG = SessionLog()
_REDIRECTED = False


def session_log() -> SessionLog:
    return _SESSION_LOG


def log(message, *, source: str = "") -> None:
    _SESSION_LOG.write(message, source=source)


def _session_header_fields(log_path: Path | None) -> dict:
    try:
        from . import __version__ as app_version
    except Exception:
        app_version = "unknown"

    frozen = bool(getattr(sys, "frozen", False))
    fields = {
        "Sierra Installer": app_version,
        "Build": "frozen executable" if frozen else "python script",
        "Executable": sys.executable if frozen else " ".join(sys.argv[:1]) or "python",
        "Started": _dt.datetime.now().astimezone().strftime("%Y-%m-%d %H:%M:%S %z"),
        "Log file": str(log_path) if log_path else "unavailable (in-memory only)",
        "OS": platform.platform(),
        "Python": platform.python_version(),
        "CPU": _cpu_brand(),
    }

    try:
        import psutil

        physical = psutil.cpu_count(logical=False) or 0
        logical = psutil.cpu_count(logical=True) or physical
        memory = psutil.virtual_memory()
        fields["Cores"] = f"{physical} physical / {logical} logical"
        fields["Memory"] = (
            f"{_format_bytes(memory.total)} total, {_format_bytes(memory.available)} available"
        )
    except Exception:
        fields["Cores"] = "unknown"
        fields["Memory"] = "unknown"

    try:
        from .paths import WORKING_DIR

        fields["Working directory"] = str(WORKING_DIR)
        fields["Working dir free"] = free_space(WORKING_DIR)
    except Exception:
        pass

    return fields


def start_session_logging() -> Path | None:
    """Open the log file, tee the standard streams, and stamp the header."""

    global _REDIRECTED

    path = _SESSION_LOG.open()

    if not _REDIRECTED:
        _REDIRECTED = True
        # With no real console, tqdm would otherwise treat the tee as a terminal
        # and fill the log with carriage-return progress frames. A real console
        # (CLI use) keeps its progress bars untouched.
        if sys.__stderr__ is None:
            os.environ.setdefault("SIERRA_TQDM", "1")
        try:
            sys.stdout = _StreamTee(_SESSION_LOG, "stdout", sys.__stdout__)
            sys.stderr = _StreamTee(_SESSION_LOG, "stderr", sys.__stderr__)
        except Exception:
            pass

    _SESSION_LOG.write_section("Sierra Installer session", _session_header_fields(path))
    return path
