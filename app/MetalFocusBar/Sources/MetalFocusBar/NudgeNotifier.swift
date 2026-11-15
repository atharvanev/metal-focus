import AppKit
import MetalFocusCore
import SwiftUI
import UserNotifications

/// Nudges as native notifications (no sound), or floating panels when notifications are denied
/// or the app isn't running from a bundle (`swift run`: UNUserNotificationCenter crashes there).
@MainActor final class NudgeNotifier: NSObject, UNUserNotificationCenterDelegate {
    static let category = "metalfocus.nudge"
    private let center: UNUserNotificationCenter? = Bundle.main.bundleIdentifier == nil ? nil : .current()
    private let onAnswer: @MainActor (Int64, NudgeAnswer) -> Void
    private var panels: [Int64: NudgePanel] = [:]
    // ponytail: cached from the last authorization request; re-request to pick up a later change in Settings.
    private(set) var allowed = false

    init(onAnswer: @escaping @MainActor (Int64, NudgeAnswer) -> Void) {
        self.onAnswer = onAnswer
        super.init()
        guard let center else { return }
        center.delegate = self
        let actions = NudgeAnswer.buttons.map { UNNotificationAction(identifier: $0.rawValue, title: $0.title, options: []) }
        center.setNotificationCategories([UNNotificationCategory(
            identifier: Self.category, actions: actions, intentIdentifiers: [], options: [.customDismissAction])])
    }

    func requestAuthorization() async -> Bool {
        guard let center else { allowed = false; return false }
        allowed = (try? await center.requestAuthorization(options: [.alert])) ?? false
        return allowed
    }

    /// Fallback panels currently on screen.
    var openPanels: [NSWindow] { Array(panels.values) }

    func show(_ item: OutboxItem, avoiding: [NSWindow]) {
        guard let center, allowed else { return showPanel(item, avoiding: avoiding) }
        let content = UNMutableNotificationContent()
        content.title = "Metal Rabbit"
        if item.test { content.subtitle = "Test" }
        content.body = item.body ?? ""
        content.categoryIdentifier = Self.category
        content.sound = nil
        center.add(UNNotificationRequest(identifier: Self.requestId(item.outboxId), content: content, trigger: nil))
    }

    func close(outboxId: Int64) {
        panels.removeValue(forKey: outboxId)?.close()
        guard let center else { return }
        let ids = [Self.requestId(outboxId)]
        center.removeDeliveredNotifications(withIdentifiers: ids)
        center.removePendingNotificationRequests(withIdentifiers: ids)
    }

    static func requestId(_ outboxId: Int64) -> String { "nudge-\(outboxId)" }

    private func showPanel(_ item: OutboxItem, avoiding: [NSWindow]) {
        panels.removeValue(forKey: item.outboxId)?.close()
        let id = item.outboxId, onAnswer = onAnswer
        let panel = NudgePanel(item: item) { onAnswer(id, $0) }
        panel.showTopRight(avoiding: avoiding + openPanels)
        panels[id] = panel
    }

    private func handle(requestId: String, actionId: String, isDismiss: Bool) {
        guard requestId.hasPrefix("nudge-"), let id = Int64(requestId.dropFirst(6)),
              let answer = NudgeAnswer(actionId: actionId, isDismiss: isDismiss) else { return }
        onAnswer(id, answer)
    }

    nonisolated func userNotificationCenter(_ center: UNUserNotificationCenter,
                                            didReceive response: UNNotificationResponse) async {
        let rid = response.notification.request.identifier, aid = response.actionIdentifier
        await handle(requestId: rid, actionId: aid, isDismiss: aid == UNNotificationDismissActionIdentifier)
    }

    nonisolated func userNotificationCenter(_ center: UNUserNotificationCenter,
                                            willPresent notification: UNNotification) async -> UNNotificationPresentationOptions {
        [.banner, .list]
    }
}

final class NudgePanel: PromptPanel {
    init(item: OutboxItem, onAnswer: @escaping @MainActor (NudgeAnswer) -> Void) {
        super.init(NudgeView(item: item, onAnswer: onAnswer))
    }
}

struct NudgeView: View {
    let item: OutboxItem, onAnswer: @MainActor (NudgeAnswer) -> Void

    var body: some View {
        VStack(alignment: .leading, spacing: 10) {
            HStack {
                Text("Metal Rabbit").font(.headline)
                Spacer()
                if item.test { TestBadge() }
            }
            Text(item.body ?? "").fixedSize(horizontal: false, vertical: true)
            HStack {
                ForEach(NudgeAnswer.buttons, id: \.self) { a in Button(a.title) { onAnswer(a) } }
            }
        }
        .padding(14)
        .frame(width: 340)
    }
}
