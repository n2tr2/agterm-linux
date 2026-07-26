# Fix: command-palette rows render the shortcut as inline title text (Linux port)

## Overview

- On macOS a palette row renders the action name left-aligned and its shortcut
  right-aligned in the dimmed secondary color. On the GTK port the row reads
  `Dashboard   ctrl+shift+m` — one left-aligned, same-colored run of text.
- Root cause: the shortcut is concatenated into the *title string* before the row exists
  (`Palette.swift:29-30`), and the row builder emits exactly one `GtkLabel`
  (`Palette.swift:209-215`). The port's item model is `[(String, () -> Void)]`
  (`AppController.swift:57-58`), so a secondary field can only be expressed by
  string-mangling the title. macOS carries a multi-field `PaletteItem` struct rendered as
  `HStack` + `Spacer` + `.foregroundStyle(.secondary)` (`agterm/Views/Palette.swift:6-43`,
  `215-251`).
- Fix: introduce a host-free `LinuxPaletteRow` presentation struct (title / shortcut /
  badge / search keys) plus pure builders, and make `filterPalette` a thin GTK composer
  that builds a horizontal box: title label (`hexpand`) + optional badge + shortcut label
  carrying Adwaita's `dim-label`. Matches the port's "logic host-free and tested, GTK code
  a thin side-effect adapter" convention.
- Scope decision (user, 2026-07-26): **shortcut column + custom-command parity**. Custom
  keymap commands stop appending `"  (custom)"` to their title and instead render a real
  trailing badge plus their own bound chord in the shortcut column, as macOS does
  (`agterm/AppActions+Palette.swift:123`, `131-140`). Session/attention rows keep their
  single-line `"name  —  workspace"` form — two-line rows and attention status glyphs are
  explicitly OUT of scope.
- `Chord.displayString` (kitty syntax, `agtermCore/.../Keybind.swift:53`) stays the Linux
  renderer. The `ctrl+shift+m`-vs-`⌘0` difference from macOS's `glyphString` (`:67`) is
  intentional and NOT part of this fix.
- Keep-in-sync verdicts (recorded so they are not relitigated):
  - **Control API: exempt.** This is palette row *rendering* — pure visual chrome with
    nothing to drive headless. No `Command` case, no `agtermctl` subcommand, no `tree`
    read-back field. Opening a palette is already interactive-only/exempt per
    `.claude/rules/menu-actions.md`.
  - **Settings ▸ Interface toggle: not proposed.** The shortcut column is not a new,
    hideable chrome affordance; it is the correct rendering of an element that already
    ships. No `InterfaceElement` case.
  - **Agent skill: already correct, no edit needed — and this change closes a gap.**
    `agterm/Resources/agent-skill/reference.md:697` already documents custom commands as
    "listed in the action palette marked `custom`", which is what the badge finally makes
    true on Linux.
  - **`site/commands.html` / `site/docs.html`: untouched** (no control command, keymap
    format, or model change). **`CHANGELOG.md`: untouched** (release-only).

## Context (from discovery)

Files involved:

- `agterm-linux/Sources/AgtermLinux/Palette.swift` (319 lines) —
  `paletteActionList()` lines 26-31 build the concatenated title
  (`let suffix = chord.map { "   \($0.displayString)" } ?? ""`); lines 33-42 append the
  Linux-only rows; line 64 appends `"  (custom)"`; `sessionPaletteList()`/
  `attentionPaletteList()` lines 141-153 build `"name  —  workspace"`; `filterPalette`
  lines 198-219 ranks via `fuzzyRank(query:items:keys: { [$0.0] })` (line 206) and renders
  one `gtk_label_new(item.0)` per `GtkListBoxRow` (lines 209-215).
- `agterm-linux/Sources/AgtermLinux/AppController.swift` — `paletteAll`/`paletteItems`
  declared as `[(String, () -> Void)]` (lines 57-58); `resolvedBuiltinChords:
  [Chord: BuiltinAction]` (line 145).
