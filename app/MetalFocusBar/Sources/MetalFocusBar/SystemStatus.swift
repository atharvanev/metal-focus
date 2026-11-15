import AppKit
import CoreServices
import MetalFocusCore
import ServiceManagement

/// Read-only system checks shown in the popover. None of these prompts the user unless asked to.
enum SystemStatus {
    enum Automation: Equatable { case granted, notAsked, denied, notRunning }

    /// Apple Events permission for one browser, checked without prompting (askUserIfNeeded: false).
    nonisolated static func automation(bundleId: String) -> Automation {
        var target = AEAddressDesc()
        let made = bundleId.withCString { AECreateDesc(DescType(typeApplicationBundleID), $0, strlen($0), &target) }
        guard made == noErr else { return .notRunning }
        defer { AEDisposeDesc(&target) }
        switch AEDeterminePermissionToAutomateTarget(&target, AEEventClass(typeWildCard), AEEventID(typeWildCard), false) {
        case noErr: return .granted
        case OSStatus(errAEEventWouldRequireUserConsent): return .notAsked
        case OSStatus(errAEEventNotPermitted): return .denied
        default: return .notRunning
        }
    }

    /// Running browsers the logger config knows about (bundle id, display name).
    @MainActor static func runningBrowsers(_ cfg: LoggerConfig) -> [(String, String)] {
        NSWorkspace.shared.runningApplications.compactMap { app in
            guard let bid = app.bundleIdentifier, cfg.browsers[bid] != nil else { return nil }
            return (bid, app.localizedName ?? bid)
        }.sorted { $0.1 < $1.1 }
    }

    /// The old v1 logger LaunchAgent would write duplicate samples next to the app.
    static var legacyLoggerAgentExists: Bool {
        FileManager.default.fileExists(atPath: NSHomeDirectory() + "/Library/LaunchAgents/com.metalfocus.logger.plist")
    }

    @MainActor static var launchAtLogin: Bool { SMAppService.mainApp.status == .enabled }

    @MainActor static func setLaunchAtLogin(_ on: Bool) throws {
        if on { try SMAppService.mainApp.register() } else { try SMAppService.mainApp.unregister() }
    }

    static func openAutomationSettings() {
        if let url = URL(string: "x-apple.systempreferences:com.apple.preference.security?Privacy_Automation") {
            NSWorkspace.shared.open(url)
        }
    }

    static func openNotificationSettings() {
        if let url = URL(string: "x-apple.systempreferences:com.apple.Notifications-Settings.extension") {
            NSWorkspace.shared.open(url)
        }
    }
}
