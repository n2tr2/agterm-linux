---
paths:
  - "agterm/Views/WorkspaceSidebar*.swift"
  - "agterm/Views/SidebarRowViews.swift"
  - "agterm/Views/SidebarRenameController.swift"
  - "agtermCore/Sources/agtermCore/SidebarDrop.swift"
  - "agtermCore/Sources/agtermCore/SidebarMode.swift"
  - "agtermCore/Sources/agtermCore/Reorder.swift"
  - "agtermUITests/SidebarUITests.swift"
  - "agtermUITests/ReorderUITests.swift"
  - "agtermUITests/FlaggedViewUITests.swift"
  - "agtermUITests/FocusWorkspaceUITests.swift"
  - "agterm-linux/Sources/AgtermLinux/AppControllerSidebar*.swift"
  - "agterm-linux/Sources/AgtermLinux/LinuxSidebarPolicy.swift"
---

## Sidebar

- **Linux port — the sizing contract: any sidebar widget that can hold arbitrary-length USER text must be
  able to report a SMALL minimum width.**
  A GTK4 `GtkLabel` with neither ellipsize nor wrap reports its WHOLE text as its MINIMUM width, and GTK
  never allocates below a minimum — so an un-ellipsized name made the row overflow the sidebar's
  `GtkScrolledWindow`, hard-cutting the name mid-glyph (no `…`) and pushing the status glyph, flag star,
  and unseen badge past the viewport edge.
  It broke EVERY row, not just the long-named one: the scroller's child is a vertical `GtkBox` whose
  minimum is the max over its children and which allocates its full width to each of them,
  so one long name lays every row out at the widest row's width.
  The GTK row already reproduced the macOS LAYOUT — the name is the only `hexpand` child and the trailing
  glyphs hug — so only the truncation half was missing, and **no `hexpand` change is ever needed here**.
  The treatment depends on the KIND of text, and getting that distinction wrong is the trap:
  - **user text ELLIPSIZES** — `gtk_label_set_ellipsize(label, PANGO_ELLIPSIZE_END)` on `makeNameWidget`'s
    plain-label branch (which covers session rows AND workspace headers), on `makeRow`'s flagged-view
    breadcrumb, and on the focus pill.
    `END`, not the `MIDDLE` that `makePaletteRow` uses: a palette title disambiguates at its TAIL
    ("Move Session to <workspace>") while a session or workspace name disambiguates at its HEAD,
    and `END` matches the macOS `.byTruncatingTail` on the very same names.
  - **fixed instructional text WRAPS** — the flagged-empty hint takes `gtk_label_set_wrap(hint, 1)` and
    never ellipsize, because truncating an instruction to `No flagged sessi…` is worse than the bug.
    Wrapping drops its minimum from its longest LINE to its longest WORD: measured under the sidebar CSS,
    156 / 225 / 346px unwrapped against 51 / 74 / 113px wrapped at 9 / 13 / 20pt.
    That wrap is load-bearing at the floor rather than defensive — at the default 13pt the unwrapped hint's
    225px minimum is already 5px WIDER than the 220px floor, and its 346px at 20pt was wider than even the
    old 240px request, so the empty flagged view clipped at large sidebar fonts before this change.
    No explicit wrap mode is needed (`PANGO_WRAP_WORD` is the default and never cuts mid-word), and its
    `GTK_JUSTIFY_CENTER` still reads correctly once wrapped — 3 centered lines at the 220px floor,
    2 at 240px.
  - **hugging trailing glyphs get NOTHING**, and the unseen badge in particular **must NOT be ellipsized**
    even though it is a `GtkLabel` and so pattern-matches the sites that do need it.
    Its natural width is part of the very chrome the sidebar floor below is derived from: under the sidebar
    CSS a `99+` badge measures 27 / 39 / 60px at 9 / 13 / 20pt — the +33 / +45 / +66 badge term in the
    floor's derivation, once the box's 6px spacing is added — and a one-digit `1` measures 14 / 19 / 30px.
    With `PANGO_ELLIPSIZE_END` the same `99+` collapses to a 10 / 14 / 22px minimum, i.e. to a bare `…`,
    which would silently invalidate the floor.
    `appendSection`'s non-workspace section header (the fixed `"Flagged"` literal, min = nat = 48 / 69 /
    106px at 9 / 13 / 20pt) needs nothing either — it can never be the constraint.
- **Linux port — two label sites that are not one-liners.**
  The focus pill gets an EXPLICIT `gtk_label_new` + `gtk_button_set_child` rather than
  `gtk_button_set_label` + an ellipsize on `gtk_button_get_child`: the internal child a button builds for a
  plain label is not part of GTK's contract, so a `GtkLabel` setter on it is the same unguarded-cast hazard
  the `breadcrumb` guard below exists to avoid — and the explicit form is shorter anyway.
  Ellipsis is a RENDER-time effect only, and a button whose child is a `GtkLabel` still exposes that text as
  its accessible name, so the full string round-trips and the AT-SPI `named(...)` lookups keep working —
  the clipping scenario now focuses a workspace and finds the pill BY that name, which pins THAT half:
  a swap that broke the accessible name would fail the lookup.
  The pill's ELLIPSIZE is a separate claim and is pinned separately, by the no-growth gate described in the
  scenario bullet below — the containment check beside it cannot pin it, because the floor follows the
  content and an un-ellipsized pill widens the column instead of overflowing it.
  What the swap DOES give up is `gtk_button_get_label`, which returns NULL for this button; nothing reads
  it today, and anything that wants the text must go through the child label or the accessible name.
  In `makeRow`, label-only calls must be guarded on the FULL `flaggedView && renaming?.id != s.id`
  condition that CHOSE the plain label — hoisted into the `breadcrumb` binding so the two cannot drift —
  because the weaker `if flaggedView` also reaches the `GtkEntry` that `makeNameWidget` returns during a
  rename and raises `gtk_label_set_xalign: assertion 'GTK_IS_LABEL (self)' failed`.
  ⚠️ **KNOWN GAP: that critical has NO automated gate, and cannot cheaply get one.**
  `launch()` in `atspi_smoke.py` sends the app's stdout AND stderr to `DEVNULL`, so no scenario can observe
  a GTK critical at all; capturing it would be a one-line change to a helper all fourteen scenarios share.
  The reason it was not made is the OTHER half: nothing the suite can drive reaches this code path.
  An inline rename inside the flagged view needs `renaming?.id == s.id` while `sidebarMode == .flagged`, and
  the flagged breadcrumb is a PLAIN `gtk_label_new` with no double-click gesture, so the only routes in are
  the `rename_session` keybind and the palette entry, both of which land on
  `AppController.startRenameActive` — both KEYBOARD, and there is no `g_action` for either (the app
  exports only `reveal` and the window-scoped `preferences`).
  Adding an `xdotool` keypress here would also break the maintainer's ONLY local way to run this scenario
  (`env -u HYPRLAND_INSTANCE_SIGNATURE …` on Wayland, where the xdotool focus path cannot address a native
  Wayland window).
  It is covered instead by the plan's Task 9 substitute (A): the offscreen C probe drove rename-in-flagged-view
  with a `g_log` handler counting criticals — 0 after the fix, exactly 2 before it
  (`invalid cast from 'GtkEntry' to 'GtkLabel'` and the `gtk_label_set_xalign` assertion).
  If a scenario ever needs keyboard input here anyway, capture stderr in `launch()` at the same time and
  assert on `Gtk-CRITICAL` / `GLib-GObject-CRITICAL` — but re-run the WHOLE suite, since every scenario
  would start observing criticals it currently discards.