- `agterm-linux/Sources/AgtermLinux/App.swift` — `installAppCSS()` (lines 126-150) holds
  the app-wide CSS block at `GTK_STYLE_PROVIDER_PRIORITY_APPLICATION` (600); the badge
  rule goes here.
- `agterm-linux/tests/atspi_smoke.py` — **hard blocker if missed.** `collect()` matches
  accessible names **exactly** (`node_name == name`, line 38) and two places encode the
  concatenated text:
  - line 894: types `"New Session"` but asserts `named(palette, "New Session   ctrl+shift+t")`
    (deliberately different from the query, so only a row can satisfy it);
  - lines 1209 / 1227 / 1237 pass `"Launch Failure  (custom)"` / `"Exit Failure  (custom)"`
    / `"Slow Failure  (custom)"` to `run_palette_action` (line 478), which uses that ONE
    string both as the text typed into the search entry (line 493) and as the expected row
    name (line 495). The three commands it seeds (lines 1169-1174) are all **chordless**,
    so their rows will show a badge and no shortcut.
- `agterm-linux/Tests/AgtermLinuxTests/` — Swift Testing suites; new presentation tests
  follow `DashboardPresentationTests.swift` / `DeckPagePresentationTests.swift`.

Related patterns found:

- Composite-row precedent in-port: `AppControllerSessionPicker.swift:55-85` builds
  icon + `heading` title + `dim-label` subtitle (`:80`) inside `GtkBox`es, using
  `gtk_widget_set_hexpand` where SwiftUI would use `Spacer`.
- `dim-label` also used at `AppControllerSidebar.swift:79` and `Search.swift:28`.
- Host-free presentation types living in the Linux module and unit-tested:
  `DashboardPresentation.swift`, `SplitPaneLayout.swift`, `LinuxSidebarPolicy.swift`.
- `CustomCommand` (`agtermCore/Sources/agtermCore/CustomCommand.swift:9-24`) carries
  `name` + `shortcut` (empty string = palette-only).
- `ThemePicker.swift:57` keeps its own `[String]` row list and is unaffected.

**Fuzzy-ranking contract (drives a design decision below).** `fuzzyScore`
(`agtermCore/Sources/agtermCore/Fuzzy.swift:12-22`) splits the query on whitespace and
requires **every term to match the SAME key**; `fuzzyRank` (`:29-41`) takes the best (min)
score across an item's keys and tie-breaks alphabetically on `keys.first`. `termScore`
(`:47-59`) scores a prefix **0**, a substring `5 + offset`, a subsequence `40 + gap`. Two
consequences the naive "one key per field" split would hit:

- a query spanning two fields (`"custom launch"`, or the AT-SPI suite's literal
  `"Launch Failure  (custom)"`) matches **no** single key and the row disappears;
- `shortcut`/`badge` as standalone keys turn every short `c…` query into a **prefix**
  (score 0) against `ctrl+…`/`custom`, flattening the ranking so chord-bound rows tie with
  genuine title matches.

So `searchKeys` keeps a **composite** key next to the title (see Solution Overview), which
reproduces today's scores while making `keys.first` the clean title for the tie-break.

**Pre-existing issues surfaced by this work** (recorded; only the first is fixed here):

- `"Open Directory…"` is rendered **twice** today — once from the shared catalog
  (`PaletteCatalog.swift:38`, title at `:80`, always visible per the `default: return true`
  in `isVisible`) and once from the Linux-only append at `Palette.swift:33`. Harmless while
  both rows are bare text; after this change one gains `ctrl+shift+o` and the other stays
  bare, which reads as a bug this change caused. Task 2 drops the redundant append.
- `Preferences…`, `Manage Integrations…`, `Keyboard Shortcuts`, `About agterm`,
  `Copy Selection`, `Paste`, `Select All` have **no `BuiltinAction`**, so no chord resolves
  and their rows show no shortcut — even though Ctrl+, opens Preferences via a hardcoded
  keyval (`KeymapDispatch.swift:305`) and copy/paste ride libghostty binding actions. Out
  of scope; deliberately NOT hardcoding chord text into rows (see the next item for why).
