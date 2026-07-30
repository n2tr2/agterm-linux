import Foundation
import Testing
import agtermCore
@testable import AgtermLinux

@Suite("Linux-owned policy and adapters")
struct LinuxPolicyTests {
    @Test("resource resolution requires complete sibling resources and preserves precedence")
    func resourceResolution() {
        let complete = [
            "/complete/ghostty/shell-integration", "/complete/terminfo/x/xterm-ghostty",
            "/later/ghostty/shell-integration", "/later/terminfo/x/xterm-ghostty"
        ]
        let resolver = GhosttyResourceResolver(
            candidates: ["relative", "/shell-only/ghostty", "/terminfo-only/ghostty",
                         "/complete/ghostty", "/later/ghostty"],
            fileExists: { complete.contains($0) || $0 == "/shell-only/ghostty/shell-integration"
                || $0 == "/terminfo-only/terminfo/x/xterm-ghostty" }
        )
        #expect(resolver.resolve() == "/complete/ghostty")
        #expect(resolver.terminalName == "xterm-ghostty")

        let incomplete = GhosttyResourceResolver(
            candidates: ["", "relative", "/shell-only/ghostty", "/terminfo-only/ghostty"],
            fileExists: { $0 == "/shell-only/ghostty/shell-integration"
                || $0 == "/terminfo-only/terminfo/x/xterm-ghostty" }
        )
        #expect(incomplete.resolve() == nil)
        #expect(incomplete.terminalName == "xterm-256color")
        #expect(GhosttyResourceResolver.terminalName(resolvedResources: "/share/ghostty") == "xterm-ghostty")
    }

    @Test("URI lists become POSIX path payloads")
    func pasteURIList() {
        let payload = "# copied files\nfile:///tmp/one%20two\nfile:///tmp/three\n"
        #expect(PasteDecoder.posixPaths(fromURIList: payload) == "/tmp/one two /tmp/three")
        #expect(ShellEscape.dropPayload("") == nil)
        #expect(ShellEscape.dropPayload("plain") == "plain")
    }

    @Test("Linux proc cmdline decoding is NUL-delimited")
    func procCmdline() {
        #expect(CommandRestore.parseProcCmdline(Data()) == nil)
        #expect(CommandRestore.parseProcCmdline(Data("zsh\0-c\0echo hi\0".utf8)) == ["zsh", "-c", "echo hi"])
    }

    @Test("Linux starter files remain comment-only or denylist-only")
    func starterFiles() {
        #expect(ConfigPaths.starterGhosttyConfig().contains("agterm-scoped ghostty config"))
        #expect(ConfigPaths.starterRestoreDenylist().contains("tmux\nscreen\nzellij"))
        #expect(GhosttyDefaults.baseConfLines.contains("cursor-click-to-move = false"))
        #expect("  value\n".linuxTrimmedOrNil == "value")
        #expect(" \n".linuxTrimmedOrNil == nil)
    }

    @Test("session switcher starts from the previous MRU entry and wraps")
    func sessionSwitcher() {
        let first = UUID()
        let second = UUID()
        var switcher = SessionSwitcherModel()
        #expect(switcher.begin([first]) == nil)
        #expect(switcher.begin([first, second]) == second)
        #expect(switcher.advance() == first)
        switcher.end()
        #expect(!switcher.isActive)
    }

    @Test("delete prompts use native Linux wording")
    func deletePrompts() {
        #expect(DeletePrompt.workspaceMessage(name: "work", sessions: 1).contains("1 session"))
        #expect(DeletePrompt.workspaceMessage(name: "work", sessions: 2).contains("2 sessions"))
        #expect(DeletePrompt.windowMessage(name: "work").contains("all its workspaces and sessions"))
    }

