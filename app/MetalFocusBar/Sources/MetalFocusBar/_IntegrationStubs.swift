// TEMPORARY: stand-in for PromptUI (app-presenter), so this branch builds
// on its own. Deleted when that branch is merged.
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

