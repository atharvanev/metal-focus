import AppKit
import ApplicationServices
import CoreGraphics
import Darwin
import IOKit.pwr_mgt
import MetalFocusCore

/// Real macOS probes (port of logger/macos/probes.py). Window titles come from Accessibility (not
/// CGWindowList, which would need Screen Recording). Input activity comes only from
/// CGEventSource "seconds since last event" — no content, no Input Monitoring, no event taps.
struct MacProbes: Probes {
    let cfg: LoggerConfig
    static let axTimeout: Float = 0.5  // a hung app must not stall the sampler
    static let anyInput = CGEventType(rawValue: ~UInt32(0))!

    func screenLocked() throws -> Bool {
        let d = (CGSessionCopyCurrentDictionary() as? [String: Any]) ?? [:]
        return (d["CGSSessionScreenIsLocked"] as? Bool) ?? false
    }

    func frontmost() throws -> AppInfo? {
        guard let app = NSWorkspace.shared.frontmostApplication else { return nil }
        return AppInfo(bundleId: app.bundleIdentifier, name: app.localizedName, pid: app.processIdentifier)
    }

    func window(pid: Int32) throws -> WindowInfo? {
        guard AXIsProcessTrusted() else { return nil }
        let appEl = AXUIElementCreateApplication(pid)
        AXUIElementSetMessagingTimeout(appEl, Self.axTimeout)
        guard let win = Self.attr(appEl, kAXFocusedWindowAttribute as String) else { return WindowInfo(title: nil, fullscreen: nil) }
        let winEl = win as! AXUIElement
        let title = Self.attr(winEl, kAXTitleAttribute as String) as? String
        let fs = Self.attr(winEl, "AXFullScreen") as? Bool
        return WindowInfo(title: (title?.isEmpty ?? true) ? nil : title, fullscreen: fs)
    }

    static func attr(_ el: AXUIElement, _ name: String) -> AnyObject? {
        var v: AnyObject?
        return AXUIElementCopyAttributeValue(el, name as CFString, &v) == .success ? v : nil
    }

    func browser(bundleId: String) throws -> BrowserInfo? {
        guard let kind = cfg.browsers[bundleId] else { return nil }
        guard let out = OSAScript.run(BrowserScripts.script(kind: kind, bundleId: bundleId), timeout: cfg.browserTimeoutS)
        else { return nil }
        return BrowserScripts.parse(kind: kind, stdout: out)
    }

    /// Power assertions need no permission (it's what `pmset -g assertions` shows).
    func mediaPlaying(app: AppInfo) throws -> Bool? {
        var out: Unmanaged<CFDictionary>?
        guard IOPMCopyAssertionsByProcess(&out) == kIOReturnSuccess, let byPid = out?.takeRetainedValue() as NSDictionary?
        else { return nil }
        let flat = byPid.allValues.compactMap { $0 as? [[String: Any]] }.flatMap { $0 }
        let bundle = NSRunningApplication(processIdentifier: app.pid)?.bundleURL?.path
        return MediaPlayback.frontmostPlays(assertions: flat, frontPid: app.pid, frontBundlePath: bundle, pathOf: Self.path)
    }

    static func path(of pid: Int32) -> String? {
        var buf = [CChar](repeating: 0, count: 4096)
        let n = proc_pidpath(pid, &buf, UInt32(buf.count))
        guard n > 0 else { return nil }
        return String(decoding: buf.prefix(Int(n)).map { UInt8(bitPattern: $0) }, as: UTF8.self)
    }

    func idleSeconds() throws -> Double? {
        CGEventSource.secondsSinceLastEventType(.hidSystemState, eventType: Self.anyInput)
    }

    func inputRecency() throws -> InputRecency? {
        let since = { (t: CGEventType) in CGEventSource.secondsSinceLastEventType(.hidSystemState, eventType: t) }
        return InputRecency(keyS: since(.keyDown), clickS: since(.leftMouseDown), scrollS: since(.scrollWheel))
    }
}
