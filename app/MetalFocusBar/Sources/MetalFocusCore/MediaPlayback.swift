import Foundation

/// Port of logger/media.py: is the frontmost app playing video?
///
/// Browsers and video players hold a `PreventUserIdleDisplaySleep` power assertion while a video plays
/// and drop it on pause; audio alone takes only a system-sleep assertion. An assertion counts when the
/// owning process (or the process it was created on behalf of) is the frontmost app or lives inside its
/// bundle. Checked against tests/fixtures/media_cases.json, like logger/media.py.
public enum MediaPlayback {
    public static let displayAssertions: Set<String> = ["PreventUserIdleDisplaySleep", "NoDisplaySleepAssertion"]

    public static func frontmostPlays(assertions: [[String: Any]], frontPid: Int32, frontBundlePath: String?,
                                      pathOf: (Int32) -> String?) -> Bool {
        let prefix = frontBundlePath.map { ($0.hasSuffix("/") ? String($0.dropLast()) : $0) + "/" }
        func isFront(_ v: Any?) -> Bool {
            guard let pid = (v as? NSNumber)?.int32Value ?? (v as? Int).map(Int32.init) else { return false }
            if pid == frontPid { return true }
            guard let prefix, let path = pathOf(pid) else { return false }
            return path.hasPrefix(prefix)
        }
        for a in assertions {
            let kind = (a["AssertionTrueType"] as? String) ?? (a["AssertType"] as? String)
            guard let kind, displayAssertions.contains(kind) else { continue }
            if isFront(a["AssertPID"]) || isFront(a["AssertionOnBehalfOfPID"]) { return true }
        }
        return false
    }
}