- `AppController.swift:230` hardcodes the tooltip `"Toggle Sidebar (Ctrl+Shift+B)"` while
  the Linux default is `ctrl+shift+s` (`LinuxKeyboardPolicy.swift:21`) — already stale.
  macOS routes palette hints and toolbar tooltips through ONE resolver
  (`.claude/rules/menu-actions.md`); the Linux title bar does not. Out of scope, flagged.

Local toolchain (this box, per project memory):

```sh
export PATH="$HOME/.local/share/mise/installs/swift/6.3.2/usr/bin:$PATH"
export LD_LIBRARY_PATH="$HOME/.local/share/swift-linux-compat"
cd agterm-linux && swift test      # and: swift build
```

`swiftlint` is not installed locally (CI installs it per-run in the separate lint job,
`.github/workflows/ci.yml:89-101`), and the AT-SPI harness needs `xvfb-run`, `xdotool`,
`openbox` — verified absent here via `which`, and `scripts/test-linux-ui.sh:16-21` hard-fails
on any missing dependency. Installing those three would let the suite run locally (it is
fully self-isolated: own `HOME`/XDG/`AGTERM_STATE_DIR`, `:30-42`); otherwise both gates land
in CI (`ci.yml:166-180`).

## Development Approach

- **testing approach**: Regular (code first, then tests, same task)
- complete each task fully before moving to the next
- make small, focused changes
- **CRITICAL: every task MUST include new/updated tests** for code changes in that task.
  The pure row model is covered by Swift Testing unit tests; the GTK composition is not
  unit-testable, so **its tests are the AT-SPI assertions**, updated in the same task that
  changes rendering. No task changes rendered text without touching `atspi_smoke.py`.
- **CRITICAL: all tests must pass before starting next task** — no exceptions, and each
  task must leave the AT-SPI suite logically green (the search keys, the rendered text, and
  the AT-SPI expectations move together, never across a task boundary)
- **CRITICAL: update this plan file when scope changes during implementation**
- run `swift test` after each change
- maintain backward compatibility: no control-protocol, persistence, or settings change;
  palette keyboard/mouse behavior (toggle, filter, ↑/↓, Enter, Esc, scroll clamp) untouched

## Testing Strategy

- **unit tests** (`agterm-linux/Tests/AgtermLinuxTests/`): the pure row model and builders —
  row composition, chord presence/absence, badge, the search-key set, and a **ranking**
  test proving a short `c` query still orders title matches above chord matches.
- **e2e tests**: the AT-SPI smoke suite (`agterm-linux/tests/atspi_smoke.py` via
  `scripts/test-linux-ui.sh`) is the only automated check of the visible fix. It gets
  **stronger**, not weaker: assert the title label AND the shortcut label AND (for custom
  rows) the badge label, each with `role="label"` — the suite's existing idiom
  (`atspi_smoke.py:509`). Asserting the shortcut/badge text is what pins the split, because
  those strings are never typed into the search entry.
- **Accessible-name contract** (decided here, documented in code):
  - A `GtkLabel` exposes its text as its AT-SPI name, so after the split a node named
    exactly `New Session` and one named exactly `ctrl+shift+t` both exist. No
    `gtk_accessible_update_property` is required for the assertions above.
  - The `GtkListBoxRow`'s own computed name will change (likely to empty) once its child is
    a multi-label box. **Nothing depends on it** — `actionable()` is never applied to
    palette rows and the `"command-palette"` widget id (`Palette.swift:179`) is unused by
    the Linux suite. Written down because it is the failure mode a future reader would
    suspect first.
  - Deliberately NOT doing: an explicit row-level `GTK_ACCESSIBLE_PROPERTY_LABEL`. Three
    sibling labels announce as three nodes under Orca, which is acceptable; if a single
    coherent row announcement is later wanted, that property on the row is the right fix
    (and would restore a stable row name) — a separate, deliberate change, not a fallback.
