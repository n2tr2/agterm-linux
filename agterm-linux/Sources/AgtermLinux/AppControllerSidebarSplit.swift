import CGtk
import agtermCore

@MainActor
extension AppController {
    /// Re-derive `sidebarWidthFloor` by MEASURING the live sidebar content, push it onto the paned start
    /// child, and re-apply the user's requested width through it.
    ///
    /// It is a direct `gtk_widget_measure` of `sidebarBox` rather than a model of the row chrome. The
    /// minimum depends on the theme's row padding, the icon set, the font FAMILY resolved from
    /// `gtk-font-name`, and the desktop's text scaling (GTK resolves the sidebar CSS's `pt` through
    /// `gtk-xft-dpi`) — a line fitted to one box's UI font under-reported DejaVu Sans, the default sans
    /// on many distros, by 9px at 20pt and clipped the badge there. Measuring is right for all of them
    /// with nothing to keep calibrated, and the box is the widest sidebar site by construction: it is
    /// the parent of every row, header, list box, focus pill, and the wrapped empty-state hint.
    ///
    /// Called at the END of every `rebuildSidebar`, so the number always matches the widgets that
    /// exist, and from the app-level `gtk-xft-dpi`/`gtk-font-name` observer (see `App.swift`) so a live
    /// GNOME "Large Text" toggle moves the floor instead of leaving the sidebar clipped for the rest of
    /// the session. Measuring WITHOUT rebuilding is stale for a pure CSS font-size reload — GTK
    /// revalidates the CSS node on the next frame, so `gtk_widget_measure` right after
    /// `gtk_css_provider_load_from_string` still reports the OLD size (verified on GTK 4.22.4) — which
    /// is why `applySidebarFontSize` no longer refreshes the floor itself and both Settings paths that
    /// change the sidebar font call it and then `rebuildSidebar`.
    ///
    /// The floor is NOT the EFFECTIVE floor. The paned start child is the sidebar `AdwToolbarView`, so
    /// the sidebar cannot narrow below the max of this, the `AdwHeaderBar` minimum, and the footer
    /// bottom bar's minimum — `sidebarEffectiveMinimum` is that number, and everything that has to
    /// reason about what GTK will actually do with the divider (`captureSidebarWidth`) reads it instead
    /// of this one. See the sidebar rule for the measured header/bottom-bar numbers and the
    /// decoration-layout split that makes the header the binding term on desktops that draw client-side
    /// window buttons.
    ///
    /// Also called from `applyToolbarMode`, because showing or hiding the sidebar header moves that
    /// effective minimum: GTK clamps the divider UP when the header appears but never pulls it back
    /// down when it goes away, so without the re-apply the sidebar would keep the header's width for
    /// the rest of the session.
    func refreshSidebarWidthFloor() {
        guard let paned = splitView, let sidebar = gtk_paned_get_start_child(paned) else { return }
        var minimum: Int32 = 0
        gtk_widget_measure(W(sidebarBox), GTK_ORIENTATION_HORIZONTAL, -1, &minimum, nil, nil, nil)
        sidebarWidthFloor = LinuxSidebarPolicy.sidebarWidthFloor(
            measuredContentMinimum: Double(minimum) + sidebarScrollbarOverhead())
        gtk_widget_set_size_request(sidebar, Int32(sidebarWidthFloor), -1)
        applySidebarWidth(paned)
    }

