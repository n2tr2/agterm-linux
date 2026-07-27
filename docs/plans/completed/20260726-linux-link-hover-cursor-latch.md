# Fix hand-cursor latch after first hyperlink hover (Linux)

## Overview

Once the pointer crosses any hyperlink in a terminal pane — an OSC 8 link or a regex-detected URL — the
GTK port shows the hand ("pointer") cursor over the whole surface for the lifetime of that surface. The
I-beam never returns: not over plain text, not after the linked content scrolls away, and not after an
application resets the shape via OSC 22. Only closing the session clears it. macOS agterm is unaffected.

Root cause (verified against the pinned libghostty revision `11b9a6ef` — the Zig citations below were read
from `raw.githubusercontent.com/ghostty-org/ghostty/11b9a6ef.../src/...`, since
`scripts/setup-linux.sh:36-44` clones the source into a `mktemp -d` it deletes on exit and
`agterm-linux/vendor/ghostty/` keeps only `include/`, `lib/`, `share/`; line numbers will drift on the next
`GHOSTTY_REV` bump, so symbol names are given alongside them):
`GhosttyApp.swift:195` decides link-hover state with `action.action.mouse_over_link.url != nil`, which is a
tautology. libghostty signals "hover ended" with an **empty string, never a null pointer** — all three clear
sites in `src/Surface.zig` (`mouseRefreshLinks` 1666-1670, the mouse-reporting modifier reset 2748-2752,
and `cursorPosCallback`'s out-of-viewport branch 4535-4539) send `.mouse_over_link` with `.url = ""`, and
`MouseOverLink.cval` (`src/apprt/action.zig:664-669`) passes `.url = self.url.ptr` from a non-optional
`[:0]const u8`, whose `.ptr` points at the terminator. The clear event therefore arrives as
`url = <non-null>, len = 0`, the port reads it as "over a link", and `mouseOverLink` latches `true` with no
code path that ever clears it. Because that flag outranks `mouseShapeName` in `applyMouseCursor()`
(`GhosttySurface.swift:507` — `!mouseVisible ? "none" : (mouseOverLink ? "pointer" : mouseShapeName)`, so
only the typing-hides-pointer layer sits above it), every later `mouse_shape` update is computed and
discarded.

Two aggravating details found during the investigation:

- `GhosttySurface.mouseLeft()` (`:426-431`) reports `(-1, -1)` *specifically to clear hover state*, which
  hits `Surface.zig:4525-4541` and emits another `.url = ""` — the one mechanism designed to clear the flag
  re-arms it.
- With `link-previews = false` (or `osc8` plus a regex URL), the *set* event never fires at all — it is
  gated behind `if (preview)` at `Surface.zig:1646` — while clears stay ungated, so the pane latches on the
  first hover-**out** having never been told about a link. Default is `.true`, so the common repro still
  sees a legitimate set first.

The fix reads the authoritative field (`len`) through a small host-free helper in `GhosttyActionDecoder`,
mirroring how the adjacent `GHOSTTY_ACTION_OPEN_URL` arm already decodes the same pointer+len shape.

## Context (from discovery)

- Files/components involved:
  - `agterm-linux/Sources/AgtermLinux/GhosttyApp.swift:193-196` — the misreading action arm
  - `agterm-linux/Sources/AgtermLinux/GhosttyActionDecoder.swift` — 9-line, import-free decode helper
    (`AgtermLinux` target; not `agtermCore`, not libghostty)
  - `agterm-linux/Sources/AgtermLinux/GhosttySurface.swift:47, 455-460, 506-509` — the latched flag and the
    cursor precedence that makes it visible
  - `agterm-linux/Tests/AgtermLinuxTests/GhosttyLifetimeTests.swift` — swift-testing suite, CI-guarded by
    `swift test (agterm-linux)` (`.github/workflows/ci.yml:166`)
  - `agterm-linux/vendor/ghostty/include/ghostty.h:735-738` — `const char* url; size_t len;`
- Related patterns found: commit `7e30660 fix(linux): own libghostty callback buffers` introduced
  `GhosttyActionDecoder.utf8String(_:length:)` **and** tested the exact contract this bug violates
  (`GhosttyLifetimeTests.swift:18` asserts `utf8String(nil, length: 0) == ""`), then applied it to
  `open_url` only. The handler itself dates from the original `feat(linux): add GTK implementation` commit.
- Dependencies identified: none new. The helper is pure Swift over `UnsafePointer<CChar>?` / `UInt`.
- Prior audit gap: `docs/plans/2026-07-15-linux-libghostty-callback-audit.json:4` swept the action union as
  a class for buffer *lifetime* ("could this pointer dangle?"), recording "length-delimited URL copied
  before launch; other strings consumed synchronously" with no per-field entry for `mouse_over_link` — it
  never asked what a pointer *means* when `len == 0`.
- Toolchain on this box: `swift` is not on PATH. Use
  `~/.local/share/mise/installs/swift/6.3.2/usr/bin/swift` with
  `LD_LIBRARY_PATH=~/.local/share/swift-linux-compat`. `swiftlint` is **not** installed locally — lint is
  enforced by CI only.

## Development Approach

- **testing approach**: TDD (tests first)
- complete each task fully before moving to the next
- make small, focused changes
- **CRITICAL: every task MUST include new/updated tests** for code changes in that task
- **CRITICAL: all tests must pass before starting next task** — no exceptions
- **CRITICAL: update this plan file when scope changes during implementation**
- run tests after each change
- maintain backward compatibility
- **Tasks 1 and 2 land as ONE commit.** Task 1's exit state is a deliberately failing `swift test`, and CI
  runs `swift test (agterm-linux)` on push (`.github/workflows/ci.yml:166`) — the red is a local checkpoint,
  never a pushed commit.

## Testing Strategy

- **unit tests**: swift-testing (`@Test` / `#expect`). The new case is a payload-*semantics* test rather
  than a lifetime test; it first landed in `GhosttyLifetimeTests.swift` because that suite owned the
  `GhosttyActionDecoder` seam, and the phase-2 code-smell review moved every decoder case out to
  `agterm-linux/Tests/AgtermLinuxTests/GhosttyActionDecoderTests.swift`
  (suite `libghostty action payload decoding`), so the file is named for its subject like every other test
  file in the target. Task 3's `readSelection` change is covered
  the same way — by routing its decode through a second tested helper (`lossyUTF8String`) rather than
  inlining it, since `readSelection` itself needs a live GTK surface and cannot be unit-tested directly.
- **red phase is behavioral, not a compile error**: Task 1 adds the helper with today's shipped semantics
  (`bytes != nil`) so the new test compiles and *fails on the length-0 case*. This proves the predicate
  discriminates the two payload semantics — it does **not** prove the call site is wired correctly.
- **narrowed residual gap** (was "no automated test covers the call site at all"; tightened during the
  post-implementation review): the decode half of the action arm **is** covered — the arm calls the payload
  overload `linkHoverActive(_ value: ghostty_action_mouse_over_link_s)`, so the field selection (`value.url` /
  `value.len`) and the `size_t` → `UInt` conversion are asserted host-free in
  `GhosttyActionDecoderTests.linkHoverPayloadLength()` and in `GhosttySurfaceCursorTests`. Verified by
  experiment, not by inspection: rewriting the overload body back to `value.url != nil` fails two tests
  (`link hover follows the payload length, not the pointer` and `a hover-end payload restores the shape
  instead of latching the hand`). What stays uncovered is only the surface-bound half — `Self.wrapper(fromTarget:)`
  resolution and the `setLinkHover(…)` call — so an arm rewritten to bypass the decoder entirely would still
  pass. The repo's one action-boundary test hook,
  `GhosttyApp.exerciseURLAction` (`#if DEBUG`, driven by `AGTERM_ATSPI_OPEN_URL` at `App.swift:91-93`),
  does not extend cheaply: it calls `handleAction` with a zero `ghostty_target_s()`, which the `OPEN_URL`
  arm tolerates because it never touches the target, while `Self.wrapper(fromTarget:)` requires
  `target.tag == GHOSTTY_TARGET_SURFACE` plus a non-nil surface and userdata, so the `MOUSE_OVER_LINK` arm
  would resolve `nil` and silently no-op. The wiring is therefore
  covered only by the manual verification under Post-Completion.
- **no e2e tests** (corrected during the post-implementation review — the original justification, "the
  harness cannot observe `gtk_widget_set_cursor_from_name`", was wrong): the harness solves exactly this
  unobservable-side-effect problem for the URL launch with a `#if DEBUG` env-gated capture-to-file
  (`GhosttyApp.swift` writes `AGTERM_ATSPI_URL_CAPTURE`, consumed in `agterm-linux/tests/atspi_smoke.py`),
  and it already drives the pointer with `xdotool mousemove --sync`. The same pattern would work for the
  cursor (capture the resolved name from `applyMouseCursor()`), so e2e is *deferred*, not impossible — it is
  listed as a follow-up under Post-Completion. Cursor behavior here is covered by the unit tests
  (`GhosttySurfaceCursorTests`, which pin the precedence rule and the payload → cursor-name sequence) plus
  the manual verification listed under Post-Completion.
- **test command** (this box — the shell is fish, where `VAR=… cmd` is a syntax error and `~` after `=` is
  not expanded, so use `env` and absolute paths; `--package-path` also avoids the relative-`cd` drift
  `CLAUDE.md` warns about). The path below points at the **worktree** this branch is developed in — running
  it against the main checkout (`/home/n/p/github/agterm-linux/agterm-linux`) validates a tree that does not
  contain this change:

  ```
  env LD_LIBRARY_PATH=$HOME/.local/share/swift-linux-compat \
    /home/n/.local/share/mise/installs/swift/6.3.2/usr/bin/swift test \
    --package-path /home/n/p/github/agterm-linux/.claude/worktrees/linux-link-hover-cursor-latch/agterm-linux
  ```

## Progress Tracking

- mark completed items with `[x]` immediately when done
- add newly discovered tasks with ➕ prefix
- document issues/blockers with ⚠️ prefix
- update plan if implementation deviates from original scope
- keep plan in sync with actual work done

### 2026-07-26 — Task 3 payload audit outcome

The eight-struct table re-derived clean from the vendored header — every line number and length type in it
still matches (`version_len`:396, `ghostty_string_s.len`:405, `text_len`:415, `color_list.len`:517,
`command_list.len`:523, `mouse_over_link.len`:737, `key_table.activate.len`:783, `open_url.len`:829). The
pin has not moved; no correction was needed. The re-derivation grep also surfaced the 7 function prototypes
the plan predicted (h:1072, 1086, 1089, 1137-1139, 1163) — not payload structs, skipped.

Per-struct result:

- `ghostty_info_s`, `ghostty_string_s`, `ghostty_config_color_list_s`, `ghostty_config_command_list_s` — **no
  consumers**. `grep -rn "ghostty_info\|ghostty_string_s\|color_list\|command_list" agterm-linux/Sources`
  returned **zero hits**, exactly as the plan expected. The only config read is the scalar
  `ghostty_config_color_s` in `GhosttyConfigTheme.color(from:key:)`, which carries no length pair.
- `ghostty_text_s` — **one finding, now closed** (the known one): `readSelection()` discarded `out.text_len`
  and read to the NUL terminator. Routed through the new `lossyUTF8String(_:length:)`. Its sibling
  `readScreenText` (`GhosttySurface.swift:368-369`) was already correct — it guarded `text_len > 0` and
  decoded by length. It initially kept that inline decode; the phase-2 code-smell review routed it through
  the helper as `lossyUTF8String(...) ?? ""`, which reproduces its length-0-reads-as-`""` contract exactly
  and additionally survives an over-`Int.max` length that the inline `Int(out.text_len)` would have
  trapped on.
- `ghostty_action_mouse_over_link_s` — the Task 1-2 subject; fixed and unit-tested.
- `ghostty_action_open_url_s` — **already correct**, unchanged: `GhosttyApp.swift:189` still decodes through
  `utf8String(value.url, length: value.len)`, and `exerciseURLAction` (`:264-277`) still drives the real
  action boundary with a non-NUL-terminated, `XY`-suffixed buffer and `len: UInt(raw.utf8.count)`.
- `ghostty_action_key_table_u.activate` — **no handler arm**: `grep -rn "KEY_TABLE\|key_table"` over
  `Sources` and `Tests` returned zero hits, so there is nothing to misread today. Contract for whoever adds
  one: `activate.len` is authoritative, the `name` pointer is not NUL-terminated by contract — decode via
  `GhosttyActionDecoder.utf8String(_:length:)` (or `lossyUTF8String` if an undecodable name must survive),
  never `String(cString:)`.

**Conclusion: no further misreads.** One finding total (`readSelection`), the one already located during
review, and it was latent rather than live — `Surface.Text.text` is `[:0]const u8` today, so the old code
did not read out of bounds; it truncated at an embedded NUL, ignored the authoritative length, and diverged
from macOS. The `.text` field hits at `GhosttySurface.swift:258, 573, 590` are `ghostty_input_key_s.text`
on the **write** direction (host→libghostty key events), out of audit scope for the same reason
`surface_config.env_vars` is.

### 2026-07-26 — Task 4 field-grep verdicts

The field grep returned **12 hits**, in four classes. Every hit was opened and judged; none is an unguarded
pointer+length read.

| Hit(s) | Class | Verdict |
|---|---|---|
| `GhosttySurface.swift:356` | read, `ghostty_text_s.text` | **clean (fixed in Task 3)** — `lossyUTF8String(out.text, length: out.text_len)`, length-authoritative |
| `GhosttySurface.swift:369` | read, `ghostty_text_s.text` | **clean (already)** — decoded by `text_len`; routed through `lossyUTF8String(out.text, length: out.text_len) ?? ""` in the phase-2 review, contract unchanged |
| `GhosttyApp.swift:189` | read, `open_url.url` | **clean (already)** — `utf8String(value.url, length: value.len)` |
| `GhosttyApp.swift:201` | read, `mouse_over_link.url` | **clean (this fix)** — `linkHoverActive(value.url, length: UInt(bitPattern: value.len))` |
| `GhosttySurface.swift:258, 574, 579, 591` | **write**, `ghostty_input_key_s.text` | **out of scope, re-verified independently** — host→libghostty key events. The header shows the struct carries a bare `const char* text` with **no companion length field** (h:351-359), so there is no length to misread; same rationale as `surface_config.env_vars`. Task 3's classification holds (its line numbers 573/590 shifted by one to 574/591 after the Task 3 edit) |
| `GhosttySurface.swift:254` | not a C field | **false positive** — Swift `case .text(let run)` on the port's own `KeystrokeSegments` enum |
| `GhosttyConfigTheme.swift:51` | not a C field | **false positive** — `GhosttyConfigTheme.colors(from:)`, a Swift static method, not `ghostty_config_color_list_s.colors`. The only C config read remains the scalar `ghostty_config_color_s` in `color(from:key:)`; its `UInt(key.utf8.count)` is the *key* length on the write side |
| `GhosttyActionDecoder.swift:25`, `GhosttyApp.swift:195` | comments | **not code** — both are the new `.ptr` / `[:0]const u8` contract notes |

Two supporting checks the plan's notes asked for:

- `GhosttyApp.swift:159` (`set_title.title`) and `:163` (`pwd.pwd`) are indeed **absent** from the pattern and
  are length-free by construction — confirmed in the vendored header, `ghostty_action_set_title_s` (h:674-677)
  and `ghostty_action_pwd_s` (h:685-688) each hold a single bare `const char*`. `String(cString:)` is correct
  there.
- The `Ghostty*.swift` glob is **sufficient**, with one addendum to the plan's note.
  `grep -rln "ghostty_" Sources/AgtermLinux/` lists seven files; the glob covers five. The uncovered ones are
  `GtkInterop.swift` (`ghostty_input_mods_e(rawValue:)` only) and `LinuxSettingsController.swift`
  (`ghostty_config_free` only), exactly as predicted, **plus a third the plan did not name**:
  `LinuxThemePolicy.swift`, whose only hit is `:83`, a doc comment mentioning `ghostty_config_get` — no call.
  None reads a pointer+length payload, so the conclusion is unchanged.

⚠️ Second pre-existing failure found on this box, unrelated to this plan and **not fixed here**:
`agtermCore` ▸ `CodexStatusHookTests` ▸ `stopReportsBlockedWhenAssistantMessageEndsInQuestionMark()`.
Root cause is environmental, not a regression. `agterm/Resources/agent-status/agterm-codex-status.sh:30`
gates its JSON extraction on `[ -x /usr/bin/plutil ]`, treating the binary's presence as a proxy for macOS.
This Manjaro box ships **GNUstep's** `plutil` at that path (`pacman -Qo /usr/bin/plutil` → `gnustep-base
1.31.1-3`), which is a different program: it rejects `-o - raw`
(`NSInvalidArgumentException … Invalid fmt raw`) and exits 1. The hook therefore takes the macOS branch,
`assistant_asked_question` returns 1, and the Stop hook degrades to `completed` instead of `blocked` —
which is exactly why only the "blocked" expectation fails while the sibling `completed` cases pass. The
`python3` and `jq` fallbacks below it both work here (verified by hand: the extraction plus the
`\?[[:space:]]*$` match returns MATCH), so the bug is purely the branch order/probe, never reached.
The branch touches no file involved, and the failure reproduces from the shell independently of Swift.

➕ Discovered follow-up, deliberately out of this plan's scope: make that hook's macOS probe real (e.g.
`[ "$(uname -s)" = Darwin ]` alongside the `-x` test, or probe `plutil -extract` support) so a Linux box with
`gnustep-base` installed gets working Codex blocked-detection. It belongs with the agent-status hook surface,
not with the link-hover cursor fix, so it is left for a separate change.

### 2026-07-26 — post-implementation review outcome

No correctness defect was found in the shipped fix; the review's confirmed findings were coverage and
documentation. Applied:

- **Payload overload** `GhosttyActionDecoder.linkHoverActive(_ value: ghostty_action_mouse_over_link_s)`,
  called by the action arm. Before this, the field selection and the `size_t` → `UInt` conversion lived
  outside the tested seam, so rewriting them wrongly left all tests green; now the same rewrite
  (`value.url != nil` in the overload) fails two tests — checked by actually making that edit and running the
  suite, then reverting. The decoder now carries `import CGtk` for the struct type; the test target already
  imported it. The arm's remaining half (target resolution + `setLinkHover`) is still surface-bound and
  therefore still uncovered — see Testing Strategy.
- **`GhosttySurfaceCursor.name(mouseVisible:overLink:shapeName:)`** extracted from
  `GhosttySurface.applyMouseCursor()` (new `GhosttySurfaceCursor.swift` + `GhosttySurfaceCursorTests.swift`,
  the `GhosttySurfaceGeometry` pattern). The three-way precedence *is* the user-visible symptom, and it was
  unassertable while it sat inside a private method ending in `gtk_widget_set_cursor_from_name`. The new
  suite also pins the payload → cursor-name sequence (hover → `pointer`, `len == 0` clear → back to `text`).
- **`readSelection` comment corrected**: it claimed "contract unchanged"; the predicate actually moved from
  "the NUL-terminated decode is empty" to `text_len == 0`. Behavior is right, the comment was not. An
  embedded-NUL assertion now pins the new predicate.
- **`linkHoverActive` doc corrected**: the nil conjunct was justified as covering a future `(nil, 0)`
  encoding, which `length > 0` already covers on its own. Kept for totality, described accurately.
- Plan corrections: the recorded test command pointed at the main checkout rather than this worktree; the
  Task 5 "move the plan" box was `[x]` for an action that has not happened; the "no e2e possible"
  justification was wrong (the harness has an env-gated capture pattern and drives the pointer already).
- `.claude/rules/libghostty.md` gained the payload-semantics gotcha and now scopes its `paths:` to the port's
  `Ghostty*.swift` too (it previously loaded only for macOS files, so the rule never fired on the file the
  bug lived in). `docs/plans/2026-07-15-linux-libghostty-callback-audit.json` gained a `scope` key so its
  clean verdict is not read as covering payload semantics, and `ARCHITECTURE.md`'s C-boundary contract gained
  the length-authoritative clause.

Declined, with reasons:

- **Delete `linkHoverActive` and inline `value.len > 0`** (simplification review). Rejected: the plan's
  Solution Overview weighs exactly this trade-off and chooses the helper because the inline form leaves
  nothing host-free to assert. The review's own strongest finding — that the call site was untested — is an
  argument for moving *more* into the helper, not for deleting it, and the payload overload above does that.
- **Drop the `bytes != nil` conjunct** — one term, keeps the predicate total; the doc is corrected instead.
- **Unify the three pointer+length decoders** (`utf8String` / `lossyUTF8String` / `readScreenText`'s inline
  decode) behind one total `String`-returning helper. Partially adopted in the phase-2 code-smell review:
  `readScreenText` now calls `lossyUTF8String(...) ?? ""`, contract-identical for a nil pointer and for
  `text_len == 0`, so no inline pointer+length decode remains in the module. Full unification behind a
  single helper stays rejected — the two length-0 contracts (`""` vs nil) are genuinely different and are
  best expressed at the call site by `?? ""`.
- **Drop the `length <= UInt(Int.max)` guard** — Task 3 requires it for one overflow discipline across both
  helpers, and without it `Int(length)` traps rather than returning nil.
- **Split the bundled decoder tests into `@Test(arguments:)` cases** — the surrounding suite bundles related
  assertions per behavior (see `lengthDelimitedURL`), so splitting would diverge from the file's style for a
  naming nicety.

### 2026-07-27 — review iteration 2 outcome

One confirmed finding, fixed here; it is **pre-existing** (the switch it lives in is untouched by this
branch's diff) but sits in the same function and the same cursor-resolution surface this plan fixes and
documents, so it is closed rather than deferred.

- **`GHOSTTY_MOUSE_SHAPE_DEFAULT` had no case in `GhosttySurface.setMouseShape`** — confirmed against
  `agterm-linux/vendor/ghostty/include/ghostty.h:691-725`, which declares 34 constants against the switch's
  33 arms, so `DEFAULT` (and only `DEFAULT`) fell into `default: name = "text"` and drew an I-beam.
  It is reachable twice over at this pin: `SurfaceMouse.keyToMouseShape` returns `.default` on every eligible
  key event while a TUI has mouse reporting on (so vim/htop/claude showed the I-beam where upstream's GTK
  apprt shows the arrow), and `printf '\e]22;default\a'` requests it outright.
  `default` is the correct GTK spelling — GTK's named cursors are the CSS set, where `default` *is* the arrow,
  and the macOS sibling `nsCursor(for:)` resolves the same constant to `.arrow`
  (`agterm/Ghostty/GhosttySurfaceView+Input.swift:483`).
- **Fix**: the whole shape → cursor-name map moved out of `setMouseShape` into
  `GhosttySurfaceCursor.shapeName(for: ghostty_action_mouse_shape_e) -> String`, joining the precedence rule
  in the seam this branch already created (the `GhosttySurfaceGeometry` pattern), with the missing
  `DEFAULT` → `"default"` arm added.
  Every other case is byte-identical, and the `default:` arm still resolves to `"text"` — it now catches only
  a shape a future `GHOSTTY_REV` adds, and `text` (the terminal's resting shape and the surface's initial
  `mouseShapeName`) remains the better guess there than the arrow.
  `GhosttySurface.swift` lost 35 lines in the move; `GhosttySurfaceCursor.swift` gained an `import CGtk` for
  the enum type, exactly as `GhosttyActionDecoder.swift` did for its payload overload.
- **Tests** (three new cases in `GhosttySurfaceCursorTests`): the full 34-constant table asserted in header
  order, plus `#expect(GHOSTTY_MOUSE_SHAPE_ZOOM_OUT.rawValue == UInt32(expected.count - 1))` so an added shape
  fails the table instead of silently reaching the fallback; the arrow case asserted through both halves of
  the seam (`shapeName(for:)` and then `name(mouseVisible:overLink:shapeName:)`); and the unknown-shape
  fallback (`ghostty_action_mouse_shape_e(rawValue: 9_999)` → `"text"`).
- `.claude/rules/libghostty.md` gained the exhaustiveness rule alongside the payload-semantics gotcha.
- Suites after the fix: port **127 tests / 17 suites, 1 issue** (the known Flatpak baseline only — the three
  new tests took it from 124), `agtermCore` **1733 tests / 74 suites, 1 issue** (the known GNUstep-`plutil`
  `CodexStatusHookTests` baseline). One full-suite run also flagged
  `CodexStatusHookTests.watcherReportsVisibleQuestionDialog()`, which did **not** reproduce on a repeat full
  run or on three filtered runs — a load-induced flake in that shell-hook watcher, and impossible to attribute
  to this branch, which changes zero files under `agtermCore/` or `agterm/Resources/`.

The rest of iteration 2 was clean: the quality pass found no critical or major defect, and the
implementation pass independently re-derived the fix against the pinned libghostty (`11b9a6ef`) — the
`.url = ""` clear, the paired `mouse_shape` restore at all three clear sites, the `over_link = false` reset in
`cursorPosCallback`, upstream's own `value.url.len > 0` test, and `text_len` excluding the NUL sentinel (so
`session.copy` is unaffected) — and confirmed the wiring end to end.

## Solution Overview

Keep the `mouseOverLink` precedence layer, but feed it the authoritative field. Add
`GhosttyActionDecoder.linkHoverActive(_:length:)` — a two-condition host-free predicate — and route the
`GHOSTTY_ACTION_MOUSE_OVER_LINK` arm through it, exactly as `GHOSTTY_ACTION_OPEN_URL` routes through
`utf8String(_:length:)`.
The arm calls the **payload overload** `linkHoverActive(_ value: ghostty_action_mouse_over_link_s)`
(added during the post-implementation review), which performs the field read and the `size_t` → `UInt`
conversion inside the tested seam, so the only line that carried the bug is asserted host-free rather than
by inspection.
The precedence rule the symptom is actually made of — `!mouseVisible ? "none" : (overLink ? "pointer" :
shapeName)` — is likewise extracted host-free as `GhosttySurfaceCursor.name(mouseVisible:overLink:shapeName:)`
(same pattern as `GhosttySurfaceGeometry`), so the set → clear → shape-restored transition is a test rather
than a hand-trace, and the parked "delete the layer" follow-up below cannot land silently.

Why this over the alternatives:

- **vs. an inline `.len > 0` one-liner**: same runtime behavior, but nothing host-free to assert against.
  The helper puts the decision in the one seam the repo already unit-tests and CI already runs.
- **vs. deleting the `mouseOverLink` layer entirely**: verified removable, and removing it would actually
  align the port with upstream. Ghostty's own GTK frontend at this pin computes the cursor from exactly two
  inputs — `mouse_hidden` and `mouse_shape` (`src/apprt/gtk/class/surface.zig:2360-2427`,
  `propMouseHidden`/`propMouseShape`) — with no over-link flag anywhere in the cursor path, and routes
  `mouse_over_link` to `setMouseHoverUrl` for the *link preview* instead (`src/apprt/gtk/App.zig:2146-2156`).
  The port's `mouse_over_link`→cursor wiring is a hand-port invention with no upstream counterpart.
  Not taken here because it is a larger behavioral change than the one-line semantic fix and is equally
  untestable host-free; parked in Post-Completion as the aligned-with-upstream cleanup.

## Technical Details

- New API: `GhosttyActionDecoder.linkHoverActive(_ bytes: UnsafePointer<CChar>?, length: UInt) -> Bool`,
  returning `length > 0 && bytes != nil`, plus the payload overload
  `linkHoverActive(_ value: ghostty_action_mouse_over_link_s) -> Bool` that the action arm calls (it forwards
  `value.url` and `UInt(bitPattern: value.len)`). The overload is why `GhosttyActionDecoder.swift` now carries
  an `import CGtk`; the underlying predicate stays import-free in shape and is still the thing being asserted.
  The nil check is defensive — libghostty's field is non-optional by Zig type. Stated precisely (the first
  draft over-claimed here): `length > 0` **alone** already handles a clear encoded as `(nil, 0)`, so the
  conjunct's only distinct input is the malformed `(nil, length > 0)`; it is kept because it costs one term
  and keeps the predicate total, not because a future encoding needs it. Either way a `GHOSTTY_REV` bump
  cannot silently reintroduce the latch.
  There **is** an upstream reference implementation for this predicate after all: ghostty's GTK frontend
  writes `if (value.url.len > 0) value.url else null` (`src/apprt/gtk/App.zig:2152-2154`). The port matches
  upstream semantics, not merely a reading of the Zig types — only the *destination* differs (upstream feeds
  a link-preview property; the port feeds a cursor flag).
