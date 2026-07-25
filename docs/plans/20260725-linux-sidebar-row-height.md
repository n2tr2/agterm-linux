# Fix: sidebar font size does not change session row height (Linux port)

## Overview

- Changing Settings → Appearance → Sidebar font size rescales the row *text* in the
  workspace/sessions sidebar, but the row height never changes: libadwaita pins
  `navigation-sidebar` rows at `min-height: 36px`, so smaller text does not densify rows
  and larger text gets no more room. On macOS the same setting scales row height via
  `NSOutlineView.rowHeight = AppSettings.sidebarRowHeight(fontSize:)`.
- Fix: extend the CSS emitted by `applySidebarFontSize()` to override the two Adwaita
  `min-height` floors, deriving the height from the existing core helper
  `AppSettings.sidebarRowHeight(fontSize:)` — floors of 13 pt → 28 px, 9 pt → 24 px,
  20 pt → 35 px. The name label keeps its 4 px vertical margins, so at 13 pt the
  rendered height is ~30 px (content sits just above the floor); at 9 pt the 24 px
  floor governs.
- The CSS derivation moves into a pure `LinuxSidebarPolicy` function so it gets real
  unit coverage, matching the port's "logic host-free, side effects thin" convention.
- Keep-in-sync verdict (recorded so it isn't relitigated): no control command is owed —
  `sidebarFontSize` has no `Command` case in the Linux port or upstream — and no
  Settings ▸ Interface toggle (this fixes an existing setting, adds no chrome).

## Context (from discovery)

- Files involved:
  - `agterm-linux/Sources/AgtermLinux/AppControllerSidebar.swift` —
    `applySidebarFontSize()` (lines 42–55) emits only
    `.agterm-sidebar label { font-size: Xpt; }` into a reloadable CSS provider
    (priority 651); `makeRow` (lines 185+) gives the name label 4 px top/bottom margins;
    session rows are `GtkListBoxRow`s in a `navigation-sidebar` list box (line 163).
  - `agterm-linux/Sources/AgtermLinux/LinuxSidebarPolicy.swift` — existing host-testable
    policy enum (imports `agtermCore` only), new function's home.
  - `agterm-linux/Tests/AgtermLinuxTests/LinuxPolicyTests.swift` — Swift Testing suite
    "Linux-owned policy and adapters", already tests `LinuxSidebarPolicy`.
  - `agtermCore/Sources/agtermCore/AppSettings.swift` — `sidebarRowHeight(fontSize:)`
    (= `clampSidebarFontSize(size).rounded() + 15`, line 436), `clampSidebarFontSize`
    (9…20), `defaultSidebarFontSize` (13). Already tested in core; NOT modified here.
- libadwaita 1.9.2 theme rules being overridden:
  `.navigation-sidebar > row { min-height: 36px; … }` and
  `.navigation-sidebar > row > box { padding: 3px 14px; min-height: 36px; … }`.
- Decisions made in brainstorm (do not relitigate):
  - macOS-parity density: default rows get denser than today's 36 px (28 px floor at
    13 pt; ~30 px rendered with the kept margins) — intended, not a regression.
  - Workspace header rows stay content-driven (plain `GtkBox`es, not theme-pinned;
    their buttons keep Adwaita click-target size). CSS scopes to
    `.agterm-sidebar .navigation-sidebar > row` only.
  - REVERSED (2026-07-26, user decision): the label's 4 px top/bottom margins STAY —
    `makeRow` is not touched. Consequence, accepted: at 13 pt the label line (~22 px)
    plus 8 px margins ≈ 30 px slightly exceeds the 28 px floor, so default rows render
    ~30 px rather than macOS's exact 28 (still far denser than the old 36 px pin). At
    9 pt content sits below the floor, so 24 px governs exactly.

## Development Approach

- **testing approach**: Regular (code first, then tests, same task)
- complete each task fully before moving to the next
- make small, focused changes
- **CRITICAL: every task MUST include new/updated tests** for code changes in that task
- **CRITICAL: all tests must pass before starting next task** — no exceptions
- **CRITICAL: update this plan file when scope changes during implementation**
- maintain backward compatibility (CSS provider identity/priority unchanged; settings
  schema untouched)

## Testing Strategy

