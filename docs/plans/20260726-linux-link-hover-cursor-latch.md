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

- **unit tests**: `agterm-linux/Tests/AgtermLinuxTests/GhosttyLifetimeTests.swift`, swift-testing
  (`@Test` / `#expect`), extending the existing `libghostty buffer lifetimes` suite. The new case is a
  payload-*semantics* test rather than a lifetime test; it goes there because that suite owns the
  `GhosttyActionDecoder` seam, not because the suite name fits. Task 3's `readSelection` change is covered
  the same way — by routing its decode through a second tested helper (`lossyUTF8String`) rather than
  inlining it, since `readSelection` itself needs a live GTK surface and cannot be unit-tested directly.
- **red phase is behavioral, not a compile error**: Task 1 adds the helper with today's shipped semantics
  (`bytes != nil`) so the new test compiles and *fails on the length-0 case*. This proves the predicate
  discriminates the two payload semantics — it does **not** prove the call site is wired correctly.
- **known residual gap**: no automated test covers `GhosttyApp.swift:195` itself, so a miswired call
  (swapped arguments, a stray negation) would pass the suite. The repo's one action-boundary test hook,
  `GhosttyApp.exerciseURLAction` (`#if DEBUG`, driven by `AGTERM_ATSPI_OPEN_URL` at `App.swift:91-93`),
  does not extend cheaply: it calls `handleAction` with a zero `ghostty_target_s()`, which the `OPEN_URL`
  arm tolerates because it never touches the target, while `Self.wrapper(fromTarget:)` requires
  `target.tag == GHOSTTY_TARGET_SURFACE` plus a non-nil surface and userdata, so the `MOUSE_OVER_LINK` arm
  would resolve `nil` and silently no-op. The wiring is therefore
  covered only by the manual verification under Post-Completion.
- **no e2e tests**: the Linux UI harness (`scripts/test-linux-ui.sh`) drives AT-SPI + xdotool under
  xvfb/openbox and cannot observe `gtk_widget_set_cursor_from_name`; cursor behavior is covered by the unit
  test plus the manual verification listed under Post-Completion.
- **test command** (this box — the shell is fish, where `VAR=… cmd` is a syntax error and `~` after `=` is
  not expanded, so use `env` and absolute paths; `--package-path` also avoids the relative-`cd` drift
  `CLAUDE.md` warns about):

  ```
  env LD_LIBRARY_PATH=$HOME/.local/share/swift-linux-compat \
    /home/n/.local/share/mise/installs/swift/6.3.2/usr/bin/swift test \
    --package-path /home/n/p/github/agterm-linux/agterm-linux
  ```

## Progress Tracking

- mark completed items with `[x]` immediately when done
- add newly discovered tasks with ➕ prefix
- document issues/blockers with ⚠️ prefix
- update plan if implementation deviates from original scope
- keep plan in sync with actual work done

## Solution Overview

Keep the `mouseOverLink` precedence layer, but feed it the authoritative field. Add
`GhosttyActionDecoder.linkHoverActive(_:length:)` — a two-condition host-free predicate — and route the
`GHOSTTY_ACTION_MOUSE_OVER_LINK` arm through it, exactly as `GHOSTTY_ACTION_OPEN_URL` routes through
`utf8String(_:length:)`.

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
  returning `length > 0 && bytes != nil`. The nil check is defensive — libghostty's field is non-optional by
  Zig type — and it earns its keep twice: it keeps the helper total for tests, and it makes the fix
  **independent of the upstream-source claim above**. `length > 0 && bytes != nil` is correct whether a
  clear arrives as `("", 0)` (today) or as `(nil, 0)` (if a future `GHOSTTY_REV` changes the encoding), so a
  libghostty bump cannot silently reintroduce the latch.
  There **is** an upstream reference implementation for this predicate after all: ghostty's GTK frontend
  writes `if (value.url.len > 0) value.url else null` (`src/apprt/gtk/App.zig:2152-2154`). The port matches
  upstream semantics, not merely a reading of the Zig types — only the *destination* differs (upstream feeds
  a link-preview property; the port feeds a cursor flag).
- Call site becomes:

  ```swift
  case GHOSTTY_ACTION_MOUSE_OVER_LINK:
      // libghostty signals "hover ended" with an EMPTY string, never a null pointer
      // (MouseOverLink.cval passes a non-optional [:0]const u8 .ptr), so len is the
      // only field that distinguishes set from clear.
      let value = action.action.mouse_over_link
      Self.wrapper(fromTarget: target)?.setLinkHover(
          GhosttyActionDecoder.linkHoverActive(value.url, length: value.len))
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
  yields nil rather than `""`. That second axis is why `readScreenText` keeps its inline decode and is *not*
  routed through this helper — its documented contract is that a blank screen reads as `""`, reserving nil
  for a failed read.
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

- [ ] add `linkHoverActive(_ bytes: UnsafePointer<CChar>?, length: UInt) -> Bool` to
      `GhosttyActionDecoder` with today's shipped semantics as the body (`bytes != nil`), so the red phase
      is a genuine behavioral failure rather than a build error
- [ ] add `@Test("link hover follows the payload length, not the pointer")` to the existing
      `libghostty buffer lifetimes` suite
- [ ] cover the clear event: `"".withCString { linkHoverActive($0, length: 0) }` → `#expect(... == false)`
- [ ] cover the set event: a real URL pointer with `length: url.utf8.count` → `#expect(... == true)`
- [ ] cover defensive nil input: `linkHoverActive(nil, length: 0)` and `linkHoverActive(nil, length: 4)`
      → both `false`
- [ ] run `swift test` — the clear-event case MUST fail, confirming the test reproduces the latch
      (red; goes green in Task 2)

