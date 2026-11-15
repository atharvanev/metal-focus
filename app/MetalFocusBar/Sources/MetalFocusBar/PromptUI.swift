import AppKit
import MetalFocusCore

/// The UI side of PromptCoordinator: one check-in panel at a time, nudges via NudgeNotifier.
/// Closing is idempotent. Answers go to the callbacks; the coordinator then calls close.
@MainActor final class PromptUI: PromptPresenter {
    private let onCheckinAnswer: @MainActor (CheckinAnswer) -> Void
    private let nudges: NudgeNotifier
    private var checkin: CheckinPanel?

    init(onCheckinAnswer: @escaping @MainActor (CheckinAnswer) -> Void,
         onNudgeAnswer: @escaping @MainActor (Int64, NudgeAnswer) -> Void) {
        self.onCheckinAnswer = onCheckinAnswer
        nudges = NudgeNotifier(onAnswer: onNudgeAnswer)
    }

    /// Asks for alerts only (no sound, no badge). False outside a bundle or if denied.
    func requestNotificationAuthorization() async -> Bool { await nudges.requestAuthorization() }

    var notificationsAllowed: Bool { nudges.allowed }

    /// Current permission, read live (never prompts).
    func refreshNotificationAuthorization() async -> Bool { await nudges.refreshAuthorization() }

    func showCheckin(_ item: OutboxItem, deadline: Date) {
        closeCheckin()
        let panel = CheckinPanel(item: item, deadline: deadline, onAnswer: onCheckinAnswer)
        checkin = panel
        panel.showTopRight(avoiding: nudges.openPanels)
    }

    func closeCheckin() {
        checkin?.close()
        checkin = nil
    }

    func showNudge(_ item: OutboxItem, deadline: Date) { nudges.show(item, avoiding: checkin.map { [$0] } ?? []) }

    func closeNudge(outboxId: Int64) { nudges.close(outboxId: outboxId) }
}
