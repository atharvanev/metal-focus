import Foundation
import Testing

/// Guards for the user's privacy rules: no input hooks, no network, and no child process may use the
/// app's Accessibility/Automation grants. Only osascript is spawned normally; the Python jobs are
/// spawned only by JobSupervisor.swift, with responsibility disclaimed (so they hold no grants).
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
        for (name, text) in Self.sources where name != "JobSupervisor.swift" {
            for line in text.split(separator: "\n") where line.contains("executableURL") || line.contains("launchPath") {
                #expect(line.contains("/usr/bin/osascript"), "\(name): unexpected executable: \(line)")
            }
            let lower = text.lowercased()
            #expect(!lower.contains("python") || name == "nothing", "\(name) mentions python")
            #expect(!lower.contains("/uv\"") && !lower.contains("\"uv\""), "\(name) mentions uv")
        }
    }

    @Test func pythonOnlySpawnedDisclaimed() throws {
        let text = try #require(Self.sources.first { $0.0 == "JobSupervisor.swift" }?.1)
        #expect(text.contains("responsibility_spawnattrs_setdisclaim") && text.contains("disclaim(&attr, 1) == 0"))
        #expect(text.contains("POSIX_SPAWN_CLOEXEC_DEFAULT"))  // children get fds 0-2 only
        #expect(!text.contains("Process()") && !text.contains("executableURL") && !text.contains("launchPath"))
        #expect(!text.contains("/uv\"") && !text.contains("\"uv\""))
    }
}
