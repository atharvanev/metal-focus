// TEMPORARY: stand-ins for PromptUI (app-presenter) and ViewerWindow (app-viewer), so this branch builds
// on its own. Deleted when those branches are merged.
import AppKit
import MetalFocusCore

@MainActor final class PromptUI: PromptPresenter {
    init(onCheckinAnswer: @escaping @MainActor (CheckinAnswer) -> Void, onNudgeAnswer: @escaping @MainActor (Int64, NudgeAnswer) -> Void) {}
    func requestNotificationAuthorization() async -> Bool { false }
    var notificationsAllowed: Bool { false }
    func showCheckin(_ item: OutboxItem, deadline: Date) {}
    func closeCheckin() {}
    func showNudge(_ item: OutboxItem, deadline: Date) {}
    func closeNudge(outboxId: Int64) {}
}

@MainActor enum ViewerWindow { static func show(store: Store) {} }
