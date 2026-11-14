import Foundation
import Testing

/// Guards for the user's privacy rules: no input hooks, no network, never spawn Python/uv
/// (child processes would inherit the app's Accessibility/Automation grants).
@Suite struct SourceGuardTests {
    static let sources: [(String, String)] = {
        let root = URL(fileURLWithPath: #filePath).deletingLastPathComponent().deletingLastPathComponent()
            .deletingLastPathComponent().appendingPathComponent("Sources")
        let e = FileManager.default.enumerator(at: root, includingPropertiesForKeys: nil)!
        return e.compactMap { $0 as? URL }.filter { $0.pathExtension == "swift" }
            .map { ($0.lastPathComponent, try! String(contentsOf: $0, encoding: .utf8)) }
    }()

    @Test func sourcesFound() { #expect(Self.sources.count >= 8) }

    @Test func noHooksNoNetwork() {
        let banned = ["CGEventTap", "addGlobalMonitorForEvents", "addLocalMonitorForEvents", "IOHIDManager",
                      "CGRequestListenEventAccess", "URLSession", "URLRequest", "NWConnection",
                      "NSApp.activate", "NSApplication.shared.activate", "activate(ignoringOtherApps"]
        for (name, text) in Self.sources {
            for b in banned { #expect(!text.contains(b), "\(name) contains \(b)") }
        }
    }

    @Test func onlyOsascriptIsSpawned() {
        for (name, text) in Self.sources {
            for line in text.split(separator: "\n") where line.contains("executableURL") || line.contains("launchPath") {
                #expect(line.contains("/usr/bin/osascript"), "\(name): unexpected executable: \(line)")
            }
            let lower = text.lowercased()
            #expect(!lower.contains("python") || name == "nothing", "\(name) mentions python")
            #expect(!lower.contains("/uv\"") && !lower.contains("\"uv\""), "\(name) mentions uv")
        }
    }
}