- **unit tests**: new `@Test` in `LinuxPolicyTests.swift` covering the emitted CSS
  (success + clamp/nil edge cases). Assert via string-contains on the three rules, not
  full-literal equality, so formatting tweaks don't break tests.
- **e2e/UI**: a NEW AT-SPI scenario is added — row pixel heights ARE observable
  (`atspi_smoke.py` already reads `component.get_extents(...)` and finds session rows
  via `collect(app, role="list item")`, and several scenarios already seed
  `settings.json` in `AGTERM_STATE_DIR` before `launch(env)`). The scenario launches
  with default settings and asserts a session row's extent height is in `28 <= h < 36`
  (~30 expected with the kept label margins; the old 36 px pin defeated), then
  relaunches with seeded `{"sidebarFontSize": 9}` and asserts `24 <= h <= 26` plus
  strictly less than the default height — proving row height follows the setting
  end-to-end without coupling to font metrics. Visual polish (selection rounding, glyph fit) stays
  a manual pass (Post-Completion).
- The existing AT-SPI suite is also affected: CI's `build-linux` job runs
  `scripts/test-linux-ui.sh` on every push to `linux-port`, and existing scenarios
  click sidebar rows by pointer at AT-SPI extents — this change alters exactly that
  geometry, and there is no PR gate on this branch. Run the suite locally before
  pushing (deps: `dbus-run-session`, `openbox`, `xdotool`, `xvfb-run`, python
  `gi`/`Atspi`); if a dep is missing, note it here and explicitly accept CI as the
  gate.

## Progress Tracking

- mark completed items with `[x]` immediately when done
- add newly discovered tasks with ➕ prefix
- document issues/blockers with ⚠️ prefix

## Solution Overview

- New pure function `LinuxSidebarPolicy.sidebarCSS(fontSize: Double?) -> String`
  (no `@MainActor` — touches no store): clamps via `AppSettings.clampSidebarFontSize`
  (nil → `defaultSidebarFontSize`), derives
  `rowHeight = Int(AppSettings.sidebarRowHeight(fontSize:))`, returns three rules:

  ```css
  .agterm-sidebar label { font-size: <size>pt; }
  .agterm-sidebar .navigation-sidebar > row { min-height: <rowHeight>px; }
  .agterm-sidebar .navigation-sidebar > row > box { min-height: <rowHeight>px; padding-top: 0; padding-bottom: 0; }
  ```

- `applySidebarFontSize()` becomes a thin loader: keep the
  `guard let display = gdk_display_get_default() else { return }` early return, load
  settings, call the policy, feed the existing reloadable provider. Provider creation
  and priority 651 untouched.
- Font-size formatting is pinned: keep `\(size)pt` interpolation verbatim (emits
  `13.0pt` today) — do not "tidy" to `Int(size)`; the test asserts the exact substring.
- `makeRow` is untouched — the label keeps its 4 px vertical margins (reversed
  decision, see Context).
- `min-height` is a floor, not a cap: wherever the label's natural height plus the
  8 px margins exceeds the floor (≈30 px at 13 pt, more at 18–20 pt), the row grows —
  that is the desired "larger text gets more room" behavior; at small sizes the floor
  delivers the densification.

## Technical Details

- Height mapping (`round(size) + 15`): 9 → 24, 13 → 28, 18 → 33, 20 → 35 (today: all
  36). These are CSS floors; the rendered height is effectively
  max(floor, label line height + 8 px margins).
- `padding-top/bottom: 0` on the inner box is required — Adwaita's 3 px vertical
  padding would otherwise fight the lowered `min-height`. Horizontal padding is left
  to the theme.
- Flagged risk (visual pass covers it): interaction of the lowered row `min-height`
  with the selection-highlight rounding (`border-radius: 9px`) at small sizes, given
  the port already mirrors selection into an explicit class to work around
  `navigation-sidebar` paint quirks.
- Selector choice: `> row > box` (node names) deliberately mirrors the exact Adwaita
  rule being overridden. `makeRow` also tags the child with
  `agterm-session-row-content`, which would work too — the node-name form is kept so
  the override visibly pairs with the theme rule it defeats.

## What Goes Where

- **Implementation Steps**: code, tests, doc updates in this repo.
- **Post-Completion**: manual visual verification in the running app; upstream
  reporting; AUR release flow.