- Call site becomes (as shipped — the review moved the field read into the overload, so the arm no longer
  spells the conversion):

  ```swift
  case GHOSTTY_ACTION_MOUSE_OVER_LINK:
      // libghostty signals "hover ended" with an EMPTY string, never a null pointer
      // (MouseOverLink.cval passes a non-optional [:0]const u8 .ptr), so len is the
      // only field that distinguishes set from clear.
      Self.wrapper(fromTarget: target)?.setLinkHover(
          GhosttyActionDecoder.linkHoverActive(action.action.mouse_over_link))
      return true
  ```

- Second API, added by the audit in Task 3:
  `GhosttyActionDecoder.lossyUTF8String(_ bytes: UnsafePointer<CChar>?, length: UInt) -> String?` — nil for a
  nil pointer, an over-`Int.max` length, or `length == 0`; otherwise a length-delimited
  `String(decoding:as: UTF8.self)`. Keep the `guard length <= UInt(Int.max)` that its sibling `utf8String`
  carries (`GhosttyActionDecoder.swift:3`) so the two helpers share one overflow discipline — without it
  `Int(length)` traps rather than returning nil.
  It differs from `utf8String` on two axes, both deliberate: invalid UTF-8 yields U+FFFD rather than nil
  (matching macOS `GhosttySurfaceView+IO.swift:64` and the sibling `readScreenText`), and `length == 0`
  yields nil rather than `""`. That second axis is what `readScreenText` bridges with a trailing `?? ""`
  (the phase-2 code-smell review) — its documented contract is that a blank screen reads as `""`, reserving
  nil for a failed read.
