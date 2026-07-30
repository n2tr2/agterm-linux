# Fix: shrinking the sidebar clips session names and hides every status glyph (Linux port)

## Overview

- Dragging the sidebar divider narrower hard-clips session names mid-glyph (no `…`) and pushes
  the agent-status glyph, flag star, and unseen badge off the right edge — on **every** row, not
  just the long-named one. Reported in
  `docs/issues/20260727-linux-sidebar-shrink-clips-names-and-hides-status.md` (v0.16.1,
  Wayland/Hyprland).
- Root cause: no sidebar `GtkLabel` sets `gtk_label_set_ellipsize`. A GTK4 label with no ellipsize
  and no wrap reports its FULL text width as its MINIMUM width, so the row cannot shrink below
  `chrome + the entire name`. GTK never allocates below a minimum, so the row overflows the
  sidebar's `GtkScrolledWindow` viewport and is clipped by it. The status glyph is packed AFTER the
  hexpanding name label, so it is the first thing pushed out.
- One long name breaks every row because the scroller's child is a vertical `GtkBox` whose minimum
  width is the MAX over its children, and a `GtkBox` allocates its full width to every child — so
  all rows are laid out at the widest row's width and every trailing glyph lands at the same
  off-viewport x.
- Fix, in two parts:
  1. Set `PANGO_ELLIPSIZE_END` on every sidebar label that can hold arbitrary-length user text, and
     `wrap` on the one that holds fixed instructional text.
  2. Replace the two disagreeing hardcoded width minimums with a single **measured, derived** Linux
     sidebar floor — see the decision block below; the naive "just use the shared 160" is wrong and
     the plan says why.
- This restores the macOS sizing contract, stated explicitly at
  `agterm/Views/WorkspaceSidebar+RowRendering.swift`: *"the name hugs and resists compression
  weakly while the icon and badge hug and resist strongly, so the name truncates first and the icon
  and badge stay whole."* The GTK row already reproduces the macOS LAYOUT (`hexpand` on the name
  only, hugging trailing widgets) — only the truncation half was missing.

## Decisions made during planning (do not relitigate)