## Implementation Steps

### Task 1: Verify toolchain and vendored libghostty, baseline build

**Files:**
- none (environment verification)

- [ ] verify `agterm-linux/vendor/ghostty/lib/libghostty.so` and
      `vendor/ghostty/include/ghostty.h` exist (background `scripts/setup-linux.sh`
      from this session — task `bi561r3jb` — must have completed; re-run the script if
      not: it is idempotent)
- [ ] baseline build:
      `cd agterm-linux && PATH="$HOME/.local/share/mise/installs/swift/6.3.2/usr/bin:$PATH" LD_LIBRARY_PATH="$HOME/.local/share/swift-linux-compat" swift build`
- [ ] baseline tests: same env, `swift test` — record any pre-existing failures so the
      fix isn't blamed for them

### Task 2: Add `LinuxSidebarPolicy.sidebarCSS(fontSize:)` with tests

**Files:**
- Modify: `agterm-linux/Sources/AgtermLinux/LinuxSidebarPolicy.swift`
- Modify: `agterm-linux/Tests/AgtermLinuxTests/LinuxPolicyTests.swift`

- [ ] add `static func sidebarCSS(fontSize: Double?) -> String` to
      `LinuxSidebarPolicy`: clamp (nil → default), derive row height via
      `AppSettings.sidebarRowHeight`, emit the three rules from Solution Overview
- [ ] write ONE thematic `@Test` (matching the suite's grouped style, e.g.
      "sidebar CSS derives row height from the shared font-size clamp") with
      `#expect`s covering: 13 pt → `font-size: 13.0pt` + both `min-height: 28px`
      rules; 9 → 24 px; 20 → 35 px; nil → the 13 pt/28 px default; out-of-range
      clamps (40 → the 20 pt/35 px CSS)
- [ ] in the same test, assert the load-bearing selectors verbatim:
      `"> row { min-height: 28px;"` (uniquely pins the row rule — a bare `"> row "`
      substring would also match the box rule) and
      `".agterm-sidebar .navigation-sidebar > row > box"` with
      `"padding-top: 0"` — not just the pixel values
- [ ] run tests — must pass before task 3

### Task 3: Wire the policy into the controller

**Files:**
- Modify: `agterm-linux/Sources/AgtermLinux/AppControllerSidebar.swift`

- [ ] replace `applySidebarFontSize()`'s clamp + CSS literal with
      `LinuxSidebarPolicy.sidebarCSS(fontSize: settings.sidebarFontSize)` (provider
      creation/priority 651 unchanged; `makeRow` and its label margins untouched)
- [ ] full build of both products (`swift build`) — compiles clean
- [ ] run `swift test` — the Task 2 tests plus the whole suite must pass
- [ ] `swiftlint lint --strict` over the tree if swiftlint is available; if not
      installed, apply the manual substitutes CI would catch (`git diff --check` for
      whitespace; new lines under the 200-col `line_length` limit) and note the skip
      here

### Task 4: Add AT-SPI scenario asserting row height follows the font size

**Files:**
- Modify: `agterm-linux/tests/atspi_smoke.py`

- [ ] add scenario `verify_sidebar_row_height_follows_font_size(env)` modeled on
      `verify_surface_configuration_lifetimes` (the launch → stop → seed → relaunch
      shape; it seeds state files between two launches — closer than the
      single-launch `verify_notification_focus_policy`): launch with default
      settings, find a session row via `collect(app, role="list item")`, read its
      height with this harness's GI binding — `node.get_component_iface()` then
      `component.get_extents(Atspi.CoordType.WINDOW)` (WINDOW, not SCREEN: SCREEN
      reports a 0,0 origin under Wayland; do NOT reach for pyatspi
      `queryComponent()` idioms)
- [ ] poll for a settled extent before asserting — a11y nodes can appear before first
      allocate and report height 0 (the harness's own `mouse_click` polls up to 8 s
      for stable bounds for exactly this reason): `wait_for(lambda: h(row) > 1, …)`
      in BOTH launches, then assert