- Test construction of a clear event: `"".withCString { GhosttyActionDecoder.linkHoverActive($0, length: 0) }`
  yields a valid non-null pointer to a NUL terminator — the exact shape Zig's `"".ptr` produces.
- Processing flow after the fix: hover in → `mouse_shape(.pointer)` + `mouse_over_link(len > 0)` → hand;
  hover out → `mouse_shape(terminal.mouse_shape)` + `mouse_over_link(len == 0)` → flag drops, the shape
  update is no longer suppressed → I-beam.

## What Goes Where

- **Implementation Steps** (`[ ]` checkboxes): helper, action arm, unit tests, payload audit — all inside
  this repo.
- **Post-Completion** (no checkboxes): manual cursor verification in a running build, the optional report or
  PR to `melonamin/agterm-linux`, and the two optional follow-ups.

## Implementation Steps

### Task 1: Red — regression test for the hover-clear payload

**Files:**
- Modify: `agterm-linux/Sources/AgtermLinux/GhosttyActionDecoder.swift`
- Modify: `agterm-linux/Tests/AgtermLinuxTests/GhosttyLifetimeTests.swift`

- [x] add `linkHoverActive(_ bytes: UnsafePointer<CChar>?, length: UInt) -> Bool` to
      `GhosttyActionDecoder` with today's shipped semantics as the body (`bytes != nil`), so the red phase
      is a genuine behavioral failure rather than a build error