- **Linux port — the sidebar's minimum width is ONE derived value,
  `AppController.sidebarWidthFloor`, on the paned START CHILD.**
  It replaced BOTH the shared `AppStore.sidebarWidthMin` (160) and a hardcoded
  `gtk_widget_set_size_request(scroller, 240, -1)`.
  Two constraints was one too many: the 240 silently won, so the 160 clamp in `captureSidebarWidth` never
  fired and `gtk_paned_set_position(AppStore.sidebarWidthDefault)` (220) was pushed straight back up to
  240 and re-persisted there — every real window record on disk held `"sidebarWidth": 240`.
  **Never add a second `size_request` anywhere on the sidebar widget tree**; a request on the scroller
  wins over the paned child's and reintroduces exactly that drift.
  Only the PINNING is host-free (`LinuxSidebarPolicy.sidebarWidthFloor(measuredContentMinimum:)`,
  unit-covered in `LinuxPolicyTests`); the MEASUREMENT itself needs a GTK widget and therefore lives in
  the app target's `AppControllerSidebarSplit.refreshSidebarWidthFloor`, which measures, pins, pushes the
  result onto the start child, and re-lays the divider out.
  The short version is that the floor is PINNED to `AppStore.sidebarWidthDefault` (220) so the default
  width stays REACHABLE, and rises above it only where the measured content minimum demands it.
  The shared `AppStore.sidebarWidthMin` was deliberately NOT raised: it is `agtermCore`, so a Linux chrome
  measurement must never move the macOS floor.
  Only the LOWER bound is Linux-owned; `AppStore.sidebarWidthMax` (560) stays shared.
