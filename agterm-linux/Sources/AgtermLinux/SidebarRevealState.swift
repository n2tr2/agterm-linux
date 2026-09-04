import Foundation
import agtermCore

/// Positive-only overlay over persisted `Workspace.isExpanded`: selecting a session inside a collapsed
/// workspace shows its rows without a store write. Linux has no legitimate negative override, so the
/// overlay can only add. The gate makes a reveal happen once per selection change, so a status or title
/// resync never reopens a workspace the user collapsed while its session stayed selected. Flagged mode
/// reports no owner and leaves the gate alone — unlike macOS, which advances it there — because the
/// flagged view cannot show the owner, so returning to tree view must still reveal it.
struct SidebarRevealState {
    private(set) var transientlyExpandedWorkspaceIDs: Set<UUID> = []
    private(set) var lastRevealedSelection: UUID?
    private var lastSidebarMode: SidebarMode?

    /// The ONE derivation of what the sidebar shows expanded, run once per sync pass: drop ids the store
    /// no longer has, reset the gate when the mode has just come back to tree, reveal the owner of a newly
    /// selected session, then read the effective set. Every step is idempotent, so the second call a
    /// rebuild pass makes changes nothing — and a mode WRITE that changes nothing resets nothing.
    @MainActor
    mutating func syncedExpansion(store: AppStore) -> Set<UUID> {
        if !transientlyExpandedWorkspaceIDs.isEmpty {
            transientlyExpandedWorkspaceIDs.formIntersection(store.workspaces.map(\.id))
        }
        let mode = store.sidebarMode
        if mode == .tree, lastSidebarMode != .tree { lastRevealedSelection = nil }
        lastSidebarMode = mode
        let selected = store.selectedSessionID
        revealIfSelectionChanged(
            selected: selected,
            owner: mode == .tree ? selected.flatMap { store.workspace(forSession: $0)?.id } : nil)
        return effectiveExpandedIDs(workspaces: store.workspaces)
    }

    mutating func revealIfSelectionChanged(selected: UUID?, owner: UUID?) {
        guard let selected else {
            lastRevealedSelection = nil
            return
        }
        guard let owner, selected != lastRevealedSelection else { return }
        transientlyExpandedWorkspaceIDs.insert(owner)
        lastRevealedSelection = selected
    }

    mutating func clearTransient(_ id: UUID) {
        transientlyExpandedWorkspaceIDs.remove(id)
    }

    mutating func clearAllTransient() {
        transientlyExpandedWorkspaceIDs.removeAll()
    }

    @MainActor
    func effectiveExpandedIDs(workspaces: [Workspace]) -> Set<UUID> {
        Set(workspaces.lazy
            .filter { $0.isExpanded || transientlyExpandedWorkspaceIDs.contains($0.id) }
            .map(\.id))
    }
}
