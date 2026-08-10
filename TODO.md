# Sierra Patcher TODO

## Hotfix candidates

- [ ] Validate the user-selected destination copy before patching.
  - Keep the existing registered Live Tarkov version check as the pre-copy/outdated-Live warning.
  - Also require `EscapeFromTarkov.exe` to exist in the selected destination.
  - Compare the selected destination EXE version against the release metadata before any patch files are applied.
  - Stop with a clear incompatibility message if the destination copy is the wrong version.

## Next major / planned improvements

- [ ] Improve .NET dependency detection and warnings.
  - Prefer runtime requirements derived from the target SPT runtimeconfig files when generating a release.
  - Detect the actual required framework family (for example `Microsoft.NETCore.App` / `Microsoft.AspNetCore.App`) and minimum servicing version.
  - Treat newer compatible patches in the same major/minor runtime train as valid; do not treat a higher major runtime as an automatic substitute.
  - Keep user-facing labels/download links at the normal major/minor level (for example .NET 9.0 / 10.0).
  - Preserve the current hardcoded SPT-version inference as a fallback for older Sierra packages.

- [ ] Replace the current trivially discoverable dev-mode activation with stronger author access control.
  - Keep `dev.enable` only as a local/discovery gate if useful.
  - Add a second authorization gate before Generate/Repository access.
  - Preferred direction discussed: server-side credential validation with short-lived signed authorization, rather than downloading a credential list and checking it locally.
  - Longer-term option: separate public and author builds so privileged author modules are absent from the public executable.