### Task 2: Green — length-authoritative helper and rewired action arm

**Files:**
- Modify: `agterm-linux/Sources/AgtermLinux/GhosttyActionDecoder.swift`
- Modify: `agterm-linux/Sources/AgtermLinux/GhosttyApp.swift`

- [ ] replace the stub body with `length > 0 && bytes != nil`
- [ ] document the contract on the helper: libghostty clears hover with `.url = ""`, and
      `MouseOverLink.cval` passes a non-optional `[:0]const u8` `.ptr`, so the pointer is never null
- [ ] rewrite the `GHOSTTY_ACTION_MOUSE_OVER_LINK` arm (`GhosttyApp.swift:193-196`) to bind
      `let value = action.action.mouse_over_link` and call the helper
- [ ] delete the stale `// a non-null url means the pointer is over a hyperlink` comment on `:194` and
      replace it with the len-is-authoritative note
- [ ] run `swift test` — the Task 1 test now passes, whole suite green before Task 3

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

- [ ] re-derive the table above from the current vendored header (`grep -n "uintptr_t\|size_t len"`) and
      correct it if the pin has moved
- [ ] write tests for `lossyUTF8String` first (same red-then-green discipline as Tasks 1-2): honors
      `length` over trailing bytes, `length == 0` → nil, nil pointer → nil, over-`Int.max` length → nil,
      invalid UTF-8 → U+FFFD replacement rather than nil
- [ ] add `GhosttyActionDecoder.lossyUTF8String(_ bytes: UnsafePointer<CChar>?, length: UInt) -> String?` —
      `nil` when `bytes` is nil, `length > Int.max`, or `length == 0`; otherwise
      `String(decoding: UnsafeRawBufferPointer(start: bytes, count: Int(length)), as: UTF8.self)`.
      Do **not** reuse `utf8String` for this: it returns `nil` on invalid UTF-8, which would make
      `session.copy` report "no selection" for an undecodable selection, whereas macOS
      (`GhosttySurfaceView+IO.swift:64`) and the sibling `readScreenText` (`:368-369`) substitute U+FFFD.
      And do **not** retrofit `readScreenText` onto the new helper — its length-0 contract is `""`, not nil
- [ ] route `readSelection()` (`:348-356`) through the new helper, preserving today's contract
      (nil iff `text_len == 0`) and closing the divergence from `readScreenText` and macOS
- [ ] confirm `GHOSTTY_ACTION_OPEN_URL` still decodes through `utf8String(_:length:)` (`GhosttyApp.swift:189`)
      and that `GhosttyApp.exerciseURLAction` still exercises the non-NUL-terminated case
- [ ] confirm `GHOSTTY_ACTION_KEY_TABLE` has no handler arm in the port (nothing to misread today) and note
      the length-is-authoritative contract for whoever adds one
- [ ] confirm `ghostty_info_s`, `ghostty_string_s`, `ghostty_config_color_list_s`, and
      `ghostty_config_command_list_s` have **no consumers** in the port — expected result is zero hits from
      `grep -rn "ghostty_info\|ghostty_string_s\|color_list\|command_list" agterm-linux/Sources` (the only
      config read is the scalar `ghostty_config_color_s` in `GhosttyConfigTheme.swift:27-34`) — and record
      that result
- [ ] record the audit outcome (findings, or "no further misreads") under Progress Tracking with the date
- [ ] run `swift test` — must pass before Task 4

### Task 4: Verify acceptance criteria

- [ ] verify the Overview symptom is addressed: `mouseOverLink` is `false` for every `len == 0` event, so
      `applyMouseCursor()` falls through to `mouseShapeName`
- [ ] grep by *field*, not by comparison shape — the idiomatic `guard let ptr = <field>` spelling is what
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
- [ ] run the port suite (see Testing Strategy for the `env` + absolute-path form this shell needs):
      `… swift test --package-path /home/n/p/github/agterm-linux/agterm-linux`
- [ ] run the shared host-free suite (must stay green, untouched):
      `… swift test --package-path /home/n/p/github/agterm-linux/agtermCore`
- [ ] confirm a release build still compiles:
      `… swift build -c release --package-path /home/n/p/github/agterm-linux/agterm-linux`
- [ ] note that `swiftlint` is unavailable locally — lint is verified by CI's `lint` job on push

### Task 5: [Final] Update documentation

- [ ] confirm no user-facing surface changed — no control command, keybinding, flag, or mode — so
      `README.md`, `site/`, and the bundled agent skill need no update (record this explicitly). Task 3
      touches `readSelection`, which backs the `session.copy` control command, but only in the
      embedded-NUL / non-terminated edge case; its "empty selection reads as nil" contract is preserved, so
      the documented behavior is unchanged
- [ ] confirm `CHANGELOG.md` is untouched (release-only per AGENTS.md)
- [ ] move this plan to `docs/plans/completed/`

## Post-Completion

*Items requiring manual intervention or external systems — no checkboxes, informational only*

**Manual verification:**

- Build and run the port, print a link with
  `printf '\e]8;;https://example.com\e\\click me\e]8;;\e\\\n'`, hover it, then move to blank cells — the
  I-beam must return. Repeat over a regex-detected bare URL.
- Confirm an application's OSC 22 shape request (`printf '\e]22;text\a'`) is honored again after a link
  hover.
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
- Optional follow-up for the residual coverage gap named in Testing Strategy: a `#if DEBUG` action hook that
  resolves the *focused* surface instead of the action target would make surface-bound action arms
  (`MOUSE_OVER_LINK` among them) drivable from the AT-SPI harness, closing the call-site wiring gap that
  `exerciseURLAction` cannot reach.
