from __future__ import annotations

from pathlib import Path

from . import cli, gui_web
from .paths import STORAGE_out_DIR
from .runtime_requirements import write_runtime_requirements_manifest


_ENABLED = False


def _argument(args, kwargs, position: int, name: str, default=None):
    if len(args) > position:
        return args[position]
    return kwargs.get(name, default)


def enable_runtime_requirement_hooks() -> None:
    """Stamp target SPT runtimeconfig requirements during package generation."""

    global _ENABLED
    if _ENABLED:
        return
    _ENABLED = True

    # This hook intentionally wraps the current final generator callable. main
    # enables source-integrity hooks first, so both post-generation manifests are
    # produced without changing the hybrid patch engine itself.
    original_generate = gui_web.generate_patches

    def generate_with_runtime_requirements(*args, **kwargs):
        result = original_generate(*args, **kwargs)

        target_root = _argument(args, kwargs, 1, "dest_root")
        if target_root is None:
            raise RuntimeError("could not determine target SPT root for .NET prerequisite discovery")

        manifest = write_runtime_requirements_manifest(target_root, STORAGE_out_DIR)
        print(f"runtime requirements manifest ready: {manifest}")
        return result

    gui_web.generate_patches = generate_with_runtime_requirements
    cli.generate_patches = generate_with_runtime_requirements