- **Linux port — the floor is MEASURED off the live sidebar, never modelled.**
  `refreshSidebarWidthFloor` calls `gtk_widget_measure(sidebarBox, GTK_ORIENTATION_HORIZONTAL, …)` at the
  END of every `rebuildSidebar`, so the number always matches the widgets that exist.
  A fitted line was tried first and REPLACED: the row minimum depends on the theme's row padding, the icon
  set, the desktop's text scaling (`applySidebarFontSize` emits CSS `pt` and GTK resolves `pt` through
  `gtk-xft-dpi`, so GNOME's "Large Text" widens the row exactly like a bigger sidebar font — 16pt at 1.25
  and 20pt at 1.0 both measure 210px), and — the case that killed the model — the font FAMILY resolved
  from `gtk-font-name`, which the sidebar CSS never sets.
  Same fully decorated row at 20pt / scale 1.0: 210px in Noto Sans, 214 in Cantarell, 216 in Liberation
  Sans, but **229 in DejaVu Sans**, the default sans on many distros — 9px past a line fitted to this
  box's font, i.e. the reported clipping, smaller.
  Measuring is right for every family, theme and icon set with nothing to keep calibrated, and it deleted
  both `decoratedRowMinimum` and `AppController.textScaleFactor()`.
  Three consequences to keep in mind.
  (1) A measure taken WITHOUT rebuilding is stale for a pure CSS font-size reload — GTK revalidates a CSS
  node on the next frame, so `gtk_widget_measure` straight after `gtk_css_provider_load_from_string` still
  reports the OLD size (GTK 4.22.4).
  That is why `applySidebarFontSize` does NOT refresh the floor and both Settings paths that change the
  sidebar font (`setSidebarFontSize`, `resetWindowAppearance`) call it and then `rebuildSidebar`.
  A `gtk-xft-dpi` change, by contrast, IS visible immediately, because GtkSettings' own `notify` class
  closure updates the font map before any connected handler runs.
  (2) A live text-scale change must re-measure: `App.swift` connects `notify::gtk-xft-dpi` and
  `notify::gtk-font-name` on `gtk_settings_get_default()` once, app-wide (the same shape as the
  `notify::dark` observer), and rebuilds every window's sidebar.
  Without it, toggling GNOME "Large Text" mid-session left the floor where it was and the sidebar clipped
  for the rest of the session — measured 210 → 230px of required column against a 220px request.
  When the measurement lands above 220 the floor follows it exactly, with NO name allowance on top: the
  correct requirement at large text is only that the decorated chrome fit INSIDE the floor, so the glyphs
  stay whole and the name simply truncates harder — deriving an allowance there instead would answer
  "I cannot narrow the sidebar" by making the sidebar LESS narrowable.
  (3) The `sidebarBox` measure is not the whole column.
  The floor is pushed onto the paned START CHILD and the `GtkScrolledWindow` sits between them, so a
  vertical scrollbar that is NOT an overlay indicator claims real width before the box is allocated any —
  `refreshSidebarWidthFloor` therefore adds `AppController.sidebarScrollbarOverhead()` to the measurement.
  Measured on GTK 4.22.4 with this exact widget shape: with `gtk-overlay-scrolling` FALSE a 15px bar
  shrank an 84px viewport to 69 and carried the row's trailing badge exactly 15px past the edge — the same
  clipping, 15px further in.
  It is harmless only while the floor is PINNED (39px of slack at 13pt) and the slack reaches zero exactly
  at the large font × text scale where the floor follows the measurement instead, i.e. the population
  Decision A deliberately serves.
  The overhead is ZERO in the DEFAULT configuration (overlay scrolling on, the bar floats over the content
  and takes no layout width), which is why it is CONDITIONAL on the setting rather than a flat constant:
  adding it unconditionally would widen the floor for everyone to buy nothing, and at 20pt it would push a
  210px measurement over the 220px pin.
  The setting is LIVE, so `App.swift` observes `notify::gtk-overlay-scrolling` next to `gtk-xft-dpi` and
  `gtk-font-name`.
  ⚠️ **Do NOT measure the LIVE scroller's `gtk_scrolled_window_get_vscrollbar` there.**
  Its width is CSS-ANIMATED across the setting change, so inside the `notify` handler — and still one idle
  turn later — it reports the OLD 6px indicator width and only settles at 15px several main-loop turns on,
  which would bake the stale number straight into the floor.
  The overhead is measured off a THROWAWAY `GtkScrolledWindow` instead: an unrooted one never takes the
  `.overlay-indicator` class, so its bar reports the non-overlay width immediately and with no transition
  (probed: a constant 15 in both modes, against the live bar's animated 6 ↔ 15).
- **Linux port — ⚠️ the floor MAY now exceed 240, and that is a DELIBERATE override of the original
  "never wider than today's 240" gate.**
  The plan this work came from required the new floor never to exceed the 240px `size_request` it
  replaced.
  That gate was crossed on purpose after the measurement replaced the model: above roughly 27 EFFECTIVE
  points (sidebar font size × desktop text scaling) the measured content minimum passes 240, and the
  floor goes with it — so a user at 20pt under GNOME "Large Text" 1.5, or at 20pt in DejaVu Sans plus any
  scaling, gets a minimum sidebar WIDER than they had before.
  Accepted: the chrome physically needs that width, and whole glyphs beat a clipped badge.
  Do NOT re-introduce a 240 ceiling anywhere — not in the policy, not in the AT-SPI scenario (whose old
  `column.width < 240` assertion false-failed on any text-scaled desktop and was replaced, see the gate
  bullet below).
- **Linux port — a LAYOUT change moves the divider only; `store.sidebarWidth` stays the user's REQUEST.**
  `captureSidebarWidth` persists a `notify::position` only when it is NOT what the current LAYOUT makes of
  the standing request — `LinuxSidebarPolicy.persistedSidebarWidth(observed:requested:minimum:layoutMaximum:)`
  returns `nil` otherwise — and `refreshSidebarWidthFloor` re-lays the divider out through
  `applySidebarWidth`, i.e. at
  `laidOutSidebarWidth(requested: store.sidebarWidth, minimum: sidebarEffectiveMinimum, layoutMaximum:)`,
  without writing anything.
  Both policy functions take that bound as `minimum:` and never as the content floor — the label is named
  for the EFFECTIVE minimum on purpose, because passing `sidebarWidthFloor` to it is exactly the bug the
  next bullets describe.
  This is load-bearing, not tidiness: the floor rises with the sidebar font and the desktop text scale,
  GTK clamps the divider up when it does, and writing THAT back overwrites the saved width for good —
  nothing pulls the divider back when the floor later drops, because the store already matches it.
  Verified end-to-end (DejaVu Sans forced through an isolated `gtk-4.0/gtk.css`, three launches over one
  state dir): 13pt → column 220, saved 220; 20pt with a `99+` badge → column 229, saved still **220**;
  back to 13pt → column 220 again.
  With the write-back restored, the same run persisted 229 and the sidebar stayed 229px at 13pt forever.
  **BOTH bounds of "what the layout makes of the request" must be the LAYOUT's own, not the sidebar
  content's** — each leg was a real, measured drift, and each ratchets:
  - the LOWER bound is `AppController.sidebarEffectiveMinimum`, NOT `sidebarWidthFloor`.
    Passing the content floor read GTK's clamp up to the start child's own minimum as a DRAG, so on every
    desktop that draws client-side window buttons (the DEFAULT — see the effective-floor bullet below) a
    fresh window rewrote its saved width to the `AdwHeaderBar` minimum.
    Reproduced against the real binary under Xvfb: a seeded 220 came back at the start child's own
    minimum instead — 235 in the reference environment the effective-floor bullet below measures.
    It is the same drift this whole bullet exists to remove, reborn at the header's number instead of the
    old 240 `size_request`'s.
  - the UPPER bound is GtkPaned's own `max-position`, which shrinks with the WINDOW.
    Without it, narrowing the window past the saved width persisted the capped divider and destroyed the
    wider request for good (measured 400 → 349 at a 350px window).
    A fresh paned reports `max-position = G_MAXINT` (2147483647) and `min-position = 0` before its first
    allocation — probed, NOT the 0 an earlier note claimed — so the pre-allocation state is already
    unbounded and needs no special case.
    The policy nonetheless ALSO reads a non-positive maximum as unbounded, which is now purely defensive
    cover for a failed `g_object_get_property`; do not "tighten" that guard on the belief that 0 is the
    real pre-allocation value.
  ⚠️ **A third case is not a bound at all: when the EFFECTIVE MINIMUM exceeds the shared
  `AppStore.sidebarWidthMax` (560), `persistedSidebarWidth` persists NOTHING.**
  Above that the layout cannot honour ANY request, so every position it produces is an artifact rather
  than a drag, and the two sides disagree BY CONSTRUCTION: `clampSidebarWidth` caps the layout's answer at
  the maximum while GTK clamps the divider up to the real minimum.
  Worked example (`LinuxPolicyTests` pins it): requested 220, effective minimum 700, GTK clamps to 700,
  `laidOutSidebarWidth` answers 560, the 140px gap reads as a DRAG, and the clamped 560 overwrites the
  user's 220 permanently — the same ratcheting write-back the other two legs describe, driven by the
  shared MAXIMUM instead of the minimum.
  `sidebarEffectiveMinimum` is a raw `gtk_widget_measure` of the start child and is NOT capped (only
  `sidebarWidthFloor` is), so an extreme text scale, a theme, or a user `gtk.css` puts it there.
  The guard belongs on the DISCRIMINATOR, not on `captureSidebarWidth`'s corrective `width >= minimum`
  arm: "the layout could not honour the request" is a statement about the layout, and that arm only ever
  sees a width already clamped below the minimum.
  With the guard in place that arm can no longer fail, so it is a kept invariant rather than a live
  branch — do not delete it on the strength of that, it is what keeps the corrective `set_position` off a
  layout GTK would bounce forever.
  The consequence to accept is that a legacy record clamped only to the shared 160 is NO LONGER rewritten
  to the floor on load; it lays out at the floor and keeps 160 on disk, which is what "the store holds the
  request, not the answer" means.
  **The cap is only half the story: something must also put the width BACK when the window widens again.**
  Not persisting the cap keeps `store.sidebarWidth` at 400, but GTK does not re-lay the divider out on its
  own, and — the part that makes this non-obvious — it does not even emit `notify::position` for the
  widening.
  Probed on GTK 4.22.4: narrowing the paned allocation to 350 emits `notify::max-position` AND
  `notify::position` (both at pos=349), while widening back to 900 emits `notify::max-position` ALONE and
  the divider stays at 349.
  So `buildSidebarSplit` connects `notify::max-position` to `applySidebarWidth`, the window-width
  counterpart of the header re-apply below; without it the sidebar sits at the narrow window's cap
  indefinitely in an idle window, which is a NEW symptom the cap introduced (the older build destroyed the
  400 instead, trading data loss for a stuck divider).
  **BOTH sidebar-paned notify handlers resolve the controller through `controllerForWidget(paned)`, never
  `Unmanaged.passUnretained(self)` signal data** — the same idiom the split paned's own `onPanedPosition`
  uses.
  GTK emits paned notifications while a closing window unmaps, and `windowWillClose` drops the controller
  from `gWindows` BEFORE that (the ordering `onWindowActive` already documents), so unretained signal data
  can be dereferenced after the controller is deallocated; resolving through the live registry degrades to
  a clean no-op.
  It also no-ops for the seeding calls inside `AppController.init` — the paned is not parented to the
  window until later and `gWindows[windowID]` is not assigned until init returns — which costs nothing,
  because `applySidebarWidth` has just written the position the discriminator would compare against.
  ⚠️ **That handler runs INSIDE GtkPaned's own `size_allocate`, so `applySidebarWidth` MUST cap itself at
  `max-position`** (it does, through `LinuxSidebarPolicy.laidOutSidebarWidth`, which
  `persistedSidebarWidth` shares so the two cannot disagree about what the layout's answer is).
  **Sharing the FUNCTION is not enough — both must also pass the same `minimum:`, and that minimum is
  `sidebarEffectiveMinimum`, never the content floor `sidebarWidthFloor`.**
  They differ in the DEFAULT configuration, because `toolbarMode` defaults to `compact` and so shows the
  sidebar `AdwHeaderBar`: the content floor is the 220 pin while the paned start child measures 235 in
  the reference environment the effective-floor bullet below owns (the single home of these numbers).
  `applySidebarWidth` shipped passing the content floor, so it laid the divider out at 220 — below the
  minimum GTK will honour — and since `persistedSidebarWidth` reads that same `position` property back to
  tell a drag from a layout, the 220 it had just written could only read as a DRAG.
  Every allocation therefore manufactured a phantom drag and then cancelled it through
  `captureSidebarWidth`'s past-the-maximum branch, which exists for an unrelated purpose: two extra
  `set_position` and two to three re-entrant `gtk_widget_measure` per resize allocation, with the correct
  final geometry resting on that accidental rescue.
  Passing the effective minimum converges in a single `set_position` with no corrective round-trip;
  verified against the real binary under Xvfb, the final geometry is byte-identical
  (seeded 220 → column 235 saved 220; seeded 400 → 400, narrowed to 349, widened back to 400;
  legacy 160 → column 235 with 160 still on disk).
  A `gtk_paned_set_position` past the maximum from there is NOT re-clamped: the `queue_allocate` it
  triggers is swallowed by the allocation already in flight, so the start child stays allocated at the
  over-wide position — measured, an uncapped re-apply of 400 left a 400px sidebar inside a 350px paned
  with the terminal squeezed to 0, which is worse than the bug it fixes.
  Capped, the narrowing notify re-applies the number GTK just picked and nothing moves.
  It cannot feed back either: a position change can never move `max-position`, which tracks only the
  paned's allocation and its children's minimums.
  Probed over a full narrow→widen cycle: 4 apply calls, 3 position notifies, 3 max-position notifies,
  divider back at the request, store never rewritten.
- **Linux port — `sidebarWidthFloor` is NOT the effective floor everywhere, because the paned start child
  is the `AdwToolbarView`, not the scroller.**
  The sidebar cannot narrow below
  `max(sidebarWidthFloor, AdwHeaderBar minimum, sidebar bottom-bar minimum)` — the toolbar view's own
  minimum is the max over its top bar, its bottom bar, and its content, and the bottom bar (the footer
  with New Workspace / New Session / Flagged) is a real term that is easy to forget, measured 138px on
  both decoration layouts.
  The header term is decoration-dependent:
  `LinuxDesktopEnvironment.hidesClientSideWindowButtons()` emits `":"` where the compositor draws the
  window buttons (Hyprland) and `"close,minimize,maximize:"` everywhere else, including CI's X11 runner.
  **This bullet is the SINGLE home of these measurements** — every other note and doc comment that needs
  one points here rather than restating it, because three sites once carried three different numbers for
  the same quantity.
  Re-measured on GTK 4.22.4 / libadwaita 1.9.2 at text scale 1.0 in the REFERENCE environment (the CI
  X11/CSD session: Xvfb + openbox, a fresh `HOME`, so the chrome font resolves to GTK's own default —
  `Adwaita Sans 11`): **96px with `":"` and 235px with the three buttons**, ~46px per button.
  The paned start child therefore measures **235** in the DEFAULT configuration, i.e.
  `max(220 pin, 235 header, 138 bottom bar)` — confirmed twice over, by a standalone GTK probe of the same
  widget tree and by the real binary (seeded 220 → column 235, saved 220; seeded 160 → column 235, 160
  still on disk).
  ⚠️ **These are environment-relative, so re-measure before trusting any absolute number here.**
  The header term is NOT chrome-font independent, whatever an earlier revision of this bullet claimed:
  its minimum moves ~5px per point of the DESKTOP chrome font (`gtk-font-name` — NOT the sidebar font
  setting, which the header never sees, since `.agterm-sidebar` is on the scroller), measured
  220 / 225 / 230 / 235 / 240px for the three buttons at 8 / 9 / 10 / 11 / 12pt.
  It is not text-scale independent either — the `":"` header measures 96 / 106 / 121 / 146px at scale
  1.0 / 1.25 / 1.5 / 2.0.
  That is the likeliest origin of the 88px / 227px pair this bullet used to quote and the 225 the bullets
  above used to: the SAME quantity measured where the chrome metrics resolved ~1.5pt smaller, not a
  different quantity and not a different decoration layout.
  Re-running the ORIGINAL probe today reproduces 96 / 235, so what moved was the environment, not the
  widget tree.
  So with the sidebar header SHOWN the sidebar bottoms out at the floor on Hyprland-style desktops and at
  the header's minimum elsewhere; in hidden-toolbar mode the header is gone and the floor is the bound
  everywhere.
  Any claim of the form "X is now the single floor" is wrong.
  That max is `AppController.sidebarEffectiveMinimum(_:)`, a `gtk_widget_measure` of the paned START CHILD,
  and everything that has to reason about what GTK will actually DO with the divider reads it rather than
  `sidebarWidthFloor`.
  It is measured at `notify::position` time and deliberately NOT cached beside the floor: the header's
  window-control minimum does not exist until the window is ROOTED, so the same measure taken from
  `AppController.init` reports the bare size request and only the post-map one reports the real number.
  It also tracks a mid-session header toggle a frame earlier than GtkPaned's own `min-position`, which
  only updates on the NEXT allocation.
  For the same reason `applyToolbarMode` calls `refreshSidebarWidthFloor` after showing or hiding the
  header: GTK clamps the divider UP when the header appears but never pulls it back down when it goes
  away, so without the re-apply the sidebar keeps the header's width for the rest of the session.
- **Linux port — no `gtk_label_set_width_chars` / `gtk_editable_set_width_chars` floor, deliberately.**
  The width the name can claim already falls out of `sidebarWidthFloor` in PIXELS, so a second floor in
  CHARACTERS on the label is a redundant constraint that would have to be re-derived on every change to the
  sidebar font range or the row chrome — and set too high it re-creates the very bug it guards against.
  The rename `GtkEntry` needs nothing either: its minimum measures **18px** (natural 168) and is font-size
  independent, because the `.agterm-sidebar label` CSS matches `label` nodes while an entry is an
  `entry` > `text` pair — and `gtk_editable_set_width_chars` RAISES that minimum monotonically on GTK
  4.22.4 (~8px per character, 1→26px through 6→66px), so adding one manufactures a floor it does not have.
- **Linux port — the regression gate is the AT-SPI scenario `sidebar-narrow-clipping`**
  (`verify_sidebar_narrow_clipping` in `agterm-linux/tests/atspi_smoke.py`), which seeds a narrow sidebar
  through the legacy `workspaces.json` migration and then runs FOUR launches.
  Each launch is its OWN module-level helper — `sidebar_pin_gate`, `sidebar_containment_sweep`,
  `sidebar_measurement_gate`, `sidebar_window_width_gate` — with `verify_sidebar_narrow_clipping` left
  as the seed-then-call orchestrator that carries the scenario docstring;
  the shared containment helpers (`sidebar_column_now`, `sidebar_settled`, `sidebar_fits`,
  `sidebar_does_not_widen`, `sidebar_row_fits`) sit beside them rather than nested inside one launch.
  Launch 2 is the containment sweep: the 20pt sidebar font (the thinnest-margin configuration), one row
  decorated at runtime, and every descendant's right edge asserted inside the column — in tree mode, for
  the workspace header's `+` button, for the focus pill of a focused workspace, for the flagged view's
  breadcrumb row, and for the wrapped flagged-empty hint.
  ⚠️ **Containment ALONE is a VACUOUS check for every site whose un-truncated text still fits under
  `AppStore.sidebarWidthMax` (560), and that is most of them.**
  The floor FOLLOWS the measured content, so a label that lost its ellipsize WIDENS the column rather than
  overflowing it, and `sidebar_fits` then passes against a column the regression itself sized.
  Measured against the shipped binary, with a user `gtk.css` raising ONE site's minimum to 400px:
  `.agterm-focus-pill label` took the column 220 → 450 with the pill's right edge at 408 (contained), and
  `.agterm-sidebar label.dim-label` took it 220 → 400 with the hint's right edge at 400 (contained);
  only `.agterm-sidebar label` at 700px, which clamps the floor at 560, produced a real overflow.
  What survives as a genuine containment gate is therefore only the two sites whose text clears that cap:
  the 40-character session name (~528px at 20pt plus chrome) and the ~68-character flagged breadcrumb.
  The focus pill and the flagged-empty hint are gated on the column **NOT GROWING** instead
  (`sidebar_does_not_widen`, against the tree-mode column captured once): correctly truncated, both are far
  narrower than a decorated row — the pill collapses to `✕ …` plus its padding, the wrapped hint to its
  longest WORD — so neither can move a column the rows already sized, at any font size or text scale,
  while losing the ellipsize/wrap makes each report its whole string, which is wider than the row chrome.
  Both gates are PROVEN to bite: dropping `gtk_label_set_ellipsize` from the pill grew the column
  235 → 413px and dropping `gtk_label_set_wrap` from the hint grew it 235 → 354px, each failing ONLY the
  no-growth assertion while the `sidebar_fits` call immediately before it passed.
  The workspace header's `+` is a BACKSTOP, not a discriminator: the header name goes through the same
  `makeNameWidget` as the row name, so the long session name is what actually gates its ellipsize.
  It measures BOTH edges off the sidebar's own `role="scroll pane"` node: that widget IS the column (the
  clipping boundary, so its allocation tracks the paned position), while **any node INSIDE it** — viewport,
  content box, list box, the row's parent box — inherits the same overflow under the bug and makes the
  assertion vacuously true.
  Taking the left edge from it too is load-bearing, not tidiness: AT-SPI WINDOW coordinates are relative to
  the toplevel SURFACE, which under client-side decorations includes the shadow inset, so the sidebar's
  left edge is not reliably 0.
  ⚠️ **It is not reliably NON-NEGATIVE either, and `window_extents` must never treat a negative origin as
  "not yet allocated".**
  GTK reports WINDOW coordinates relative to the toplevel's CONTENT area, which sits INSIDE the CSD resize
  border, so under X11/CSD every leftmost widget has a negative x — measured under the Xvfb + openbox
  session CI runs, the frame reports `x=-5` and the fully allocated 220px sidebar scroll pane reports
  `x=-5 y=-5 w=220 h=648`.
  A `bounds.x < 0` term in that guard therefore rejected a perfectly good box, `sidebar_column` never
  resolved, and the scenario timed out on its very FIRST check under CI while passing on the maintainer's
  Wayland session — the failure mode that made this branch red.
  The SIZE alone (`width <= 1 or height <= 1`) is the whole pre-allocation test; every caller is
  origin-relative, since `sidebar_column` picks the minimum x and `sidebar_fits` compares
  `column.x + column.width` against `box.x + box.width`.
  That column must be **re-read immediately before EVERY containment check**
  (the `sidebar_column_now` helper),
  never captured once up front: each step — decorating the row, focusing the workspace, switching to
  flagged mode, dropping the last flag — goes through `rebuildSidebar` → `refreshSidebarWidthFloor`, which
  re-measures and can move the divider either way, so a hoisted limit makes a NARROWING step pass
  vacuously and a widening one false-fail with a clipping message that is not a clipping.
  Launches 1 and 3 are the two halves of the FLOOR gate — the assertions a regression cannot satisfy by
  moving the yardstick (launch 2 has no yardstick of its own: it measures the column against itself,
  either at the same instant or against its own earlier value) — and **neither
  covers the other**, because `sidebarWidthFloor` PINS the measured content minimum to
  `AppStore.sidebarWidthDefault` and FOLLOWS it above that.
  Launch 1 is the PIN half: nothing added to the sidebar, `column.width == 220` EXACTLY.
  It needs two seeds to be exact on any host: `toolbarMode: hidden` (the `AdwHeaderBar`'s own minimum
  where the compositor does not draw the window buttons — CI's layout, 235px in the reference
  environment above — would otherwise bind instead of the floor) and the SMALLEST sidebar font
  (its measured row minimum, 165px here and ~176px in DejaVu Sans, stays under 220 for host text
  scaling up to ~1.75).
  Launch 3 is the MEASUREMENT half: the same two seeds plus a user `gtk-4.0/gtk.css` under an isolated
  `XDG_CONFIG_HOME` raising `.agterm-sidebar label` to `min-width: 300px`, then `column.width > 220` AND
  the row contained inside it.
  **The measurement half is not optional cover — launch 1 cannot detect a floor that stopped being
  measured**, despite once claiming to: replacing the whole `gtk_widget_measure` with the constant 220
  satisfies launch 1 by construction, and satisfies launch 2 as well, because this scenario's one-digit
  badge keeps the 20pt content minimum under the pin on every font family, so the measured branch is never
  reached there.
  `min-width` in px rather than a font size on purpose: the raised minimum is then the same number on every
  font family and text scale, and stays well under `AppStore.sidebarWidthMax` (560) — a floor capped by the
  max would overflow its column and report as a clipping instead.
  The class is on the SCROLLER, so the lever raises exactly the sidebar CONTENT the floor measures and
  cannot widen the toolbar view's other terms.
  **The scale CANNOT be pinned instead** — GTK 4.22.4 ignores `gtk-4.0/settings.ini` whenever a settings
  portal or an XSETTINGS manager answers first, verified by pointing an isolated `XDG_CONFIG_HOME` at a
  settings.ini setting `gtk-xft-dpi` AND `gtk-font-name`, which changed neither, on both backends and with
  and without `DBUS_SESSION_BUS_ADDRESS`.
  (A user `gtk-4.0/gtk.css` in that same isolated config dir IS honoured, which is how the font-family
  cases above were driven end-to-end, and what launch 3 rides on.)
  The gate deliberately asserts NO upper bound at 20pt: the old `column.width < 240` false-failed on any
  text-scaled desktop (the app correctly floored at 243–293 and the scenario reported it as a regression),
  and 240 is no longer a ceiling at all — see the Decision-A bullet above.
  Launch 4 is the WINDOW-WIDTH gate for the `notify::max-position` re-apply: it patches a 400px
  `sidebarWidth` into the per-window record the earlier launches wrote, reuses launch 1's seeds so the
  floor is the plain 220 pin, asserts the column comes back at 400, then drives a narrow → widen cycle
  with `window resize` and asserts it lands at 400 again.
  Its narrow leg is CONDITIONAL and prints a SKIP when it does not take, which is not laziness: `window
  resize` is `gtk_window_set_default_size`, and a Wayland compositor is free to ignore it for a window it
  manages — a tiled Hyprland window keeps its tile, verified, so the cycle cannot run on the maintainer's
  own session at all.
  It runs under the Xvfb + openbox session `scripts/test-linux-ui.sh` builds, which is how CI executes
  this suite, and that is where the gate is authoritative.
  That is EXECUTED, not assumed: under a reconstructed X11 session (`dbus-run-session` + `xvfb-run`
  `1440x900x24` + `openbox --sm-disable`, `XDG_CURRENT_DESKTOP` unset as on a GitHub runner) the leg ran
  and printed *"the sidebar was capped to 349px by the narrow window and returned to its 400px request
  when the window widened again"*.
  Until the `bounds.x < 0` fix above it could NOT have run — the scenario aborted at launch 1 — so
  `notify::max-position` had no executed coverage anywhere; do not let that claim drift back to an
  assumption.
  On a box where `dbus-run-session` cannot activate the a11y registry through systemd (Manjaro), the
  script's own run dies with `agterm app not present in the AT-SPI tree`; starting
  `/usr/lib/at-spi-bus-launcher --launch-immediately` and `/usr/lib/at-spi2-registryd` by hand inside that
  session is what makes the reconstruction work.
  The parts that run everywhere stay HARD: the 400px restore on launch, and a post-quit re-read of the
  record asserting `sidebarWidth` is still 400 (cover for the cap ever being persisted again).
  A sixth trap, found when CI first ran this scenario: containment is asserted on the row's PARTS, never
  on the `GtkListBoxRow`'s own box.
  A row's AT-SPI extents include the Adwaita `.navigation-sidebar > row` margin, which is empty space, and
  the row's inset inside the column is theme-dependent — at the same 220px column the row starts 19px in on
  libadwaita 1.9.2 (Arch) and 28px in on Ubuntu noble, so an identical ~195px row box ends 5px clear on one
  host and 2px past on the other.
  Those 2px are margin, not chrome: the badge is the rightmost thing drawn and still ends ~6px inside the
  row's own right edge.
  Do not "restore" a row-box containment check to tighten the gate — it fails on some libadwaita versions for
  a clip nobody can see, and the parts check is both the reported symptom and strictly sharper (a label that
  stopped ellipsizing overflows by hundreds of pixels; the breadcrumb experiment reports 1065px against a
  560px column).
  Five more traps it encodes: the inside-the-scroller one above; the re-read-the-column one above; the
  un-pinnable scale; the badge it drives is a one-digit `1`, ~30px narrower at 20pt than the `99+` worst
  case, which is covered by widget measurement (`LinuxPolicyTests`) rather than by this scenario; and
  driving the decorations REBUILDS the row, which GTK allocates only while the window renders — `launch()`
  parks the window on a silent Hyprland workspace, stalling the frame clock so every rebuilt row reports a
  zero extent and the settle polls time out, so run it locally as `env -u HYPRLAND_INSTANCE_SIGNATURE …`
  (that one bites first, and it originates in `launch()`, which every scenario calls).
- The sidebar is an AppKit `NSOutlineView` (`WorkspaceSidebar`, an `NSViewRepresentable`),
  not a SwiftUI `List` — chosen for native cross-workspace drag-and-drop.
  Its `@MainActor` `Coordinator` is the data source/delegate, backed by `AppStore`.
  Outline items are cached reference-type `SidebarNode`s, reused across reloads for stable identity (expansion/selection
  survive `reloadData`).
- **Drag reorder (sessions AND workspaces).**
  The Coordinator's `validateDrop`/`acceptDrop` now HONOR `proposedChildIndex` for sessions and feed the
  host-free `SidebarDrop` helpers so validate and accept agree exactly instead of force-retargeting every
  drop to `NSOutlineViewDropOnItemIndex` — enabling intra-workspace SESSION reorder (drop between rows for
  a precise slot) AND precise cross-workspace placement (a cross-workspace drag now lands at the drop
  position, no longer always-append).
  Workspace ROWS are draggable too: a second pasteboard type `com.umputun.agterm.workspace` is added
  to `registerForDraggedTypes` (LOAD-BEARING — without it AppKit never delivers validate/accept for workspace
  drags) and `pasteboardWriterForItem` emits it (carrying the workspace UUID) for workspace nodes.
  **Workspace reorder is a TOP-LEVEL move, but it does NOT use AppKit's proposed `item`/`childIndex`.**
  With workspaces expanded their sessions fill the gaps between workspace rows,
  so `NSOutlineView` only ever proposes drops INTO a workspace's children (`proposedItem != nil`) — never
  the clean root between-rows slot — so the old `proposedItem == nil`-only gate rejected EVERY drop and
  made workspace drag impossible once any workspace held sessions (the real-world state).
  `resolveWorkspaceMove` therefore IGNORES the proposed item/index and derives the insert slot from the
  CURSOR Y against the workspace ROWS' midpoints (`info.draggingLocation` → `rect(ofRow:).midY`,
  sessions ignored): the slot is the count of workspace rows whose midpoint sits above the cursor,
  so the top half of a row drops before it and the bottom half after it — reachable everywhere.
  It still feeds that slot to the host-free `SidebarDrop.resolveWorkspace` for the post-removal/no-op
  math, and `validateDrop` highlights it via `setDropItem(nil, dropChildIndex:)`.
  Covered by `ReorderUITests.testReorderWorkspaceOntoSessionRow` (drag a workspace onto a session row
  — the case the `proposedItem == nil` gate broke).
  The session helper still HONORS `proposedChildIndex` (sessions are real same-level siblings,
  so the outline proposes precise between-rows slots). It supports single-row and multi-row drags:
  dragging from a selected session writes the full `sidebarSelectionIDs` block to the pasteboard in visual
  order; dragging an unselected session writes just that row.
  Both session and workspace drops feed `SidebarDrop`. For a single session, `resolveSession` applies the
  same-parent downward `childIndex - 1` post-removal adjustment (only when `sourceIndex < childIndex`).
  For a multi-selection, `resolveSessions` removes every dragged session first and inserts the whole block
  at the post-removal slot, preserving the selected visual order and handling same-workspace / cross-workspace
  mixes atomically. Workspace reorders use `resolveWorkspace` with the same remove-then-insert convention.
  The PURE index arithmetic (drop-on-row `sessionIndex + 1` redirect, source-removal adjustment,
  cross-workspace vs same-parent index spaces, batch block insertion, and no-op checks) lives host-free in
  `agtermCore.SidebarDrop` (`resolveSession`/`resolveSessions`/`resolveWorkspace`), table-tested in
  `SidebarDropTests`; the Coordinator helpers only do the AppKit/store glue (read the pasteboard, resolve
  ids → indices via `AppStore.sessionLocation(ofSession:)`) and feed `SidebarDrop`, so the trickiest part
  is unit-covered without the fragile XCUITest drag.