- [x] add `@Test("link hover follows the payload length, not the pointer")` to the existing
      `libghostty buffer lifetimes` suite
- [x] cover the clear event: `"".withCString { linkHoverActive($0, length: 0) }` → `#expect(... == false)`
- [x] cover the set event: a real URL pointer with `length: url.utf8.count` → `#expect(... == true)`
- [x] cover defensive nil input: `linkHoverActive(nil, length: 0)` and `linkHoverActive(nil, length: 4)`
      → both `false`
- [x] run `swift test` — the clear-event case MUST fail, confirming the test reproduces the latch
      (red; goes green in Task 2)

⚠️ Pre-existing, unrelated failure on this box (present on a clean tree before Task 1, verified by
stashing the Task 1 edits): `Linux integration service` ▸ "Flatpak process environments do not offer a host
launcher" (`Tests/LinuxIntegrationsTests/IntegrationServiceTests.swift:765`) reports `.installed` where it
expects `.unavailable`. Out of scope for this plan; treat the suite baseline as "that one failure" when
judging Tasks 2-4 green.

### Task 2: Green — length-authoritative helper and rewired action arm

**Files:**
- Modify: `agterm-linux/Sources/AgtermLinux/GhosttyActionDecoder.swift`
- Modify: `agterm-linux/Sources/AgtermLinux/GhosttyApp.swift`