    /// The width the sidebar scroller's own vertical scrollbar takes OUT of the viewport, which the
    /// `sidebarBox` measure above cannot see on its own.
    ///
    /// The floor is pushed onto the paned START CHILD (the `AdwToolbarView`) and the
    /// `GtkScrolledWindow` sits between them, so a scrollbar that is not an overlay indicator claims
    /// its width before the box is allocated any: measured on GTK 4.22.4 with this widget shape, a
    /// 15px bar shrank an 84px viewport to 69 and carried the row's trailing badge exactly 15px past
    /// the right edge — the clipping the floor exists to prevent, just 15px further in. It is harmless
    /// only while the floor is PINNED (39px of slack at 13pt), and the slack reaches zero at the large
    /// font × text scale where the floor follows the measurement instead.
    ///
    /// ZERO in the default configuration: `gtk-overlay-scrolling` is on, the bar floats over the
    /// content, and adding anything there would widen the floor for everyone to buy nothing. It is a
    /// LIVE GtkSettings property (GNOME's `org.gnome.desktop.interface overlay-scrolling`, an XSETTINGS
    /// manager, or `gtk-4.0/settings.ini` on a desktop with no settings portal), so `App.swift` observes
    /// it next to `gtk-xft-dpi` and re-measures every sidebar when it flips.
    ///
    /// Measured off a THROWAWAY `GtkScrolledWindow`, not this window's live one, because the live bar's
    /// width is CSS-ANIMATED across that flip: inside the `notify` handler — and still one idle turn
    /// later — the live bar still reports the 6px indicator width and only settles at 15px several
    /// main-loop turns on (probed on GTK 4.22.4), so the observer would bake the stale number into the
    /// floor. An unrooted scrolled window never takes the `.overlay-indicator` class, so its bar reports
    /// the non-overlay width immediately and with no transition — which is the number wanted here,
    /// exactly when the guard above says it applies.
    private func sidebarScrollbarOverhead() -> Double {
        guard !desktopUsesOverlayScrolling(), let probe = OpaquePointer(gtk_scrolled_window_new())
        else { return 0 }
        _ = g_object_ref_sink(RAW(probe))
        defer { g_object_unref(RAW(probe)) }
        guard let bar = gtk_scrolled_window_get_vscrollbar(probe) else { return 0 }
        var minimum: Int32 = 0
        gtk_widget_measure(bar, GTK_ORIENTATION_HORIZONTAL, -1, &minimum, nil, nil, nil)
        return Double(max(0, minimum))
    }

    /// The sidebar's EFFECTIVE minimum: the measured minimum of the paned START CHILD, which is the
    /// width GTK clamps the divider up to. `sidebarWidthFloor` is only the sidebar CONTENT's minimum —
    /// the start child is the sidebar `AdwToolbarView`, so its own minimum is the max over that content
    /// (through the size request `refreshSidebarWidthFloor` pushes onto it), the `AdwHeaderBar`, and the
    /// footer bottom bar.
    ///
    /// Measured HERE, at `notify::position` time, and deliberately NOT cached alongside the floor: the
    /// header's window-control minimum does not exist until the window is ROOTED, so the same measure
    /// taken from `AppController.init` reports the bare size request and only the post-map one reports
    /// the real minimum. It also tracks a header that is hidden or shown mid-session a frame earlier
    /// than GtkPaned's own `min-position`, which only updates on the NEXT allocation.
    /// See the sidebar rule's effective-floor bullet for the measured header/bottom-bar terms.
    private func sidebarEffectiveMinimum(_ paned: OpaquePointer) -> Double {
        guard let child = gtk_paned_get_start_child(paned) else { return sidebarWidthFloor }
        var minimum: Int32 = 0
        gtk_widget_measure(child, GTK_ORIENTATION_HORIZONTAL, -1, &minimum, nil, nil, nil)
        return max(sidebarWidthFloor, Double(minimum))
    }

    /// GtkPaned's own `max-position` — the widest divider position the CURRENT window width leaves
    /// (the allocation minus the handle, since the end child may shrink). Read back off the widget
    /// instead of modelled from the window width and the theme's handle size. A paned reports
    /// `G_MAXINT` here until its first allocation, i.e. already unbounded; `laidOutSidebarWidth` also
    /// reads a non-positive value as unbounded, so a failed property read degrades to no cap.
    private func sidebarLayoutMaximum(_ paned: OpaquePointer) -> Double {
        Double(intProperty("max-position", on: paned))
    }

    /// Read an `int` GObject property. The `GType` is spelled as its fundamental-type id because the
    /// `G_TYPE_*` names are GObject macros Swift cannot import, like the G_TYPE_STRING 64 the drop
    /// payloads use.
    private func intProperty(_ name: String, on object: OpaquePointer) -> Int32 {
        var value = GValue()
        _ = g_value_init(&value, GType(24))   // G_TYPE_INT
        name.withCString { g_object_get_property(GOBJ(object), $0, &value) }
        let result = g_value_get_int(&value)
        g_value_unset(&value)
        return result
    }