- **Visual acceptance**: isolated dev instance with a seeded keymap — see Post-Completion.

## Progress Tracking

- mark completed items with `[x]` immediately when done
- add newly discovered tasks with ➕ prefix
- document issues/blockers with ⚠️ prefix
- update plan if implementation deviates from original scope
- keep plan in sync with actual work done

## Solution Overview

Three layers, mirroring how macOS separates model from row rendering:

1. **`LinuxPaletteRow`** (new, host-free): `title`, `shortcut: String?`, `badge: String?`,
   plus `searchKeys: [String]` = `[title]` and — only when a shortcut or badge exists — a
   second **composite** key `[title, badge, shortcut].compactMap { $0 }.joined(separator: " ")`.
   `min`-over-keys then reproduces today's scores for both title and chord queries, keeps
   cross-field queries (`"custom launch"`) working, and leaves `keys.first == title` as the
   alphabetical tie-break.
2. **`LinuxPaletteRows`** (new, pure namespace): `action(title:chord:)`, `custom(_:)`,
   `plain(_:)`.
3. **`Palette.swift`** keeps ONLY GTK: `filterPalette` composes a horizontal `GtkBox` per
   row — title label (`xalign 0`, `hexpand 1`, ellipsize end) → optional badge label
   (`agterm-palette-badge`) → optional shortcut label (`dim-label`, `xalign 1`) — and
   passes `row.searchKeys` to `fuzzyRank`.

Key design decisions and rationale:

- **Why a struct, not a wider tuple**: the mapping logic (chord → display string, badge
  assignment, search-key set) becomes unit-testable outside the `@MainActor` GTK code. A
  tuple leaves all of it inside untestable render code.
- **Why the composite search key**: see the fuzzy-ranking contract in Context. Splitting
  fields into standalone keys would break the AT-SPI suite's own query and silently reorder
  short-prefix results.
- **Why the badge is a CSS class**: GTK4/Adwaita has no generic pill class for labels, and
  `agterm-focus-pill` / `agterm-session-picker` are added in-tree with **no CSS rule
  anywhere**, so a real rule must be added to `installAppCSS()`. It follows
  `.agterm-dashboard-caption`'s shape (padding / border-radius / `alpha(@window_fg_color, …)`)
  so it tracks the desktop theme instead of hardcoding a color.
- **Why the title ellipsizes**: dynamic rows get long (`Delete Window: <name>`,
  `Move Session to <workspace>`); without ellipsize the title pushes the shortcut column
  out of the 480 px palette instead of truncating. `gtk_label_set_ellipsize` /
  `PANGO_ELLIPSIZE_END` are available — `Sources/CGtk/shim.h:3` includes
  `pango/pangocairo.h` (same path as `PANGO_ALIGN_CENTER` at `WatermarkRenderer.swift:42`).
- **Why custom rows change in their own task**: keeping `"  (custom)"` in the title through
  Task 2 keeps the three `run_palette_action` scenarios green on the unchanged query, so
  each task ends with a logically green suite and no throwaway transitional code.

## Technical Details

- `LinuxPaletteRow` (new file `agterm-linux/Sources/AgtermLinux/PalettePresentation.swift`):

  ```swift
  struct LinuxPaletteRow: Equatable {
      let title: String
      let shortcut: String?
      let badge: String?

      // computed, NOT stored: stays out of the memberwise init and Equatable synthesis
      var searchKeys: [String] { … }
  }
  ```

- `paletteAll` / `paletteItems` become `[(row: LinuxPaletteRow, run: () -> Void)]` (named
  tuple elements so `$0.row.title` reads clearly at the sort/rank sites).
- Empty-query sort keys off `row.title.lowercased()`. Today it sorts the *concatenated*
  string, so same-prefix titles can reorder slightly — intended.
