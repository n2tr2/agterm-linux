import agtermCore

enum LinuxSidebarPolicy {
    /// The CSS that scales the sidebar rows to the configured sidebar font size (nil = the shared
    /// default): the row text size, plus a `min-height` derived from the shared
    /// `AppSettings.sidebarRowHeight` that lowers libadwaita's `navigation-sidebar` row pin.
    /// That height is a FLOOR, not a cap — taller content still grows the row.
    /// Read the Linux row-height bullet in `.claude/rules/sidebar.md` before changing this: it records
    /// which libadwaita rule is overridden, why the row's inner box is deliberately left alone, and what
    /// the emitted floor actually measures at each supported size.
    static func sidebarCSS(fontSize: Double?) -> String {
        let size = AppSettings.clampSidebarFontSize(fontSize ?? AppSettings.defaultSidebarFontSize)
        let rowHeight = Int(AppSettings.sidebarRowHeight(fontSize: size))
        return """
            .agterm-sidebar label { font-size: \(size)pt; }
            .agterm-sidebar .navigation-sidebar > row { min-height: \(rowHeight)px; }
            """
    }

    @MainActor
    static func flaggedRowLabel(for session: Session, in store: AppStore) -> String {
        if let workspace = store.workspace(forSession: session.id) {
            return "\(session.displayName)  —  \(workspace.name)"
        }
        return session.displayName
    }

    /// The Linux sidebar's minimum width in pixels for a MEASURED sidebar-content minimum — the single
    /// width constraint on the sidebar, replacing both the shared `AppStore.sidebarWidthMin` (160) and a
    /// hardcoded 240 request that used to sit on the scroller.
    ///
    /// `measuredContentMinimum` is the measured `sidebarBox` minimum plus any non-overlay scrollbar
    /// width, which `refreshSidebarWidthFloor` adds together; this side only pins it. The floor is
    /// PINNED to `AppStore.sidebarWidthDefault` (220) so the default width stays REACHABLE — a floor
    /// above it would silently widen every fresh window, the drift this replaced — and rises above it
    /// exactly where the measured content no longer fits, with NO name allowance on top: the correct
    /// requirement at large text is only that the chrome fit INSIDE the floor, so the glyphs stay whole
    /// and the name simply truncates harder.
    ///
    /// ⚠️ The floor therefore MAY exceed 240, the hardcoded request this replaced, at roughly 27
    /// effective points (sidebar font size × desktop text scaling) and above. That is a DELIBERATE
    /// override of the plan's original "never wider than today's 240" gate: those users get a wider
    /// minimum than before because the chrome physically needs it, and whole glyphs beat clipping.
    ///
    /// The result is clamped to `AppStore.sidebarWidthMax`, which is the invariant
    /// `clampSidebarWidth(_:minimum:)` needs: were the floor ever to exceed the max, the clamp would write
    /// back a position GTK cannot honor and `gtk_paned_set_position` ↔ `notify::position` would feed back
    /// on each other instead of settling.
    @MainActor
    static func sidebarWidthFloor(measuredContentMinimum: Double) -> Double {
        let measured = measuredContentMinimum.isFinite ? measuredContentMinimum.rounded(.up) : 0
        return min(AppStore.sidebarWidthMax, max(AppStore.sidebarWidthDefault, measured))
    }

    /// Clamp a proposed sidebar width into `[minimum, AppStore.sidebarWidthMax]`. Both legs are live: the
    /// `max` raises a restored-below-the-minimum width (`AppStore.load` only clamps to the shared 160) to
    /// the width the layout can actually honor, and the `min` caps a drag at the shared maximum.
    @MainActor
    static func clampSidebarWidth(_ proposed: Double, minimum: Double) -> Double {
        min(AppStore.sidebarWidthMax, max(minimum, proposed))
    }