- Add affordances live in a bottom bar in `WindowContentView`: a workspace button and a session menu (New Session
  / Open Directory…).
  The two session actions are also on each workspace row's right-click menu.
  Each workspace ROW additionally carries a hover-revealed `+` (New Session) affordance (`SidebarCellView`'s
  `addButton`, id `workspace-add-session`, shown on `mouseEntered`, same action as the footer New Session)
  — a SEPARATE toggleable Interface element (`workspaceAddSession`, gated in `mouseEntered`; see [[settings]]).
- **A single click anywhere on a workspace ROW toggles its expansion** (not just the disclosure triangle),
  so the whole row is the hit target.
  Wired via the outline's `action` (`Coordinator.handleSingleClick`) — which fires on a genuine click,
  NEVER during a drag, so workspace drag-reorder is untouched — and guarded against the disclosure-triangle
  region (`frameOfOutlineCell`) so a triangle click doesn't double-toggle.
  The toggle is DEFERRED by `NSEvent.doubleClickInterval` and CANCELED by `handleDoubleClick`,
  so a double-click (rename) doesn't flip the workspace open/closed on its way into edit mode
  (instant-toggle was tried and rejected: AppKit commits the first click of a double before it knows a
  second is coming, so instant forces a visible toggle-then-revert flicker on rename).
  This is pure click-routing over the existing per-workspace `expandItem`/`collapseItem` (an exempt case
  under the control keep-in-sync rule): the row click itself adds NO control command.
  The per-workspace collapse/expand IS driven over the socket by `workspace.collapse`/`workspace.expand`
  (distinct from the ALL-workspaces `sidebar.expand`/`sidebar.collapse`), but by a SEPARATE path that does
  NOT route through this click handler: `AppActions.setWorkspaceExpanded` persists `isExpanded` on the
  store DIRECTLY (source of truth, so it survives a hidden sidebar whose Coordinator is torn down), THEN
  posts `.agtermSetWorkspaceExpanded` for the Coordinator's `setWorkspaceExpandedNotified` to sync only the
  live outline row + tracked set (see the Control API rule).
  Covered by `SidebarUITests.testClickWorkspaceRowTogglesExpansion`.