- [ ] assertions: default launch `28 <= h < 36` (floor respected, Adwaita's 36 px pin
      defeated; ~30 expected with the kept 8 px label margins); `stop(process)`;
      write `{"sidebarFontSize": 9}` to `settings.json` in `AGTERM_STATE_DIR`,
      relaunch, assert `24 <= h <= 26` AND `h < default_h` — the `>= 24` bound is the
      load-bearing "floor governs" claim guaranteed by the CSS; exact-24 would couple
      the test to font metrics that differ between this box (Cantarell) and the CI
      container (no font package installed)
- [ ] add a one-line triage comment in the test: `h ≈ 36` → the CSS override did not
      apply; off by 1–2 px at 9 pt → font metrics, widen the band
- [ ] register the scenario in BOTH `main()` locations: the child-scenario name tuple
      (~line 1631) that the no-arg parent run iterates, AND the `elif scenario == …`
      dispatch chain (~line 1665) — an arm without the tuple entry is a SILENT no-op
      (in-repo precedent: `"notification-banner"` has a dispatch arm but is missing
      from the tuple, so it never runs by default)
- [ ] after the local run, confirm `PASS: sidebar-row-height` appears in the output —
      do not trust an overall PASS
- [ ] run the full suite locally as
      `LD_LIBRARY_PATH="$HOME/.local/share/swift-linux-compat" scripts/test-linux-ui.sh`
      (the script does NOT set this box's soname bridges itself; existing scenarios
      also click sidebar rows at their extents — this change moves that geometry, and
      CI runs the suite on push with no PR gate); if a dependency is missing or the
      binary won't launch under Xvfb, note it and explicitly accept CI as the gate

### Task 5: Verify acceptance criteria

- [ ] verify all requirements from Overview are implemented (CSS override present,
      policy pure, `makeRow`/margins untouched, headers untouched)
- [ ] run full test suite: `cd agterm-linux && swift test` (env as in Task 1)
- [ ] run `cd agtermCore && swift test` — core untouched, must stay green
- [ ] launch ISOLATED — `scripts/run-linux.sh` sets no state dir, and
      `ControlServer.start()` unlinks-then-binds the DEFAULT socket, which would steal
      it from the installed `agterm-linux-bin` daily driver and touch real workspace
      state. Use `AGTERM_STATE_DIR=/tmp/agterm-rowheight scripts/run-linux.sh` — the
      path must be SHORT (`start()` hard-guards socket paths ≥ 104 bytes and silently
      never binds)
- [ ] the isolated state dir starts empty: create 3–4 sessions across two workspaces
      by hand (or seed copied state) so density is actually visible
- [ ] confirm the app starts with the denser default sidebar, then go HANDS-OFF — the
      user does the detailed visual pass (see Post-Completion); no `agtermctl` pokes
      at the running instance

### Task 6: [Final] Update documentation

- [ ] `README.md` check is expected to be a no-op (it has no "sidebar font size" text,
      and `.claude/rules/settings.md` already documents the `sidebarRowHeight`
      coupling this fix makes true on Linux) — confirm and move on; do NOT touch
      `CHANGELOG.md` (release-only)
- [ ] commit as `fix(linux): scale sidebar row height with sidebar font size` directly
      on `linux-port` (matches repo history; plan file committed alongside or as
      `docs: …`)
- [ ] move this plan to `docs/plans/completed/`

## Post-Completion

**Manual verification** (user-driven, in the launched dev instance):
- Move Sidebar font size 13 → 9 → 18: session rows densify at 9 (≈24 px), sit ≈30 px
  at 13 (kept margins hold them just above the 28 px floor — still well under the old
  36 px pin), grow at 18. At 18–20 pt confirm glyphs are fully visible vertically
  (ascenders/descenders not cut off): `min-height` is a floor, not a cap, so the row
  must grow with the label rather than truncate it.
- Workspace headers stay content-sized (disclosure/+ buttons unchanged).
- Selection highlight looks right at the smallest size (border-radius vs 24 px rows);
  flagged/status glyphs and the unseen badge still fit the row.
- Inline rename still works (the `GtkEntry` has its own Adwaita min-height and will
  temporarily heighten its row — expected).

**External follow-ups:**
- Report the bug (and this fix) upstream to `melonamin/agterm-linux` — unreported as of
  2026-07-25.
- Next AUR `agterm-linux-bin` release picks the fix up through the normal
  `release-linux` flow; no packaging change needed.
