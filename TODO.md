# Sierra Patcher TODO

## Hotfix candidates

- [ ] Validate the user-selected destination copy before patching.
  - Keep the existing registered Live Tarkov version check as the pre-copy/outdated-Live warning.
  - Also require `EscapeFromTarkov.exe` to exist in the selected destination.
  - Compare the selected destination EXE version against the release metadata before any patch files are applied.
  - Stop with a clear incompatibility message if the destination copy is the wrong version.

## Completed

- [x] Improve .NET dependency detection and warnings.
  - Runtime requirements are derived from the target SPT `*.runtimeconfig.json` files when generating new releases.
  - Exact framework families and minimum servicing versions are recorded.
  - A newer patch in the same major/minor runtime train is accepted; a higher major alone is not treated as a substitute.
  - User-facing labels and Microsoft download links stay at the normal major/minor level.
  - Older Sierra packages still use the existing SPT-version inference fallback.

## Next major / planned improvements

- [ ] Replace the current trivially discoverable dev-mode activation with stronger author access control.
  - Keep `dev.enable` only as a local/discovery gate if useful.
  - Add a second authorization gate before Generate/Repository access.
  - Preferred direction discussed: server-side credential validation with short-lived signed authorization, rather than downloading a credential list and checking it locally.
  - Longer-term option: separate public and author builds so privileged author modules are absent from the public executable.