- [x] replace the stub body with `length > 0 && bytes != nil`
- [x] document the contract on the helper: libghostty clears hover with `.url = ""`, and
      `MouseOverLink.cval` passes a non-optional `[:0]const u8` `.ptr`, so the pointer is never null
- [x] rewrite the `GHOSTTY_ACTION_MOUSE_OVER_LINK` arm (`GhosttyApp.swift:193-196`) to bind
      `let value = action.action.mouse_over_link` and call the helper
- [x] delete the stale `// a non-null url means the pointer is over a hyperlink` comment on `:194` and
      replace it with the len-is-authoritative note
- [x] run `swift test` — the Task 1 test now passes, whole suite green before Task 3

⚠️ Deviation from the Technical Details snippet: `ghostty_action_mouse_over_link_s.len` is `size_t`, which
the Swift importer maps to `Int` — unlike `ghostty_action_open_url_s.len` (`uintptr_t` → `UInt`), whose arm
passes `value.len` straight through. `length: value.len` therefore does not compile. The call site converts
with `UInt(bitPattern: value.len)` (exact, total, no trap) and the helper keeps the `UInt` signature the plan
and the tests specify, so it stays uniform with its sibling `utf8String(_:length:)`. The importer-type split
is noted in the call-site comment.

### Task 3: Audit every pointer+length payload for the same misread

