import Foundation
import Yams

/// The parts of nudge/config.yaml the menu bar shows (the risk meter's threshold tick).
/// The nudge engine owns the file; the app only reads it. Bundled by app/build-app.sh.
public struct NudgeSettings: Sendable, Equatable {
    public var riskThreshold: Double

    public static let fallback = NudgeSettings(riskThreshold: 0.6)

    public init(riskThreshold: Double) { self.riskThreshold = riskThreshold }

    /// Missing file or key -> `fallback`; never throws, so a bad config can't stop sampling.
    public static func load(repo: URL) -> NudgeSettings {
        let url = repo.appendingPathComponent("nudge/config.yaml")
        guard let text = try? String(contentsOf: url, encoding: .utf8),
              let y = (try? Yams.load(yaml: text)) as? [String: Any] else { return fallback }
        let t = (y["risk_threshold"] as? Double) ?? (y["risk_threshold"] as? Int).map(Double.init)
        guard let t, t > 0, t < 1 else { return fallback }
        return NudgeSettings(riskThreshold: t)
    }
}