- `fuzzyRank(query:items:keys: { $0.row.searchKeys })`.
- Row widget tree per entry:
  `GtkListBoxRow > GtkBox(HORIZONTAL, spacing 8, margins 6/6/10/10)` containing
  `GtkLabel(title, xalign 0, hexpand 1, ellipsize END)`, optional
  `GtkLabel(badge, css: agterm-palette-badge)`, optional `GtkLabel(shortcut, css: dim-label,
  xalign 1)`.
- CSS added to `installAppCSS()`:
  `.agterm-palette-badge { font-size: 0.8em; padding: 1px 6px; border-radius: 6px;
  background-color: alpha(@window_fg_color, 0.14); }`
- Custom rows: title = `command.name`, badge = `"custom"`, shortcut =
  `command.shortcut.linuxTrimmedOrNil` (reuses the existing `LinuxStringPolicy` helper, so a
  whitespace-only keymap `shortcut` yields nil).

## What Goes Where

- **Implementation Steps** (`[ ]` checkboxes): the Swift model + builders, the GTK row
  composition, the CSS rule, the AT-SPI expectation updates, unit tests, `swift test` /
  `swift build`.
- **Post-Completion** (no checkboxes): `swiftlint`, the AT-SPI smoke run, and the manual
  visual check in an isolated dev instance.

## Implementation Steps

### Task 1: Add the host-free palette row model and builders

**Files:**
- Create: `agterm-linux/Sources/AgtermLinux/PalettePresentation.swift`
- Create: `agterm-linux/Tests/AgtermLinuxTests/PalettePresentationTests.swift`

- [ ] create `LinuxPaletteRow` (`title`, `shortcut: String?`, `badge: String?`, `Equatable`)
      with `searchKeys` as a **computed** property: `[title]`, plus the composite
      `[title, badge, shortcut].compactMap { $0 }.joined(separator: " ")` only when a
      shortcut or badge exists
- [ ] add the pure `LinuxPaletteRows` builders: `action(title:chord:)` (nil chord → nil
      shortcut, else `chord.displayString`), `custom(_:)` (badge `"custom"`, shortcut from
      `command.shortcut.linuxTrimmedOrNil`), `plain(_:)` (title only)