The enumeration criterion is **"pointer + length pair"**, not "`size_t len`" — half the pairs in the header
use `uintptr_t`, including `open_url` itself. All eight, with their vendored-header ranges:

| Struct | Lines | Fields | Length type |
|---|---|---|---|
| `ghostty_info_s` | 393-397 | `version` / `version_len` | `uintptr_t` |
| `ghostty_string_s` | 403-407 | `ptr` / `len` (+ a `sentinel` bool) | `uintptr_t` |
| `ghostty_text_s` | 409-416 | `text` / `text_len` | `uintptr_t` |
| `ghostty_config_color_list_s` | 514-518 | `colors` / `len` | `size_t` |
| `ghostty_config_command_list_s` | 521-524 | `commands` / `len` | `size_t` |
| `ghostty_action_mouse_over_link_s` | 735-738 | `url` / `len` | `size_t` |
| `ghostty_action_key_table_u.activate` | 780-785 | `name` / `len` | `size_t` |
| `ghostty_action_open_url_s` | 825-830 | `url` / `len` | `uintptr_t` |

`ghostty_string_s` is the one to read closely: it carries an explicit `sentinel` bool, i.e. libghostty
itself treats "is this NUL-terminated?" as payload-dependent — precisely the assumption this bug class
violates.

Deliberately **excluded**: `ghostty_surface_config_s.env_vars` / `env_var_count` (h:483-484) is a ninth
pointer+count pair by the same criterion, but it runs host→libghostty (write direction), is owned by
`GhosttySurfaceConfigurationStorage`, and is already asserted in `GhosttyLifetimeTests.swift:41`. Note also
that the re-derivation grep below surfaces ~7 function prototypes (h:1072, 1086, 1089, 1137-1139, 1163)
that are not payload structs — skip them.

**Known finding to close (already located during review, do not re-derive):**
`GhosttySurface.readSelection()` (`:348-356`) discards `out.text_len` and uses `String(cString: ptr)`, while
its sibling `readScreenText` twelve lines below (`:365-369`) and the macOS reader
(`agterm/Ghostty/GhosttySurfaceView+IO.swift:63-64`) both guard on `text_len > 0` and decode by length.
Characterize it accurately: this is **not** a live memory-safety bug — `Surface.Text.text` is `[:0]const u8`
(`src/Surface.zig:1867-1869`), so the buffer *is* NUL-terminated today. It is a latent same-class fragility
(truncates at an embedded NUL, ignores the authoritative length, relies on an undocumented guarantee a
`GHOSTTY_REV` bump could change) and an unnecessary downstream divergence from macOS.

**Files:**
- Review: `agterm-linux/vendor/ghostty/include/ghostty.h`
- Review: `agterm-linux/Sources/AgtermLinux/GhosttyApp.swift`
- Modify: `agterm-linux/Sources/AgtermLinux/GhosttySurface.swift` (the `readSelection` fix)
- Modify: `agterm-linux/Sources/AgtermLinux/GhosttyActionDecoder.swift` (the `lossyUTF8String` helper)
- Modify: `agterm-linux/Tests/AgtermLinuxTests/GhosttyLifetimeTests.swift`
- Modify (only if a further misread is found): the offending call site + its test

- [x] re-derive the table above from the current vendored header (`grep -n "uintptr_t\|size_t len"`) and
      correct it if the pin has moved
- [x] write tests for `lossyUTF8String` first (same red-then-green discipline as Tasks 1-2): honors
      `length` over trailing bytes, `length == 0` → nil, nil pointer → nil, over-`Int.max` length → nil,
      invalid UTF-8 → U+FFFD replacement rather than nil
- [x] add `GhosttyActionDecoder.lossyUTF8String(_ bytes: UnsafePointer<CChar>?, length: UInt) -> String?` —
      `nil` when `bytes` is nil, `length > Int.max`, or `length == 0`; otherwise
      `String(decoding: UnsafeRawBufferPointer(start: bytes, count: Int(length)), as: UTF8.self)`.
      Do **not** reuse `utf8String` for this: it returns `nil` on invalid UTF-8, which would make
      `session.copy` report "no selection" for an undecodable selection, whereas macOS
      (`GhosttySurfaceView+IO.swift:64`) and the sibling `readScreenText` (`:368-369`) substitute U+FFFD.
      Do not retrofit `readScreenText` onto the new helper in this task — its length-0 contract is `""`,
      not nil. *(Superseded by the phase-2 code-smell review: it now routes through the helper with a
      trailing `?? ""`, which reproduces that contract exactly.)*
- [x] route `readSelection()` (`:348-356`) through the new helper — contract now **nil iff
      `text_len == 0`**, closing the divergence from `readScreenText` and macOS. Note the predicate did move
      (it was "the NUL-terminated decode is empty"); the two differ only for a selection whose bytes start
      with or contain a NUL, where the old code truncated. `session.copy`'s documented "no selection" for an
      empty selection is unaffected, and the new predicate is pinned by the embedded-NUL assertion in
      `GhosttyActionDecoderTests.lossyLengthDelimitedText()`