    /// Read a `gboolean` GObject property, SEEDED with `fallback` so a read that failed leaves the
    /// caller on its documented default rather than on a zeroed `GValue`.
    private func boolProperty(_ name: String, on object: OpaquePointer, default fallback: Bool) -> Bool {
        var value = GValue()
        _ = g_value_init(&value, GType(20))   // G_TYPE_BOOLEAN
        g_value_set_boolean(&value, fallback ? 1 : 0)
        name.withCString { g_object_get_property(GOBJ(object), $0, &value) }
        let result = g_value_get_boolean(&value) != 0
        g_value_unset(&value)
        return result
    }

    /// GtkSettings' live `gtk-overlay-scrolling`. FALSE means scrollbars are laid out BESIDE the
    /// content and take real width from it, instead of floating over it as slim indicators.
    ///
    /// Seeded TRUE before the read, which is both GTK's default and today's shipped assumption, so a
    /// property read that failed leaves the floor exactly where it is rather than silently widening
    /// every sidebar.
    private func desktopUsesOverlayScrolling() -> Bool {
        guard let settings = gtk_settings_get_default() else { return true }
        return boolProperty("gtk-overlay-scrolling", on: settings, default: true)
    }

    /// Lay the divider out at the user's requested width as the CURRENT floor and the CURRENT window
    /// width allow. Pure layout: it never writes `store.sidebarWidth`, which is what lets a floor that
    /// rose and then fell again — or a window that narrowed and then widened again — return the sidebar
    /// to the width the user actually asked for.
    ///
    /// The `max-position` cap inside `laidOutSidebarWidth` is LOAD-BEARING here, not defensive, because
    /// this also runs from the `notify::max-position` handler — that is, from inside GtkPaned's own
    /// `size_allocate`. A `gtk_paned_set_position` past the maximum from THERE is never re-clamped: the
    /// `queue_allocate` it triggers is swallowed by the allocation already in flight, so the start child
    /// stays allocated at the over-wide position. Probed on GTK 4.22.4: re-applying an uncapped 400
    /// from the narrowing notify left a 400px sidebar inside a 350px paned with the terminal squeezed
    /// to 0. Capped, the same notify re-applies the number GTK just picked and nothing moves.
    ///
    /// ⚠️ The minimum passed here is `sidebarEffectiveMinimum`, the SAME one `captureSidebarWidth`
    /// passes to `persistedSidebarWidth` — NOT the content floor `sidebarWidthFloor`. The two share
    /// `laidOutSidebarWidth` precisely so they cannot disagree about what the layout's answer is, and
    /// feeding them different minimums manufactured a phantom drag on every allocation; see
    /// `LinuxSidebarPolicy.laidOutSidebarWidth` and the sidebar rule for the full derivation.
    func applySidebarWidth(_ paned: OpaquePointer) {
        guard store.sidebarVisible else { return }
        let position = LinuxSidebarPolicy.laidOutSidebarWidth(
            requested: store.sidebarWidth, minimum: sidebarEffectiveMinimum(paned),
            layoutMaximum: sidebarLayoutMaximum(paned))
        gtk_paned_set_position(paned, Int32(position.rounded()))
    }

