# Sierra Patcher TODO

This file tracks active work only. Completed changes belong in Git history/release notes.

## Install and integrity

- [ ] Verify reused Web objects by SHA-256, not size alone.
  - Re-check cached client objects before reuse.
  - Re-check existing publisher/repository objects before trusting a hash-named file.
  - Improve diagnostics so a corrupt object identifies expected/actual size and SHA-256.

- [ ] Clean stale `.part` download files when the completed object is already valid.
  - Keep partial files only when they are genuinely useful for resume.

- [ ] Consider an optional thorough source-integrity scan.
  - Current `source_hashes.json` intentionally covers delta source files only.
  - Unchanged source/target files are not verified; a support/debug mode could hash them when needed without slowing every normal install.

- [ ] Add the storage-only early source preflight to Archived snapshots.
  - Avoid spending minutes verifying/materializing the full archive before discovering that the selected source is incompatible.

## Maintenance

- [ ] Keep localization tests synchronized with intentional Korean wording changes.
  - Current known failures are wording drift rather than functional localization failures.

- [ ] Replace the current simple dev-mode activation with stronger author access control.
  - Prefer privileged author features being unavailable to ordinary public builds, or require a second authorization gate before Generate/Repository access.
