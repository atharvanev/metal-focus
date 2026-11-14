import AppKit
import MetalFocusCore

/// Counts app switches and records sleep/wake/lock events (port of logger/macos/runloop.py's observer).
/// Notifications only: there is no event tap and no keyboard/mouse hook.
@MainActor
final class WorkspaceObserver {
    private var tokens: [(NotificationCenter, NSObjectProtocol)] = []

    init(switches: SwitchCounter, sink: SampleSink) {
        let ws = NSWorkspace.shared.notificationCenter
        tokens.append((ws, ws.addObserver(forName: NSWorkspace.didActivateApplicationNotification, object: nil, queue: nil) { _ in
            switches.increment()
        }))
        let kinds: [(NotificationCenter, Notification.Name, String)] = [
            (ws, NSWorkspace.willSleepNotification, "sleep"),
            (ws, NSWorkspace.didWakeNotification, "wake"),
            (ws, NSWorkspace.screensDidSleepNotification, "screens_sleep"),
            (ws, NSWorkspace.screensDidWakeNotification, "screens_wake"),
            (DistributedNotificationCenter.default(), Notification.Name("com.apple.screenIsLocked"), "lock"),
            (DistributedNotificationCenter.default(), Notification.Name("com.apple.screenIsUnlocked"), "unlock"),
        ]
        for (center, name, kind) in kinds {
            tokens.append((center, center.addObserver(forName: name, object: nil, queue: nil) { _ in
                let ts = Date().timeIntervalSince1970
                Task { try? await sink.write(event: ts, kind: kind, detail: nil) }
            }))
        }
    }

    func stop() {
        for (c, t) in tokens { c.removeObserver(t) }
        tokens.removeAll()
    }
}
