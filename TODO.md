# Sierra Patcher TODO

## Hotfix candidates

- [ ] Validate the user-selected destination copy before patching.
  - Keep the existing registered Live Tarkov version check as the pre-copy/outdated-Live warning.
  - Also require `EscapeFromTarkov.exe` to exist in the selected destination.
  - Compare the selected destination EXE version against the release metadata before any patch files are applied.
  - Stop with a clear incompatibility message if the destination copy is the wrong version.
  - Note: the source-hash preflight already catches a wrong destination, but only
    for packages that ship `source_hashes.json`. This check would also cover
    legacy packages and produce a clearer message. The EXE FileVersion does not
    always change across BSG content hotfixes, so it narrows but does not
    replace the hash check.

- [ ] SHA-256 verify cached web objects instead of trusting file size.
  - `web_download._ensure_object` accepts an existing cached object when its size
    matches; only freshly downloaded objects are hash-verified.
  - `archived_snapshot._prepare_archived_object_cache` already does the full
    verification. The web delivery path never got the same treatment.
  - Materialization re-hashes the assembled file, so corruption is currently
    caught late and reported as "reconstructed file verification failed" rather
    than as a bad cache entry.

- [ ] Decide what to do about unverified "unchanged" files.
  - `source_hashes.json` covers only delta sources — the files whose existing
    bytes are read as patch input. Payload destinations are overwritten, so
    they need no check.
  - Files identical between the release's source and target get no delta, no
    payload and no delete entry. They are never sent, never touched and never
    verified, so a destination carrying a different version of one passes the
    preflight and survives into the installed SPT.
  - Measured on 3.11.4: of 40 deliberately corrupted files, 21 were delta
    sources (caught), 13 were payloads (harmless), 6 were unchanged (invisible).
  - Full coverage would mean hashing ~25,000 files instead of 5,284. The
    preflight already takes ~97 seconds, so this likely becomes 5-10 minutes on
    every install.
  - Suggested direction: leave the default alone, expose an opt-in "thorough
    check" for support to request on confusing reports.

- [ ] Consider the same pre-download check for Archived snapshots.
  - `ArchivedSnapshotSource.prepare` verifies every object before
    reconstruction, which takes minutes on an HDD.
  - Reading only `storage/` from the snapshot first would reject a wrong
    destination before that work starts, exactly as the web path now does.

- [ ] Fix Korean translation drift: 3 tests fail on `main` for this reason alone.
  - `test_i18n.test_korean_translation_formats_values` — expects
    "라이브 타르코프 폴더", `i18n.py` now has "본섭 타르코프 폴더".
  - `test_localized_diagnostics.test_korean_source_integrity_summary_localizes_ui_but_preserves_data`
    — expects "게임 파일은 변경되지 않았어요.", gets
    "대상 폴더의 파일은 변경되지 않았습니다."
  - `test_localized_diagnostics.test_korean_runtimeconfig_requirement_is_fully_localized`
    — expects "...구성 요소예요.", gets "...구성 요소."
  - These look like a deliberate register change (casual "-어요/예요" to formal
    "-습니다") that the tests were never updated for. Decide which register is
    canonical for the Korean UI and align the other side.
  - Verified pre-existing: pristine `HEAD` fails exactly these 3, 23/26 pass.

## Completed

- [x] Improve .NET dependency detection and warnings.
  - Runtime requirements are derived from the target SPT `*.runtimeconfig.json` files when generating new releases.
  - Exact framework families and minimum servicing versions are recorded.
  - A newer patch in the same major/minor runtime train is accepted; a higher major alone is not treated as a substitute.
  - User-facing labels and Microsoft download links stay at the normal major/minor level.
  - Older Sierra packages still use the existing SPT-version inference fallback.

- [x] Force must never bypass the exact source-file preflight.
  - Force still overrides the heuristic checks (version string, aggregate
    folder sizes) because those can raise false alarms.
  - It no longer skips `source_hashes.json` verification. A source-hash mismatch
    is proof the deltas cannot decode, so bypassing it never produces a working
    install — it only delays the failure until after thousands of the user's
    files have been rewritten.
  - Force state is now logged explicitly at the start of every install.

- [x] Stop the patch stage early once the destination is proven wrong.
  - `apply_patches_resilient` aborts after `DEFAULT_ABORT_AFTER_SOURCE_FAILURES`
    (25) fatal source failures.
  - The abort flag is checked inside the workers, not only in the consumer loop:
    every patch is submitted up front, so a consumer-only check let the pool run
    thousands of extra patches before stopping.
  - A wrong-copy run now touches roughly 25 files instead of ~1569.
  - `PatchApplyReport` gained `not_attempted` and `aborted_early`, and derives
    `succeeded` from real results so unattempted patches are not counted as
    successes.

- [x] Split `ZSTD_FAILURE` into deterministic and transient causes.
  - `ZSTD_SOURCE_MISMATCH` ("Restored data doesn't match checksum", "Data
    corruption detected") means the destination file is not the delta's source.
    Deterministic, so it is no longer retried.
  - `ZSTD_IO` (read errors, sharing violations, unknown causes) stays retryable
    for genuine antivirus/indexer lock contention.
  - Previously a wrong-copy install retried 3828 patches twice for no possible
    benefit — roughly 7,600 pointless zstd invocations over multi-GB bundles.

- [x] Verify the destination before downloading the package.
  - `materialize_web_package` accepts a `path_filter`, and
    `WebPackageSource.prepare_storage` uses it to fetch only `storage/`.
  - Measured on 3.11.4: 4 objects / 1.51 MB instead of 9,257 objects / 8.68 GB,
    or 99.98% of the download deferred until the destination is known good.
  - A wrong folder is now rejected in roughly 90 seconds (1 second of download
    plus the 97-second hash pass) instead of after a 25-60 minute download.
  - The check is shared: `ResilientSierraPatcherGUI._verify_source_files` is
    called by the pre-download check and by the patch stage, and
    `_source_preflight_done` stops the same 5,284 files being hashed twice.
    Offline sources with no early check still verify at the patch stage.
  - Objects are content-addressed, so the storage objects fetched early are
    reused by the full download rather than re-fetched.

- [x] Durable session logging.
  - New `session_log.py` writes a timestamped log file per session, keeping the
    10 most recent, beside the executable when writable and falling back to
    LOCALAPPDATA and then the temp directory.
  - `sys.stdout`/`sys.stderr` are teed into it. In the windowed build those are
    `None`, so every `print()` in the engine modules was previously discarded —
    including low memory/disk warnings, delete-list failures, and payload
    counts.
  - A session header records build, OS, CPU, memory and free space; an install
    header records source mode, release, destination, cache, worker counts and
    Force state.
  - Every line is timestamped. Tk callback exceptions and fatal errors are
    captured.
  - The Logs tab is read-only (users were typing into it), keeps only the most
    recent lines, and gained Save / Copy / Open log folder buttons. The on-disk
    log stays complete.

## Next major / planned improvements

- [ ] Replace the current trivially discoverable dev-mode activation with stronger author access control.
  - Keep `dev.enable` only as a local/discovery gate if useful.
  - Add a second authorization gate before Generate/Repository access.
  - Preferred direction discussed: server-side credential validation with short-lived signed authorization, rather than downloading a credential list and checking it locally.
  - Longer-term option: separate public and author builds so privileged author modules are absent from the public executable.
