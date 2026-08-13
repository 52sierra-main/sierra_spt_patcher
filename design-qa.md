# Design QA — Automatic copy install mode

- Source visual truth: `/home/gomim/.codex/generated_images/019ffab5-e5e1-7ca0-be4e-c4c9df05ba82/exec-a81b63f0-c47b-4540-9820-ed40d636104c.png`
- Implementation screenshot: `/mnt/c/Users/gomim/AppData/Local/Temp/sierra-ui-qa.0HyV4D/02-auto-ready.png`
- Built executable smoke screenshot: `/mnt/c/Users/gomim/AppData/Local/Temp/sierra-ui-qa.0HyV4D/04-built-exe.png`
- Combined comparison: `/mnt/c/Users/gomim/AppData/Local/Temp/sierra-ui-qa.0HyV4D/comparison.png`
- State: Korean, Web release selected, automatic copy selected, detected Live install, new empty SPT destination ready
- Viewport: native Windows Tk window at 980 × 680 content pixels, captured with a 996 × 719 Windows frame
- Source pixels: 1484 × 1066; implementation pixels: 996 × 719
- Normalization: both captures were scaled to 1066 pixels high for the combined comparison. The native desktop window is effectively 1×; browser CSS size and browser device scale factor do not apply.

## Findings

- No actionable P0/P1/P2 differences remain. The implementation preserves the current Sierra Installer density and native Segoe UI controls while matching the selected mock's hierarchy: install-mode radios, new SPT destination, detected source summary, ready badge, and destination path/version/free-space status.
- Typography: the implementation uses the existing native Segoe UI hierarchy. It is visually smaller than the generated mock because the mock was produced at a larger raster scale; enlarging the production UI would be an unrelated redesign.
- Spacing and layout: controls remain aligned inside the existing two-column install card, with no overlap or clipping at the fixed production window size.
- Colors and tokens: the existing dark theme, green ready state, warning colors, and blue primary action match the source intent.
- Image quality and assets: the screen contains only the existing Sierra application icon; no source image asset was replaced or approximated.
- Copy and content: Korean labels clearly separate automatic copy from an existing copy and identify the detected Live source.
- Interaction and accessibility: automatic/existing mode switching, disabled/ready states, Force-independent Live-path blocking, and keyboard-native radio controls passed the Windows GUI tests. Existing Tk UI Automation naming remains a follow-up accessibility limitation rather than a regression from this change.

## Comparison history

- First pass: destination status showed no version before copying. The status now shows the detected source version as the planned destination version in automatic mode.
- Final pass: the updated native capture has no actionable visual mismatch at the intended production density.

## Follow-up polish

- P3: very long destination paths begin horizontally scrolled in the native readonly field. The existing caret/scroll behavior still exposes the full value.
- P3: a future accessibility-focused change could add explicit Windows UI Automation names to the existing Tk controls.

## Verification

- Primary interactions: install-mode switch, destination validation, version states, download-failure boundary, and package → copy → patch ordering.
- Runtime errors: none during native Windows launch and capture.
- Focused comparison: not required; all added controls and status fields are legible in the full-resolution combined comparison.

final result: passed
