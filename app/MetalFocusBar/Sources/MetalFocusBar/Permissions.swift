import AppKit
import ApplicationServices

enum Permissions {
    static var accessibility: Bool { AXIsProcessTrusted() }

    /// Shows the system Accessibility prompt and opens the matching Settings pane.
    static func promptAccessibility() {
        let opts = ["AXTrustedCheckOptionPrompt": true] as CFDictionary  // == kAXTrustedCheckOptionPrompt
        _ = AXIsProcessTrustedWithOptions(opts)
        if let url = URL(string: "x-apple.systempreferences:com.apple.preference.security?Privacy_Accessibility") {
            NSWorkspace.shared.open(url)
        }
    }
}