    /// Build the desktop split as a real GtkPaned so the divider spans the complete window and owns
    /// a native horizontal-resize gesture. The shared store already persists the per-window width.
    func buildSidebarSplit(sidebar: OpaquePointer?, content: OpaquePointer?) -> OpaquePointer {
        guard let sidebar, let content,
              let paned = OpaquePointer(gtk_paned_new(GTK_ORIENTATION_HORIZONTAL)) else {
            fatalError("failed to construct sidebar split")
        }
        splitView = paned
        gtk_widget_add_css_class(W(paned), "agterm-sidebar-split")
        gtk_widget_add_css_class(W(sidebar), "agterm-sidebar-column")
        gtk_paned_set_start_child(paned, W(sidebar))
        gtk_paned_set_end_child(paned, W(content))
        gtk_paned_set_resize_start_child(paned, 0)
        gtk_paned_set_shrink_start_child(paned, 0)
        gtk_paned_set_resize_end_child(paned, 1)
        gtk_paned_set_shrink_end_child(paned, 1)
        gtk_widget_set_visible(W(sidebar), store.sidebarVisible ? 1 : 0)
        // Both handlers resolve the controller through `controllerForWidget`, exactly like the split
        // paned's own `onPanedPosition` — never unretained signal data. GTK emits paned notifications
        // while a closing window unmaps, and `windowWillClose` drops the controller from `gWindows`
        // BEFORE that (the same ordering `onWindowActive` documents), so an unretained pointer can be
        // dereferenced after the controller is gone. Resolving through the live registry degrades to a
        // clean no-op instead.
        connect(paned, "notify::position", unsafeBitCast(onSidebarPanedPosition, to: GCallback.self))
        // The WINDOW-WIDTH counterpart of the header re-apply in `applyToolbarMode`: GTK clamps the
        // divider DOWN when the window narrows past the requested width, but never pulls it back up
        // when the window widens again — and `notify::position` does not even FIRE for the widening,
        // only `notify::max-position` does (probed: narrowing to a 350px paned emits both at pos=349,
        // widening back to 900 emits max-position alone and the divider stays at 349). Without this the
        // sidebar sits at the narrow window's cap while the store still holds the wider request, until
        // some unrelated `rebuildSidebar` happens to re-lay it out — indefinitely, in an idle window.
        //
        // It cannot feed back. `applySidebarWidth` caps itself at `max-position`, so the NARROWING
        // notify re-applies the number GTK just picked and nothing moves; and a position change cannot
        // move `max-position` at all, since that tracks only the paned's allocation and its children's
        // minimums. Probed end to end over a narrow→widen cycle: 4 apply calls, 3 position notifies,
        // 3 max-position notifies, divider back at the requested width, store never rewritten.
        connect(paned, "notify::max-position",
                unsafeBitCast(onSidebarPanedMaxPosition, to: GCallback.self))
        // Seeds the initial position too (through the floor), so the handler is connected first and a
        // legacy record clamped only to the shared 160 lays out at the floor from the very first frame.
        refreshSidebarWidthFloor()
        return paned
    }

    func applySidebarVisibility() {
        guard let paned = splitView, let sidebar = gtk_paned_get_start_child(paned) else { return }
        gtk_widget_set_visible(sidebar, store.sidebarVisible ? 1 : 0)
        applySidebarWidth(paned)
    }

    /// Persist a divider position the USER dragged to. A position the LAYOUT produced — GTK clamping
    /// the divider up to the start child's minimum, or down to the window's `max-position` — is
    /// deliberately dropped; see `LinuxSidebarPolicy.persistedSidebarWidth` for why writing either back
    /// would destroy the saved width permanently.
    func captureSidebarWidth(_ paned: OpaquePointer?) {
        guard let paned, store.sidebarVisible else { return }
        let proposed = Double(gtk_paned_get_position(paned))
        let minimum = sidebarEffectiveMinimum(paned)
        guard let width = LinuxSidebarPolicy.persistedSidebarWidth(
            observed: proposed, requested: store.sidebarWidth, minimum: minimum,
            layoutMaximum: sidebarLayoutMaximum(paned)) else { return }
        // A drag past the shared maximum: cap the layout and let the re-entrant notify persist the cap.
        // `width >= minimum` keeps that off a layout GTK cannot honor, where re-applying the cap would
        // bounce `set_position` off GTK's own clamp forever. It can no longer FAIL: an effective
        // minimum above the shared maximum now returns nil from `persistedSidebarWidth` — the whole
        // state is unpersistable, see the ⚠️ note there — so this is a kept invariant, not a live arm.
        if width != proposed, width >= minimum {
            gtk_paned_set_position(paned, Int32(width.rounded()))
            return
        }
        store.sidebarWidth = width
        layoutSaveDebouncer.schedule(after: 0.4) { [weak self] in self?.store.save() }
    }
}

private let onSidebarPanedPosition: @MainActor @convention(c) (
    OpaquePointer?, OpaquePointer?, gpointer?
) -> Void = { paned, _, _ in
    MainActor.assumeIsolated { controllerForWidget(paned)?.captureSidebarWidth(paned) }
}

/// The window got wider or narrower: re-lay the divider out at the standing request, which is the only
/// thing that pulls the sidebar back up after the narrow window's cap goes away.
private let onSidebarPanedMaxPosition: @MainActor @convention(c) (
    OpaquePointer?, OpaquePointer?, gpointer?
) -> Void = { paned, _, _ in
    guard let paned else { return }
    MainActor.assumeIsolated { controllerForWidget(paned)?.applySidebarWidth(paned) }
}