- **Fork base: `linux-palette-shortcut-column` (tip `cd3ee02`) — the branch that introduced the
  palette work.** That branch is `linux-port` plus exactly one commit, so this fix stacks on it and
  the eventual PR carries only the palette change and this one — not the unrelated hyperlink-cursor,
  MainTimer, and row-height work that also sits on `linux-port-wip`. Basing on `linux-port` itself
  was rejected: it lacks the palette ellipsize precedent this fix builds on (`cd3ee02`, squashed onto
  `linux-port-wip` as `16b9a04`). This matches `docs/upstream-pr-workflow.md`: feature work in this
  fork is a STACK, each branch forked from the previous, flattened only at PR time.
  ⚠️ **What this base does NOT carry, verified** — three plan references retarget accordingly:
  - `verify_sidebar_row_height_follows_font_size` is absent (it came with the row-height work). The
    AT-SPI model is `verify_surface_configuration_lifetimes` instead, which has the same
    launch → stop → seed → relaunch shape; the `get_extents` idiom must be lifted from `mouse_click`
    rather than copied from an existing sidebar-measuring scenario.
  - `LinuxSidebarPolicy` carries only `flaggedRowLabel` — no `sidebarCSS(fontSize:)`. It is still the
    host-free-policy precedent, just a thinner one, and `applySidebarFontSize` emits its CSS inline.
  - the stale `.claude/rules/sidebar.md` cross-reference in `LinuxSidebarPolicy.swift` does not exist
    on this base, so that cleanup drops out of scope here.
  Row heights also differ (Adwaita's 36px pin, not the densified ~32) — that is a HEIGHT difference
  and does not affect the width chrome this plan measures.
  **What the base DOES carry, verified with `git show` against `linux-palette-shortcut-column`** (so
  the implementer need not re-establish these): `makeRow` is byte-identical to the wip version — the
  chrome table holds exactly; `layoutSaveDebouncer.schedule(after: 0.4)` exists
  (`AppControllerSidebarSplit.swift:48`) — the Task 7 race note holds; the legacy `workspaces.json`
  migration exists (`WindowLibrary.swift:627`) — the Task 7 seeding mechanism holds; and the AT-SPI
  helpers Task 7 depends on (`collect`, `wait_for`, `named`, `describe_tree`, `control_json`,
  `launch`, `stop`, `mouse_click`, `verify_surface_configuration_lifetimes`, the `main()` tuple +
  dispatch shape) are all present.
- **Scope**: the sidebar label sites plus the width reconciliation. The issue's unverified
  "scope beyond the sidebar" list (session picker, MRU switcher overlay, dashboard, theme picker,
  search, zoom) is explicitly OUT — recorded under Post-Completion as follow-up surface.
- **Ellipsize mode: `END` for every user-text label in the sidebar**, including the flagged-mode row.
  The palette (`cd3ee02`, on the base branch) chose `MIDDLE` because every long palette title carries a
  *disambiguating tail* (`Delete Window: <name>`, `Move Session to <workspace>`). A session name
  disambiguates at the HEAD, so `END` is correct. The flagged row (`<session>  —  <workspace>`) has
  the palette's tail shape, but it is already scoped to the flagged view where the session name is
  what identifies the row — uniformity wins, and `END` matches the macOS `.byTruncatingTail`
  contract.
- **Instructional text WRAPS, it does not ellipsize.** The "No flagged sessions." hint is a fixed
  instruction, not user text; truncating it to `No flagged sessi…` would be worse than the bug.
  `gtk_label_set_wrap` is the correct treatment there. This ellipsize-vs-wrap distinction goes in
  the rule note (Task 8) so the next label added to the sidebar gets the right one.
- **Width reconciliation: ONE measured, derived Linux floor replacing both hardcoded numbers.**
  ⚠️ This REVERSES an earlier planning decision ("drop the 240 request and let the shared
  `AppStore.sidebarWidthMin = 160` govern"), because measurement invalidated its premise. Summing
  `makeRow` against the Adwaita `navigation-sidebar` rule:

  | | px |
  |---|---|
  | listbox `margin_start` | 14 |
  | Adwaita row padding + margin (`0 8px` / `0 6px 2px`) | 28 |
  | lead icon + its `margin_start` | 22 |
  | box spacing + label `margin_start` | 10 |
  | box `margin_end` | 6 |
  | **bare row chrome** | **~80** |
  | status glyph (+ spacing) | 22 |
  | flag star (+ spacing) | 22 |
  | unseen badge `99+` (+ spacing) — a `GtkLabel`, so it scales with sidebar font size | ~40 |
  | **fully decorated row chrome** | **~164** |

  A fully decorated row therefore cannot fit a 160px sidebar even with its name ellipsized to zero,
  and the figure grows at 20pt because the badge is a label. Dropping to 160 would reopen a narrow
  band (~160–185px) of the reported bug. Instead: measure the worst case, then set one derived
  floor = `decorated chrome + a name allowance`, commented with the derivation.
  Raising the shared `AppStore.sidebarWidthMin` was rejected — `agtermCore` is shared, so it would
  change the macOS floor for a Linux bug; the shared 160 stays a cross-platform lower bound and
  Linux's GTK chrome legitimately needs more.
- **⚠️ The floor is derived at 13pt, and MUST NOT exceed today's effective 240.** This is the
  guardrail on the previous bullet, and it is easy to get backwards. Deriving the name allowance at
  **20pt** — chrome ~180 plus ~8 characters at ~90 — lands near **270**, which is *wider than today's
  240*: a bug filed as "I cannot narrow the sidebar without breakage" would ship a fix that makes the
  sidebar **less** narrowable, permanently strands the 220 default, and widens the default sidebar
  for every user at the default font. So:
  - derive the name allowance at the **default 13pt** (≈164 chrome + ≈56 allowance ≈ **220**);
  - the 20pt requirement is the weaker, correct one — *decorated chrome at 20pt (~180) must fit
    inside the floor*, so glyphs stay whole and the name simply truncates harder. That is exactly
    what the acceptance criteria ask for, and it is satisfiable at 220;
  - **hard gate: if the measured floor exceeds 240, STOP and reconsider.** The fix must never raise
    the minimum sidebar width above today's.
  - ⚠️ **SUPERSEDED — this hard gate was deliberately OVERRIDDEN in review round 2 (Decision A).** The
    floor is now measured rather than derived-then-capped, and above roughly **27 effective points**
    (sidebar font size × desktop text scaling) it rises past 240. See "Post-review amendment (review
    round 2)" at the end of this file for the rationale; do not re-apply a 240 ceiling.
- **NO `gtk_label_set_width_chars` floor**, despite the issue suggesting one. With a derived floor
  that already includes a name allowance in PIXELS on the sidebar, a second floor in CHARACTERS on
  the label is a redundant constraint that must be re-derived on every change to the sidebar font
  range or row chrome — and if set too high it re-creates the bug it guards against. The name
  allowance in the derived floor is where that concern is handled, once.
- **`makeNameWidget` relocates to `AppControllerSidebar.swift`.** ⚠️ **User-approved during
  planning** (the root `CLAUDE.md` requires asking before restructuring a file). On the fork base
  the relocation is REQUIRED, not merely tidy: `AppController.swift` is at exactly **1000 lines
  there** (verified with `git show` against `linux-palette-shortcut-column`) — already AT the
  swiftlint `file_length` cap — so the ellipsize edit cannot land in it at all without a breach,
  and bumping the limit is forbidden. (The earlier "992, so ~995 after the edit — optional headroom"
  reading was a `linux-port-wip` measurement; it does not hold on the base.) The function is purely
  sidebar code whose only two callers already live in the destination file.
- **Testing**: Regular (code first, tests same task where possible). The behavioral coverage is a
  new AT-SPI scenario, not a unit test — see Testing Strategy.

### Keep-in-sync verdicts (recorded so they are not relitigated)

- **Control API: nothing owed.** This is a rendering fix; it adds no action to `AppActions`/
  `AppStore` and mutates no session state, so the four-point audit (`Command` case → `ControlServer`
  arm → `agtermctl` subcommand → round-trip tests) and the paired `tree` read-back do not apply.
  Note that no `sidebar.width` command exists on either platform today — adding one is a separate
  proposal, listed under Post-Completion, not smuggled into a bug fix.
- **Settings ▸ Interface toggle: none.** No chrome is added or made hideable.
- **Bundled agent skill (`agterm/Resources/agent-skill/`): unchanged.** No control command, keymap
  format, or window/workspace/session/pane model change.
- **Website (`site/docs.html`, `site/commands.html`, `site/index.html`): unchanged.** Same reason.
- **`CHANGELOG.md`: NOT touched.** Release-only, per the root `CLAUDE.md` guardrail.

## Context (from discovery)

**The label sites** (all in the sidebar widget tree; line numbers were read on `linux-port-wip` and
must be re-confirmed against the base, not trusted):

| # | site | text | treatment |
|---|---|---|---|
| 1 | `AppController.swift:709` — `makeNameWidget` plain-label branch | session names AND workspace header names | ellipsize `END` |
| 2 | `AppControllerSidebar.swift:212` — flagged-mode row label | `<session>  —  <workspace>`, the longest string the sidebar renders | ellipsize `END` |
| 3 | `AppControllerSidebar.swift:100` — focus pill | `✕  <workspace>` | ellipsize `END`, via the button's child |
| 4 | `AppControllerSidebar.swift:90` — flagged-empty hint | `No flagged sessions.\nRight-click a session → Flag.` | **wrap**, not ellipsize |
| 5 | `AppControllerSidebar.swift:166` — non-workspace section header | fixed literal `"Flagged"` | none needed — verify and record |
| 6 | `AppControllerSidebar.swift:228` — unseen badge (`gtk_label_new(nil)` + `set_markup`) | `1`…`99+` | **none, and it must NOT ellipsize** |

Notes on the awkward ones:

- **Site 1** already sets `xalign 0` and `hexpand 1`; only the ellipsize call is missing. The rename
  branch above it returns a `GtkEntry` instead — see Tasks 5-6 (Task 5 measures it, Task 6 constrains it if needed).
- **Site 3** has **no direct label handle**: `gtk_button_set_label` creates the label internally, so
  it must be reached via `gtk_button_get_child` (or the button rebuilt with `gtk_button_set_child`
  over an explicit label). The only site that is not a one-liner.
- **Site 4** was missed by the original issue. Its minimum width is its longest line (~200px at
  13pt), so it fits under today's 240 floor and only becomes a clipping source once the floor drops.
- **Site 5** is a fixed short literal; listed so the audit is provably complete, not because it
  needs a change.
- **Site 6 is a trap for a future reader.** It is a `GtkLabel`, so it pattern-matches the sites that
  DO need ellipsize — but it is a hugging trailing widget whose natural width is part of the very
  chrome the sidebar floor is derived from. Ellipsizing it would let it collapse and silently
  invalidate the floor. It is in the table specifically so nobody "completes" the sweep by adding it.

**⚠️ Pre-existing latent bug at site 2**, found during review. `makeRow` picks the plain label only
when `flaggedView && renaming?.id != s.id`, but four lines later guards on the weaker
`if flaggedView { gtk_label_set_xalign(label, 0) }`. When renaming a session *in flagged view*,
`label` is the `GtkEntry` from `makeNameWidget` and `gtk_label_set_xalign` is called on a non-label —
a GTK critical. Any new call added under that guard would reproduce it. Fix both in Task 3.

**The two disagreeing width minimums:**

- `agtermCore/Sources/agtermCore/AppStore.swift:68` — `sidebarWidthMin = 160`, applied to the paned
  start child at `AppControllerSidebarSplit.swift:16` and used as the clamp in `captureSidebarWidth`.
- `agterm-linux/Sources/AgtermLinux/AppController.swift:199` —
  `gtk_widget_set_size_request(W(scroller), 240, -1)`.

The 240 wins, so the 160 clamp never fires. **A second consequence the issue did not note:**
`AppStore.sidebarWidthDefault` is **220**, also below 240 — so `gtk_paned_set_position(220)` is
overridden by the child minimum to 240, `notify::position` fires with 240, and `captureSidebarWidth`
persists 240. Live corroboration: **all five window records in
`~/.local/share/agterm/windows/*.json` hold `"sidebarWidth": 240` exactly.** The default has been
silently drifting since the request was added.

⚠️ **Honest scope of that fix:** a derived floor at or above 220 leaves `sidebarWidthDefault` still
unreachable — the drift MOVES from 240 to the new floor rather than disappearing. What this change
actually fixes is that the floor becomes *derived and documented* instead of arbitrary, and that it
is not wider than today's. Do not claim "the default width drift is fixed" in the PR body unless the
measured floor comes in below 220.

**⚠️ The paned start child is NOT the scroller.** `AppController.swift:264` passes `sidebarToolbar`
(an `AdwToolbarView` wrapping the header bar, the scroller, and the bottom bar). So after removing
the scroller's request, the effective floor is
`max(derived floor, AdwHeaderBar minimum, bottom-bar minimum)` — and the header bar's minimum is
environment-dependent: `AppController.swift:193` emits `":"` on Hyprland (no window buttons) but
`"close,minimize,maximize:"` elsewhere, including CI's X11 runner. Any claim of the form "X is now
the single floor" must be measured under both decoration layouts.

**Row structure** (`makeRow`): `GtkListBoxRow > GtkBox(horizontal, 6)` holding
`[GtkImage terminal icon][name label: hexpand][status GtkImage][flag star][unseen badge GtkLabel]`.
The name label is the only hexpanding child and the trailing widgets already hug their content — so
**no `hexpand` changes are needed**; the priority contract is correct once ellipsize is set.

**⚠️ Two of the three decorations are EPHEMERAL and cannot be seeded.** `Session.unseenCount` and
`Session.agentIndicator` both carry the comment *"`SessionSnapshot` doesn't capture it, so it never
survives a relaunch."* Only `flagged` persists. So a test that needs a fully decorated row must drive
the status (`session.status`) and the badge (`notify`) at RUNTIME, after launch — and must not
re-select the session afterwards, because `AppStore.selectSession` zeroes `unseenCount`
(`AppStore.swift:362`).

**Workspace header** (`appendSection`): `GtkBox(horizontal, 4)` holding
`[disclosure button][grid icon][name via makeNameWidget: hexpand][add-session "+" button]`. Same
failure mode — the "+" button is what gets pushed out.

**Test surface:**

- `agterm-linux/Tests/AgtermLinuxTests/LinuxPolicyTests.swift` — Swift Testing suite
  "Linux-owned policy and adapters". Nothing in this change is host-free **unless the Task 5
  measurement takes the font-size-dependent branch**, in which case Task 6 owes a case here.
- `agterm-linux/tests/atspi_smoke.py` — the real coverage. ⚠️ **On this base it contains no
  sidebar-measuring scenario** (`verify_sidebar_row_height_follows_font_size` came with the
  row-height work, which is not on the fork base). Two things still transfer:
  `verify_surface_configuration_lifetimes` supplies the launch → stop → seed → relaunch shape, and
  `mouse_click` supplies the extents idiom
  (`node.get_component_iface()` → `component.get_extents(Atspi.CoordType.WINDOW)`) along with its
  settle-polling. The traps must therefore be re-established rather than copied: WINDOW not SCREEN
  (SCREEN reports a 0,0 origin under Wayland), and a node published before first allocate reports 0,
  so poll for a settled value.
- `scripts/test-linux-ui.sh` runs the suite; CI's `build-linux` job runs it on push.

**Prior art to read even though it is not on the base:**
`docs/plans/completed/20260725-linux-sidebar-row-height.md` — same subsystem, and it worked out the
extents-measuring scenario pattern in detail. Its scenario is not available to copy from here, but
its write-up is the best guide to the mechanism and its failure modes.
⚠️ The worktree forked from the base will NOT contain this file — open it from the main checkout at
`/home/n/p/github/agterm-linux/docs/plans/completed/20260725-linux-sidebar-row-height.md`.

## Development Approach

- **testing approach**: Regular (code first, then tests)
- complete each task fully before moving to the next
- make small, focused changes
- **every task MUST include new/updated tests for the code it changes** — with ONE documented
  exception covering Tasks 1–6, whose changes are GTK widget properties with no host-free logic and
  whose behavioral coverage lands in Task 7 — unless Task 5 measures a font-size-dependent floor, in
  which case Task 6 leaves the exception and owes a `LinuxPolicyTests` case. See Testing Strategy; the exception is not a licence to
  skip Task 7.
- **all tests must pass before starting the next task** — no exceptions
- **update this plan file when scope changes during implementation**; every measurement this plan
  asks for must have its NUMBER written back here, not just a checked box
- maintain backward compatibility: no settings-schema change, no persistence-format change, no
  control-protocol change
- **do this work in an isolated git worktree** per the root `CLAUDE.md` mandate. Fork from
  **`linux-palette-shortcut-column`** (see the decision above), fetching it first so the fork is not
  stale. A fresh
  worktree needs the artifact symlink setup (`GhosttyKit.xcframework`, `agterm/Resources/{ghostty,terminfo}`)
  before it can build.

## Testing Strategy

- **unit tests**: none are expected, and this is deliberate rather than an omission. Every change
  here is a GTK widget property or a widget-tree edit; there is no host-free logic to hoist (the
  `width-chars` policy function that would have been testable was rejected above). `LinuxPolicyTests`
  is untouched — unless the Task 5 branch below applies. `cd agtermCore && swift test` and the Linux package tests must still pass — they are
  a regression gate here, not new coverage.
  **This triggers the plan template's partial-implementation exception**: Tasks 1–6 carry no test of
  their own and their behavioral coverage lands in Task 7, which is therefore not optional and must
  not be deferred past this change.
- **⚠️ Conditional branch, decided by the Task 5 measurement:** the "no host-free logic" claim holds
  only if the floor is a plain constant. If the measurement pushes toward a **font-size-dependent**
  floor (re-applied from `applySidebarFontSize`, which already reloads on font change), that IS
  host-free logic — and both the root `CLAUDE.md` hoist rule and the direct in-file precedent
  `LinuxSidebarPolicy` (which on this base holds only `flaggedRowLabel`) then require it to live there with a
  `LinuxPolicyTests` case. Task 6 carries that branch explicitly so the convention cannot be skipped
  by omission.
- **e2e / AT-SPI (the load-bearing test)**: a new `sidebar-narrow-clipping` scenario that seeds the
  sidebar at its floor, fully decorates a long-named row (status + flag + unseen badge), and asserts
  the row's right edge stays inside the sidebar.
  ⚠️ **The reference edge must NOT be any node inside the scrolled window.** The viewport, the
  content box, the list box, and the row's parent box all inherit the same overflow width under the
  bug, so `row.width <= listbox.width` is true with AND without the fix — a vacuous test. Assert
  against the width **read back from the persisted per-window state** after launch (not the seeded
  number — Task 7 explains why, and how to avoid racing the debounced write), or against the
  scroll-pane node only if `describe_tree` proves it is exposed with an independent allocation. The
  existing
  1868-line suite uses `role="scroll pane"` nowhere, so assume it is not exposed until shown
  otherwise.
  ⚠️ **Corrected in Task 7:** it IS exposed, with what looks like an independent allocation
  (`x=0 y=47 w=220 h=406`).
  The scenario still measures against the persisted read-back, for the reasons recorded on that
  checkbox; the scroll pane is noted there as the fallback reference.
- **The scenario must be proven to fail first.** Run it against the un-fixed tree and confirm it
  fails, in its FINAL assertion form — not in an intermediate form that gets replaced afterwards.
- **Known coverage gaps, recorded rather than papered over**: the focus pill (Task 4) and the
  flagged-empty hint (Task 3) are not covered by the scenario; both are verified manually in Task 9.
- **run the full suite before pushing.** CI's `build-linux` runs it on push with no PR gate, and
  several existing scenarios click sidebar rows by pointer at their AT-SPI extents. This change
  alters sidebar geometry, so those pointer targets move. If a harness dependency is missing on this
  box (`scripts/test-linux-ui.sh` needs `dbus-run-session`, `openbox`, `xdotool`, `xvfb-run`, python
  `gi`/`Atspi` — the row-height plan recorded `openbox`/`xdotool`/`xvfb-run` as absent here), run
  the NEW scenario directly
  (`AGTERM_ATSPI_SCENARIO=sidebar-narrow-clipping python3 agterm-linux/tests/atspi_smoke.py`), note
  the skip here, and explicitly accept CI as the gate for the rest.
- **manual verification is required** (Post-Completion) — the reported symptom is a drag gesture,
  and no automated test drags the divider.

## Progress Tracking

- mark completed items with `[x]` immediately when done
- add newly discovered tasks with ➕ prefix
- document issues/blockers with ⚠️ prefix
- update the plan if implementation deviates from the original scope

## Solution Overview

1. **Relocate** `makeNameWidget` into `AppControllerSidebar.swift` — a pure move (user-approved).
2. **Ellipsize the name label** in `makeNameWidget`, covering session rows and workspace headers.
3. **Fix the two flagged-view labels** — ellipsize the breadcrumb row (under the correct guard, and
   repair the pre-existing `xalign`-on-entry bug), and wrap the empty-state hint.
4. **Ellipsize the focus pill** via the button's child.
5. **Measure** the worst-case decorated row and the toolbar minimum, recording every number here —
   no source change, so it cannot be half-done and the numbers land before any decision rests on
   them.
6. **Apply one derived sidebar floor**, replacing both hardcoded numbers, gated on Task 5's numbers.
7. AT-SPI regression scenario. 8. Docs. 9. Acceptance. 10. Close out.

The design principle, stated so it survives future edits: **any sidebar widget that can hold
arbitrary-length user text must be able to report a small minimum width** — `ellipsize` for a label,
`width-chars` for an entry. Fixed instructional text wraps instead. Trailing glyphs need nothing,
because they already hug.

## Technical Details

- `gtk_label_set_ellipsize(label, PANGO_ELLIPSIZE_END)` — both symbols are already reachable through
  the `CGtk` shim (`Palette.swift` uses `gtk_label_set_ellipsize` with `PANGO_ELLIPSIZE_MIDDLE`), so
  no `shim.h` change is needed. Confirm `PANGO_ELLIPSIZE_END` resolves at build time.
- Focus pill: `gtk_button_set_label` creates an internal `GtkLabel` child. Reach it with
  `gtk_button_get_child(BUTTON(pill))` after setting the label. If the child is not a label on GTK
  4.22.4, rebuild with an explicit `gtk_label_new` + `gtk_button_set_child`. Verify, do not guess.
- Rename entry: `GtkEntry`'s minimum comes from `GtkText`'s default sizing and can exceed the space
  the name gets at the floor. `gtk_editable_set_width_chars(entry, <small>)` lowers it. Measure
  first; if it already fits, add nothing and record the number.
- Derived floor: apply it to the **paned start child** (`AppControllerSidebarSplit.swift:16`), which
  is where the existing `AppStore.sidebarWidthMin` request lives, and delete the scroller request so
  there is exactly one constraint. Express it as a named Linux constant with the derivation in a
  comment (`decorated chrome + name allowance`), not a bare literal.

## What Goes Where

- **Implementation Steps** (`[ ]` checkboxes): source edits, the AT-SPI scenario, doc updates.
- **Post-Completion** (no checkboxes): manual drag verification, the upstream PR flatten, and the
  follow-up surface this plan does not cover.

## Implementation Steps

### Task 1: Relocate `makeNameWidget` into `AppControllerSidebar.swift`

**Files:**
- Modify: `agterm-linux/Sources/AgtermLinux/AppController.swift`
- Modify: `agterm-linux/Sources/AgtermLinux/AppControllerSidebar.swift`

- [x] move `makeNameWidget(id:text:isWorkspace:)` (including its doc comment) verbatim from
      `AppController.swift` into the `AppController` extension in `AppControllerSidebar.swift`,
      directly above `makeRow`
      — landed at `AppControllerSidebar.swift:184-212` (`func makeNameWidget` on line 188), with
      `private func makeRow` now at line 214
- [x] verify no other change is needed: the three C callbacks it references (`onRenameCommit`,
      `onRenameKey`, `onNameDoubleClick`) are internal file-scope globals in
      `AppControllerCallbacks.swift`, and the stored properties it touches (`renaming`,
      `renameEntry`, `nameLabels`) stay on `AppController` — so no access-level or import change
      — confirmed: the three callbacks are module-scope `let`s at `AppControllerCallbacks.swift:335`,
      `:340`, `:346`; `nameLabels`/`renaming`/`renameEntry` are stored properties at
      `AppController.swift:93`, `:107`, `:108`. No `import`, access-level, or `@MainActor` change was
      needed (the destination extension is already `@MainActor extension AppController`)
- [x] confirm both callers (`makeRow`, `appendSection`) are in the destination file and still resolve
      — `appendSection` calls it at `AppControllerSidebar.swift:115`, `makeRow` at `:228`; the Linux
      package builds clean (`Build complete! (25.98s)`)
- [x] record the resulting line counts here; both files must stay under the 1000-line cap and **no
      swiftlint `file_length` limit may be raised**
      — **`AppController.swift`: 1000 → 971 lines** (was exactly AT the cap, now 29 under);
      **`AppControllerSidebar.swift`: 376 → 405 lines**. Both under 1000; `.swiftlint.yml` untouched
- [x] confirm the diff is a pure move (`git diff -M` reports it as such, or the added and removed
      hunks are textually identical) — keep it as its own commit for as long as the branch allows,
      so the PR body can call it out as behavior-free
      — verified textually: 29 added lines, 29 removed lines, `diff` of the two sets is EMPTY
      (`git diff --stat -M`: `29 insertions(+), 29 deletions(-)`)
- [x] **no new tests** — a pure relocation with zero behavior change; the compiler is the check.
      Build the Linux package, run `cd agtermCore && swift test` and the Linux package tests
      — Linux package builds and links clean; `agterm-linux` tests: 133 tests / 17 suites, 1 failure;
      `agtermCore` tests: 1733 tests / 74 suites, 1 failure. ⚠️ **Both failures are PRE-EXISTING and
      environment-dependent, reproduced on the stashed (clean) tree** — the same full Linux run on
      `git stash`ed sources fails identically:
      - `Linux integration service` ▸ "Flatpak process environments do not offer a host launcher"
        (`IntegrationServiceTests.swift:765`) — this box has agterm-linux deployed under
        `/opt/agterm-linux`, so the CLI resolves as `.installed` where the test expects `.unavailable`
      - `CodexStatusHookTests` ▸ `stopReportsBlockedWhenAssistantMessageEndsInQuestionMark()`
        (`CodexStatusHookTests.swift:102`) — in `agtermCore`, which this task does not touch at all
      Neither is reachable from a GTK widget-builder move. Treated as the baseline for the rest of
      this plan; re-check them at Task 9 rather than attributing them to this change.
      ⚠️ `swiftlint` is NOT installed on this box (`which swiftlint` → not found), so `make lint` is
      unavailable for every task in this plan; the manual substitutes named in Task 8 apply

### Task 2: Ellipsize the sidebar name label (session rows + workspace headers)

**Files:**
- Modify: `agterm-linux/Sources/AgtermLinux/AppControllerSidebar.swift`

- [x] in the relocated `makeNameWidget`, add `gtk_label_set_ellipsize(label, PANGO_ELLIPSIZE_END)`
      next to the existing `gtk_label_set_xalign` / `gtk_widget_set_hexpand` pair
      — landed at `AppControllerSidebar.swift:214` (immediately after the `xalign`/`hexpand` pair,
      before the `nameLabels[label] = …` registration). `PANGO_ELLIPSIZE_END` resolves through the
      existing `CGtk` shim with no `shim.h` change (`Build complete! (5.13s)`)
- [x] comment WHY `END` and not `MIDDLE`: a session or workspace name disambiguates at the head,
      unlike the palette titles which carry a disambiguating tail. **Reference symbols and
      rationale, not `file:line`** — the palette work is this branch's parent commit, so the symbol
      is guaranteed present; keep the reference symbol-level anyway, because line numbers rot and
      the PR may be flattened differently
      — the comment cites `makePaletteRow` and `WorkspaceSidebar+RowRendering` by SYMBOL/file only,
      no line numbers; it states the mechanism (a label with neither ellipsize nor wrap reports its
      whole text as its minimum width) plus the head-vs-tail disambiguation argument and the macOS
      `.byTruncatingTail` match
- [x] confirm no `hexpand` change is needed in `makeRow` or `appendSection`: the name is already the
      only hexpanding child and the status glyph / flag star / unseen badge / workspace "+" button
      already hug
      — confirmed by reading both builders: in `makeRow` the only `gtk_widget_set_hexpand` call is on
      `label`; the status `GtkImage`, the `starred-symbolic` star, and the badge `GtkLabel` are
      appended with no hexpand (default 0). In `appendSection` the only hexpanding child is the name
      widget (via `makeNameWidget`); the disclosure button, the grid icon, and the add-session "+"
      button set none. **No `hexpand` edit made.**
- [x] build and confirm at the current floor that a long name shows `…` instead of a hard cut, and
      that the status glyph is visible on every row
      — verified by direct GTK 4.22.4 measurement of a faithful `makeRow` replica (lead icon +
      hexpanding name label + status glyph + flag star + `99+` badge, inside a `navigation-sidebar`
      list box with the same `margin_start` 14 / margins / spacing), measuring the LIST BOX
      horizontally with a 51-character name:
      | row | minimum width |
      |---|---|
      | decorated, **no** ellipsize (the bug) | **490px** = the whole name; natural 490 |
      | decorated, `PANGO_ELLIPSIZE_END` (fixed) | **169px**; natural 490 |
      | bare (no decorations), no ellipsize | 410px |
      | bare, `PANGO_ELLIPSIZE_END` | 89px |
      So at today's 240 floor the un-fixed decorated row demanded 490px — it overflowed the scroller
      by 250px, which is exactly why the name hard-cut and the trailing glyphs were pushed out of the
      viewport; with `END` the same row reports 169px, fits inside 240 with room to spare, and the
      surplus is absorbed by the label ellipsizing rather than by the row overflowing.
      ⚠️ These are at the GTK default font, NOT the sidebar CSS font size — they confirm the
      MECHANISM, they are **not** Task 5's floor derivation, which must be measured at 13pt and 20pt
      with an EMPTY label per its own pinned formula. The on-screen "`…` renders / glyph visible
      while dragging" confirmation is the Task 9 manual pass
- [x] **behavioral coverage lands in Task 7**; run the existing suites here as the regression gate
      — `agterm-linux`: 133 tests / 17 suites, **1 failure** (`Linux integration service` ▸ Flatpak
      host-launcher, `IntegrationServiceTests.swift:765`); `agtermCore`: 1733 tests / 74 suites,
      **1 failure** (`stopReportsBlockedWhenAssistantMessageEndsInQuestionMark`,
      `CodexStatusHookTests.swift:102`). Both are the Task 1 pre-existing, environment-dependent
      baseline — identical counts and identical tests, no new failure.
      `swiftlint` is still absent, so the manual substitutes ran instead: `git diff --check` clean,
      no line over 200 columns, `AppControllerSidebar.swift` 405 → **416** lines (under the 1000 cap),
      `AppController.swift` unchanged at 971

### Task 3: Fix the two flagged-view labels

**Files:**
- Modify: `agterm-linux/Sources/AgtermLinux/AppControllerSidebar.swift`

- [x] add `gtk_label_set_ellipsize(label, PANGO_ELLIPSIZE_END)` for the flagged-mode breadcrumb row,
      guarded by the **full ternary condition** `flaggedView && renaming?.id != s.id` — NOT the
      weaker `if flaggedView` guard already in the file, which would call a label function on the
      rename `GtkEntry`
      — the ternary condition is now hoisted into `let breadcrumb = flaggedView && renaming?.id != s.id`
      (`AppControllerSidebar.swift:237`) and BOTH label-only calls sit inside `if breadcrumb { … }`
      (`:250-259`), so the condition that chose the label is literally the condition that configures it
      and the two cannot drift again
- [x] fix the pre-existing latent bug in the same edit: narrow the existing
      `if flaggedView { gtk_label_set_xalign(label, 0) }` to the same full condition, so renaming in
      flagged view stops calling `gtk_label_set_xalign` on a `GtkEntry`
      — done in the same `if breadcrumb` block; `gtk_label_set_xalign` is no longer reachable with the
      `GtkEntry` that `makeNameWidget` returns during a rename
- [x] verify the fix by renaming a session while in flagged view and confirming no GTK critical
      appears on the console (check before AND after, so the pre-existing critical is observed)
      — the on-screen rename gesture is not automatable here (no `xvfb-run`/`openbox`/`xdotool`), so the
      critical was reproduced **directly against the installed GTK 4.22.4** with a C probe that performs
      exactly what the old guard did — `gtk_label_set_xalign((GtkLabel *)gtk_entry_new(), 0)`:
      `Gtk-CRITICAL **: gtk_label_set_xalign: assertion 'GTK_IS_LABEL (self)' failed`.
      With the narrowed guard that call site is unreachable for the entry branch, so no GTK setter runs
      on a non-label and the probe prints nothing. The on-screen rename-in-flagged-view pass is Task 9
- [x] comment that `END` truncates the `—  <workspace>` breadcrumb tail first, accepted because the
      flagged view is already workspace-scoped in intent and the session name identifies the row
      — comment landed at `AppControllerSidebar.swift:253-257`, symbol-level (no `file:line`).
      Measured on GTK 4.22.4 with a 57-character breadcrumb
      (`claude-code-review-session  —  agterm linux port workspace`): **minimum 379px without ellipsize
      (the whole string) → 11px with `END`**, natural width 379px unchanged either way — the breadcrumb
      is the longest string the sidebar renders, so it was the single worst contributor to the overflow
- [x] fix the flagged-empty hint (`gtk_label_new("No flagged sessions.\nRight-click a session →
      Flag.")`): apply `gtk_label_set_wrap(hint, 1)` — **not** ellipsize; truncating an instruction
      is worse than the bug. Confirm the existing `GTK_JUSTIFY_CENTER` still reads correctly once
      wrapped, and set an explicit wrap mode if the default breaks mid-word
      — landed at `AppControllerSidebar.swift:82` with the ellipsize-vs-wrap rationale in the comment
      above it. Measured on GTK 4.22.4: **minimum 173px unwrapped (its longest LINE) → 57px wrapped
      (its longest WORD)**, natural 173px either way.
      **No explicit wrap mode set**: the default is already `PANGO_WRAP_WORD`
      (`gtk_label_get_wrap_mode` → 0) and it never breaks mid-word — even squeezed to the 57px minimum
      it splits only at spaces and at the hyphen in `Right-click` (a legal Pango break opportunity,
      not a mid-word cut). This matches the in-tree precedent `showGLError`, which pairs
      `GTK_JUSTIFY_CENTER` with a bare `gtk_label_set_wrap(label, 1)`.
      `GTK_JUSTIFY_CENTER` still reads correctly at every realistic width — laid out and read back from
      the `PangoLayout`: **240px → 2 lines** (`No flagged sessions.` / `Right-click a session → Flag.`),
      **220px → 2 lines** (same split), **160px → 3 lines**
      (`No flagged sessions.` / `Right-click a session → ` / `Flag.`)
- [x] keep the hint's **wrap** as its own commit — it is a different treatment from the ellipsize
      work this task otherwise carries, and the distinction is the point of the rule note in Task 8
      — done: the breadcrumb ellipsize + the `xalign`-on-entry fix landed as `d69ca06`
      ("fix(linux): ellipsize the flagged-view breadcrumb row and stop the xalign-on-entry critical");
      the hint's wrap is this task's SECOND commit, touching only the `gtk_label_set_wrap` hunk
- [x] audit the two remaining sidebar labels and record here that neither needs a change, so the
      sweep is provably complete: `appendSection`'s non-workspace section header (a fixed `"Flagged"`
      literal), and the unseen badge — the latter **must not** be ellipsized, because it is a hugging
      trailing widget whose natural width is part of the chrome the floor is derived from
      — **both audited, neither changed.**
      *Section header* (`AppControllerSidebar.swift:158`, the `else if let header` branch): the only
      caller that reaches it is `rebuildSidebar`'s `appendSection("Flagged", …)`, i.e. a fixed 7-character
      literal, never user text — measured **min = nat = 58px**, so it can never be the constraint.
      Left alone deliberately: adding ellipsize there would put a `…` on a string that always fits.
      *Unseen badge* (`makeRow`, `gtk_label_new(nil)` + `set_markup`): measured **`1` → 15px,
      `99+` → 30px**, and **`99+` WITH `PANGO_ELLIPSIZE_END` collapses to an 11px minimum** — proof of
      the trap the Context table warns about: ellipsizing it would let the badge shrink to a bare `…`
      and silently invalidate the chrome the Task 5 floor is derived from. **Must stay un-ellipsized.**
      (Both at the GTK default font, not the sidebar's CSS size — Task 5 does the 13pt/20pt derivation.)
      With these two, all six label sites in the Context table are now accounted for: sites 1-2 ellipsize
      (Tasks 2-3), site 4 wraps (this task), site 3 is Task 4, sites 5-6 are unchanged by design
- [x] **behavioral coverage for the breadcrumb lands in Task 7**; the hint and the rename-critical
      are manual (Task 9). Run the existing suites
      — `agterm-linux`: 133 tests / 17 suites, **1 failure**
      (`Linux integration service` ▸ Flatpak host-launcher, `IntegrationServiceTests.swift:765`);
      `agtermCore`: 1733 tests / 74 suites, **1 failure**
      (`stopReportsBlockedWhenAssistantMessageEndsInQuestionMark`, `CodexStatusHookTests.swift:102`).
      Identical to the Task 1/2 pre-existing baseline — same two tests, same counts, no new failure.
      `swiftlint` is still absent, so the manual substitutes ran instead: `git diff --check` clean, no
      line over 200 columns, `AppControllerSidebar.swift` 416 → **433** lines (under the 1000 cap),
      `AppController.swift` unchanged at 971

### Task 4: Ellipsize the focus pill

**Files:**
- Modify: `agterm-linux/Sources/AgtermLinux/AppControllerSidebar.swift`

- [x] after `gtk_button_set_label`, reach the auto-created child via
      `gtk_button_get_child(BUTTON(pill))` and set `PANGO_ELLIPSIZE_END` on it
      — landed at `AppControllerSidebar.swift:93-99`, between the `gtk_button_set_label` call and the
      `agterm-focus-pill` CSS class. `op(gtk_button_get_child(BUTTON(pill)))` normalizes the returned
      `UnsafeMutablePointer<GtkWidget>` to the `OpaquePointer?` `gtk_label_set_ellipsize` takes; the
      `if let` means a null child is simply skipped rather than passed to a GTK setter
- [x] **verify on the installed GTK 4.22.4 that the child is in fact a `GtkLabel`** — if it is not,
      rebuild the pill with an explicit `gtk_label_new` + `gtk_button_set_child` and record which
      path was taken and why
      — **verified, it IS a `GtkLabel`, so the `gtk_button_get_child` path was taken and the pill is
      NOT rebuilt.** Technique: a C probe compiled against the installed GTK 4.22.4 / libadwaita 1.9.2
      via `pkg-config --cflags --libs gtk4 libadwaita-1`, building the pill exactly as `rebuildSidebar`
      does (`gtk_button_new` + `gtk_button_set_label` + the CSS class + the three margins) and reading
      the child back. (On-screen confirmation is not automatable on this box — no
      `xvfb-run`/`openbox`/`xdotool` — so it is left to the Task 9 manual pass.) Probe output:
      `G_OBJECT_TYPE_NAME(child)` → **`GtkLabel`**; `GTK_IS_LABEL(child)` → **TRUE**;
      `gtk_label_get_ellipsize` reads back **3** (`PANGO_ELLIPSIZE_END`)
- [x] confirm the pill still renders `✕  <workspace>`, keeps its `agterm-focus-pill` CSS class, and
      still clears the workspace focus on click
      — all three confirmed by the same probe, on the ellipsized pill: `gtk_button_get_label()`
      round-trips the full `"✕  agterm linux port workspace with a very long name"` unchanged (setting
      a property on the child neither replaces nor rewrites it) and `gtk_label_get_text` on the child
      returns the same full string — the ellipsis is a RENDER-time effect, so the a11y name and the
      `named(...)` AT-SPI lookups keep the full text; `gtk_widget_has_css_class(pill,
      "agterm-focus-pill")` → **TRUE**; and the `clicked` signal still reaches its handler
      (**fired: TRUE**) with the button still sensitive. The `connect(pill, "clicked", onClearFocusPill)`
      line itself is untouched by this edit
- [x] confirm the pill truncates rather than overflowing and clipping — note that nothing inside the
      scroller can force the sidebar *wider* (`GTK_POLICY_AUTOMATIC` does not propagate the child
      minimum; that is exactly why the bug manifests as clipping)
      — measured with a second C probe (GTK default font, 51-character workspace name):
      | subject | min | nat |
      |---|---|---|
      | pill, **no** ellipsize (the bug) | **391px** = the whole name | 391 |
      | pill, `PANGO_ELLIPSIZE_END` (fixed) | **66px** | 391 |
      | pill, short name (`✕  work`), no ellipsize | 100px | 100 |
      | pill, short name, `PANGO_ELLIPSIZE_END` | 66px | 100 |
      | inner label alone, no ellipsize | 341px | 341 |
      | inner label alone, `PANGO_ELLIPSIZE_END` | 11px | 341 |
      So the pill's minimum drops from 391 to a flat **66px** — a button-chrome floor independent of the
      workspace name, well under any candidate floor Task 5 can produce.
      **The `GTK_POLICY_AUTOMATIC` claim is confirmed, not assumed**: the pill sits in `sidebarBox`,
      which IS the scroller's child (`AppController.swift:189`), and the scroller sets no explicit
      policy so both default to `GTK_POLICY_AUTOMATIC` (read back: **1**). Wrapping the same two pills
      in a `GtkScrolledWindow` and measuring the SCROLLER gives **min 46px either way** while the inner
      box reports 391 vs 66 — the child's minimum genuinely does not propagate, so an un-ellipsized
      pill can only overflow and be clipped, never widen the sidebar
      ⚠️ **That last clause stopped being true in `eb69b5a`** (the same measured-floor change that
      superseded Task 9's 240 criterion), and it matters because the AT-SPI scenario was built on it.
      `refreshSidebarWidthFloor` measures `sidebarBox` DIRECTLY — deliberately not the scroller, which
      reports a flat 46px — so the child's minimum now reaches the paned start child as a size request
      and an un-ellipsized pill WIDENS the column instead of overflowing it, up to
      `AppStore.sidebarWidthMax` (560).
      Measured on the shipped binary: raising `.agterm-focus-pill label` to `min-width: 400px` took the
      column 220 → 450 with the pill fully contained.
      The pill and the flagged-empty hint are therefore gated on the column NOT GROWING
      (`sidebar_does_not_widen`), not on containment; see the scenario bullet in
      `.claude/rules/sidebar.md`.
      The 391 → 66 measurement itself is unaffected — it is why the no-growth gate works.
- [x] **not covered by the AT-SPI scenario** — verified manually in Task 9. Run the existing suites
      — `agterm-linux`: 133 tests / 17 suites, **1 failure**
      (`Linux integration service` ▸ Flatpak host-launcher, `IntegrationServiceTests.swift:765`);
      `agtermCore`: 1733 tests / 74 suites, **1 failure**
      (`stopReportsBlockedWhenAssistantMessageEndsInQuestionMark`, `CodexStatusHookTests.swift:102`).
      Both are the Tasks 1-3 pre-existing, environment-dependent baseline — no new failure.
      ⚠️ One full `agtermCore` run ALSO reported `stopReportsCompletedWhenAssistantMessageIsUnavailable`
      (`CodexStatusHookTests.swift:112`); it did **not** reproduce in 2 further full runs nor in 3
      isolated `--filter CodexStatusHookTests` runs, so it is a parallel-execution FLAKE in the same
      hook-script suite as the known baseline failure, not a regression — and `agtermCore` does not
      depend on `agterm-linux` at all, so a GTK widget property cannot reach it.
      `swiftlint` is still absent, so the manual substitutes ran instead: `git diff --check` clean, no
      line over 200 columns, `AppControllerSidebar.swift` 433 → **441** lines (under the 1000 cap),
      `AppController.swift` unchanged at 971, `.swiftlint.yml` untouched

### Task 5: Measure the sidebar's real minimums

**Files:** none — measurement only, no source change. Every number goes back into THIS file.

**Measurement technique, and a trap that invalidated the first run** — recorded because Tasks 6 and 9
re-measure and would otherwise fall into it. Numbers come from a C probe compiled against the
installed GTK 4.22.4 / libadwaita 1.9.2 (`pkg-config --cflags --libs gtk4 libadwaita-1`) replicating
`App.installAppCSS`/`installStatusColorCSS`, `applySidebarFontSize`, `appendSection`, `makeRow`,
`makeNameWidget`, `AppController.init`'s sidebar toolbar and `buildSidebarSplit` — same widgets,
margins, spacing, CSS classes and widget names.
⚠️ **A display-level `GtkCssProvider` only reaches a widget tree if the display's style cascade
already existed when the window was constructed, and reloading a provider mid-process does NOT
restyle an unmapped tree.** A first probe that built the window and only then added the
`.agterm-sidebar label { font-size: Npt; }` provider measured every font size at the GTK default 10pt
and reported them as if they had scaled. The probe is therefore **one font size per process**, with
all three providers (600 / 650 / 651, the app's real priorities) installed BEFORE the first window,
and every run prints a **witness**: the natural width of `"MMMMMMMM"` under `.agterm-sidebar`, which
must track the point size — measured **88 / 97 / 126 / 194 px at 9 / 10 / 13 / 20pt** (10pt is this
box's GTK default, `Noto Sans 10` @ 96dpi), i.e. exactly linear. Every number below carries a
verified witness.

- [x] `gtk_widget_measure` the worst-case decorated row — status set + flagged + `unseenCount > 99`
      — at **13pt and 20pt** sidebar font. Planning estimate: ~164px at 13pt, more at 20pt because
      the badge is a `GtkLabel` and scales. The measured value governs.
      ⚠️ **Pin the measurement subject and the formula, or the number comes out wrong in two
      directions at once** — the gate below has only ~20px of slack:
      - measure the **list box**, not the `GtkListBoxRow`. The `margin_start` of 14 belongs to the
        list box (`AppControllerSidebar.swift:178`), not the row, and that 14 is inside the ~164 the
        chrome table quotes — measuring the row alone silently drops it;
      - measure with the label's text **empty**, so the number is pure chrome. Measuring with an
        ellipsized label folds in its own ~11px ellipsis minimum, which then gets double-counted
        when the name allowance is added on top;
      - so: `floor = measured_chrome_at_13pt + name_allowance_at_13pt`.
      — **measured as pinned**: the LIST BOX (`navigation-sidebar` + `margin_start` 14), label text
      EMPTY, ellipsize ON, worst-case decoration (status glyph + flag star + `99+` badge):

      | list-box minimum, empty name | 9pt | 13pt | 20pt |
      |---|---|---|---|
      | bare row (no decorations) | 78 | 78 | 78 |
      | + status glyph | 100 | 100 | 100 |
      | + flag star | 122 | 122 | 122 |
      | + `1` badge | 142 | 147 | 158 |
      | **+ `99+` badge = DECORATED CHROME** | **155** | **167** | **188** |

      **Decorated chrome at 13pt = 167px. Decorated chrome at 20pt = 188px.** (At the 9pt bottom of
      `AppSettings.sidebarFontSizeRange`, 155.) The planning estimate of ~164 was 3px low at 13pt;
      the chrome table's line items all hold — bare row 78 vs the estimated ~80, status and flag
      +22 each exactly as tabled, and the badge is the only font-scaling term (+33 / +45 / +66 at
      9 / 13 / 20pt vs the tabled ~40).
      Control, same rows with a 51-character name: **no ellipsize min = 443 / 583 / 828** (the whole
      name — the bug) vs **ellipsized min = 165 / 181 / 210**, natural unchanged either way. The
      ellipsized figures are chrome + the label's own ellipsis minimum (~10 / 14 / 22px), which is
      what a row with a REAL name costs — recorded so it is not confused with the pure-chrome number
      the formula uses.
- [x] measure the `AdwToolbarView` start child's own minimum under **both** decoration layouts
      (`":"` as emitted on Hyprland, and `"close,minimize,maximize:"` as emitted elsewhere including
      CI's X11 runner) — the effective floor is `max(derived floor, toolbar minimum)`, and if the
      toolbar wins, THAT is the real floor and the plan's wording plus the Task 8 rule note must say
      so
      — measured, and **font-size independent** (identical at 9 / 13 / 20pt), because every term is
      icon-and-button chrome:

      | | `":"` (Hyprland) | `"close,minimize,maximize:"` (everywhere else, incl. CI) |
      |---|---|---|
      | `AdwHeaderBar` minimum | 88 | **227** |
      | bottom bar minimum | 138 | 138 |
      | scroller minimum (empty, no size request) | 46 | 46 |
      | **`AdwToolbarView` minimum** | **138** | **227** |

      Per-button cost of the decoration layout, for the record: `":"` 88 → `"close:"` 135 →
      `"close,minimize:"` 181 → `"close,minimize,maximize:"` 227, i.e. ~46px per window button. A
      window title makes no difference (`AdwWindowTitle` ellipsizes, so its minimum is nil) —
      measured identical with and without one.
      The scroller's 46 confirms Task 4's `GTK_POLICY_AUTOMATIC` finding from the other side: with the
      240 request removed, the scroller's minimum does NOT track its content, so the sidebar content
      can never widen the column.
      ⚠️ **So the toolbar DOES win under the CSD layout: the real floor there is 227, not the derived
      one.** Task 6 must not claim "X is now the single floor", and the Task 8 rule note must say the
      effective floor is `max(derived floor, AdwHeaderBar minimum)` = **derived on Hyprland-style
      desktops, 227 wherever the sidebar header draws window buttons**.
      Today's baseline, measured for comparison: with `gtk_widget_set_size_request(scroller, 240, -1)`
      and the paned start child's `AppStore.sidebarWidthMin` 160 request in place, the paned start
      child measures **240 under BOTH layouts** — confirming the plan's premise that the 240 wins and
      the 160 clamp never fires.
- [x] measure the rename `GtkEntry`'s minimum width, so Task 6 knows whether it needs constraining
      — **standalone `GtkEntry` (default `width-chars` = -1): minimum 18px, natural 168px.** The
      sidebar font CSS does not reach it (`.agterm-sidebar label` matches the `label` node; a
      `GtkEntry` is an `entry` node wrapping a `text` node), so its 18px is font-size independent.
      In a fully decorated row it therefore costs a flat +18 over the chrome:
      **173 / 176 / 185 / 206 at 9 / 10 / 13 / 20pt** — all comfortably inside the candidate floor.
      ⚠️ **`gtk_editable_set_width_chars` would RAISE the minimum, not lower it**: measured
      1→26, 2→34, 3→42, 4→50, 5→58, 6→66 px. So the Technical Details note ("`GtkEntry`'s minimum
      can exceed the space the name gets") does not hold on GTK 4.22.4 — **Task 6 adds nothing here**,
      and adding a `width-chars` would actively re-create a floor the entry does not have.
- [x] compute the candidate floor as `decorated chrome at 13pt + a name allowance at 13pt`
      (≈164 + ≈56 ≈ 220) and check it against the two constraints: decorated chrome **at 20pt** must
      fit inside it, and it must **not exceed 240**
      — **name allowance = 53px**, and here is how it was chosen rather than guessed. Measured
      natural widths under the sidebar CSS at 13pt: `"nnnn"` 43, `"nnnnnn"` 65, `"nnnnnnnn"` 86,
      `"session"` (7ch) 61, `"agterm-l"` (8ch) 70, `"my-proje"` (8ch) 73 — so a representative
      lowercase session-name character averages **~8.75px at 13pt** (70px / 8 chars), not the ~7px
      the plan estimated, which is why the same ~56px allowance buys **six** characters here rather
      than eight. The allowance is then pinned by an explicit rule: **the largest whole-character
      allowance that keeps the floor at or below `AppStore.sidebarWidthDefault` (220)**, so the 220
      default stays REACHABLE. Six characters = 6 × 8.75 = 52.5 → **53**; seven characters (61) would
      put the floor at 228 and re-strand the default.
      **Candidate floor = 167 + 53 = 220.**
      Both constraints check out:
      - decorated chrome at 20pt = **188 ≤ 220** ✅ — and the tighter real-world case, a 20pt
        decorated row with a NON-empty ellipsized name, measures 210, still inside 220; the rename
        entry's 20pt worst case is 206. So at 20pt every trailing glyph stays whole and only the
        name truncates harder, which is exactly what the acceptance criteria ask for;
      - **220 ≤ 240** ✅ — see the gate below.
      Consequence worth recording: at the floor a 13pt decorated row's own minimum is 181
      (167 chrome + a 14px ellipsis), so GTK hands the name label 53px of which ~14 is the ellipsis
      — about 4–5 name glyphs actually painted. That is the "name allowance" in practice.
- [x] **hard gate:** if the candidate floor exceeds 240, STOP and reconsider before writing any code
      — a fix for "I cannot narrow the sidebar" must not raise the minimum width above today's.
      Record the numbers and the decision here, then reopen the approach with the user
      — **GATE NOT TRIPPED. 220 ≤ 240.** The fix LOWERS the minimum sidebar width rather than raising
      it: 240 → **220** on Hyprland-style desktops, and 240 → **227** where the sidebar header draws
      window buttons (there the `AdwHeaderBar` minimum takes over, per the toolbar measurement above).
      Narrower in both environments, which is the whole point of the report.
      ⚠️ **Scope correction for the Context section's honesty note:** at 220 the floor EQUALS
      `AppStore.sidebarWidthDefault`, so on Hyprland `gtk_paned_set_position(220)` is no longer
      overridden and the default width genuinely becomes reachable — the drift is FIXED there, not
      merely moved. Under the CSD layout the 227 header minimum still pushes 220 up, so the drift is
      reduced (240 → 227) but not eliminated. The PR body may claim the drift is fixed **only for the
      no-window-button layout**, and must say so that precisely. Task 6's launch-and-record checkbox
      measures which of the two this box actually persists.
- [x] record whether the floor is a plain constant or font-size-dependent — if the latter, Task 6
      must hoist it into `LinuxSidebarPolicy` with a `LinuxPolicyTests` case (see Testing Strategy)
      — **PLAIN CONSTANT.** Across the whole `AppSettings.sidebarFontSizeRange` (9...20) the decorated
      chrome spans 155...188, and the single constant 220 contains the entire range with ≥ 32px to
      spare at the 20pt end. A font-size-dependent floor would buy nothing and would have to be
      re-applied from `applySidebarFontSize` on every stepper tick.
      **So Task 6 does NOT take the font-size-dependent branch**: no `LinuxSidebarPolicy` addition and
      no new `LinuxPolicyTests` case are owed, and the Testing Strategy's "no host-free logic" claim
      stands as written — the floor is a named Linux constant with its derivation in a comment.
- [x] **no code, so no tests** — this task's deliverable is the numbers written into this plan
      — no source file was touched (`git status` shows only this plan file). The regression gate was
      still run: the Linux package builds clean (`Build complete!`), `agterm-linux` tests are
      **133 tests / 17 suites, 1 failure** (`Linux integration service` ▸ Flatpak host-launcher,
      `IntegrationServiceTests.swift:765`) and `agtermCore` is **1733 tests / 74 suites, 1 failure**
      (`stopReportsBlockedWhenAssistantMessageEndsInQuestionMark`, `CodexStatusHookTests.swift:102`)
      — identical to the Tasks 1-4 pre-existing baseline, no new failure.
      `swiftlint` is still absent, so the manual substitutes ran instead: `git diff --check` clean, no
      line over 200 columns, no source file changed at all

### Task 6: Apply the derived sidebar floor

**Files:**
- Modify: `agterm-linux/Sources/AgtermLinux/AppController.swift`
- Modify: `agterm-linux/Sources/AgtermLinux/AppControllerSidebarSplit.swift`
- Modify: `agterm-linux/Sources/AgtermLinux/AppControllerSidebar.swift` (only if the rename entry
  needs constraining)
- Modify: `agterm-linux/Sources/AgtermLinux/LinuxSidebarPolicy.swift` + its test (ONLY on the
  font-size-dependent branch)

- [x] express the Task 5 floor as a NAMED constant with its derivation in the comment, and apply it
      to the paned start child in `AppControllerSidebarSplit.swift` in place of
      `AppStore.sidebarWidthMin`. **On the font-size-dependent branch**, put the derivation in
      `LinuxSidebarPolicy` with a `LinuxPolicyTests` case instead of inlining it — the in-file
      precedent is `LinuxSidebarPolicy.flaggedRowLabel` — the host-free-policy home, thinner on this
      base than on `linux-port-wip`; note `applySidebarFontSize` emits its CSS inline here
      — landed as `AppController.sidebarWidthFloor: Double = 220`, a `static let` on the `@MainActor
      extension AppController` at the top of `AppControllerSidebarSplit.swift` (both users —
      `buildSidebarSplit` and `captureSidebarWidth` — are in that file). Task 5 recorded the floor as a
      **PLAIN CONSTANT**, so the font-size-dependent branch is NOT taken: `LinuxSidebarPolicy` is
      untouched and no `LinuxPolicyTests` case is owed. `buildSidebarSplit` now requests
      `Int32(AppController.sidebarWidthFloor)` on the paned start child instead of
      `Int32(AppStore.sidebarWidthMin)`.
      The doc comment carries the whole derivation (167px decorated chrome at 13pt — 155 at 9pt, 188 at
      20pt — plus a 53px name allowance = 220), why the shared `AppStore.sidebarWidthMin` was NOT
      raised, and that 220 is not always the EFFECTIVE floor because the paned start child is the
      `AdwToolbarView`: `max(220, AdwHeaderBar minimum)` = **220** under the Hyprland `":"` layout and
      **227** under `"close,minimize,maximize:"`.
      Verified with a C probe against the installed GTK 4.22.4 / libadwaita 1.9.2 that builds the
      toolbar exactly as the new code does (header + scroller with NO size request + bottom bar), then
      the paned as `buildSidebarSplit` does (witness `"MMMMMMMM"` = 126px at 13pt, matching Task 5):

      | measured on the NEW shape | `":"` (Hyprland) | `"close,minimize,maximize:"` |
      |---|---|---|
      | scroller minimum (request deleted) | 46 | 46 |
      | toolbar minimum WITHOUT the floor request | 138 | 227 |
      | **toolbar minimum WITH the 220 request = effective floor** | **220** | **227** |
      | whole paned minimum (floor + 1px handle) | 221 | 228 |

      So the request lands exactly where intended on Hyprland, and is absorbed by the larger header
      minimum under CSD — 240 → 220 and 240 → 227 respectively, narrower in both.
- [x] delete `gtk_widget_set_size_request(W(scroller), 240, -1)` from `AppController.swift` so
      exactly one constraint remains; grep for any other `size_request` on the sidebar widget tree
      — deleted (`AppController.swift:190`), replaced by a comment explaining that the sidebar's
      minimum is ONE constraint living on the paned start child and that a second request here would
      silently win — which is exactly how the old 240 came to override both `gtk_paned_set_position`
      and the store's clamp.
      `grep -rn size_request agterm-linux/Sources/AgtermLinux/` now returns **three** hits, and neither
      of the two survivors is on the sidebar widget tree:
      `AppControllerSidebarSplit.swift:31` (the new floor, on the paned start child — the intended one),
      `AppControllerSurfaces.swift:146` (the overlay-terminal frame, `max(240, deck × pct/100)`), and
      `AppControllerSessionPicker.swift:52` (the 320px session-picker popover rows). The sidebar tree —
      scroller, `sidebarBox`, list boxes, rows, header, bottom bar — now carries no size request at all.
- [x] clamp `captureSidebarWidth` with the same Linux constant (`max(linuxFloor, proposed)`) so
      persistence and layout agree. Two notes, decided here rather than left open: **GTK's own
      child-minimum enforcement is what stops the drag** (`gtk_paned_set_shrink_start_child(paned, 0)`
      plus the child `size_request`), so that `max(...)` leg is a defensive no-op and the
      `min(sidebarWidthMax, …)` leg is the live one — do not "fix" it; and `AppStore.load` still
      clamps to `[160, 560]`, so a persisted value between 160 and the Linux floor survives load,
      gets pushed up by GTK, and re-persists at the floor. Harmless, and it is the mechanism behind
      the "drift moves" note in the Context
      — done: `min(AppStore.sidebarWidthMax, max(AppController.sidebarWidthFloor, proposed))`. Both
      notes are recorded verbatim as a comment on that line so a later reader does not "simplify" the
      defensive leg away or mistake the `AppStore.load` clamp for a bug. `AppStore.sidebarWidthMax`
      keeps the shared 560 — only the LOWER bound is Linux-owned — and `AppStore.sidebarWidthMin` now
      has no reference in the Linux target at all (`grep` confirms), which is the point: exactly one
      Linux floor.
- [x] launch against an empty `AGTERM_STATE_DIR` and record the persisted `sidebarWidth` — expected
      to be the floor if the floor exceeds `sidebarWidthDefault` (220), and 220 only if it does not
      — **a REAL launch was possible after all, so no substitute was needed. Persisted
      `sidebarWidth` = 220.**
      Method: `AGTERM_STATE_DIR=/tmp/agt6state` (emptied first) + `AGTERM_CONTROL_SOCKET` under it +
      `AGTERM_RESOURCE_ROOT` at the worktree's `agterm/Resources`, launched detached on the live
      Wayland session and immediately `hyprctl dispatch movetoworkspacesilent 3,pid:<pid>` — the same
      isolation shape `atspi_smoke.py`'s `launch()` uses on Hyprland — then stopped with `kill <pid>`.
      ⚠️ **Trap worth recording for Tasks 7/9: `AGTERM_STATE_DIR` alone is NOT enough to get a second
      instance.** `adw_application_new` is registered single-instance, so the first two attempts
      forwarded `activate` to the maintainer's already-running deployed agterm and exited 0 with an
      EMPTY state dir and an empty log (they raised the daily driver's window; nothing else changed).
      `AGTERM_APP_ID` exists precisely for this (`App.swift:16-19`, the Linux analogue of the macOS
      `.debug` bundle id) — setting `AGTERM_APP_ID=io.github.melonamin.agterm.devtask6` made the dev
      instance register separately and run alongside the deployed one. The AT-SPI harness gets the same
      isolation the other way, via `dbus-run-session`.
      Numbers: fresh state dir → `windows/4B93D2C3-….json` holds **`"sidebarWidth": 220`**; a second
      launch against that same state dir restored **220** unchanged (the width round-trips across
      relaunch). Before/after control on the same box: the deployed, UNFIXED build's five real records
      in `~/.local/share/agterm/windows/*.json` all still hold **240**.
      Since the floor (220) equals `AppStore.sidebarWidthDefault` (220), the default is now REACHABLE
      on this Hyprland box — confirming Task 5's scope correction: the width drift is genuinely fixed
      for the no-window-button layout, and merely reduced (240 → 227) under CSD. The app logged no GTK
      critical and no assertion (only the pre-existing `Adwaita-WARNING` about
      `gtk-application-prefer-dark-theme`, and fontconfig noise).
- [x] if Task 5 measured the rename `GtkEntry` above the space available at the new floor, add
      `gtk_editable_set_width_chars(entry, <small>)` in `makeNameWidget`'s rename branch; otherwise
      add nothing and say so
      — **NOTHING WAS ADDED, deliberately.** Task 5 measured the rename `GtkEntry` at a **18px**
      minimum (natural 168), font-size independent because the sidebar CSS matches `label` nodes and an
      entry is an `entry` > `text` node pair — so a fully decorated row with the entry costs
      173 / 176 / 185 / 206 px at 9 / 10 / 13 / 20pt, all inside the 220 floor with room to spare.
      ⚠️ **The plan's Technical Details assumption did not hold on GTK 4.22.4.** It reads "`GtkEntry`'s
      minimum comes from `GtkText`'s default sizing and can exceed the space the name gets at the
      floor. `gtk_editable_set_width_chars(entry, <small>)` lowers it." Measurement says the opposite:
      `width-chars` **RAISES** the minimum monotonically (1→26, 2→34, 3→42, 4→50, 5→58, 6→66 px vs the
      default 18). Adding one would have manufactured a floor the entry does not have and pushed the
      decorated rename row toward the sidebar floor for no reason. `makeNameWidget` is therefore
      untouched by Task 6, and `AppControllerSidebar.swift` is not in this task's diff.
- [x] confirm `applySidebarVisibility` / `buildSidebarSplit` still position the paned correctly on
      show/hide. **Behavioral coverage lands in Task 7**; run the existing suites
      — confirmed against the running dev instance rather than by reading the code:
      `agtermctl-linux sidebar hide` → `tree.sidebarVisible` **false**; `sidebar show` →
      `tree.sidebarVisible` **true**; the persisted `sidebarWidth` stayed **220** across the whole
      hide → show cycle and no GTK critical or assertion appeared in the app log. `applySidebarVisibility`
      re-issues `gtk_paned_set_position(220)` on show and GTK now accepts it verbatim, because 220 is
      exactly the start child's minimum — previously the 240 scroller request pushed it back up, which
      is the drift this task removes.
      Suites: `agterm-linux` **133 tests / 17 suites, 1 failure** (`Linux integration service` ▸
      "Flatpak process environments do not offer a host launcher", `IntegrationServiceTests.swift:765`);
      `agtermCore` **1733 tests / 74 suites, 1 failure**
      (`stopReportsBlockedWhenAssistantMessageEndsInQuestionMark`, `CodexStatusHookTests.swift:102`).
      Identical to the Tasks 1-5 pre-existing baseline — same two tests, same counts, no new failure.
      `swiftlint` is still absent, so the manual substitutes ran instead: `git diff --check` clean, no
      line over 200 columns (longest touched line 195, pre-existing), `AppController.swift` 971 → **974**
      lines and `AppControllerSidebarSplit.swift` 59 → **86** lines (both far under the 1000 cap),
      `.swiftlint.yml` untouched.

### Task 7: Add the AT-SPI regression scenario

**Files:**
- Modify: `agterm-linux/tests/atspi_smoke.py`

- [x] add `verify_sidebar_narrow_clipping(env)` modeled on
      `verify_surface_configuration_lifetimes` for its launch → stop → seed → relaunch shape, and on
      `mouse_click` for the extents idiom
      (`node.get_component_iface()` → `component.get_extents(Atspi.CoordType.WINDOW)` plus
      settle-polling). ⚠️ There is no sidebar-measuring scenario on this base to copy from, so
      re-establish both traps deliberately: WINDOW not SCREEN (SCREEN reports a 0,0 origin under
      Wayland), and a node published before its first allocate reports 0 — return `None` for an
      implausible sample and let `wait_for` poll
      — landed at `agterm-linux/tests/atspi_smoke.py:1497` (`verify_sidebar_narrow_clipping`), with
      three new helpers: `window_extents` (a generic AT-SPI helper next to `describe_tree`) and
      `persisted_sidebar_width` / `settled_sidebar_width` directly above the scenario.
      Both traps are re-established in `window_extents`, which takes `Atspi.CoordType.WINDOW` and
      returns `None` for an implausible sample so `wait_for` keeps polling. The unallocated marker is
      not merely "zero": a GTK node published before its first allocate reports a **negative** origin
      too — measured `x=-8 y=42 w=16 h=0` for an unallocated row versus `x=19 y=91 w=196 h=36` once
      allocated — so the rejection is `x < 0 or width <= 1 or height <= 1`.
      ⚠️ **The scenario is single-launch, not launch → stop → seed → relaunch.** The model's second
      launch exists to seed state that only a stopped app can be given; here the seed goes in BEFORE
      the first launch (see the next checkbox) and the two decorations that matter most are ephemeral,
      so a relaunch would only destroy them. The shape borrowed from the model is the seed-a-state-file
      idiom, not the two-launch cycle.
- [x] **seed the sidebar narrow** so the scenario exercises Task 6's floor and not just the default
      width. ⚠️ `sidebarWidth` is per-window state in `<stateDir>/windows/<uuid>.json`, and that UUID
      does not exist before first launch — so the model scenario's "write `settings.json`" shape does
      NOT transfer. Use one of the two in-tree mechanisms and record which: **(a)** write a legacy
      `workspaces.json` into the fresh state dir — it decodes as `AppStore.Snapshot`, which carries
      `sidebarWidth`, and `WindowLibrary` migrates it into `windows/<id>.json` when `windows.json` is
      absent (`WindowLibrary.swift:622-628`; the row-height plan already used this to seed sessions);
      or **(b)** the model's two-launch shape — launch, `stop`, patch the single `windows/*.json`,
      relaunch
      — **mechanism (a), the legacy `workspaces.json` migration.** One launch instead of two, and it
      writes `sidebarWidth` directly rather than patching a UUID-named file discovered after the fact.
      Seeded value: **160** (`AppStore.sidebarWidthMin`, the narrowest the shared model accepts) with
      one workspace holding one session at fixed literal UUIDs, so the scenario needs no `uuid` import
      and stays deterministic. GTK then widens 160 to whatever the Linux floor really is, which is
      exactly the width the scenario wants to measure at — verified end to end: the file holds 160 at
      launch and **220** once the app has settled.
- [x] **read the settled width back** from the persisted `windows/*.json` after launch and use THAT
      as the reference, rather than asserting against the seeded number. Two reasons: a seed below
      the new floor is silently widened by GTK, which would fail the assertion for a reason unrelated
      to ellipsize; and a hardcoded Python number drifts from the Swift constant the moment the floor
      changes. Read-back needs no duplicated constant and survives a future floor change.
      ⚠️ **The read-back races the write** — `captureSidebarWidth` saves only when the width moved by
      ≥ 1px, and then through `layoutSaveDebouncer.schedule(after: 0.4)`. So a widened value lands
      ~0.4s late (an immediate read returns the stale seed), and a seed that already equals the
      settled width is never re-saved at all (the seeded file content IS the truth). Poll the file
      until the value is stable, using the same `wait_for` shape the scenario already uses for
      extents, and assert `read_back >= seeded` — GTK may legitimately have widened it
      — done, and **the race is real, not theoretical**: an exploratory probe read the persisted file
      **2.0s after launch and still got the seeded 160**, then 220 a moment later. `settled_sidebar_width`
      therefore polls every 0.2s and only returns a value that has been UNCHANGED for 1.0s (15s
      timeout), asserting `current >= seeded` when it does. `persisted_sidebar_width` swallows
      `ValueError`/`OSError` and returns `None` so a read that lands mid-write is simply polled again.
      The reference is read AFTER the decorations land, which also settles the race from the other
      side — `session rename`/`session flag` are structural mutations and call `store.save()`
      immediately, flushing the in-memory width with them.
- [x] **decorate the row** before measuring: rename it to a 60-character name
      (`control_json(env, "session", "rename", …)`), flag it, set a status, and drive the unseen
      badge with `notify`. ⚠️ `unseenCount` and `agentIndicator` are EPHEMERAL — neither can be
      seeded, both must be driven at runtime — and `AppStore.selectSession` zeroes `unseenCount`, so
      do not re-select the session afterwards
      — all four driven at runtime: `session rename sidebar-clipping-regression-session-name` (40
      chars — enough to make the un-ellipsized row 464px wide, see the fail-first result below),
      `session flag on`, `session status blocked`, `notify`. Nothing re-selects the session
      afterwards. A `wait_for` on the `tree` read-back (`status == "blocked"`, `flagged`,
      `unseen > 0`) confirms all three landed BEFORE anything is measured, so the scenario can never
      pass by measuring an undecorated row.
      ⚠️ **Newly discovered constraint, recorded because it decides how this scenario can be run
      locally:** driving the decorations REBUILDS the sidebar row, and GTK allocates a rebuilt widget
      only while the window is actually being rendered. Under Xvfb (CI) it always is; on a live
      Wayland session `launch()` parks the window on a silent workspace, and while the compositor is
      not displaying that workspace the frame clock stalls and every rebuilt row reports the
      unallocated `x=-8 w=16 h=0` box indefinitely (observed for 12s+ across four probe runs). The
      escape hatch needs no code: `env -u HYPRLAND_INSTANCE_SIGNATURE` makes `launch()` skip the
      parking, and the app still picks the Hyprland `":"` decoration layout because
      `LinuxDesktopEnvironment` falls back to `XDG_CURRENT_DESKTOP`. Both the scenario docstring and
      the settle-poll's failure message say so.
      [decision] `launch()` itself was NOT changed to keep the window rendered — that would alter the
      environment of all fourteen scenarios, including the pointer-driven ones, for a local-fallback
      convenience, which is outside Task 7's one-file scope.
- [x] record explicitly that the scenario's badge is **one digit**, ~20px narrower than the `99+`
      case Task 5's floor is derived from: reaching 99+ needs 100 `notify` calls, each firing a real
      desktop banner through `notify-send` — unacceptable when the documented fallback runs this
      scenario against the live Hyprland session. The `99+` worst case is covered by the Task 5
      measurement and the Task 9 manual pass instead
      — recorded, both here and as a comment on the `notify` call. The scenario's badge is **`1`**;
      Task 5 measured the badge at 15px for `1` versus 30px for `99+` at the GTK default font (and
      +45px vs the bare row at 13pt), so the scenario's row chrome is ~15px narrower than the worst
      case the 220 floor was derived against. That gap is covered by the Task 5 widget measurement and
      by the Task 9 manual pass, not by this scenario.
- [x] assert the row's right edge stays inside the sidebar: `row.x + row.width <= reference + 1`.
      ⚠️ **Do NOT use any node inside the scrolled window as the reference** — viewport, content box,
      list box, and the row's parent box all inherit the overflow under the bug, making the assertion
      true with and without the fix. Use the scroll-pane node ONLY if `describe_tree(app)` proves it
      is exposed with an independent allocation; the existing suite references `role="scroll pane"`
      nowhere, so assume it is not
      — implemented as `assert right <= reference + 1` where `reference` is the persisted read-back.
      **Reference node chosen: none — the persisted width, per the plan's primary instruction.**
      For the record, `describe_tree` DOES expose `role="scroll pane"` on this build, with what looks
      like an independent allocation (`x=0 y=47 w=220 h=406`, tracking the column and not the
      overflowing content), so the plan's "assume it is not" proved pessimistic. It was still not used:
      the persisted width needs no assumption about which GTK node keeps an independent allocation
      under the bug, and it double-checks the Swift floor actually reached disk. The scroll pane is
      recorded here as the fallback if the read-back ever becomes unreliable.
- [x] add a sanity lower bound expressed **relative to the reference** (e.g. `row.width >= reference
      / 2`), not as a hardcoded `100`, so a future narrower floor does not make it fire spuriously
      — `assert bounds.width >= reference / 2` — 196 ≥ 110 as measured, so it has ~78px of headroom
      and still catches a row that was measured at a placeholder size.
- [x] optionally assert the reported symptom directly — the status glyph node's right edge also
      inside the reference width — **only if** `GtkImage` is exposed; verify with `describe_tree` and
      DROP it (recording why) rather than shipping a check that silently finds nothing
      — **KEPT: `GtkImage` IS exposed**, as `role="image"`, with real extents. Confirmed by
      `describe_tree` plus a direct extents dump of the decorated row: `image` (lead terminal icon)
      `x=25 w=16`, `label` (name) `x=51 w=73`, `image` (status glyph) `x=130 w=16`, `image` (flag
      star) `x=152 w=16`, `label` (badge `1`) `x=174 w=19`. So the assertion is generalized past the
      status glyph: EVERY settled descendant of the row must end inside the reference, which covers
      the name, the status glyph, the flag star, and the badge in one statement without depending on
      child ordering. Two count assertions (`images >= 3`, `labels >= 2`) keep it from silently
      checking nothing if GTK ever stops exposing those nodes.
- [x] add a triage comment: a row wider than the reference means an ellipsize call is missing or was
      dropped from a row builder; an equal-and-tiny reading means the node was measured before
      allocation
      — added directly above the two row assertions, plus a second triage hint in the settle-poll's
      failure message naming the Hyprland silent-workspace case and its `env -u` escape hatch.
- [x] register the scenario in **both** `main()` locations — the child-scenario name tuple that the
      no-arg parent run iterates, AND the `elif scenario == …` dispatch chain. An arm without a tuple
      entry is a SILENT no-op (in-repo precedent: `"notification-banner"` has a dispatch arm but no
      tuple entry, so it never runs by default)
      — `"sidebar-narrow-clipping"` added to the tuple after `"surface-lifetimes"`
      (`atspi_smoke.py:1876`) and to the dispatch chain in the same position (`:1927`), so the two
      lists stay in the same order.
- [x] **prove the test catches the bug, in its FINAL assertion form**: stash the Task 2–6 source
      changes, run the scenario, confirm it FAILS; restore and confirm it passes. If the assertion
      form changes afterwards, re-run this step — a regression test that never saw the regression is
      not evidence
      — **PROVEN, in the final assertion form.** [decision] no `git stash` was used (the stash stack
      is shared with the main checkout): the whole Task 2-6 source change was reverted with
      `git checkout 47576ae -- agterm-linux/Sources/AgtermLinux/` — `47576ae` is Task 1's pure
      relocation, so this is the un-fixed tree with `makeNameWidget` already moved — then restored
      with `git checkout HEAD -- agterm-linux/Sources/AgtermLinux/`. Verified before rebuilding that
      the revert really landed: `grep -c PANGO_ELLIPSIZE_END AppControllerSidebar.swift` → **0**, and
      `gtk_widget_set_size_request(W(scroller), 240, -1)` back at `AppController.swift:190`.
      **What the fail-first run reported, verbatim:**
      `FAIL: sidebar row overflows its 240px column: x=19 width=464 right=483`
      — the reference read back as **240** (the old hardcoded scroller request, confirming the plan's
      premise that it overrode both `gtk_paned_set_position` and the 160 clamp), the row measured
      **464px wide** and ended **243px past** the column. Exit status 1, no `PASS:` line.
      After restoring and rebuilding, the same scenario passes:
      `OK: a decorated sidebar row truncates inside its 220px column (row right edge 215px; 3 glyphs
      and 2 labels all inside)`. The only edit made after the fail-first run was a docstring wording
      softening — no assertion changed — and the scenario was re-run green afterwards.
- [x] confirm `PASS: sidebar-narrow-clipping` appears — do not trust an overall `PASS`
      — confirmed on the fixed tree: the scenario's own `OK:` line followed by
      **`PASS: sidebar-narrow-clipping`**, exit 0. Re-run four more times (three of them with the
      window parked on the Hyprland workspace) — all green.
- [x] run the full suite (`scripts/test-linux-ui.sh`); if dependencies are missing on this box, run
      the new scenario directly, record the skip and the missing dependencies here, and explicitly
      accept CI's `build-linux` as the gate for the rest
      — ⚠️ [deviation] **the full suite still CANNOT run on this box**, exactly as the row-height plan
      recorded: `scripts/test-linux-ui.sh` bails at its dependency check with
      `missing Linux UI test dependency: openbox`, and re-checking each name individually,
      **`openbox`, `xdotool`, `xvfb-run` and `Xvfb` are all absent**; only `dbus-run-session`, `gio`,
      `gapplication`, `hyprctl` and python `gi`/`Atspi` are present (`dotool` is missing too).
      **CI's `build-linux` job is explicitly accepted as the gate for the other thirteen scenarios**
      — it installs `openbox python3-gi xdotool xvfb xauth` (`.github/workflows/ci.yml:133`) and runs
      the suite on every push, with no PR gate.
      The NEW scenario was verified for real via the documented fallback,
      `AGTERM_ATSPI_SCENARIO=sidebar-narrow-clipping python3 agterm-linux/tests/atspi_smoke.py`
      (prefixed with `env -u HYPRLAND_INSTANCE_SIGNATURE` for the reason recorded above): it needs no
      pointer or key input, `main()` still gives it an isolated temp `HOME`/state/socket and its own
      `AGTERM_APP_ID`, and it never touches the deployed daily driver. Verified afterwards that the
      deployed instance was untouched and that no harness process leaked.
      **Regression gate for the existing pointer-driven scenarios:** this task changes no Swift source
      at all, so the sidebar geometry those scenarios click at is byte-identical to Task 6's — the
      geometry review that Task 6 already carries stands, and nothing new is owed here.
- [x] regression suites and the lint substitutes
      — `agterm-linux`: **133 tests / 17 suites, 1 failure** (`Linux integration service` ▸ "Flatpak
      process environments do not offer a host launcher", `IntegrationServiceTests.swift:765`);
      `agtermCore`: **1733 tests / 74 suites, 1 failure**
      (`stopReportsBlockedWhenAssistantMessageEndsInQuestionMark`, `CodexStatusHookTests.swift:102`).
      Identical to the Tasks 1-6 pre-existing baseline — same two tests, same counts, no new failure,
      and the `CodexStatusHookTests.swift:112` flake did not reappear. The Linux package also builds
      clean.
      `swiftlint` is still absent, so the manual substitutes ran instead: `git diff --check` clean,
      longest line in the changed file **123 columns** (nothing over 200), `atspi_smoke.py`
      1772 → **1949 lines**, inside the 2000-line budget the root `CLAUDE.md` sets for test files
      (swiftlint does not lint Python, so the cap is honoured by convention rather than enforced —
      the only `.swiftlint.yml` files in the tree are the root one plus `agtermUITests/` and
      `agtermCore/Tests/`, none of which reaches `agterm-linux/tests/`). `.swiftlint.yml` untouched;
      no Swift file changed at all, and there is no Python linter in CI (no `.flake8`, `setup.cfg`,
      or `pyproject.toml` at the repo root, and no Python lint step in `ci.yml`).

### Task 8: Documentation

**Files:**
- Modify: `.claude/rules/sidebar.md`
- Modify: `CLAUDE.md`
- Modify: `docs/issues/20260727-linux-sidebar-shrink-clips-names-and-hides-status.md`

- [x] add the Linux sidebar source paths to `.claude/rules/sidebar.md`'s `paths:` frontmatter
      (`agterm-linux/Sources/AgtermLinux/AppControllerSidebar.swift`,
      `agterm-linux/Sources/AgtermLinux/LinuxSidebarPolicy.swift`) so the rule actually loads when
      editing the Linux sidebar — today its globs match only macOS files
      — added, appended after the macOS entries, matching the `control-api.md` precedent (the only other
      rule carrying Linux paths, which likewise lists them last).
      [decision] the first path went in as the GLOB
      **`agterm-linux/Sources/AgtermLinux/AppControllerSidebar*.swift`** rather than the bare
      `AppControllerSidebar.swift` the plan names: it covers the named file AND
      `AppControllerSidebarSplit.swift`, which now owns `AppController.sidebarWidthFloor` — the constant
      this very rule note documents — so editing the floor loads the rule that explains it. The glob form
      also matches the file's existing macOS style (`WorkspaceSidebar*.swift`).
      `agterm-linux/tests/atspi_smoke.py` was deliberately NOT added: it holds all fourteen scenarios
      across every subsystem, so it would load the sidebar rule for unrelated AT-SPI work
- [x] **update the `sidebar.md` line in the root `CLAUDE.md` subsystem index in the same edit** — it
      currently advertises the trigger set as macOS-only and goes stale the moment the frontmatter
      changes
      — updated in the same commit (`CLAUDE.md:441-448`). The entry now names the Linux GTK sidebar's two
      subjects (the label sizing contract, and the one derived `AppController.sidebarWidthFloor` that
      replaced the two disagreeing width minimums) and extends the Triggers sentence with
      `AppControllerSidebar*.swift`/`LinuxSidebarPolicy.swift`, so index and frontmatter agree
- [x] add a Linux bullet to `.claude/rules/sidebar.md` recording the sizing contract: the name label
      is the only hexpanding child and MUST carry `PANGO_ELLIPSIZE_END`; fixed instructional text
      WRAPS instead; trailing glyphs hug and need no `hexpand` change; the sidebar floor is one
      derived Linux constant (with its measured derivation) and not a per-widget `size_request` on
      the scroller; the paned start child is the `AdwToolbarView`, so the header-bar minimum is part
      of the effective floor and varies by decoration layout; and why no `width-chars` floor is used.
      Use semantic line breaks — one sentence per line — per the root `CLAUDE.md` rule
      — landed as **six `Linux port —` bullets** at the TOP of the `## Sidebar` body
      (`.claude/rules/sidebar.md:19-110`), placed before the macOS content exactly as `control-api.md`
      places its `**Linux adapter:**` bullet. Semantic line breaks throughout, longest added line
      **107 columns**. What each bullet carries, all six plan requirements covered:
      1. **the sizing contract**, with the mechanism (a `GtkLabel` with neither ellipsize nor wrap
         reports its WHOLE text as its MINIMUM, and GTK never allocates below a minimum) and the
         one-long-name-breaks-every-row consequence; states that the name is already the only `hexpand`
         child so **no `hexpand` change is ever needed**; then splits the treatment by KIND of text as a
         3-item sub-list — user text ELLIPSIZES (`END` not `MIDDLE`, head-vs-tail disambiguation),
         fixed instructional text WRAPS (173px longest LINE → 57px longest WORD), hugging trailing
         glyphs get NOTHING with the unseen badge called out as the trap (30px → 11px ellipsized would
         silently invalidate the floor) and the `"Flagged"` header recorded as needing nothing.
         **This ellipsize-vs-wrap distinction is the point of the note**, per the planning decision
      2. **the two non-one-liner sites**: the focus pill via `gtk_button_get_child` (verified a real
         `GtkLabel` on GTK 4.22.4, with the rebuild fallback named), that ellipsis is RENDER-time so the
         AT-SPI `named(...)` lookups keep working, and the `breadcrumb`-binding guard that keeps
         `gtk_label_set_xalign` off the rename `GtkEntry`
      3. **the derived floor**: `AppController.sidebarWidthFloor` (220) on the paned START CHILD, what it
         replaced, why two constraints was one too many (the 240 silently won → the drift), the full
         derivation (167px chrome at 13pt / 155 at 9pt / 188 at 20pt + a 53px name allowance), why the
         allowance is derived at 13pt and NOT 20pt, and why `AppStore.sidebarWidthMin` was not raised.
         Carries the explicit **"never add a second `size_request` anywhere on the sidebar widget tree"**
      4. **the effective floor is not single-valued**: the paned start child is the `AdwToolbarView`, so
         it is `max(220, AdwHeaderBar minimum)` = **220 under Hyprland's `":"` and 227 under
         `"close,minimize,maximize:"`** (~46px per button, font-size independent), and any "X is now the
         single floor" claim is wrong
      5. **why no `width-chars` floor**: redundant with a pixel allowance and re-creates the bug if set
         too high; plus the rename `GtkEntry`'s 18px minimum and that `gtk_editable_set_width_chars`
         RAISES it (1→26 … 6→66px) on GTK 4.22.4
      6. **where the coverage is**: the `sidebar-narrow-clipping` AT-SPI scenario, its two encoded traps
         (measure against the persisted read-back, never a node inside the scrolled window), and its
         one-digit-badge gap
      ⚠️ Two figures were written WITHOUT the font size the plan's Context estimated for them, because
      Task 3 measured them at the GTK default font and never at 13pt: the hint's 173px (Context guessed
      "~200px at 13pt") and the badge's 30px/11px pair. The badge sub-bullet says so explicitly — those
      figures show the RATIO, not the floor's terms — so a later reader cannot mistake them for the
      13pt/20pt numbers the floor is built from
- [x] update the issue file: mark it resolved, correct its "`grep -rn ellipsize` returns zero hits"
      claim (the palette commit on the base branch already sets `PANGO_ELLIPSIZE_MIDDLE`), strike `Palette.swift` from its follow-up list, add the two sites it missed (the
      flagged-empty hint and the section header), and **record that its suggested
      `gtk_label_set_width_chars` floor was deliberately rejected and why** — otherwise the two
      documents contradict each other for the next reader
      — all five edits made. ⚠️ [decision] **the issue file was edited IN PLACE in the main checkout at
      `/home/n/p/github/agterm-linux/docs/issues/`, not in this worktree, and is NOT in this task's
      commit.** The whole `docs/issues/` directory is UNTRACKED (`git ls-files docs/issues/` is empty in
      both the worktree and the main checkout, and it is not gitignored — all three issue files are
      local working artifacts the maintainer never committed), so the file does not exist in this
      worktree at all and there is no other copy. Copying it in and committing it would have added a
      tracked file to the branch against the repo's own convention and put it in the upstream PR;
      editing the untracked original touches no git state and cannot disturb the main checkout's branch.
      The five edits:
      - **Status** flipped from "open, root cause identified, not fixed" to **RESOLVED** on this branch,
        naming the plan file, the three-part fix, and the `sidebar-narrow-clipping` scenario with its
        fail-first evidence verbatim (`sidebar row overflows its 240px column: x=19 width=464 right=483`)
      - **the `grep` claim corrected** under a ⚠️ block: true of the deployed v0.16.1 build but not of the
        fork base, where `makePaletteRow` already sets `PANGO_ELLIPSIZE_MIDDLE` (`cd3ee02`) — so the port
        had exactly one correct precedent when the issue was filed and the sidebar had none
      - **the two missed sites added** as items 4 and 5 of the remediation surface: the flagged-empty hint
        (WRAPS, not ellipsize, with the 173px → 57px measurement) and `appendSection`'s `"Flagged"` header
        (min = nat = 58px, no change needed, recorded so the sweep is provably complete)
      - **the `width-chars` rejection recorded** as its own `###` subsection with both reasons — redundant
        with the pixel allowance already inside `sidebarWidthFloor`, and the report's premise is simply
        FALSE on GTK 4.22.4 since `gtk_editable_set_width_chars` RAISES the entry's 18px minimum
        (1→26 … 6→66px). The unseen-badge trap (30px → 11px) is flagged in the same subsection
      - **`Palette.swift` struck** from the follow-up list with `~~strikethrough~~` and a sentence saying
        why it comes off, and the list re-synced with the plan's Post-Completion surface (adding
        `Search.swift` and `AppControllerZoom.swift`, which the original omitted)
      Two edits beyond the checkbox, both for internal consistency: a **"the two width minimums,
      reconciled"** subsection (the issue's "Secondary finding" flagged the disagreement but predated the
      fix, so it would otherwise read as still-open), including the ⚠️ that the effective floor is
      `max(220, AdwHeaderBar minimum)` and that the width drift is fixed only on the no-window-button
      layout; and the **Workaround** section marked superseded but retained for anyone still on v0.16.1
- [x] re-verify the keep-in-sync verdicts recorded in the Overview (no control command, no Interface
      toggle, no agent-skill change, no website change, no `CHANGELOG.md` edit)
      — **all five RE-VERIFIED against the actual branch diff, not assumed.** The whole branch
      (`git diff --stat linux-palette-shortcut-column...HEAD`) touches exactly five files:
      `AppController.swift`, `AppControllerSidebar.swift`, `AppControllerSidebarSplit.swift`,
      `atspi_smoke.py`, and this plan — plus this task's `.claude/rules/sidebar.md` + `CLAUDE.md`.
      1. **Control API — nothing owed, confirmed.** No control-channel file is in the diff: filtering the
         changed paths for `Control*.swift` / `LinuxControlDispatcher` / `agtermctl` returns NOTHING (the
         three `AppController*.swift` hits are GTK controller files that merely contain the substring
         "Control"). Grepping the added source lines for `case .`, `Command.`, `ControlAction`,
         `AppActions`, `ControlSessionNode`, or `store.set`/`store.save` returns NOTHING — the change adds
         no action to the `AppActions`/`AppStore` seam and mutates no session state, so neither the
         four-point audit nor a paired `tree` read-back applies. Still no `sidebar.width` command on
         either platform; that stays the separate proposal under Post-Completion.
      2. **Settings ▸ Interface toggle — none.** No `InterfaceElement` / `hiddenInterfaceElements` hit
         anywhere in the diff. Nothing hideable was added; ellipsize and a width floor are sizing
         behavior, not chrome a user would want a toggle for.
      3. **Bundled agent skill — unchanged, and correctly so.** No `agterm/Resources/agent-skill/` path in
         the diff, AND a positive check that it does not go stale: `grep -rn "sidebarWidth\|sidebar width"
         agterm/Resources/agent-skill/` returns **zero hits**, so the skill documents no sidebar width or
         label behavior that this change could invalidate. No control command, keymap format, or
         window/workspace/session/pane model change.
      4. **Website — unchanged, and correctly so.** No `site/` path in the diff, and the same positive
         check: no `sidebarWidth`/`sidebar width` mention anywhere under `site/`. `site/commands.html`
         needs no entry because no `Command` case was added. (The one `grep -i ellipsi` hit in
         `site/docs.html:1793` is the words "blue ellipsis" describing the agent-status GLYPH — unrelated
         to text ellipsizing.)
      5. **`CHANGELOG.md` — NOT touched.** Absent from the diff, per the release-only guardrail.
- [x] run `make lint` if `swiftlint` is available; if not, apply the manual substitutes CI would
      catch (`git diff --check` clean, no line over the 200-column limit, no file over its size cap)
      and record the skip here
      — ⚠️ **`swiftlint` is NOT installed on this box** (`command -v swiftlint` → nothing), unchanged from
      Tasks 1-7, so `make lint` was **SKIPPED** and the manual substitutes ran instead:
      - `git diff --check` → **clean** (no whitespace errors, no conflict markers);
      - longest line **107 columns** across every line this task ADDED, far under the 200 limit. The two
        200+ lines that exist in `CLAUDE.md` (438 at 240 cols, 439 at 221) are PRE-EXISTING — the
        semantic-line-break mandate paragraph — and `git diff CLAUDE.md | grep -c "^+.\{200,\}"` is **0**;
      - file sizes: `.claude/rules/sidebar.md` 273 → **366** lines, `CLAUDE.md` 490 → **494**, the issue
        file 139 → **204**. No cap applies to any of them (swiftlint lints only Swift; the size caps are
        the 1000-line source / 2000-line test budgets) and **`.swiftlint.yml` is untouched** — no limit
        raised anywhere in this plan.
      **This task changed no Swift and no Python at all** (`git status --short` shows only the two
      markdown files), so it cannot move the lint surface: the substitutes are a formality here and the
      real gate stays CI's `lint` job.
      Regression suites re-run anyway as the standing gate: `agterm-linux` **133 tests / 17 suites,
      1 failure** (`Linux integration service` ▸ "Flatpak process environments do not offer a host
      launcher", `IntegrationServiceTests.swift:765`); `agtermCore` **1733 tests / 74 suites, 1 failure**
      (`stopReportsBlockedWhenAssistantMessageEndsInQuestionMark`, `CodexStatusHookTests.swift:102`).
      Identical to the Tasks 1-7 pre-existing baseline — same two tests, same counts, no new failure, and
      the `CodexStatusHookTests.swift:112` flake did not reappear. The Linux package builds clean
      (`Build complete!`).

### Task 9: Verify acceptance criteria

**How these criteria were verified, and what genuinely could NOT be verified here.**
Six of them describe a DRAG GESTURE or an on-screen appearance, and nothing on this box can perform a
pointer drag (`openbox`, `xdotool`, `xvfb-run`, `Xvfb` are all absent — the Task 7 finding still
holds).
Each such criterion was checked with the most faithful automatable substitute available and says
below WHICH one it used; nothing below claims a visual confirmation that was not obtained.
Three substitutes were used:

- **(A) allocation + offscreen-render probe.**
  A C probe compiled against the installed GTK 4.22.4 / libadwaita 1.9.2 rebuilds the real sidebar
  tree — the `AdwToolbarView` (header carrying the decoration layout, scroller holding `sidebarBox`,
  bottom bar) as the paned START child with the 220px request, exactly as `AppController.init` and
  `buildSidebarSplit` construct it — and MAPS it on a private `gtk4-broadwayd` display, so GTK really
  allocates and really paints it with no compositor involved and the maintainer's screen untouched.
  It then reads `gtk_widget_compute_bounds` for every row, glyph, badge, and button against the
  scroller, `pango_layout_is_ellipsized` for every label, and writes the column out as a PNG.
  Every case ran twice: against the FIXED tree and against a `broken` mode that restores the pre-fix
  shape (no ellipsize, no wrap, the deleted 240px scroller request), so every number below has its
  before/after.
  Two mechanics worth keeping for a re-run: `GtkWidgetPaintable` only replays a render node the
  RENDERER already produced and broadway with no attached client never produces one, so the PNG comes
  from driving the toolbar's own `GtkWidgetClass.snapshot` vfunc directly; and each run prints Task
  5's `"MMMMMMMM"` witness (88 / 126 / 194px at 9 / 13 / 20pt), so a run whose CSS cascade missed the
  tree is caught immediately.
- **(B) the real app over AT-SPI** — Task 7's `sidebar-narrow-clipping` scenario, which measures
  agterm's own widgets in its own process.
- **(C) the real app's persisted state** — a launch → stop → relaunch script reusing `atspi_smoke`'s
  isolation (`AGTERM_APP_ID` plus a temp `HOME`/state/socket), so the deployed daily driver is never
  touched.

⚠️ **What (A) is NOT**: a faithful replica of the widget tree is not agterm's own process, and a PNG
is not the maintainer's screen.
The on-screen pass — dragging the divider by pointer at the maintainer's own theme, font scale, and
compositor — remains MANUAL and is listed under Post-Completion.

- [x] at the new floor with a 30+ character session name: names show `…`, no hard mid-glyph cut
      — **verified by substitute (A) + (B) (on-screen pass remains manual).**
      At the 220px floor and 13pt, the 40-character name label is allocated **53px** — precisely the
      name allowance the floor was derived for — and reports `pango_layout_is_ellipsized = 1` against
      a 345px natural width, so Pango really inserted the `…` rather than the row being cut.
      The rendered PNG shows `side…`.
      Pre-fix control, same probe: the same label is allocated **341px** with `is_ellipsized = 0`
      inside a 240px column and ends at x=400, i.e. **160px past the viewport edge**, where the
      scroller hard-cuts it — the reported symptom, reproduced and photographed.
      At 20pt the label is allocated 32px and still ellipsizes (natural 528px).
- [x] the agent-status glyph, flag star, and unseen badge stay visible on EVERY row at the floor —
      including short-named rows beside a long-named sibling (the "one long name breaks every row"
      symptom), and including a row carrying **all three** decorations at once, which is the case the
      Task 5 measurement exists to guarantee
      — **verified by substitute (A) + (B) (on-screen pass remains manual).**
      The probe's normal-mode sidebar holds a long-named fully decorated row, a short-named (`dev`)
      fully decorated row beside it, and an undecorated long-named row, all in one list box at the
      220px floor.
      Every one of the 16 watched widgets fits:

      | 13pt, 220px column | status glyph | flag star | `99+` badge | row right edge |
      |---|---|---|---|---|
      | long-named decorated row | x=118 → 134 | x=140 → 156 | x=162 → 201 | 215 |
      | short-named `dev` row | x=138 → 154 | x=160 → 176 | x=182 → 201 | 215 |

      Pre-fix control: **all 16 overflow** the 240px column — the long row's status
      glyph sits at x=406, its badge ends at 489, and the SHORT `dev` row's glyphs are pushed to
      exactly the same off-viewport x (426 / 448 / 470), which is the "one long name breaks every row"
      symptom measured rather than described.
      At 20pt, the worst case the floor must survive: the `99+` badge is 60px wide and still ends at
      201 inside the 220px column, every glyph whole, only the name truncating harder — 0 overflow.
      Substitute (B) agrees from inside the real app: `OK: a decorated sidebar row truncates inside its
      220px column (row right edge 215px; 3 glyphs and 2 labels all inside)` — the same 215px the probe
      computes.
- [x] workspace headers truncate and the add-session "+" button stays visible at the floor
      — **verified by substitute (A) (on-screen pass remains manual).**
      At 220px/13pt the header name is allocated 116px with `is_ellipsized = 1` (natural 251px) and the
      "+" button sits at **x=184 w=36, right edge 220** — flush inside the column.
      ⚠️ **"Visible" here means allocated inside the column, not painted at rest**: the app CSS makes
      the "+" `opacity: 0` until the workspace row is hovered
      (`.agterm-sidebar #workspace-row .workspace-add-session`), which is deliberate hover-reveal
      chrome, so the default render shows no "+".
      Re-rendered with that hover rule forced on, the "+" paints at the right edge inside the column.
      Pre-fix control: the "+" is allocated at **x=472 → 508 in a 240px column**, 268px off-viewport —
      unreachable even by hovering, which is the reported symptom.
- [x] flagged-mode breadcrumb rows truncate and keep their trailing glyphs
      — **verified by substitute (A) (on-screen pass remains manual).**
      In flagged mode at the 220px floor the breadcrumb label reports `is_ellipsized = 1`, its row ends
      at 215, and the status glyph (right edge 156) and badge (right edge 201) are both inside; 0 of 10
      widgets overflow, at 13pt and at 20pt.
      Pre-fix control: **10 of 10 overflow** the 240px column.
      The PNG shows `sidebar-c…` and `dev — agt…` with the glyph and badge intact on both rows.
- [x] the flagged-empty hint wraps instead of clipping, at the floor
      — **verified by substitute (A) (on-screen pass remains manual), and it turned up a number the
      plan was missing.**
      Task 3 measured this hint at the GTK default font (173px → 57px) and Task 8 flagged the gap;
      measured under the sidebar CSS it is **156 / 225 / 346px unwrapped versus 51 / 74 / 113px wrapped
      at 9 / 13 / 20pt**.
      So at the default 13pt the unwrapped hint's 225px minimum is **5px WIDER than the 220px floor** —
      the wrap is load-bearing at the new floor, not defensive — and its 346px at 20pt exceeded even
      the old 240px request, meaning the empty flagged view already clipped at large sidebar fonts
      before this change.
      Wrapped, at the floor: `is_ellipsized = 0`, right edge 220, laid out as **3 centered lines**
      (`No flagged sessions.` / `Right-click a session →` / `Flag.`), 2 lines at 240px.
      The PNG confirms the centered 3-line layout.
- [x] the focus pill truncates instead of overflowing and clipping
      — **verified by substitute (A) (on-screen pass remains manual).**
      At the 220px floor the pill's minimum is **66px**, it is allocated 204px and ends at 212, and its
      internal label reports `is_ellipsized = 1` at 170px allocated against a 404px natural.
      The PNG shows `✕  agterm linux po…` inside the column.
      Pre-fix control: the pill's minimum is **454px**, it is allocated 492px and ends at **500 in a
      240px column** — 260px past the viewport, exactly the clipping the fix removes.
- [x] inline rename works at the floor without overflowing — in the normal view AND in flagged view,
      the latter with no GTK critical on the console
      — **verified by substitute (A) for the geometry and the criticals; the on-screen typing pass
      remains manual.**
      Normal view at the 220px floor: the rename `GtkEntry` is allocated 53px, its row ends at 215, and
      the status glyph, flag star, and badge are all still inside (134 / 156 / 201) — the PNG shows the
      entry with all three decorations beside it.
      Flagged view: the entry is allocated 89px, the row ends at 215, 0 overflow.
      **GTK criticals, counted by a `g_log` handler rather than eyeballed: 0 in both views.**
      Pre-fix control on the same rename-in-flagged-view path reproduces exactly two, which is the
      latent bug Task 3 fixed:
      `GLib-GObject: invalid cast from 'GtkEntry' to 'GtkLabel'` and
      `Gtk: gtk_label_set_xalign: assertion 'GTK_IS_LABEL (self)' failed`.
- [x] the divider reaches the floor and stops there; the persisted width round-trips across relaunch
      — **the STOP is verified by substitute (A), the round-trip by substitute (C) against the real
      app; the pointer drag itself remains manual.**
      Driving the divider past the floor the way a drag does — `gtk_paned_set_position(paned, 40)` on
      the mapped tree — settles at **220 under the Hyprland `":"` layout and 227 under
      `"close,minimize,maximize:"`**, and at **240 on the pre-fix tree**.
      Round-trip in the real app, one isolated state dir per case, two launches each:

      | seeded `sidebarWidth` | launch 1 | launch 2 |
      |---|---|---|
      | 220 (the floor) | 220 | 220 |
      | 300 (above the floor) | 300 | 300 |
      | 160 (below the floor) | 160 | 220 |

      The 220 and 300 cases are the criterion: a width at or above the floor round-trips byte-exact,
      so the floor is a floor and not a pin.
      ⚠️ Observation recorded rather than glossed: a seed BELOW the floor is displayed at the floor
      immediately (GTK enforces the start child's minimum) but is re-persisted at 220 only on the NEXT
      launch, not during the launch that loaded it — the debounced `captureSidebarWidth` write does not
      land on a first launch that is also migrating the legacy `workspaces.json`, whereas the Task 7
      scenario sees it promptly because its `session rename`/`session flag` calls force an immediate
      `store.save()`.
      This matches the "survives load, gets pushed back up by GTK, and re-persists at the floor" note on
      Task 6's clamp, affects no acceptance criterion (the sidebar is drawn at the floor either way),
      and is a pre-existing first-launch-migration timing detail rather than anything this change
      introduces.
- [x] **the floor is not wider than today's 240** — the fix must leave the sidebar at least as
      narrowable as it was, per the Task 5 gate
      ⚠️ **SUPERSEDED by an explicit maintainer decision — read the annotation below before quoting
      this checkbox. It was verified as written, then the criterion itself was retired.**
      — **verified by substitute (A), measuring both sides rather than asserting the premise.**
      The same probe, same tree, only the fix toggled: the pre-fix shape stops the divider at **240**
      and the fixed shape stops it at **220** (Hyprland `":"`) / **227**
      (`"close,minimize,maximize:"`).
      220 < 240 and 227 < 240, so the sidebar is strictly MORE narrowable in both environments — and
      today's 240 is confirmed by measurement, not taken from the plan's premise.

      ⚠️ **What changed, and when.** The numbers above describe the MODELLED floor this task audited
      (a fixed 220 derived from a measured chrome table).
      Post-completion review replaced the model with a live `gtk_widget_measure` of `sidebarBox`
      (`eb69b5a`, 2026-07-27, "measure the sidebar width floor instead of modelling it") after the
      model was shown to under-report DejaVu Sans by 9px at 20pt and clip the badge there.
      A MEASURED floor cannot honour a fixed ceiling: it pins to `AppStore.sidebarWidthDefault` (220)
      wherever the content fits, and rises with the content where it does not — past 240 at roughly
      **27 effective points** (sidebar font size × desktop text scaling) and above, capped only at
      `AppStore.sidebarWidthMax` (560).
      The maintainer took that trade EXPLICITLY rather than keep the gate: at those sizes the row
      chrome physically needs the width, and whole glyphs beat clipping — the name simply truncates
      harder.
      The decision is recorded in the shipped code, on
      `LinuxSidebarPolicy.sidebarWidthFloor` ("a DELIBERATE override of the plan's original
      'never wider than today's 240' gate").
      **Shipped behaviour, replacing the criterion:** the floor is `min(560, max(220, measured))`.
      For the default configuration nothing moved — the sidebar is still MORE narrowable than the old
      240, which is what the criterion was protecting.
      Only the large-font × large-text-scale population gets a wider minimum than before, and gets it
      knowingly.
      Two of this task's other criteria were audited against the modelled floor too and remain
      correct under the measured one, because they assert containment rather than a width: the
      glyph/star/badge criterion is now STRUCTURALLY satisfied (the floor is measured from
      `sidebarBox`, whose minimum already contains the badge's natural width), and the truncation
      criteria are re-gated by Task 7's scenario at the column the app chooses.
- [x] every measurement this plan asked for has its number written into this file
      — **audited task by task; every requested number is present, and the four gaps found are filled
      here.**
      Present and checked: Task 1's line counts (1000 → 971, 376 → 405); Task 2's mechanism table
      (490 / 169 / 410 / 89); Task 3's breadcrumb (379 → 11), hint (173 → 57), wrap mode (0), justify
      line counts, section header (58) and badge (15 / 30 / 11); Task 4's pill table
      (391 / 66 / 100 / 66 / 341 / 11) plus the scroller policy (1) and its 46px minimum; Task 5's
      chrome table (155 / 167 / 188 at 9 / 13 / 20pt), toolbar table (88 / 227, bottom 138, scroller
      46, toolbar 138 / 227), per-button cost (~46), today's baseline 240, the rename entry
      (18 / 168, `width-chars` 26 → 66, in-row 173 / 176 / 185 / 206), the glyph-width sample
      (43 / 65 / 86 / 61 / 70 / 73 → ~8.75px per character), the 53px allowance and the 220 floor;
      Task 6's floor table (46 / 138 / 227 / 220 / 227 / 221 / 228) and the persisted 220; Task 7's
      fail-first (`width=464 right=483` against 240), pass (215), child extents, and the file growth
      1772 → 1949; Task 8's doc line counts.
      **Gaps found and filled:**
      1. the flagged-empty hint at the SIDEBAR font size, which Task 8 explicitly flagged as missing —
         **156 / 225 / 346px unwrapped, 51 / 74 / 113px wrapped, at 9 / 13 / 20pt** (Task 3's
         173 → 57 was at the GTK default font);
      2. the unseen badge at the sidebar font size — **`99+` 27 / 39 / 60px and `1` 14 / 19 / 30px at
         9 / 13 / 20pt**, which reproduces Task 5's +33 / +45 / +66 badge term once the box's 6px
         spacing is added, and **10 / 14 / 22px when ellipsized**, the trap restated at the real sizes;
      3. the `"Flagged"` section header at the sidebar font size — **48 / 69 / 106px** (min = nat) at
         9 / 13 / 20pt, against Task 3's 58px at the default font;
      4. a correction: Task 8 recorded `.claude/rules/sidebar.md` as 273 → **366** lines; the commit
         actually landed **367** (`git show d410752:.claude/rules/sidebar.md | wc -l`).
      [decision] Gaps 1–3 were also written into `.claude/rules/sidebar.md`, replacing the
      default-font figures and the caveat that flagged them as ratios — the note is the durable home
      for the sizing contract, and leaving a 173px figure there when 225px is the number that actually
      exceeds the floor would mislead the next reader.
- [x] run the full test suite: `cd agtermCore && swift test`, the Linux package tests, and
      `scripts/test-linux-ui.sh` (or the accepted substitute recorded in Task 7)
      — Linux package builds clean (`Build complete!`); `agterm-linux` **133 tests / 17 suites, 1
      failure** (`Flatpak process environments do not offer a host launcher`,
      `IntegrationServiceTests.swift:765`); `agtermCore` **1733 tests / 74 suites, 1 failure**
      (`stopReportsBlockedWhenAssistantMessageEndsInQuestionMark`, `CodexStatusHookTests.swift:102`).
      Identical to the Tasks 1-8 pre-existing baseline — same two tests, same counts, no new failure,
      and the `CodexStatusHookTests.swift:112` flake did not reappear.
      ⚠️ [deviation] `scripts/test-linux-ui.sh` still cannot run here: re-checked, it bails at
      `missing Linux UI test dependency: openbox`, and `openbox`, `xdotool`, `xvfb-run`, `Xvfb` and
      `dotool` are all still absent (`dbus-run-session`, `gio`, `gapplication`, `hyprctl` and python
      `gi`/`Atspi` are present).
      The accepted substitute ran green:
      `env -u HYPRLAND_INSTANCE_SIGNATURE AGTERM_ATSPI_SCENARIO=sidebar-narrow-clipping python3
      agterm-linux/tests/atspi_smoke.py` → `PASS: sidebar-narrow-clipping`, exit 0.
      **CI's `build-linux` job stays the accepted gate for the other thirteen scenarios.**
- [x] `make lint` passes (or the recorded substitute); file sizes stay under their caps with no limit
      raised
      — ⚠️ `swiftlint` is still absent (`command -v swiftlint` → nothing), so `make lint` was SKIPPED
      and the manual substitutes ran over the whole branch diff:
      `git diff --check` **clean**; **0** added lines over 200 columns
      (`git diff linux-palette-shortcut-column...HEAD | grep -c "^+.\{201,\}"`); file sizes
      `AppController.swift` **974**, `AppControllerSidebar.swift` **441**,
      `AppControllerSidebarSplit.swift` **86** (all far under the 1000-line source cap),
      `agterm-linux/tests/atspi_smoke.py` **1949** (under the 2000-line test budget),
      `.claude/rules/sidebar.md` **367 → 372** after this task's two figure updates, `CLAUDE.md` 494.
      Longest line added by this task: **107** columns in `.claude/rules/sidebar.md`, matching Task 8's
      own ceiling and far under the 200 limit.
      **`.swiftlint.yml` is untouched on the entire branch** — no limit raised anywhere in this plan.

### Task 10: [Final] Close out

- [x] update `README.md` only if it documents sidebar sizing behavior — check; likely no change
      — **checked by grep, NOT assumed; no change made.** Searched the 796-line `README.md`
      case-insensitively for `sidebarwidth`, `sidebar width`, `widthMin`, `width_min`,
      `minimum width`, `narrow`, `divider`, `size_request`, `ellipsi`, `truncat`, and the three
      literal widths `240`/`220`/`160px`, then read all **23** `sidebar` mentions.
      **Nothing in `README.md` documents sidebar WIDTH, its minimum, or divider dragging.** The five
      near-misses are all something else:
      - `:348` "the divider position is remembered" — the SPLIT-PANE divider, not the sidebar one;
      - `:385` "right-clicking outside **narrows** to the clicked row" — selection scope, not width;
      - `:718` "a blue **ellipsis**" — the agent-status GLYPH, not text ellipsizing (the same
        false-positive Task 8 found in `site/docs.html`);
      - `:46` "configurable toolbar and sidebar **text**" and `:418` "sidebar tint and **text size**"
        — the sidebar FONT SIZE setting, which this change does not alter (it only measures the floor
        across that setting's 9–20pt range; no setting, default, or range moved);
      - `:528` `session resize --split-ratio` — the split divider again.
      So the README describes the sidebar's CONTENT and its font, never its geometry — and this fix
      adds no user-visible setting, keybinding, control command, or default to document.
      [decision] no README edit: writing one would be the first sidebar-geometry sentence in the file,
      documenting an internal constant a user cannot set, and the user-visible behavior change (the
      divider now stops at 220/227 instead of 240) is a bug FIX restoring the documented-nowhere status
      quo, not a feature. It belongs in the release changelog at release time, which is out of scope
      here per the release-only guardrail.
- [x] confirm the only `CLAUDE.md` edit needed is the rules-index line from Task 8 — the
      "arbitrary user text in a width-constrained container must report a small minimum" principle
      belongs in `.claude/rules/sidebar.md`, not the root file
      — **confirmed on both halves.**
      1. *The principle is in the rule, verbatim.* `.claude/rules/sidebar.md:19-20` opens the `## Sidebar`
         body with **"Linux port — the sizing contract: any sidebar widget that can hold
         arbitrary-length USER text must be able to report a SMALL minimum width."** — the plan's
         Solution-Overview sentence, landed where the plan says it belongs.
      2. *The root file carries nothing else, and nothing stale.*
         `grep -nEi "linux|gtk|ellipsi|sidebarwidth|agterm-linux" CLAUDE.md` returns **five** hits: the
         three lines of the Task 8 index entry (`:444`, `:446`, `:449`) plus two PRE-EXISTING
         "Coveralls on Linux" mentions in the CI bullets (`:257`, `:489`) that this change does not
         touch. `git diff linux-palette-shortcut-column...HEAD -- CLAUDE.md` is exactly the Task 8
         hunk — **3 added lines in the index entry, 1 line replaced in its Triggers sentence, nothing
         else**, and the file is 494 lines.
      Placement rationale, recorded so it is a decision and not an omission: the root file states its
      own contract — *"Detailed per-subsystem engineering notes live in `.claude/rules/*.md` … that is
      what keeps this root file lean"* — and this principle is subsystem-specific GTK sidebar
      engineering, not a cross-cutting norm. The root file's own norms are all cross-cutting process
      rules (control-API coverage, the Settings-toggle proposal, worktrees, build gotchas); it holds no
      per-widget UI design principle, so adding one would be the precedent-setting exception. The
      frontmatter added in Task 8 is what makes the rule LOAD when the Linux sidebar files are edited,
      which is the mechanism that keeps the principle in front of the next editor — restating it in the
      root file would create a second copy to drift.
      ⚠️ One honest limit, recorded rather than glossed: the rule's `paths:` globs cover
      `AppControllerSidebar*.swift` and `LinuxSidebarPolicy.swift`, but **not** `AppController.swift`,
      which this branch also touched (it is the hub file `makeNameWidget` moved OUT of). That is
      deliberate and matches the root file's own guidance — it already warns that "a cross-cutting edit
      that touches only a hub file (`AppStore.swift`, `ContentView.swift`, `agtermApp.swift`) may not
      match a glob, so consult this index and open the rule yourself", which is precisely what the
      updated index line at `:444-449` now enables for the Linux sidebar.
- ➕ [x] re-run the standing regression gate at closeout, as every prior task did — this task changes
      no Swift and no Python (`git status --short` shows only this plan file), so the gate is a
      formality, but a closeout that skipped it would be the one task with no evidence
      — Linux package builds clean (`Build complete! (2.30s)`); `agterm-linux` **133 tests / 17 suites,
      1 failure** (`Flatpak process environments do not offer a host launcher`,
      `IntegrationServiceTests.swift:765`); `agtermCore` **1733 tests / 74 suites, 1 failure**
      (`stopReportsBlockedWhenAssistantMessageEndsInQuestionMark`, `CodexStatusHookTests.swift:102`).
      Identical to the Tasks 1-9 pre-existing baseline — same two tests, same counts, no new failure,
      and the `CodexStatusHookTests.swift:112` flake did not reappear.
      `swiftlint` is still absent (`command -v swiftlint` → nothing), so `make lint` was SKIPPED for the
      tenth time and the manual substitutes ran: `git diff --check` **clean**, **0** added lines over
      200 columns (longest added line **106**), and every file under its cap —
      `AppController.swift` 974, `AppControllerSidebar.swift` 441, `AppControllerSidebarSplit.swift` 86
      (source cap 1000), `atspi_smoke.py` 1949 (test budget 2000), `.claude/rules/sidebar.md` 372,
      `CLAUDE.md` 494, `README.md` 796 unchanged.
      **`.swiftlint.yml` is untouched across the entire branch** — confirmed once more against
      `linux-palette-shortcut-column...HEAD`; no limit was raised anywhere in this plan.
- [x] move this plan to `docs/plans/completed/`
      — moved with `git mv` (so the rename is tracked and lands in the same commit) to
      **`docs/plans/completed/20260727-linux-sidebar-shrink-clipping.md`**, joining the 20260725
      row-height plan the Context section cites as prior art. Performed as the LAST action of this
      task, after every checkbox above was marked and the suites were re-run.

## Post-Completion

*Items requiring manual intervention or external systems — no checkboxes, informational only*

**Manual verification (required — no automated test drags the divider):**

- Build and launch an isolated dev instance and drag the sidebar divider slowly from wide to the
  floor, watching for the threshold behavior described in the issue. The AT-SPI scenario only pins
  the static allocation.
- ⚠️ **Start with a deliberate drag.** The maintainer's own state already persists
  `"sidebarWidth": 240` in all five window records, so nothing changes on launch until the divider
  is moved — an "it looks the same" first impression would be expected, not a failed fix.
- Check all three sidebar modes at the floor: normal (workspace headers + session rows), flagged
  (breadcrumb rows and the empty-state hint), and workspace-focused (the pill).
- Check both ends of the sidebar font range (Settings ▸ Appearance, 9pt and 20pt) at the floor — the
  unseen badge is a `GtkLabel` and scales, so 20pt is the worst case the floor must survive.
- Confirm the deployed daily driver is untouched throughout.
- ⚠️ **What this pass still has to establish, after Task 9.** Task 9 verified the static ALLOCATION of
  every one of those cases — all three sidebar modes, both ends of the font range, both decoration
  layouts, the rename entry in each view, and the divider's stop at the floor — by mapping the real
  widget tree on a private `gtk4-broadwayd` display and measuring it, plus the real app over AT-SPI and
  across a relaunch.
  What no substitute could reach is the GESTURE and the maintainer's own display: that the drag feels
  right and stops cleanly at the floor rather than juddering, and that the result reads correctly at the
  maintainer's theme, font scale, and fractional-scaling factor.
  Those are what the manual pass is for; the geometry itself is already pinned by measurement.

**Upstream PR:**

- This work stacks on `linux-palette-shortcut-column`; the fork's `linux-port-wip` is an integration
  branch and is never a PR base. Follow
  `docs/upstream-pr-workflow.md`: flatten to exactly one commit over `linux-port`, fold the plan-doc
  commit into the feature commit, and match the house body shape
  (What → Root cause → Fix → Keep-in-sync → Change → Testing).
- ⚠️ **Decide whether the PR carries the palette commit too.** The base branch is `linux-port` plus
  `cd3ee02`; this fix stacks on top. Either open it as a second PR after the palette one lands, or
  flatten both onto `linux-port` — but do not silently drop `cd3ee02`, since Task 2's comment cites
  the palette's ellipsize choice as its counter-example. Build and test the flattened branch either
  way; a clean cherry-pick does not prove it stands alone.
- The issue's measurement table is ready-made PR-body evidence; re-measure after the fix so the body
  can show before/after, and include the decorated-row numbers from Task 5.

**Open decision, deliberately deferred:**

- Once names ellipsize, the full name is unreadable at narrow widths and GTK adds no tooltip
  automatically. A `gtk_widget_set_tooltip_text` with the full name is one line. Recommendation: no
  — macOS does not do it either, and it is scope. Noted so it is a decision rather than an omission.
  The a11y name stays the full text either way, which is what keeps the AT-SPI `named(...)` lookups
  working.

**Follow-up surface (explicitly OUT of scope here):**

- The issue lists other bare `gtk_label_new` sites in width-constrained containers that were never
  verified: `AppControllerSessionPicker.swift` (picker rows in a 320px popover), the MRU switcher
  overlay in `AppController.swift`, `AppControllerDashboard.swift`, `ThemePicker.swift`,
  `Search.swift`, `AppControllerZoom.swift`. `Palette.swift` is already correct on the base branch (`cd3ee02`) and
  comes off that list. Each needs the same measure-then-decide pass; none is confirmed broken.
- No `sidebar.width` control command exists on either platform. If the sidebar width is worth driving
  from a script, that is a separate proposal owing the full four-point audit plus a `tree` read-back
  field — not part of this fix.

---

## Post-review amendment (2026-07-27, review round 1)

The multi-agent review found one substantive defect and several test/doc gaps in the landed work. All
were addressed in a follow-up commit on the same branch; the record below supersedes the Task 5/6
conclusion that the floor could be a plain constant.

**The 220px floor was measured at text-scaling factor 1.0 only.** `applySidebarFontSize` emits the
sidebar font size as CSS `pt`, and GTK resolves `pt` through `gtk-xft-dpi` — so GNOME's "Large Text"
widens the row chrome exactly like a bigger sidebar font. Re-measured with a third independent C probe
against GTK 4.22.4 (`gtk_widget_measure` on a faithful `makeRow` rebuild under the sidebar CSS, one
`gtk-xft-dpi` per process), the decorated-row minimum in EFFECTIVE points (font size × scale) is:

| effective pt | 9 | 11.25 | 13 | 13.5 | 16 | 16.25 | 18 | 19.5 | 20 | 24 | 25 | 26 | 30 | 32 | 40 |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| min px | 165 | 174 | 181 | 184 | 193 | 195 | 201 | 208 | 210 | 226 | 230 | 234 | 250 | 258 | 291 |

16pt × 1.25 and 20pt × 1.0 both measure 210, confirming effective points is the right variable. At
20pt × 1.25 the row needs 230px, so the fixed 220 floor reopened the original clipping symptom for
large-sidebar-font users on a scaled desktop. Defaults (13pt) stay safe at every scale (181/195/208).

**Resolution: the font-size-dependent branch this plan carved out and then declined was taken.**
`LinuxSidebarPolicy` now owns `decoratedRowMinimum(effectivePoints:)` (the line above: 165px at 9
effective points rising 4.1px/pt, rounded up, never under-reporting any sample),
`sidebarWidthFloor(sidebarFontSize:textScaleFactor:)` (pinned to `AppStore.sidebarWidthDefault`, rising
only where the row demands it, clamped to `sidebarWidthMax`), and `clampSidebarWidth(_:floor:)`.
`AppController.textScaleFactor()` reads `gtk-xft-dpi` off `GtkSettings`, and
`refreshSidebarWidthFloor()` re-applies the size request on every `applySidebarFontSize`.
The former static `AppController.sidebarWidthFloor` constant is now a stored per-controller value.
`LinuxPolicyTests.sidebarWidthFloor` covers all 15 measurements, the pinning, the bad-scale fallbacks,
the `[min, max]` invariant across a 4–30pt × 0.5–6.0 grid, and all three clamp legs — the unit coverage
Tasks 1–6 waived on "no host-free logic" grounds, which was wrong: the clamp is pure arithmetic.

The 53px "name allowance" is gone: it was back-solved from 220, not measured, and above 220 the correct
requirement is only that the chrome fit inside the floor.

**Other review findings addressed.** The AT-SPI scenario now takes both edges from the sidebar's own
`role="scroll pane"` node instead of comparing a window-relative x against a width read out of
`windows/<id>.json` — the old form assumed the sidebar's left edge sat at window-relative 0, which the
CSD shadow inset breaks (a fail-run confirmed the row origin is not 0). That also deletes
`persisted_sidebar_width`/`settled_sidebar_width`, their 0.4s-debounce settle race, and a dead
`current >= seeded` assertion. A `column.width < 240` gate now gates the floor itself, since a
read-back reference can never catch a floor regression (verified: raising the floor to 300 fails).
Coverage was extended to the workspace header's `+` button, the flagged breadcrumb row, and the
flagged-empty hint, and the scenario seeds the 20pt font. The focus pill is built with an explicit
`gtk_label_new` + `gtk_button_set_child` instead of setting an ellipsize on `gtk_button_get_child`'s
unguaranteed child. The rule note gained the sidebar bottom bar (138px) in the effective-floor formula,
the third AT-SPI trap, and the fact that the header-bar minimum is text-scale dependent (88/98/103/123
at 1.0/1.25/1.5/2.0 with `":"`).

---

## Post-review amendment (2026-07-27, review round 2)

Round 2 found that the round-1 fix, while closing the original hole, opened new ones. Two binding
user decisions drove the follow-up; both are recorded here so they are not relitigated.

### ⚠️ Decision A — the floor MAY exceed 240 (the round-0 hard gate is OVERRIDDEN)

The planning bullet "**⚠️ The floor is derived at 13pt, and MUST NOT exceed today's effective 240**"
(with its "hard gate: if the measured floor exceeds 240, STOP and reconsider") **no longer holds.**
The floor is now `max(AppStore.sidebarWidthDefault (220), measured content minimum)`, and above
roughly **27 effective points** (sidebar font size × desktop text scaling) the measurement passes 240
and the floor goes with it. Concretely: 20pt under GNOME "Large Text" 1.5, or 20pt in DejaVu Sans on a
scaled desktop, gets a minimum sidebar **wider** than before this branch.

That is accepted. The gate was written to stop a fix that made a "cannot narrow the sidebar" bug worse
by inflating the minimum for *everyone*; it does not apply to a floor that only rises where the chrome
physically does not fit. Whole glyphs beat a clipped badge, and the alternative — capping at 240 —
reopens the exact clipping the branch exists to remove for large-text users. **Do not re-introduce a
240 ceiling** in `LinuxSidebarPolicy`, in `refreshSidebarWidthFloor`, or in the AT-SPI scenario.

### Decision B — the fitted model is replaced by a direct measurement

`decoratedRowMinimum(effectivePoints:)` (the round-1 line: 165px at 9 effective points, +4.1px/pt) and
`AppController.textScaleFactor()` are **deleted**. `refreshSidebarWidthFloor` now calls
`gtk_widget_measure(sidebarBox, GTK_ORIENTATION_HORIZONTAL, …)` at the end of every `rebuildSidebar`.

The model was calibrated to one box's `gtk-font-name`; the sidebar CSS sets only `font-size`, so the
family comes from the theme. Same decorated row (`99+` badge, status glyph, star) at 20pt, scale 1.0,
varying only the family (GTK 4.22.4, `gtk_widget_measure` on the sidebar box):

| gtk-font-name | required column px | old floor | result |
|---|---|---|---|
| Noto Sans (this box) | 210 | 220 | fits |
| Cantarell | 214 | 220 | fits |
| Liberation Sans | 216 | 220 | fits |
| **DejaVu Sans** | **229** | **220** | **clipped by 9px** |

DejaVu Sans is the default sans on many distros. At 13pt the same rows measure 181/184/186/**194**, all
comfortably inside 220. Verified end-to-end, not just in the probe: forcing DejaVu Sans through an
isolated `XDG_CONFIG_HOME`'s `gtk-4.0/gtk.css` makes the app's own sidebar column settle at **229px**
where the host font gives 220px — the measured floor follows the family, with nothing to calibrate.

Two GTK facts the measurement depends on, both probed:
- a measure taken **without rebuilding** is stale for a pure CSS font-size reload (GTK revalidates the
  CSS node on the next frame), so `applySidebarFontSize` no longer refreshes the floor — both Settings
  paths that change the sidebar font already call it and then `rebuildSidebar`, which measures;
- a `gtk-xft-dpi` change **is** visible immediately, because GtkSettings' own `notify` class closure
  updates the font map before any connected handler runs.

The host-free/app-target split moved accordingly (root `CLAUDE.md` hoist rule): the measurement needs a
GTK widget and lives in the app target, while `LinuxSidebarPolicy` keeps the `max(220, measured)`
pinning, the `[sidebarWidthMin, sidebarWidthMax]` invariant, `clampSidebarWidth`, and the new
`persistedSidebarWidth`, all still unit-covered in `LinuxPolicyTests`.

### The other round-2 findings

- **Live text-scale change now recomputes the floor.** `App.swift` connects `notify::gtk-xft-dpi` and
  `notify::gtk-font-name` on `gtk_settings_get_default()` once, app-wide (the same shape as the
  existing `notify::dark` observer), and rebuilds every window's sidebar. Round 1 declined this on
  "the setting is changed rarely and from outside the app" grounds, which was weakest exactly where it
  mattered: toggling GNOME "Large Text" with agterm running took the required column from 210 to 230px
  against a 220px request and left the sidebar clipped for the whole session.
- **A floor increase no longer overwrites the persisted sidebar width.** `store.sidebarWidth` is the
  user's REQUEST; the layout is `clampSidebarWidth(request, floor)`. `captureSidebarWidth` persists a
  `notify::position` only when it is not what the current floor makes of the standing request. Round 1
  wrote every clamped-up position straight back, which destroyed the saved width for good — nothing
  pulls the divider back when the floor drops, because the store already matches. Verified over three
  launches on one state dir with DejaVu forced: 13pt → column 220/saved 220; 20pt with a `99+` badge →
  column 229/saved still **220**; back to 13pt → column 220 again. With the write-back restored the
  same run persisted 229 and stayed 229px at 13pt forever.
  The accepted consequence: a legacy record clamped only to the shared 160 is no longer rewritten to
  the floor on load — it lays out at the floor and keeps 160 on disk.
- **The AT-SPI scenario's `column.width < 240` gate is gone**, both because Decision A makes 240 wrong
  and because it inherited the host's text scaling and false-failed at scale ≥ ~1.45 with the actively
  misleading *"the sidebar floor regressed to 252px"*. It is replaced by a first launch that gates the
  floor EXACTLY at 220 with two seeds chosen to make that true on any host: `toolbarMode: hidden`
  (dropping the `AdwHeaderBar`'s 227px minimum, which binds under CI's decoration layout) and the
  smallest sidebar font (measured minimum 165px here, ~176 in DejaVu — under 220 up to ~1.75 scaling).
  **Pinning the scale instead does not work**: GTK 4.22.4 ignores `gtk-4.0/settings.ini` whenever a
  settings portal or an XSETTINGS manager answers first — verified by pointing an isolated
  `XDG_CONFIG_HOME` at a settings.ini setting `gtk-xft-dpi` AND `gtk-font-name`, which changed neither,
  on both backends and with and without `DBUS_SESSION_BUS_ADDRESS`. A user `gtk.css` in the same dir IS
  honoured, which is how the font-family runs above were driven.
- **The focus pill has coverage.** The scenario focuses the workspace, finds the pill by its
  accessible name and asserts containment — which also pins the round-1 claim that a button with an
  explicit `GtkLabel` child still exposes the whole string to AT-SPI (`gtk_button_get_label` returns
  NULL for it; no caller reads it).
- **The duplicate settings read in `refreshSidebarWidthFloor` is gone** with the model: the function
  reads nothing from `AppSettings` any more.

### Negative experiments run against the redesigned gates

| experiment | result |
|---|---|
| re-add `gtk_widget_set_size_request(W(scroller), 240, -1)` | launch 1 fails: *"the sidebar floor is 240px, not the 220px AppStore.sidebarWidthDefault it pins to…"* |
| drop the focus pill's `gtk_label_set_ellipsize` | launch 2 fails: *"the focus pill is pushed past the 220px sidebar column (right edge 220px): x=8 width=402 right=410"* |
| restore the unconditional persisted-width write-back | the three-launch round-trip persists 229 and never returns to 220 |
