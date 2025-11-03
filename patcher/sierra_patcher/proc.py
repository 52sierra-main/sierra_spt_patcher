# sierra_patcher/proc.py
from __future__ import annotations
import os, sys, subprocess, threading, time

# Track live processes so we can kill them on Abort
_live: set[subprocess.Popen] = set()
_live_lock = threading.Lock()

# Windows flags to hide console windows
CREATE_NO_WINDOW = 0x08000000
CREATE_NEW_PROCESS_GROUP = 0x00000200
STARTF_USESHOWWINDOW = 0x00000001
SW_HIDE = 0

class Cancelled(Exception):
    """Raised when a child process is cancelled by the user."""
    pass

def _startupinfo_windows():
    if os.name != "nt":
        return None
    si = subprocess.STARTUPINFO()
    si.dwFlags |= STARTF_USESHOWWINDOW
    si.wShowWindow = SW_HIDE
    return si

def _reader(pipe, sink_list, on_output):
    """Line-buffered stream reader that forwards chunks to on_output."""
    try:
        for line in iter(pipe.readline, ''):
            sink_list.append(line)
            if on_output:
                on_output(line)
    except Exception:
        pass
    finally:
        try:
            pipe.close()
        except Exception:
            pass

def run_quiet(cmd: list[str],
              cwd: str | None = None,
              env: dict | None = None,
              check: bool = True,
              capture: bool = True,
              cancel_event: threading.Event | None = None,
              poll_interval: float = 0.1,
              on_output=None) -> subprocess.CompletedProcess:
    """
    Spawn a process with NO console window (on Windows), capture output,
    stream it via on_output in real time, and support cancellation.
    """
    kwargs = dict(
        cwd=cwd, env=env, shell=False,
        stdin=subprocess.DEVNULL,
        startupinfo=_startupinfo_windows(),
        creationflags=(CREATE_NO_WINDOW | CREATE_NEW_PROCESS_GROUP) if os.name == "nt" else 0,
    )
    if capture:
        # line-buffered text for streaming
        kwargs.update(stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, bufsize=1)
    else:
        kwargs.update(stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

    p = subprocess.Popen(cmd, **kwargs)
    with _live_lock:
        _live.add(p)

    out_chunks: list[str] = []
    err_chunks: list[str] = []
    t_out = t_err = None

    try:
        # start reader threads if capturing
        if capture:
            if p.stdout:
                t_out = threading.Thread(target=_reader, args=(p.stdout, out_chunks, on_output), daemon=True)
                t_out.start()
            if p.stderr:
                t_err = threading.Thread(target=_reader, args=(p.stderr, err_chunks, on_output), daemon=True)
                t_err.start()

        # cooperative cancel loop
        while True:
            if cancel_event and cancel_event.is_set():
                try:
                    p.terminate()
                except Exception:
                    pass
                try:
                    p.wait(timeout=0.5)
                except Exception:
                    try:
                        p.kill()
                    except Exception:
                        pass
                raise Cancelled()

            rc = p.poll()
            if rc is not None:
                break
            time.sleep(poll_interval)

        # join readers (drain)
        if t_out: t_out.join(timeout=0.5)
        if t_err: t_err.join(timeout=0.5)

        out = "".join(out_chunks) if capture else None
        err = "".join(err_chunks) if capture else None
        if check and p.returncode != 0:
            if cancel_event and cancel_event.is_set():
                raise Cancelled()
            raise subprocess.CalledProcessError(p.returncode, cmd, out, err)
        return subprocess.CompletedProcess(cmd, p.returncode, out, err)

    finally:
        with _live_lock:
            _live.discard(p)


def kill_all():
    """Force-kill all tracked child processes (used by Abort)."""
    with _live_lock:
        procs = list(_live)
    for p in procs:
        try:
            if p.poll() is None:
                if os.name == "nt":
                    p.terminate()
                else:
                    p.terminate()
                time.sleep(0.2)
                if p.poll() is None:
                    p.kill()
        except Exception:
            pass
    with _live_lock:
        _live.clear()