- **A session ROW click reveals a blocked session's pane-tagged pane.**
  `Coordinator.outlineViewSelectionDidChange` selects the clicked session (`selectSession`) then — async,
  after the selection + the sidebar's own focus-restore settle — calls `AppActions.revealActiveBlockedPane()`,
  so clicking a session whose agent blocked in its split (right) or scratch pane lands you on THAT pane,
  not the plain focused pane.
  It is a no-op (plain `focusActiveSession`) for an IDLE session (no status set),
  so ordinary clicks are unaffected — the reveal never dismisses a merely-shown scratch (a non-idle
  nil-tagged block is treated as `left`/main).
  This matches attention-nav, plain session nav, the command palettes, and idle auto-follow,
  which all route through the same helper (see the Menu/actions + Notifications rules).
  Covered by `PaneAwareStatusUITests.testSidebarClickRevealsBlockedSplitPane`.
- Accessibility identifiers `session-row`, `workspace-row`, `edit-field`,
  and `add-session` back the XCUITests.
  Note the rename field surfaces as a `TextField` for sessions and a `StaticText` for workspaces,
  so UI tests match `edit-field` by identifier across element types.
- **Sidebar multi-selection.**
  `AppStore.selectedSessionID` remains the durable active terminal. The broader sidebar selection is
  a private transient array in host-free `AppStore`, exposed through `sidebarSelectionIDs` normalized to
  the current visible session order so batch actions are deterministic in tree and flagged modes.
  AppKit Shift-click and Command-click update the outline selection; `outlineViewSelectionDidChange`
  mirrors it through `AppStore.setSidebarSelection(_:)`. `allowsEmptySelection` stays TRUE because a
  focused workspace can intentionally hide the active session and `syncSelection` must be able to
  `deselectAll(nil)` in that state.
  Right-click follows standard Mac list behavior: inside the current multi-selection it keeps the whole
  selection for the context menu, outside it narrows to the clicked row. Context menu target resolution
  is `AppStore.sidebarSelectionTargets(forContextSession:)`, which filters through the visible projection.
  Batch row actions: move uses `AppStore.moveSessions`, close uses `AppActions.closeSessions(_:in:)` →
  `AppStore.softCloseSessions`, flag uses `AppActions.toggleFlags(_:in:)` → `setFlag(_:forSessions:)`,
  and clear-status loops `setAgentIndicator` once per selected session (loop-equivalent to `session status idle`).
  SINGLE-selection-only row actions (shown only when the context menu resolves to exactly ONE session, since
  they have no sensible batch meaning): Rename, **Duplicate Session** (right after Rename), and Reveal in Finder.
  **Duplicate Session** creates a fresh session — a plain new login shell — in the SAME workspace, inserted directly
  AFTER the source, rooted at the source's focused-pane cwd (`Session.focusedCwd`, the same directory the row
  shows and Reveal in Finder opens), then selects + focuses it.
  ONLY the directory carries over: the duplicate does NOT inherit the source's custom name, initial command,
  split, scratch, status, flag, font size, or watermark — it is "New Session seeded with the source's cwd",
  not a clone of state.
  Its control half is `session.duplicate` (`agtermctl session duplicate [--target]`), which reads back off
  `tree` as a new node right after its source carrying the source's focused-pane cwd — equal to the source
  node's `tree.cwd` unless the source is a split focused off its primary pane (then `tree.cwd` reports the
  primary and the two differ), see the Control API rule.