    @Test("session reports and pane focus mutate the owning shared model")
    @MainActor
    func sessionAdapters() {
        let session = Session(initialCwd: "/start")
        session.hasSplit = true
        let store = AppStore(workspaces: [Workspace(name: "work", sessions: [session])])

        #expect(store.recordPwd("/main", forSession: session.id, isSplit: false))
        #expect(store.recordPwd("/split", forSession: session.id, isSplit: true))
        #expect(store.recordTitle("main", forSession: session.id, isSplit: false))
        #expect(store.recordTitle("split", forSession: session.id, isSplit: true))
        #expect(!store.recordPwd("/main", forSession: session.id, isSplit: false))
        #expect(!store.recordTitle("split", forSession: session.id, isSplit: true))
        store.setPaneFocus(true, forSession: session.id)

        #expect(session.currentCwd == "/main")
        #expect(session.splitCwd == "/split")
        #expect(session.oscTitle == "main")
        #expect(session.splitTitle == "split")
        #expect(session.splitFocused)
        #expect(LinuxSidebarPolicy.flaggedRowLabel(for: session, in: store) == "main  —  work")
    }

    @Test("sidebar CSS derives row height from the shared font-size clamp")
    func sidebarCSS() {
        let standard = LinuxSidebarPolicy.sidebarCSS(fontSize: 13)
        #expect(standard.contains(".agterm-sidebar label { font-size: 13.0pt; }"))
        // The full selector, closing brace included, pins the exact libadwaita rule being lowered.
        #expect(standard.contains(".agterm-sidebar .navigation-sidebar > row { min-height: 28px; }"))
        // Only the row rule may be emitted: Adwaita's inner-box rule is AdwSidebar-scoped and never
        // matches this port's widget tree, so an override there would be inert CSS.
        #expect(!standard.contains("> row > box"))
        // nil means "unset", which resolves to the same shared default as an explicit 13pt.
        #expect(LinuxSidebarPolicy.sidebarCSS(fontSize: nil) == standard)

        let dense = LinuxSidebarPolicy.sidebarCSS(fontSize: 9)
        #expect(dense.contains("font-size: 9.0pt;"))
        #expect(dense.contains("> row { min-height: 24px; }"))

        let large = LinuxSidebarPolicy.sidebarCSS(fontSize: 20)
        #expect(large.contains("font-size: 20.0pt;"))
        #expect(large.contains("> row { min-height: 35px; }"))

        // A hand-edited fractional size keeps its exact point value while the row height rounds.
        let fractional = LinuxSidebarPolicy.sidebarCSS(fontSize: 13.6)
        #expect(fractional.contains("font-size: 13.6pt;"))
        #expect(fractional.contains("> row { min-height: 29px; }"))

        // Out-of-range values clamp to the shared bounds rather than emitting a degenerate row.
        #expect(LinuxSidebarPolicy.sidebarCSS(fontSize: 40) == large)
        #expect(LinuxSidebarPolicy.sidebarCSS(fontSize: 2) == dense)
    }