- [ ] document in a doc comment WHY the composite key exists (cite `fuzzyScore`'s
      every-term-one-key rule and `termScore`'s prefix-is-0), so nobody "simplifies" it to
      one key per field
- [ ] write tests for `action` (chord → `ctrl+shift+t`; no chord → nil shortcut) and `plain`
- [ ] write tests for `custom` (badge `custom`; bound chord surfaces as shortcut; empty and
      whitespace-only `CustomCommand.shortcut` → nil)
- [ ] write tests for `searchKeys`: bare row → `[title]` only; chorded row → title +
      composite; badged row → title + composite; both → composite contains title, badge,
      shortcut in that order
- [ ] write a **ranking** test over `fuzzyRank` with the real row set: query `"c"` keeps
      `Clear Status` / `Copy Selection` (title prefix) ahead of `Dashboard`
      (chord-only match), and query `"custom launch"` still finds a badged custom row
- [ ] run `swift test` — must pass before task 2

### Task 2: Route the palette through the row model and render the shortcut column

**Files:**
- Modify: `agterm-linux/Sources/AgtermLinux/AppController.swift`
- Modify: `agterm-linux/Sources/AgtermLinux/Palette.swift`
- Modify: `agterm-linux/tests/atspi_smoke.py`

- [ ] change `paletteAll`/`paletteItems` (`AppController.swift:57-58`) to
      `[(row: LinuxPaletteRow, run: () -> Void)]`
- [ ] rebuild `paletteActionList()`: catalog commands via
      `LinuxPaletteRows.action(title:chord:)`; `.plain(_:)` for the Linux-only rows
      (`Preferences…`, `Manage Integrations…`, `Keyboard Shortcuts`, `About agterm`,
      `Copy Selection`, `Paste`, `Select All`) and the dynamic rows (window switch/rename/
      delete, move-session, workspace focus)
- [ ] drop the redundant `"Open Directory…"` append (`Palette.swift:33`) — the shared
      catalog already emits it, and after this change the duplicate rows would differ
      visibly (one chorded, one bare)
- [ ] keep custom commands on `.plain(cmd.name + "  (custom)")` for now, so the three
      `run_palette_action` scenarios stay green until Task 3 changes them deliberately
- [ ] convert `sessionPaletteList()`/`attentionPaletteList()` to `.plain(_:)`, keeping the
      `"name  —  workspace"` text verbatim (out of scope to change)
- [ ] replace the single-label row in `filterPalette` with a horizontal `GtkBox` (spacing 8,
      the existing 6/6 vertical + 10 start margins moved onto the box, plus a 10 end margin)
      hosting the title label (`xalign 0`, `hexpand 1`, ellipsize end), and append the
      shortcut label (`dim-label`, `xalign 1`) when `row.shortcut != nil`
- [ ] point the empty-query sort at `row.title.lowercased()` and `fuzzyRank`'s `keys:` at
      `$0.row.searchKeys`; keep `runPaletteIndex` on `.run`
- [ ] add a comment in `filterPalette` recording the accessible-name contract (title and
      shortcut are separate label nodes; the row's own name goes empty and nothing depends
      on it) so a future refactor does not re-merge the labels
- [ ] update `atspi_smoke.py:894` to assert BOTH `named(palette, "New Session", role="label")`
      and `named(palette, "ctrl+shift+t", role="label")` — the shortcut label is never typed,
      so this is what pins the split end-to-end
- [ ] run `swift test` and `swift build` — must pass before task 3

### Task 3: Custom-command rows get a real badge + their own chord

**Files:**
- Modify: `agterm-linux/Sources/AgtermLinux/Palette.swift`
- Modify: `agterm-linux/Sources/AgtermLinux/App.swift`
- Modify: `agterm-linux/tests/atspi_smoke.py`

- [ ] switch custom keymap commands to `LinuxPaletteRows.custom(_:)`, dropping the
      `"  (custom)"` title suffix
- [ ] append the badge label between title and shortcut when `row.badge != nil`, with the
      `agterm-palette-badge` CSS class
- [ ] add the `.agterm-palette-badge` rule to `installAppCSS()` (`App.swift:126-150`),
      themed via `alpha(@window_fg_color, …)` like `.agterm-dashboard-caption`
- [ ] update the three `run_palette_action` call sites (`atspi_smoke.py:1209`, `1227`,
      `1237`) to the plain names (`"Launch Failure"`, `"Exit Failure"`, `"Slow Failure"`) —
      required, not cosmetic: the old query `"Launch Failure  (custom)"` spans two fields
      and `fuzzyScore` would drop the row entirely
- [ ] add a badge assertion in the custom-command scenario:
      `named(palette, "custom", role="label")` (its three seeded commands are chordless, so
      they render title + badge and no shortcut)
- [ ] leave `run_palette_action`'s signature alone — with the suffix gone, the typed query
      and the expected row name are the same string again, so no extra parameter is needed
- [ ] run `swift test` and `swift build` — must pass before task 4

### Task 4: Verify acceptance criteria

- [ ] verify a bound catalog command renders title + right-aligned dim chord, and an
      unbound one renders title only
- [ ] verify a custom command renders title + `custom` badge + its own chord, and a
      chordless custom command renders title + badge only
- [ ] verify search: find-by-chord still filters, `custom` still filters custom commands,
      `"custom launch"`-style cross-field queries still match, and a short `c` query still
      ranks title matches first (the Task 1 ranking test)
- [ ] verify `Open Directory…` now appears exactly once, with `ctrl+shift+o`
- [ ] verify long dynamic titles (`Delete Window: <long name>`) ellipsize instead of pushing
      the shortcut column out of the 480 px palette
- [ ] verify session/attention rows are unchanged (`"name  —  workspace"`, single line)
- [ ] run the full host-free suites: `cd agterm-linux && swift test` and
      `cd agtermCore && swift test`
- [ ] run `swift build` and `swift build -c release` (release/WMO is a separate CI gate)
- [ ] run `git diff --check` (CI's "Reject whitespace errors" step, `ci.yml:105`) and
      eyeball the 200-column `line_length` limit on the touched files, since swiftlint
      cannot run locally
- [ ] grep the tree for leftover concatenated palette strings (`"   ctrl+"`, `"  (custom)"`)
      outside `docs/plans/`

### Task 5: [Final] Update documentation

- [ ] record the README verdict: the row layout is not documented, so no change is needed —
      but note that `README.md:405` describes a "custom-commands palette (Ctrl-Shift-O)"
      that does not exist on Linux (`customCommandPalette` is deliberately keyless,
      `LinuxKeyboardPolicy.swift:26-28`, and Ctrl+Shift+O is Open Directory). Flag it; fix
      only if the user wants it in this change
- [ ] confirm the agent-skill verdict in the Overview (reference.md:697 already says
      "marked `custom`" — this change makes Linux match the shipped doc)
- [ ] add a Linux-palette note to `AGENTS.md` or a `.claude/rules/` file **only if the user
      wants one** — no rule file currently owns the Linux palette (`.claude/rules/menu-actions.md`
      is macOS-path-scoped); otherwise the contract lives in the Task 2 code comment
- [ ] re-confirm the keep-in-sync verdicts still hold at merge time (no control command, no
      Interface toggle, no site change)
- [ ] move this plan to `docs/plans/completed/`

## Post-Completion

*Items requiring manual intervention or external systems — no checkboxes, informational only*

**Manual verification** — the recipe matters, because `scripts/run-linux.sh` sets **no**
`AGTERM_STATE_DIR` (`:1-26`), so a bare run reads/writes the real state dir and
`ControlServer.start()` unlink-rebinds the **default** socket, stealing it from an installed
daily driver. An isolated state dir also redirects the **config** dir
(`AppControllerSurfaces.swift` → `ConfigPaths.configDirectory(setting:stateDir:)`), so an
isolated instance sees only the seeded starter `keymap.conf` and would have **no custom
commands at all** — making the badge half of the fix unobservable. Seed it first:

```sh
D=/tmp/agterm-palette          # SHORT path: unix sockets cap at ~104 bytes
mkdir -p "$D/config"
printf 'command "Chorded Demo" true\ncommand "Chordless Demo" true\n' > "$D/config/keymap.conf"
# give the first one a chord in keymap.conf, leave the second bare
AGTERM_STATE_DIR="$D" scripts/run-linux.sh
```

Then open the palette with **Ctrl+Shift+P** (not Ctrl+Shift+O — that is Open Directory on
Linux, and the custom-command palette is keyless) and confirm: the shortcut column is
right-aligned and dimmed; the `custom` badge reads as a badge in both light and dark desktop
themes; the chordless custom row shows a badge and no shortcut; nothing overflows at the
480 px palette width. Hands-off after launch unless asked to drive it.

Optional: screen-reader sanity with Orca — a row announces its title, badge, and shortcut as
separate labels (see the Testing Strategy note on why no row-level a11y label is set).

**CI-only gates** (not runnable here — `swiftlint`, `xvfb-run`, `xdotool`, `openbox` absent):
- `swiftlint lint --strict --quiet` (`ci.yml:89-101`) — the new file must be clean and
  `Palette.swift` must stay under the 1000-line limit (currently 319; expect ~355).
- The GTK AT-SPI smoke suite (`scripts/test-linux-ui.sh`, `ci.yml:177-180`) — the real gate
  on the accessible-name changes. On failure read `artifacts/linux-ui/accessibility-tree.txt`
  from the run to see what names the rows actually expose before adjusting either side.
  Installing the three missing dependencies would make this runnable locally.
