import Foundation

/// Runs AppleScript through /usr/bin/osascript with a hard timeout, so a hung browser can't stall sampling.
/// Apple Events from this child are attributed to the app (that is intended: Automation belongs to the app).
public enum OSAScript {
    public static func run(_ source: String, timeout: Double) -> String? {
        let p = Process()
        p.executableURL = URL(fileURLWithPath: "/usr/bin/osascript")
        p.arguments = ["-e", source]
        let out = Pipe()
        p.standardOutput = out
        p.standardError = FileHandle.nullDevice
        let done = DispatchSemaphore(value: 0)
        p.terminationHandler = { _ in done.signal() }
        do { try p.run() } catch { return nil }
        if done.wait(timeout: .now() + timeout) == .timedOut {
            p.terminate()
            _ = done.wait(timeout: .now() + 0.5)
            return nil
        }
        guard p.terminationStatus == 0 else { return nil }
        return String(decoding: out.fileHandleForReading.readDataToEndOfFile(), as: UTF8.self)
    }
}

/// Port of logger/macos/browser.py (same scripts, same reply parsing).
public enum BrowserScripts {
    public static func script(kind: BrowserKind, bundleId: String) -> String {
        switch kind {
        case .chromium:
            "tell application id \"\(bundleId)\" to return (URL of active tab of front window) & linefeed & (mode of front window)"
        case .arc:
            "tell application id \"\(bundleId)\" to return (URL of active tab of front window) & linefeed & (incognito of front window as text)"
        case .safari:  // Safari exposes no private-window flag to AppleScript.
            "tell application id \"\(bundleId)\" to return URL of current tab of front window"
        }
    }

    public static func parse(kind: BrowserKind, stdout: String) -> BrowserInfo {
        let lines = stdout.trimmingCharacters(in: .whitespacesAndNewlines).split(separator: "\n", omittingEmptySubsequences: false)
        var url = lines.first.map { $0.trimmingCharacters(in: .whitespaces) }
        if url == nil || url == "" || url == "missing value" { url = nil }
        let flag = lines.count > 1 ? lines[1].trimmingCharacters(in: .whitespaces).lowercased() : ""
        var isPrivate: Bool? = nil
        switch kind {
        case .chromium: isPrivate = ["incognito": true, "normal": false][flag]
        case .arc: isPrivate = ["true": true, "false": false][flag]
        case .safari: break
        }
        return BrowserInfo(url: url, isPrivate: isPrivate)
    }
}
