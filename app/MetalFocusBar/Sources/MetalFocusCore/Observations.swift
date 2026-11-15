import Foundation

public struct AppInfo: Sendable, Equatable {
    public var bundleId: String?, name: String?, pid: Int32
    public init(bundleId: String?, name: String?, pid: Int32) { self.bundleId = bundleId; self.name = name; self.pid = pid }
}

public struct WindowInfo: Sendable, Equatable {
    public var title: String?, fullscreen: Bool?
    public init(title: String?, fullscreen: Bool?) { self.title = title; self.fullscreen = fullscreen }
}

public struct BrowserInfo: Sendable, Equatable {
    public var url: String?, isPrivate: Bool?
    public init(url: String?, isPrivate: Bool?) { self.url = url; self.isPrivate = isPrivate }
}

/// Seconds since the last key press / click / scroll (system-wide; no content).
public struct InputRecency: Sendable, Equatable {
    public var keyS: Double?, clickS: Double?, scrollS: Double?
    public init(keyS: Double?, clickS: Double?, scrollS: Double?) { self.keyS = keyS; self.clickS = clickS; self.scrollS = scrollS }
}

public enum SQLValue: Sendable, Equatable {
    case null, int(Int64), double(Double), text(String)

    public init(_ v: Int?) { self = v.map { .int(Int64($0)) } ?? .null }
    public init(_ v: Double?) { self = v.map { .double($0) } ?? .null }
    public init(_ v: String?) { self = v.map { .text($0) } ?? .null }

    public var int: Int? { switch self { case .int(let i): Int(i); case .double(let d): Int(d); default: nil } }
    public var double: Double? { switch self { case .int(let i): Double(i); case .double(let d): d; default: nil } }
    public var string: String? { if case .text(let s) = self { return s }; return nil }
    public var isNull: Bool { self == .null }
}

/// One row of the `samples` table (minus `id`).
public struct Sample: Sendable, Equatable {
    public var ts: Double, tzOffsetMin: Int, dt: Double
    public var bundleId: String?, appName: String?, windowTitle: String?, domain: String?
    public var isPrivate: Int?, excluded: Int, fullscreen: Int?, locked: Int
    public var idleS: Double?, appSwitches: Int
    public var keyActive: Int?, clickActive: Int?, scrollActive: Int?
    /// 1 = the frontmost app holds a display-sleep assertion (video playing). Schema v2.
    public var mediaPlaying: Int? = nil

    /// (column name, value) pairs in `samples` column order (data/schema.sql).
    public var columns: [(String, SQLValue)] {
        [("ts", .double(ts)), ("tz_offset_min", SQLValue(tzOffsetMin)), ("dt", .double(dt)),
         ("bundle_id", SQLValue(bundleId)), ("app_name", SQLValue(appName)), ("window_title", SQLValue(windowTitle)),
         ("domain", SQLValue(domain)), ("private", SQLValue(isPrivate)), ("excluded", SQLValue(excluded)),
         ("fullscreen", SQLValue(fullscreen)), ("locked", SQLValue(locked)), ("idle_s", SQLValue(idleS)),
         ("app_switches", SQLValue(appSwitches)), ("key_active", SQLValue(keyActive)),
         ("click_active", SQLValue(clickActive)), ("scroll_active", SQLValue(scrollActive)),
         ("media_playing", SQLValue(mediaPlaying))]
    }
}