    @Test("the sidebar floor pins the measured content minimum inside the shared width range")
    @MainActor
    func sidebarWidthFloor() {
        // Anything the sidebar content can fit inside pins the floor to the shared default, so a fresh
        // window's 220px stays REACHABLE and never drifts wider — the regression this replaced. The
        // samples are real GTK 4.22.4 measurements of a fully decorated row: 181px at the default 13pt
        // on this box's UI font, 194px for the same row in DejaVu Sans, 210px at 20pt.
        for measured in [0.0, 181, 194, 210, AppStore.sidebarWidthDefault] {
            #expect(LinuxSidebarPolicy.sidebarWidthFloor(measuredContentMinimum: measured)
                == AppStore.sidebarWidthDefault)
        }
        // …and follows the measurement exactly once the chrome no longer fits: DejaVu Sans at 20pt, and
        // 20pt again under GNOME "Large Text" at 1.25. Both land ABOVE the 240px request this replaced
        // once the effective size passes ~27pt — accepted deliberately, because the chrome needs it.
        #expect(LinuxSidebarPolicy.sidebarWidthFloor(measuredContentMinimum: 229) == 229)
        #expect(LinuxSidebarPolicy.sidebarWidthFloor(measuredContentMinimum: 230) == 230)
        #expect(LinuxSidebarPolicy.sidebarWidthFloor(measuredContentMinimum: 254.2) == 255)
        // A widget GTK has not measured yet, or a nonsensical read, is no floor rather than no sidebar.
        for broken in [-1, Double.nan, Double.infinity] {
            #expect(LinuxSidebarPolicy.sidebarWidthFloor(measuredContentMinimum: broken)
                == AppStore.sidebarWidthDefault)
        }

        // The invariant clampSidebarWidth depends on: a floor above the shared max would make the clamp
        // write back a position GTK cannot honor, and set_position/notify::position would feed back.
        // Only the upper leg can fail — the pin puts the lower bound at 220 by construction, and the
        // loop above already asserts that exactly.
        for measured in stride(from: 0.0, through: 2000.0, by: 25.0) {
            #expect(LinuxSidebarPolicy.sidebarWidthFloor(measuredContentMinimum: measured)
                <= AppStore.sidebarWidthMax)
        }

        let floor = LinuxSidebarPolicy.sidebarWidthFloor(measuredContentMinimum: 181)
        #expect(LinuxSidebarPolicy.clampSidebarWidth(AppStore.sidebarWidthMin, minimum: floor) == floor)
        #expect(LinuxSidebarPolicy.clampSidebarWidth(999, minimum: floor) == AppStore.sidebarWidthMax)
        #expect(LinuxSidebarPolicy.clampSidebarWidth(320, minimum: floor) == 320)
    }

    @Test("a floor-driven divider move is not persisted, so the requested width survives it")
    @MainActor
    func persistedSidebarWidth() {
        // `minimum` is the EFFECTIVE minimum the caller measures off the paned start child, not the
        // content floor. The default maximum is the `G_MAXINT` a paned really reports before its first
        // allocation (probed on GTK 4.22.4, together with `min-position = 0`) — the pre-allocation
        // state the app actually meets, rather than a 0 no GtkPaned ever produces.
        func persisted(observed: Double, requested: Double, minimum: Double,
                       layoutMaximum: Double = Double(Int32.max)) -> Double? {
            LinuxSidebarPolicy.persistedSidebarWidth(observed: observed, requested: requested,
                                                     minimum: minimum, layoutMaximum: layoutMaximum)
        }
        // The floor rises past the saved width (a bigger sidebar font, or GNOME "Large Text"): GTK
        // clamps the divider up and notifies, and persisting THAT would overwrite 220 with 250 for good
        // — nothing pulls the divider back when the floor drops, because the store would already match.
        #expect(persisted(observed: 250, requested: 220, minimum: 250) == nil)
        // The same holds for the legacy record clamped only to the shared 160.
        #expect(persisted(observed: 220, requested: 160, minimum: 220) == nil)
        // …and once the floor drops back the layout returns to the requested width, still unpersisted.
        #expect(LinuxSidebarPolicy.clampSidebarWidth(220, minimum: 220) == 220)
        #expect(persisted(observed: 220, requested: 220, minimum: 220) == nil)
        // The EFFECTIVE minimum leg: the sidebar content pins the floor to 220, but the start child
        // also carries the AdwHeaderBar's window controls, so GTK lays the divider out at 235 (the
        // measurement in the sidebar rule). Reading that as a drag is what rewrote every fresh
        // window's width on a client-side-decorated desktop.
        #expect(persisted(observed: 235, requested: 220, minimum: 235) == nil)

        // A real drag is anything the layout did not produce — wider, or down onto the floor itself.
        #expect(persisted(observed: 300, requested: 220, minimum: 220) == 300)
        #expect(persisted(observed: 220, requested: 300, minimum: 220) == 220)
        #expect(persisted(observed: 250, requested: 300, minimum: 250) == 250)
        // A drag past the shared maximum records the maximum, which the caller also lays the divider
        // out at — the re-entrant notify then sees observed == max != requested and persists it once.
        #expect(persisted(observed: 900, requested: 300, minimum: 220) == AppStore.sidebarWidthMax)
        // Sub-pixel jitter around the standing request is not a drag.
        #expect(persisted(observed: 220.4, requested: 220, minimum: 220) == nil)

        // The `max-position` leg: narrowing the WINDOW caps the divider below the saved request, and
        // persisting that cap destroys the wider request for good (measured 400 → 349 at a 350px
        // window). The cap is not a drag; a drag anywhere below it still is.
        #expect(persisted(observed: 349, requested: 400, minimum: 220, layoutMaximum: 349) == nil)
        #expect(persisted(observed: 300, requested: 400, minimum: 220, layoutMaximum: 349) == 300)
        // A maximum ABOVE the standing request never masks a drag.
        #expect(persisted(observed: 349, requested: 400, minimum: 220, layoutMaximum: 900) == 349)
        // Both readings of "unbounded" agree: the `G_MAXINT` GtkPaned reports before its first
        // allocation, and the non-positive values only a failed property read can produce.
        for unbounded in [Double(Int32.max), 0, -1] {
            #expect(persisted(observed: 349, requested: 400, minimum: 220,
                              layoutMaximum: unbounded) == 349)
        }

        // ⚠️ An effective minimum ABOVE the shared maximum is a LAYOUT constraint, not a drag.
        // Worked example: requested 220, effective minimum 700 (an extreme text scale, a theme, or a
        // user `gtk.css` raising the paned start child's own minimum past `AppStore.sidebarWidthMax`),
        // GTK clamps the divider to 700 while `laidOutSidebarWidth` answers 560 because
        // `clampSidebarWidth` caps at that maximum. Observed 700 ≠ laid-out 560 read as a DRAG and
        // persisted 560, overwriting the user's 220 permanently — the exact class of layout-artifact
        // write-back this discriminator exists to prevent.
        #expect(persisted(observed: 700, requested: 220, minimum: 700) == nil)
        // One pixel past the maximum is already unhonourable, so nothing there is persistable either —
        // including a position the user could plausibly have dragged to.
        #expect(persisted(observed: 561, requested: 220, minimum: 561) == nil)
        #expect(persisted(observed: 800, requested: 220, minimum: 700) == nil)
        // The boundary itself stays LIVE: at exactly the maximum the layout can honour its own answer,
        // so the ordinary discriminator applies and a position equal to it is simply not a drag.
        #expect(persisted(observed: AppStore.sidebarWidthMax, requested: 220,
                          minimum: AppStore.sidebarWidthMax) == nil)
    }

    @Test("the divider is laid out at the request the floor and the window width both allow")
    @MainActor
    func laidOutSidebarWidth() {
        func laidOut(requested: Double, minimum: Double, layoutMaximum: Double) -> Double {
            LinuxSidebarPolicy.laidOutSidebarWidth(requested: requested, minimum: minimum,
                                                   layoutMaximum: layoutMaximum)
        }
        // The window is wide enough: the request stands, raised by the floor and capped by the shared
        // maximum exactly as `clampSidebarWidth` does on its own.
        #expect(laidOut(requested: 400, minimum: 220, layoutMaximum: 899) == 400)
        #expect(laidOut(requested: 160, minimum: 220, layoutMaximum: 899) == 220)
        #expect(laidOut(requested: 900, minimum: 220, layoutMaximum: 899)
            == AppStore.sidebarWidthMax)
        // Narrowed past the request, the window wins — and this is what `applySidebarWidth` MUST use
        // when it runs from `notify::max-position`, i.e. inside GtkPaned's own size_allocate, where an
        // over-wide `gtk_paned_set_position` is not re-clamped and leaves the sidebar overhanging.
        #expect(laidOut(requested: 400, minimum: 220, layoutMaximum: 349) == 349)
        // Widened again, the SAME standing request comes back: nothing rewrote it while it was capped,
        // which is the whole point of keeping `store.sidebarWidth` the request rather than the answer.
        #expect(laidOut(requested: 400, minimum: 220, layoutMaximum: 899) == 400)
        // Before the first allocation a paned reports G_MAXINT, and a failed read reports 0 or less —
        // all three mean "no window cap yet", never "cap the sidebar to nothing".
        for unbounded in [Double(Int32.max), 0, -1] {
            #expect(laidOut(requested: 400, minimum: 220, layoutMaximum: unbounded) == 400)
        }
        // `applySidebarWidth` feeds this straight into `gtk_paned_set_position`, so it must stay finite
        // for every input, including the unbounded ones above.
        for maximum in [Double(Int32.max), 0, -1, 349, 899] {
            #expect(laidOut(requested: 400, minimum: 220, layoutMaximum: maximum).isFinite)
        }
        // The layout's answer is the one `persistedSidebarWidth` compares against, so a position equal
        // to it is never a drag and a position away from it always is — the two must not drift apart.
        for maximum in [Double(Int32.max), 349, 899] {
            let answer = laidOut(requested: 400, minimum: 220, layoutMaximum: maximum)
            #expect(LinuxSidebarPolicy.persistedSidebarWidth(
                observed: answer, requested: 400, minimum: 220, layoutMaximum: maximum) == nil)
            #expect(LinuxSidebarPolicy.persistedSidebarWidth(
                observed: answer - 30, requested: 400, minimum: 220, layoutMaximum: maximum)
                == answer - 30)
        }
    }

    @Test("notification delivery delegates policy and identity to shared core")
    @MainActor
    func notificationDelivery() {
        let session = Session(initialCwd: "/tmp")
        let store = AppStore(workspaces: [Workspace(name: "work", sessions: [session])])
        let windowID = UUID()
        let delivery = store.recordTerminalNotification(TerminalNotificationRecord(
            sessionID: session.id,
            windowID: windowID,
            pane: .split,
            title: "done",
            body: "ready",
            firingIsFocused: false,
            appActive: true
        ))

        #expect(session.unseenCount == 1)
        #expect(delivery?.identity == TerminalNotification.identity(
            windowID: windowID,
            sessionID: session.id,
            pane: .split
        ))
    }

    @Test("focused OSC suppression precedes unseen mutation while explicit control delivery bypasses it")
    @MainActor
    func notificationSuppressionOrdering() {
        let session = Session(initialCwd: "/tmp")
        let store = AppStore(workspaces: [Workspace(name: "work", sessions: [session])])
        let windowID = UUID()

        let suppressed = store.recordTerminalNotification(TerminalNotificationRecord(
            sessionID: session.id, windowID: windowID, pane: .main,
            title: "focused", body: "ignored", firingIsFocused: true, appActive: true))
        #expect(suppressed == nil)
        #expect(session.unseenCount == 0)

        let inactive = store.recordTerminalNotification(TerminalNotificationRecord(
            sessionID: session.id, windowID: windowID, pane: .main,
            title: "inactive", body: "delivered", firingIsFocused: true, appActive: false))
        #expect(inactive != nil)
        #expect(session.unseenCount == 1)

        let explicitControl = store.recordTerminalNotification(TerminalNotificationRecord(
            sessionID: session.id, windowID: windowID, pane: .main,
            title: "control", body: "requested", firingIsFocused: false, appActive: true))
        #expect(explicitControl != nil)
        #expect(session.unseenCount == 2)
    }

    @Test("Linux surface roles map every terminal kind deliberately")
    func surfaceNotificationRoles() {
        #expect(LinuxSurfaceRole.main.notificationPane == .main)
        #expect(LinuxSurfaceRole.split.notificationPane == .split)
        #expect(LinuxSurfaceRole.overlay.notificationPane == .overlay)
        #expect(LinuxSurfaceRole.scratch.notificationPane == .overlay)
        #expect(LinuxSurfaceRole.quick.notificationPane == nil)
        #expect(LinuxSurfaceRole.scratch.statusPane == .scratch)
    }

    @Test("pane identities coalesce independently and stale reveal panes fall back safely")
    func notificationIdentityAndReveal() {
        let windowID = UUID()
        let sessionID = UUID()
        let main = TerminalNotification.identity(windowID: windowID, sessionID: sessionID, pane: .main)
        let split = TerminalNotification.identity(windowID: windowID, sessionID: sessionID, pane: .split)
        let overlay = TerminalNotification.identity(windowID: windowID, sessionID: sessionID, pane: .overlay)
        #expect(Set([main, split, overlay]).count == 3)
        #expect(NotificationManager.notificationID(main) != NotificationManager.notificationID(split))
        #expect(LinuxNotificationRevealFocus.resolve(
            pane: .split, sessionExists: true, hasSplit: true, coverActive: false) == .split)
        #expect(LinuxNotificationRevealFocus.resolve(
            pane: .split, sessionExists: true, hasSplit: false, coverActive: false) == .primary)
        #expect(LinuxNotificationRevealFocus.resolve(
            pane: .overlay, sessionExists: true, hasSplit: false, coverActive: true) == .overlay)
        #expect(LinuxNotificationRevealFocus.resolve(
            pane: .overlay, sessionExists: true, hasSplit: false, coverActive: false) == .primary)
        #expect(LinuxNotificationRevealFocus.resolve(
            pane: .main, sessionExists: false, hasSplit: false, coverActive: false) == nil)
    }
}
