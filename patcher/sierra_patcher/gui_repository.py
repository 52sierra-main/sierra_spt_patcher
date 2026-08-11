from __future__ import annotations

import json
import threading
import tkinter as tk
from pathlib import Path
from tkinter import filedialog, messagebox, ttk

from .gui import _hide_console_on_windows, _safe_call
from .gui_hybrid import HybridSierraPatcherGUI
from .i18n import canonical_choice, tr
from .paths import WORKING_DIR
from .repository_tools import (
    RepositoryToolError,
    list_releases,
    load_release_metadata,
    rebuild_catalog,
    update_release_metadata,
    verify_release,
)


_REPOSITORY_RELEASE_PLACEHOLDER = "choose release"


class RepositorySierraPatcherGUI(HybridSierraPatcherGUI):
    """Hybrid GUI plus author-only local repository maintenance tools."""

    def __init__(self, dev: bool = False):
        super().__init__(dev=dev)
        if dev:
            notebook = next(
                (child for child in self.winfo_children() if isinstance(child, ttk.Notebook)),
                None,
            )
            if notebook is not None:
                self._repository_tab = self._build_repository_tab(notebook)
                # Generate is first in dev mode; keep repository work beside it.
                notebook.insert(1, self._repository_tab, text=tr("Repository"))

    def _build_repository_tab(self, nb) -> ttk.Frame:
        root = ttk.Frame(nb)
        root.columnconfigure(0, weight=1)
        root.columnconfigure(1, weight=1)

        selector = ttk.LabelFrame(root, text=tr("Local repository"))
        selector.grid(row=0, column=0, columnspan=2, sticky="ew", padx=10, pady=(8, 4))
        selector.columnconfigure(1, weight=1)

        default_root = (
            self.g_web_repo.get().strip()
            if hasattr(self, "g_web_repo") and self.g_web_repo.get().strip()
            else str(Path(WORKING_DIR) / "web_repo_output")
        )
        self.r_repository_var = tk.StringVar(value=default_root)
        self.r_release_var = tk.StringVar(value=tr(_REPOSITORY_RELEASE_PLACEHOLDER))

        ttk.Label(selector, text=tr("Repository directory")).grid(
            row=0, column=0, sticky="w", padx=(10, 8), pady=(8, 4)
        )
        ttk.Entry(selector, textvariable=self.r_repository_var).grid(
            row=0, column=1, sticky="ew", pady=(8, 4)
        )
        ttk.Button(selector, text=tr("Browse..."), command=self._repository_browse).grid(
            row=0, column=2, padx=(8, 10), pady=(8, 4)
        )

        ttk.Label(selector, text=tr("Release")).grid(
            row=1, column=0, sticky="w", padx=(10, 8), pady=(4, 8)
        )
        self.r_release = ttk.Combobox(
            selector,
            textvariable=self.r_release_var,
            state="readonly",
            values=(tr(_REPOSITORY_RELEASE_PLACEHOLDER),),
        )
        self.r_release.grid(row=1, column=1, sticky="ew", pady=(4, 8))
        self.r_release.bind("<<ComboboxSelected>>", lambda _event: self._repository_load_metadata())
        ttk.Button(selector, text=tr("Refresh"), command=self._repository_refresh).grid(
            row=1, column=2, padx=(8, 10), pady=(4, 8)
        )

        metadata = ttk.LabelFrame(root, text=tr("Release metadata"))
        metadata.grid(row=1, column=0, sticky="nsew", padx=(10, 5), pady=4)
        metadata.columnconfigure(1, weight=1)

        self.r_version_var = tk.StringVar()
        self.r_title_var = tk.StringVar()
        self.r_description_var = tk.StringVar()
        self.r_dependencies_var = tk.StringVar()
        self._repository_metadata_original: dict | None = None

        self._repository_row(metadata, 0, "Live version", self.r_version_var)
        self._repository_row(metadata, 1, "Release title", self.r_title_var)
        self._repository_row(metadata, 2, "Description / date", self.r_description_var)
        self._repository_row(metadata, 3, "Dependencies", self.r_dependencies_var)

        ttk.Label(metadata, text=tr("Integrity folders")).grid(
            row=4, column=0, sticky="nw", padx=(10, 8), pady=(6, 4)
        )
        self.r_integrity_text = tk.Text(metadata, height=6, width=34, wrap="none")
        self.r_integrity_text.grid(
            row=4, column=1, columnspan=2, sticky="nsew", padx=(0, 10), pady=(6, 4)
        )
        ttk.Label(
            metadata,
            text=tr("JSON object. Clearing this removes aggregate folder-size checks from the release metadata."),
            foreground="#666",
            wraplength=330,
        ).grid(row=5, column=0, columnspan=3, sticky="w", padx=10, pady=(2, 6))

        metadata_actions = ttk.Frame(metadata)
        metadata_actions.grid(row=6, column=0, columnspan=3, sticky="ew", padx=10, pady=(2, 8))
        self.r_btn_clear_integrity = ttk.Button(
            metadata_actions,
            text=tr("Clear integrity checks"),
            command=self._repository_clear_integrity,
        )
        self.r_btn_clear_integrity.pack(side="left")
        self.r_btn_save_metadata = ttk.Button(
            metadata_actions,
            text=tr("Save metadata to release"),
            command=self._repository_save_metadata,
        )
        self.r_btn_save_metadata.pack(side="left", padx=(8, 0))

        maintenance = ttk.LabelFrame(root, text=tr("Repository maintenance"))
        maintenance.grid(row=1, column=1, sticky="nsew", padx=(5, 10), pady=4)
        maintenance.columnconfigure(0, weight=1)

        ttk.Label(
            maintenance,
            text=tr(
                "These tools operate only on the selected local repository. "
                "They never modify the HFS server directly."
            ),
            wraplength=330,
            foreground="#555",
        ).grid(row=0, column=0, sticky="w", padx=10, pady=(10, 8))

        self.r_btn_verify = ttk.Button(
            maintenance,
            text=tr("Verify selected release"),
            command=self._repository_verify,
        )
        self.r_btn_verify.grid(row=1, column=0, sticky="ew", padx=10, pady=4)

        self.r_btn_catalog = ttk.Button(
            maintenance,
            text=tr("Rebuild catalog.json from local releases"),
            command=self._repository_rebuild_catalog,
        )
        self.r_btn_catalog.grid(row=2, column=0, sticky="ew", padx=10, pady=4)

        ttk.Separator(maintenance).grid(row=3, column=0, sticky="ew", padx=10, pady=10)
        ttk.Label(
            maintenance,
            text=tr(
                "Metadata edits create/reuse a new SHA-256 object and update only "
                "storage/metadata.info in the selected manifest. Old objects are left intact."
            ),
            wraplength=330,
            foreground="#666",
        ).grid(row=4, column=0, sticky="w", padx=10, pady=(0, 10))

        self.r_status_var = tk.StringVar(value=tr("Select a repository release."))
        ttk.Label(
            root,
            textvariable=self.r_status_var,
            foreground="#555",
            wraplength=740,
        ).grid(row=2, column=0, columnspan=2, sticky="w", padx=12, pady=(4, 8))

        self._repository_set_editor_enabled(False)
        self._repository_refresh()
        return root

    @staticmethod
    def _repository_row(parent, row: int, label: str, variable: tk.StringVar) -> None:
        ttk.Label(parent, text=tr(label)).grid(
            row=row, column=0, sticky="w", padx=(10, 8), pady=4
        )
        ttk.Entry(parent, textvariable=variable).grid(
            row=row, column=1, columnspan=2, sticky="ew", padx=(0, 10), pady=4
        )

    def _repository_root(self) -> Path:
        value = self.r_repository_var.get().strip()
        return Path(value or (Path(WORKING_DIR) / "web_repo_output")).resolve()

    def _repository_selected_release(self) -> str | None:
        value = self.r_release_var.get().strip()
        if (
            not value
            or canonical_choice(
                value,
                (_REPOSITORY_RELEASE_PLACEHOLDER,),
            ) == _REPOSITORY_RELEASE_PLACEHOLDER
        ):
            return None
        return value

    def _repository_browse(self) -> None:
        selected = filedialog.askdirectory(title=tr("Select Sierra web repository"))
        if selected:
            self.r_repository_var.set(selected)
            self._repository_refresh()

    def _repository_refresh(self) -> None:
        if not hasattr(self, "r_release"):
            return
        try:
            releases = list_releases(self._repository_root())
        except Exception as exc:
            releases = []
            self.r_status_var.set(tr("Could not inspect repository: {error}", error=exc))
        values = (tr(_REPOSITORY_RELEASE_PLACEHOLDER), *releases)
        current = self._repository_selected_release()
        self.r_release.configure(values=values)
        if current in releases:
            self.r_release_var.set(current)
            self._repository_load_metadata()
        else:
            self.r_release_var.set(tr(_REPOSITORY_RELEASE_PLACEHOLDER))
            self._repository_metadata_original = None
            self._repository_clear_editor()
            self._repository_set_editor_enabled(False)
            if releases:
                self.r_status_var.set(
                    tr("Local repository contains {count} release(s).", count=len(releases))
                )
            else:
                self.r_status_var.set(tr("No release manifests found in this local repository."))

    def _repository_clear_editor(self) -> None:
        self.r_version_var.set("")
        self.r_title_var.set("")
        self.r_description_var.set("")
        self.r_dependencies_var.set("")
        self.r_integrity_text.configure(state="normal")
        self.r_integrity_text.delete("1.0", tk.END)
        self.r_integrity_text.insert("1.0", "{}")

    def _repository_set_editor_enabled(self, enabled: bool) -> None:
        state = "normal" if enabled else "disabled"
        for button in (
            getattr(self, "r_btn_clear_integrity", None),
            getattr(self, "r_btn_save_metadata", None),
            getattr(self, "r_btn_verify", None),
        ):
            if button is not None:
                button.configure(state=state)
        if hasattr(self, "r_integrity_text"):
            self.r_integrity_text.configure(state=state)

    def _repository_load_metadata(self) -> None:
        release = self._repository_selected_release()
        if not release:
            self._repository_metadata_original = None
            self._repository_clear_editor()
            self._repository_set_editor_enabled(False)
            return
        try:
            data = load_release_metadata(self._repository_root(), release)
        except Exception as exc:
            self._repository_metadata_original = None
            self._repository_clear_editor()
            self._repository_set_editor_enabled(False)
            self.r_status_var.set(
                tr("Could not load {release} metadata: {error}", release=release, error=exc)
            )
            return

        self._repository_metadata_original = dict(data)
        self.r_version_var.set(str(data.get("version", "")))
        self.r_title_var.set(str(data.get("title", "")))
        self.r_description_var.set(str(data.get("description", "")))
        dependencies = data.get("dependencies")
        self.r_dependencies_var.set("" if dependencies is None else str(dependencies))
        integrity = data.get("integrity_folders", {}) or {}
        self.r_integrity_text.configure(state="normal")
        self.r_integrity_text.delete("1.0", tk.END)
        self.r_integrity_text.insert("1.0", json.dumps(integrity, indent=2, ensure_ascii=False))
        self._repository_set_editor_enabled(True)
        self.r_status_var.set(tr("Loaded metadata for {release}.", release=release))

    def _repository_clear_integrity(self) -> None:
        self.r_integrity_text.configure(state="normal")
        self.r_integrity_text.delete("1.0", tk.END)
        self.r_integrity_text.insert("1.0", "{}")
        self.r_status_var.set(
            tr("Integrity checks cleared in the editor. Click Save metadata to publish the change locally.")
        )

    def _repository_collect_metadata(self) -> dict:
        if self._repository_metadata_original is None:
            raise RepositoryToolError(tr("no release metadata is loaded"))
        try:
            integrity = json.loads(self.r_integrity_text.get("1.0", tk.END).strip() or "{}")
        except json.JSONDecodeError as exc:
            raise RepositoryToolError(
                tr("integrity folders JSON is invalid: {error}", error=exc)
            ) from exc
        if not isinstance(integrity, dict):
            raise RepositoryToolError(tr("integrity folders must be a JSON object"))
        for path, size in integrity.items():
            if not isinstance(path, str) or not path.strip():
                raise RepositoryToolError(tr("integrity folder paths must be non-empty strings"))
            if not isinstance(size, int) or size < 0:
                raise RepositoryToolError(
                    tr(
                        "integrity size for {path} must be a non-negative integer",
                        path=repr(path),
                    )
                )

        data = dict(self._repository_metadata_original)
        data["version"] = self.r_version_var.get().strip()
        data["title"] = self.r_title_var.get().strip()
        data["description"] = self.r_description_var.get().strip()
        dependencies = self.r_dependencies_var.get().strip()
        data["dependencies"] = dependencies or None
        data["integrity_folders"] = integrity
        return data

    def _repository_save_metadata(self) -> None:
        release = self._repository_selected_release()
        if not release:
            messagebox.showerror(
                tr("Release required"),
                tr("Choose a local repository release first."),
            )
            return
        try:
            data = self._repository_collect_metadata()
        except Exception as exc:
            messagebox.showerror(tr("Metadata"), str(exc))
            return
        if not messagebox.askyesno(
            tr("Update local release metadata"),
            tr(
                "Update metadata for {release} in the local repository?\n\n"
                "This creates/reuses a new content-addressed object and updates the local manifest. "
                "It does not upload anything to HFS.",
                release=release,
            ),
        ):
            return

        try:
            object_id = update_release_metadata(self._repository_root(), release, data)
        except Exception as exc:
            self._log(f"[repository] metadata update failed for {release}: {exc}")
            messagebox.showerror(tr("Repository metadata"), str(exc))
            return

        self._repository_metadata_original = dict(data)
        self.r_status_var.set(
            tr(
                "Updated {release} metadata. New object: {object_id}. "
                "Upload that object and the updated release manifest to HFS; catalog.json is unchanged.",
                release=release,
                object_id=object_id,
            )
        )
        self._log(f"[repository] metadata updated release={release} object={object_id}")

    def _repository_rebuild_catalog(self) -> None:
        try:
            catalog_path, releases = rebuild_catalog(self._repository_root())
        except Exception as exc:
            self._log(f"[repository] catalog rebuild failed: {exc}")
            messagebox.showerror(tr("Repository catalog"), str(exc))
            return
        self.r_status_var.set(
            tr(
                "Rebuilt {name} with {count} local release(s): {releases}",
                name=catalog_path.name,
                count=len(releases),
                releases=", ".join(releases) if releases else tr("(none)"),
            )
        )
        self._log(f"[repository] rebuilt catalog releases={len(releases)} path={catalog_path}")

    def _repository_verify(self) -> None:
        release = self._repository_selected_release()
        if not release:
            messagebox.showerror(
                tr("Release required"),
                tr("Choose a local repository release first."),
            )
            return

        self.r_btn_verify.configure(state="disabled")
        self._repository_running = True
        self.r_status_var.set(tr("Verifying {release}...", release=release))
        cancel_event = threading.Event()

        def progress(current: int, total: int, logical_path: str) -> None:
            _safe_call(
                self,
                self.r_status_var.set,
                tr(
                    "Verifying {release}: {current}/{total}  {path}",
                    release=release,
                    current=current,
                    total=total,
                    path=logical_path,
                ),
            )

        def finish_verification() -> None:
            self._repository_running = False
            self.r_btn_verify.configure(state="normal")

        def worker() -> None:
            try:
                result = verify_release(
                    self._repository_root(),
                    release,
                    on_progress=progress,
                    cancel_event=cancel_event,
                )
                mib = result.total_logical_bytes / (1024 * 1024)
                message = tr(
                    "Verified {release}: {files} logical file(s), "
                    "{objects} object reference(s), {size:,.1f} MiB.",
                    release=release,
                    files=result.file_count,
                    objects=result.object_references,
                    size=mib,
                )
                _safe_call(self, self.r_status_var.set, message)
                self._log(f"[repository] {message}")
            except Exception as exc:
                self._log(f"[repository] verification failed for {release}: {exc}")
                _safe_call(
                    self,
                    self.r_status_var.set,
                    tr("Verification failed: {error}", error=exc),
                )
                _safe_call(
                    self,
                    messagebox.showerror,
                    tr("Repository verification"),
                    str(exc),
                )
            finally:
                _safe_call(self, finish_verification)

        threading.Thread(target=worker, daemon=True).start()


def main(dev: bool = False):
    _hide_console_on_windows()
    app = RepositorySierraPatcherGUI(dev=dev)
    app.mainloop()