- [x] confirm `GHOSTTY_ACTION_OPEN_URL` still decodes through `utf8String(_:length:)` (`GhosttyApp.swift:189`)
      and that `GhosttyApp.exerciseURLAction` still exercises the non-NUL-terminated case
- [x] confirm `GHOSTTY_ACTION_KEY_TABLE` has no handler arm in the port (nothing to misread today) and note
      the length-is-authoritative contract for whoever adds one
- [x] confirm `ghostty_info_s`, `ghostty_string_s`, `ghostty_config_color_list_s`, and
      `ghostty_config_command_list_s` have **no consumers** in the port — expected result is zero hits from
      `grep -rn "ghostty_info\|ghostty_string_s\|color_list\|command_list" agterm-linux/Sources` (the only
      config read is the scalar `ghostty_config_color_s` in `GhosttyConfigTheme.swift:27-34`) — and record
      that result
- [x] record the audit outcome (findings, or "no further misreads") under Progress Tracking with the date
- [x] run `swift test` — must pass before Task 4

### Task 4: Verify acceptance criteria

- [x] verify the Overview symptom is addressed: `mouseOverLink` is `false` for every `len == 0` event, so
      `applyMouseCursor()` falls through to `mouseShapeName` — **traced end to end, confirmed.** The flag now
      has a clearing path: `GhosttyApp.swift:199-201` binds `let value = action.action.mouse_over_link` and
      calls `setLinkHover(linkHoverActive(value.url, length: UInt(bitPattern: value.len)))`, whose helper
      body is `length > 0 && bytes != nil` (`GhosttyActionDecoder.swift:29-31`), so a `len == 0` clear now
      yields `false` where the old `url != nil` yielded `true`. `setLinkHover`
      (`GhosttySurface.swift:458-461`) assigns `mouseOverLink = overLink` unconditionally — no
      `guard overLink` short-circuit — then calls `applyMouseCursor()`, which computes
      `!mouseVisible ? "none" : (mouseOverLink ? "pointer" : mouseShapeName)` (`:508`) and falls through to
      `mouseShapeName`. `GhosttyApp.swift:200` is the **only** writer of the flag in the tree
      (`grep -rn "setLinkHover\|mouseOverLink"` → declaration `:47`, the assignment `:459`, the read `:508`,
      the one call site), so no other path can re-latch it
- [x] grep by *field*, not by comparison shape — the idiomatic `guard let ptr = <field>` spelling is what
      this codebase actually uses (`GhosttySurface.swift:353` is the in-scope example), so a `!= nil`-only
      grep returns clean while the bug class persists:
      `grep -rn "\.text\b\|\.url\b\|\.name\b\|\.ptr\b\|\.colors\b\|\.commands\b" agterm-linux/Sources/AgtermLinux/Ghostty*.swift`
      — eyeball every hit for a matching length guard.
      Two notes so the result is interpretable: `GhosttyApp.swift:159` (`set_title.title`) and `:163`
      (`pwd.pwd`) use the same `guard let ptr =` spelling but are **length-free by construction** (bare
      `const char*`, no companion field) — out of audit scope, and correctly absent from the pattern.
      The `Ghostty*.swift` glob is sufficient: the only other files calling `ghostty_*` are
      `GtkInterop.swift` (`ghostty_input_mods_e(rawValue:)`) and `LinuxSettingsController.swift`
      (`ghostty_config_free`), neither of which reads a pointer+length payload
      — **ran; 12 hits, every one eyeballed and cleared.** Per-hit verdicts recorded under
      Progress Tracking (2026-07-26 — Task 4 field-grep verdicts)
- [x] run the port suite (see Testing Strategy for the `env` + absolute-path form this shell needs):
      `… swift test --package-path <worktree>/agterm-linux`
      — **120 tests in 16 suites, 1 issue: the known baseline failure only** (`Linux integration service` ▸
      "Flatpak process environments do not offer a host launcher"). `libghostty buffer lifetimes` — the suite
      carrying the two new decoder tests — passed
- [x] run the shared host-free suite (must stay green, untouched):
      `… swift test --package-path <worktree>/agtermCore`
      — **1733 tests in 74 suites, 1 issue, and it is NOT ours**: `CodexStatusHookTests` ▸
      `stopReportsBlockedWhenAssistantMessageEndsInQuestionMark()`. This branch changes **zero** files under
      `agtermCore/` or `agterm/Resources/` (`git diff --name-only <merge-base>..HEAD -- agtermCore
      agterm/Resources` → 0), so the package under test is byte-identical to the merge base. Root-caused as a
      box-specific environment failure — see the ⚠️ note below
- [x] confirm a release build still compiles:
      `… swift build -c release --package-path <worktree>/agterm-linux`
      — **`Build complete! (149.89s)`**, `AgtermLinux` and `agtermctl-linux` both linked. The `-pthread` /
      `-msse` "prohibited flag(s)" lines are pre-existing pkg-config warnings from the GTK modulemap, not
      errors
- [x] note that `swiftlint` is unavailable locally — lint is verified by CI's `lint` job on push
      — **confirmed unavailable and deliberately not installed**; no lint run locally. The change is three
      small edits well inside the configured `line_length` (200) and file-size budgets

### Task 5: [Final] Update documentation

