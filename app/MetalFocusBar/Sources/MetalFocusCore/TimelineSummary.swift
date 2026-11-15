import Foundation

/// Pure helpers for the viewer's Today timeline.
public enum TimelineSummary {
    /// Labels drawn in neutral gray instead of a palette color.
    public static let neutralLabels: Set<String> = ["Away", "Excluded"]

    /// Stable palette slot for a label (FNV-1a over UTF-8), unlike Swift's per-process randomized `hashValue`.
    public static func paletteIndex(for label: String, count: Int) -> Int {
        precondition(count > 0)
        var h: UInt64 = 0xcbf2_9ce4_8422_2325
        for b in label.utf8 { h = (h ^ UInt64(b)) &* 0x100_0000_01b3 }
        return Int(h % UInt64(count))
    }

    /// Total seconds per label, largest first (ties by label).
    public static func totals(_ segments: [Segment]) -> [(label: String, seconds: Double)] {
        var t: [String: Double] = [:]
        for s in segments { t[s.label, default: 0] += max(0, s.end - s.start) }
        return t.map { (label: $0.key, seconds: $0.value) }
            .sorted { $0.seconds != $1.seconds ? $0.seconds > $1.seconds : $0.label < $1.label }
    }
}