- **Flagged working-set view (`AppStore.sidebarMode` `.tree`/`.flagged`).**
  `SidebarMode` (`agtermCore/SidebarMode.swift`, `String`-backed `Codable`/`Sendable`) drives a per-window
  MODE toggle between the normal two-level tree and a FLAT list of just the flagged sessions.
  A session is flagged via the observed `Session.flagged: Bool`; the flat list is the PURE derived projection
  `AppStore.flaggedSessions` (`workspaces.flatMap(\.sessions).filter(\.flagged)`,
  already in tree order — workspace-then-session).
  No second container: a session always has exactly one home workspace, the flag dies with the session
  and survives a workspace move (the projection re-sorts).
  The ONE `NSOutlineView` renders either source — `numberOfChildrenOfItem`/`child`/`isItemExpandable`
  branch on `store.sidebarMode`; in `.flagged` the root's children are `flaggedSessions` as flat,
  non-expandable rows labeled `session : workspace` (the session `displayName`,
  then the owning workspace name) with the base leading icon — a plain terminal for a single session,
  the split-rectangle for a split one so a split stays distinguishable (the FILLED flag variant is suppressed;
  every row here is flagged) — plus the usual `StatusIconView` + `BadgeView`.
  A row click routes through the existing `selectSession`; the mode switch is VIEW-ONLY (never re-selects/refocuses).
  Drag-reorder is DISABLED in `.flagged` mode.
  An empty flagged set shows a centered, non-scrolling empty-state hint ("No flagged sessions. / Right-click
  a session → Flag.") overlaid in the scroll view, re-tinted on `.agtermAppearanceChanged` and toggled
  by `updateEmptyStateHint` (visible only in `.flagged` with `flaggedSessions.isEmpty`).
  Mutators: `AppStore.setFlag(_:forSession:)` / `setFlag(_:forSessions:)` (clean no-op + no save on
  unknown ids or unchanged values, prune the transient selection when the current sidebar mode hides the
  changed rows), `clearFlags()` (single save + prune), `setSidebarMode(_:)` (save).
  GUI half: the bottom-bar `flagged-view-toggle` button (right of the trailing `Spacer()`,
  2-state flag/checkmark glyph, tinted `chromeText`, flips `sidebarMode` and animates via `WindowContentView`'s
  `.animation(value:)`), the row context-menu Flag/Unflag → `AppActions.toggleFlags(_:in:)`,
  the View-menu Show Flagged/Show All + Flag Session + Clear Flagged, the ⌃⇧P palette entries,
  and the two `BuiltinAction`s `toggleFlaggedView`/`toggleFlag` (expressible/keyless).
  **Clear Flagged** is a plain menu/palette item (NOT a `BuiltinAction`,
  mirroring Reload/Edit Keymap) → `AppActions.clearFlags()` with a light confirm alert when the set is
  non-empty (skipped under the XCUITest launch, like the quit-confirm).
- **Tree-mode flagged indicator (filled-icon variant).**
  In `.tree` mode a flagged session's row swaps its leading icon to the FILLED SF Symbol variant of its
  base glyph — `terminal.fill` for a single session, `rectangle.split.2x1.fill` for a split (the same
  filled split symbol the titlebar shows for a SHOWN split; outline = unflagged,
  filled = flagged) — via the cached `flaggedSessionIcon`/`flaggedSplitSessionIcon`
  template images, tinted with the chrome/theme color.
  It is a pure SF Symbol swap (`Self.rowIcon(...)`), NOT a composited corner badge — same-size,
  so it is inherently layout-shift-free.
  `flagged` is folded into the row's `RowContent` (Equatable), so a flag/unflag re-renders ONLY that
  row (per-row `reloadItem`).
  The filled variant is tree-mode only — the flat flagged view shows the unfilled base icon,
  so a split session still gets the split-rectangle to stay distinguishable;
  only the FILLED flag variant is suppressed there (every row is flagged).
- **Focus filter (`AppStore.focusedWorkspaceID`).**
  A per-workspace toggle collapses the `.tree` to a single root: `visibleWorkspaces` is the focused workspace
  when `focusedWorkspaceID` is set AND still present, else ALL workspaces — the source of truth the tree
  filters on (the data source maps `store.visibleWorkspaces` in `.tree`).
  Focus is ORTHOGONAL to flagged: the flat flagged view ignores focus (it always shows the full cross-workspace
  set).
  `setFocusedWorkspace(_:)` (delta-guarded so callers stay idempotent, nil unfocuses,
  saves) is driven by the workspace-row context-menu Focus/Unfocus → `AppActions.focusWorkspace(_:)`,
  the bottom-bar `focus-pill` ("<name> ✕" — the focused workspace name with no "Focused:" prefix,
  shown only while focused, ✕ unfocuses), `AppActions.focusActiveWorkspace()` (targets `currentWorkspaceID`,
  analogous to `deleteActiveWorkspace`) wired to `BuiltinAction.focusWorkspace` + a View-menu/palette
  "Focus Workspace", and `AppActions.clearFocus()` (a plain menu/palette "Clear Focus",
  NOT a `BuiltinAction`).
  `removeWorkspace` clears focus when the removed workspace was the focused one.
- **Scoped session navigation (the VISIBLE/FILTERED set).**
  Session navigation operates over `AppStore.navigableSessions`, NOT the whole tree:
  `sidebarMode == .flagged ? flaggedSessions : visibleWorkspaces.flatMap(\.sessions)` — i.e. the flagged
  set in `.flagged` mode, the focused workspace's sessions when a workspace is focused (tree mode),
  else ALL sessions.
  Computed LIVE (`visibleWorkspaces` already collapses to the focused workspace or the full tree,
  including the stale-focus-id fallback), so clearing the flag/focus naturally restores the full set.
  `navigateSession(_:)` flattens `navigableSessions` for EVERY direction — next/prev/first/last AND attention-nav
  (next-attention/prev-attention scope to the filtered set too) — keeping the same "no/invalid selection
  → first of the filtered list", "next/prev WRAP within the filtered set (like attention-nav)" semantics
  over the filtered list.
  This is shared by `session.go` (control, no ControlServer change — it already routes through `navigateSession`),
  the ⌥⌘↑/↓ + ⌃⌥↑/↓ menu/palette nav, the Ctrl-Tab MRU switcher (`SessionSwitcher.begin()` scopes its
  candidate set to `store.navigableSessions.map(\.id)`; the MRU ORDER still comes from `sessionRecency`),
  AND the ⌃P fuzzy session palette (`AppActions.paletteSessions()` lists `store.navigableSessions`,
  so the searchable set matches the visible sidebar — in a focused workspace ⌃P shows only that workspace's
  sessions, in flagged mode only the flagged ones).
  This SUPERSEDES the earlier "global nav reveals its target" behavior.
- **Focus×selection auto-unfocus contract (load-bearing, now the cross-set safety net).** Because nav
  is scoped, its targets are ALWAYS in-set, so nav never crosses the focus boundary.
  `selectSession` still AUTO-CLEARS focus when the newly selected session is NOT in the focused workspace
  (`workspace(forSession:)?.id != focusedWorkspaceID` → `focusedWorkspaceID = nil`) — but this now only
  fires for an EXPLICIT cross-set select: `session.select <id>` of a hidden session,
  a notification reveal, or a move/close that reselects elsewhere.
  This keeps the active session inside the visible set for those cases, which also keeps `currentWorkspaceID`
  (new-session placement) consistent with NO special-case.
  No-op when unfocused or nothing selected.
  The contract is ONE-DIRECTIONAL by design: an explicit cross-set select auto-unfocuses (reveal),
  but focusing a workspace that does NOT contain the active session deliberately does NOT reselect or
  switch the active terminal — focus is a pure view filter, never a terminal switch,
  so the active session's terminal keeps rendering while the sidebar shows no selection until the next
  select (the focus pill signals the state, and it self-heals on the next `selectSession`/`addSession`).
  This stranded-selection state is intentional, not a bug.
- **Mode/focus-aware reconcile signal.**
  The reconcile `TreeShape` is computed from the MODE-selected/filtered roots:
  in `.tree` it is `visibleWorkspaces` → `(workspaceID, sessionIDs)` (so a focus flip re-shapes),
  in `.flagged` it is a SINGLE flat group keyed on a stable pseudo-id (`flaggedShapeID`,
  so within flagged mode only a change to the flagged list — not a fresh per-call id — rebuilds).
  A `lastMode` flip swaps the whole data source and forces a `rebuildAndReload` regardless of the shape
  diff; `sidebarMode`, `focusedWorkspaceID`, and each session's `flagged` are folded into the `updateNSView`
  dependency read so a mode/focus/flag change is seen.
  **Task 9 expansion-restore fix:** `NSOutlineView` discards the expansion state of items DROPPED from
  the data source during a flagged-mode reload, so expanded workspace ids are tracked independently in
  `expandedWorkspaceIDs` via the `outlineViewItemDidExpand`/`outlineViewItemDidCollapse` delegate callbacks
  (and `expandAll`) and re-applied in `rebuildAndReload` (`expandItem` for each tracked id),
  surviving the round-trip through flagged mode.
- **Expand / collapse all workspaces (per-window).**
  Two sidebar tree operations: **Expand Workspaces** (`AppActions.expandAllWorkspaces(in:)` → the Coordinator's
  existing `expandAll`, every workspace open) and **Collapse Workspaces** (`collapseOtherWorkspaces(in:)`
  → the Coordinator's `collapseOthers`, every workspace collapsed EXCEPT the active session's `currentWorkspaceID`,
  kept expanded + `scrollRowToVisible`'d).
  Both keep `expandedWorkspaceIDs` in sync (so the state survives a flagged-mode round-trip).
  Per-window scoping rides a notification (`.agtermExpandWorkspaces`/`.agtermCollapseWorkspaces`) posted
  with the TARGET window's `AppStore` as the object; each Coordinator registers its observer with `object: store`,
  so only the matching window's sidebar acts (unlike the rename notifications,
  which self-scope via the selected-session guard).
  This object-scoping is what lets the control path target ANY open window.
  Graceful no-op in `flagged` mode (no workspace rows).
  GUI surfaces (frontmost window): View ▸ Expand/Collapse Workspaces (plain keyless items,
  disabled with no store or in flagged mode) + the ⌃⇧P palette (tree-mode only).
  Control: `sidebar.expand`/`sidebar.collapse` resolve the target store via `resolvePlacementStore(window)`
  (frontmost by default, the global `--window` selector for any open window) and call the `(in:)` variants
  — so unlike the frontmost-only `sidebar`/`sidebar.mode`, these can drive a background window's tree
  (see the Control API catalog).
- **Persistence (per-window, no version bump).**
  `Session.flagged` persists via `SessionSnapshot.flagged: Bool?` (decode → `false`),
  `sidebarMode` via `Snapshot.sidebarMode: SidebarMode?` (→ `.tree`), `focusedWorkspaceID` via `Snapshot.focusedWorkspaceID: UUID?`
  (naturally Optional → nil), and each workspace's expand/collapse state via `WorkspaceSnapshot.collapsed: Bool?`
  (decode → `false` → expanded).
  All four Optional fields, so legacy JSON with none of the keys decodes to the unflagged / `.tree`
  / unfocused / expanded defaults without throwing (the load-fresh-on-decode-failure contract) — no `Snapshot`
  version bump.
  `collapsed` is stored as the INVERSE of `Workspace.isExpanded` and only WRITTEN when collapsed (`true`);
  an expanded workspace omits it, so an all-expanded tree serializes byte-identically to a legacy snapshot,
  and "lack of the field = expanded" holds.
  The sidebar Coordinator seeds `expandedWorkspaceIDs` from `Workspace.isExpanded` in `makeNSView`
  (`seedExpansionFromModel`, replacing the old unconditional `expandAll`) so a collapsed workspace restores
  collapsed.
  **Only a GENUINE user toggle persists.**
  The `outlineViewItemDidExpand`/`DidCollapse` callbacks write back via `AppStore.setWorkspaceExpanded(_:expanded:)`
  (a PER-workspace mutator, so toggling one row never rewrites another's saved state), and `expandAll`/`collapseOthers`
  persist the whole tree once via `setWorkspacesExpanded(_:)`.
  A `suppressExpansionPersist` flag is set around every PROGRAMMATIC `expandItem`/`collapseItem` — the launch/`rebuildAndReload`
  re-apply, the `syncSelection` reveal, and the focused-workspace force-expand — so those update the VISUAL
  `expandedWorkspaceIDs` (needed for the flagged-mode round-trip) WITHOUT touching the persisted `isExpanded`.
  This is what makes a deliberate collapse durable: revealing a session inside a collapsed workspace (nav,
  notification click, or the launch-time active-session reveal) or focusing it shows the row but does NOT
  un-collapse it on disk — the collapse survives until the user expands the row themselves.
  The active session is still force-revealed on launch (`syncSelection`), so it is never hidden inside a
  collapsed workspace; the row just re-collapses on the next launch (its persisted state is untouched).
  Round-trips + legacy-decode (incl. explicit `collapsed:false`) covered in `PersistenceTests`,
  per-workspace + whole-tree mutators / no-op-no-write in `AppStoreOrganizationTests`, and the
  collapse-survives-relaunch + reveal-does-not-repersist end-to-end cases in `SidebarUITests`.