    /// What the LAYOUT makes of a standing request: the request through the sidebar's minimum, then
    /// capped by what the current WINDOW width leaves for the divider.
    ///
    /// Both sides of the sidebar-width contract read it, and they must agree exactly or they fight:
    /// `AppController.applySidebarWidth` LAYS the divider out at this number, and
    /// `persistedSidebarWidth` below decides an observed position is a drag precisely when it is NOT
    /// this number.
    ///
    /// ⚠️ Agreeing means the same ARGUMENTS, not merely the same function. `minimum` is the paned START
    /// CHILD's measured minimum (`AppController.sidebarEffectiveMinimum`), which folds in the sidebar
    /// `AdwHeaderBar` and the footer bottom bar — never the sidebar CONTENT floor `sidebarWidthFloor`,
    /// which is smaller in the DEFAULT configuration and made this lay the divider out below the
    /// minimum GTK will honour, so `persistedSidebarWidth` read the position back as a phantom drag on
    /// every allocation. The sidebar rule's effective-floor bullet carries the derivation and the
    /// measured numbers.
    ///
    /// `layoutMaximum` is GtkPaned's own `max-position`. A paned reports `G_MAXINT` before its first
    /// allocation (probed on GTK 4.22.4, alongside `min-position = 0`), which is genuinely unbounded
    /// and needs no special case; a NON-POSITIVE maximum is nonetheless read as unbounded too, so a
    /// property read that failed degrades to the pre-cap behaviour instead of collapsing the sidebar.
    @MainActor
    static func laidOutSidebarWidth(requested: Double, minimum: Double, layoutMaximum: Double) -> Double {
        min(clampSidebarWidth(requested, minimum: minimum), layoutMaximum > 0 ? layoutMaximum : .infinity)
    }

    /// The width to PERSIST for an observed divider position, or `nil` when the position is one the
    /// LAYOUT produced rather than one the user dragged to.
    ///
    /// `requested` is the persisted width — the user's REQUEST, never the layout's answer to it. The
    /// distinction is load-bearing: the minimum moves with the sidebar font and the desktop text scale,
    /// and GTK clamps the divider up whenever it rises, so persisting that clamped position would
    /// overwrite the saved width for good — nothing pulls the divider back when the minimum later
    /// drops, because the store already matches it. A position within a pixel of the layout's answer to
    /// the standing request is therefore skipped; anything else is a drag and is persisted (clamped, so
    /// a drag past the shared maximum records the maximum).
    ///
    /// Both BOUNDS of that answer must be the layout's own, not the sidebar content's, and both are
    /// `laidOutSidebarWidth`'s — see it for what `minimum` has to be, and why. `layoutMaximum` matters
    /// here for a second reason: it shrinks with the WINDOW, and without it a window narrowed past the
    /// saved width persists the capped divider and destroys the wider request for good (400 → 349
    /// measured at a 350px window).
    ///
    /// ⚠️ An effective minimum ABOVE `AppStore.sidebarWidthMax` persists NOTHING AT ALL, because the
    /// layout cannot honour ANY request there and every position it produces is therefore an artifact
    /// rather than a drag. `clampSidebarWidth` caps its answer at that shared maximum, so above it the
    /// two disagree BY CONSTRUCTION: at an effective minimum of 700 GTK clamps the divider to 700 while
    /// the layout answers 560, the discriminator below reads the 140px gap as a drag, and the clamped
    /// 560 overwrites the user's smaller request for good — the same permanent write-back the whole
    /// function exists to prevent, just driven by the shared MAXIMUM instead of the minimum.
    /// The guard is deliberately on the discriminator rather than on the caller's corrective branch:
    /// "the layout could not honour the request" is a statement about the LAYOUT, and the caller's
    /// `width >= minimum` check cannot make it, since it only ever sees a width already clamped below
    /// the minimum. It costs the ability to record a genuine drag in that state, which is not a real
    /// loss: no reachable position there is persistable without being clamped into corruption first.
    /// `AppController.sidebarEffectiveMinimum` measures the paned start child and is NOT capped (only
    /// `sidebarWidthFloor` is), so an extreme text scale, a theme, or a user `gtk.css` can put it there.
    @MainActor
    static func persistedSidebarWidth(observed: Double, requested: Double, minimum: Double,
                                      layoutMaximum: Double) -> Double? {
        guard minimum <= AppStore.sidebarWidthMax else { return nil }
        let laidOut = laidOutSidebarWidth(requested: requested, minimum: minimum,
                                          layoutMaximum: layoutMaximum)
        guard abs(observed - laidOut) >= 1 else { return nil }
        return clampSidebarWidth(observed, minimum: minimum)
    }
}
