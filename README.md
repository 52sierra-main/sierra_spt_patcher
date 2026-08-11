# sierra_spt_patcher
new, refactored version of the legacy sierra spt patcher

## GUI languages

The GUI supports English and Korean. Choose **Language → English** or
**Language → 한국어** from the menu bar; the window is rebuilt immediately and
the choice is remembered for future launches. A running install, generation,
or archive task must be finished or cancelled before switching languages.

On first launch, Sierra Patcher follows the operating-system language when it
is supported and otherwise uses English. Set `SIERRA_PATCHER_LANGUAGE=en` or
`SIERRA_PATCHER_LANGUAGE=ko` to override automatic selection.