- [x] confirm no user-facing surface changed — no control command, keybinding, flag, or mode — so
      `README.md`, `site/`, and the bundled agent skill need no update (record this explicitly). Task 3
      touches `readSelection`, which backs the `session.copy` control command, but only in the
      embedded-NUL / non-terminated edge case; its "empty selection reads as nil" contract is preserved, so
      the documented behavior is unchanged
      — **confirmed, no doc update needed.** The branch diff against the merge base (`3d8f1cf`, which
      `git merge-base 3d8f1cf HEAD` confirms is the fork point) touches only
      `agterm-linux/Sources/AgtermLinux/{GhosttyActionDecoder,GhosttyApp,GhosttySurface,GhosttySurfaceCursor}.swift`,
      the `agterm-linux/Tests/AgtermLinuxTests/Ghostty{Lifetime,ActionDecoder,SurfaceCursor}Tests.swift`
      suites, `.claude/rules/libghostty.md`, `ARCHITECTURE.md`, and this plan. Nothing under
      `README.md`, `site/`, `agterm/Resources/agent-skill/`, or `agterm-linux/docs/`
      (`git diff --name-only 3d8f1cf...HEAD -- README.md site/ agterm/Resources/agent-skill/ docs/` returns
      only the plan file), and no `ControlProtocol`/`agtermctl`/keymap file is in the diff, so there is no
      new command, argument, `tree` read-back field, keybinding, flag, or mode to document.
      Both altered behaviors were then checked against the doc surfaces for an existing description that
      would now be stale:
      (1) *link-hover cursor shape* — `grep -rniE "hand cursor|pointer cursor|mouse shape|mouse_shape|hover.*link|link.*hover|OSC 8|hyperlink|osc 22"` plus a second pass for
      `"i-beam|ibeam|cursor shape|mouse cursor"` over `README.md`, `site/`, `agterm/Resources/agent-skill/`,
      and `agterm-linux/docs/` returns **no description of the cursor shape anywhere**. The only two hits are
      `README.md:710` and its `site/docs.html:1732` mirror, which document the macOS ⌘-click `file://`
      reveal-vs-open security boundary — link *activation*, not the hover cursor. The latched hand cursor was
      an undocumented Linux-only defect, so fixing it makes no doc stale.
      (2) *`session.copy` decode* — the documented contract is `README.md:555` ("With no selection it exits
      non-zero with `no selection`"), mirrored at `agterm/Resources/agent-skill/reference.md:280,300`,
      `examples.md:386-397`, and `site/commands.html:827-890`. `readSelection` still returns nil exactly when
      `text_len == 0` (`lossyUTF8String` is nil-on-length-0 by design; `readScreenText` bridges that with a
      trailing `?? ""`), so `no selection` still fires on an empty selection. The change
      is confined to a selection whose bytes contain an embedded NUL or are not NUL-terminated, which no
      surface documents. `agterm-linux/docs/x11-wayland.md:14` mentions primary selection only as a
      GTK clipboard-integration matrix row, untouched by this decode path
- [x] confirm `CHANGELOG.md` is untouched (release-only per AGENTS.md)
      — **confirmed from the diff, not from memory**: `git diff --name-only 3d8f1cf...HEAD -- CHANGELOG.md`
      returns **0 lines**, and the repo has exactly one changelog (`git ls-files | grep -i changelog` →
      `CHANGELOG.md`). Matches `AGENTS.md:38` ("`CHANGELOG.md` is release-only; do not touch it for ordinary
      feature work") and `:135`
- [ ] move this plan to `docs/plans/completed/` — **not done here, deliberately**: the exec orchestrator
      performs the move after the review/finalize phases, which still need to read the plan in place. Left
      unchecked because the action has not happened yet

## Post-Completion

*Items requiring manual intervention or external systems — no checkboxes, informational only*

**Manual verification:**

- Build and run the port, print a link with
  `printf '\e]8;;https://example.com\e\\click me\e]8;;\e\\\n'`, hover it, then move to blank cells — the
  I-beam must return. Repeat over a regex-detected bare URL.
- Confirm an application's OSC 22 shape request is honored again after a link hover. Use **two** shapes, not
  one: `printf '\e]22;crosshair\a'` (a shape the latch would have suppressed) and `printf '\e]22;default\a'`,
  which must draw the **arrow**. The `default` case is the one that also covers the review-iteration-2 fix —
  `\e]22;text` alone passes either way, since `text` was what the missing-`DEFAULT` fallback resolved to.
- Confirm a mouse-reporting TUI shows the arrow, not the I-beam: run `htop` (or `vim` with `set mouse=a`) and
  move the pointer over it — libghostty asks for `GHOSTTY_MOUSE_SHAPE_DEFAULT` on every eligible key event
  while mouse reporting is on.
- Debugging hint if the hand persists: suspect a stale `mouseShapeName`, not the predicate. The I-beam
  returns only because each clear site *also* emits the paired `mouse_shape(terminal.mouse_shape)` restore
  immediately before the `mouse_over_link` clear — verified at all three sites in the pinned source
  (`Surface.zig` 1661-1665, 2743-2747, 4530-4534), but the first thing to re-check against a newer
  `GHOSTTY_REV`.
- Repeat with `link-previews = false` in the ghostty config, where the port receives clear events with no
  preceding set.
- Confirm the cursor still reverts after the pointer leaves the widget entirely (the `mouseLeft()` →
  `(-1, -1)` path that previously re-armed the latch).

**External system updates:**

- The defect exists identically in `melonamin/agterm-linux` (no `upstream` remote is wired in this
  checkout); consider filing the report or a PR there.
- Optional follow-up, deliberately out of scope here: delete the `mouseOverLink` cursor layer outright.
  This is not speculative — upstream's GTK frontend drives the cursor from `mouse_hidden` + `mouse_shape`
  only (`src/apprt/gtk/class/surface.zig:2360-2427`) and spends `mouse_over_link` on a link-preview property
  (`src/apprt/gtk/App.zig:2146-2156`), so removal converges the port onto upstream and would let the port
  adopt the link preview later using the URL the payload already carries and the port currently discards.
  The one behavioral difference to weigh first: with the layer, a link hover outranks an application's
  OSC 22 shape request; without it, the last `mouse_shape` action wins. That difference is real, not
  hypothetical — upstream's OSC 22 path (`Surface.zig:1034-1041`, `set_mouse_shape`) emits `mouse_shape`
  with no `over_link` guard. Upstream *does* factor `over_link` into the shape it computes on key events
  (`SurfaceMouse.keyToMouseShape()`, `:2758-2770`), so the divergence is narrow: an OSC 22 arriving while
  the pointer sits on a link.
- Follow-up that would retire the manual checklist above: mirror the harness's existing URL-capture pattern
  for the cursor — under `#if DEBUG`, have `applyMouseCursor()` append the resolved name to
  `AGTERM_ATSPI_CURSOR_CAPTURE` (the same shape as `AGTERM_ATSPI_URL_CAPTURE` in `GhosttyApp.swift`,
  consumed in `agterm-linux/tests/atspi_smoke.py`), then add a harness case that prints an OSC 8 link,
  `xdotool mousemove --sync`es onto it and off it, and asserts `pointer` then `text`. Not done in this change
  because the harness needs xvfb/openbox and could not be exercised on this box, so an unrunnable new case
  would have shipped unverified — but the "no e2e is possible" framing in the first draft was wrong, and this
  is the case worth having on the next `GHOSTTY_REV` bump.
- Optional follow-up for the residual coverage gap named in Testing Strategy: a `#if DEBUG` action hook that
  resolves the *focused* surface instead of the action target would make surface-bound action arms
  (`MOUSE_OVER_LINK` among them) drivable from the AT-SPI harness, closing the call-site wiring gap that
  `exerciseURLAction` cannot reach.
