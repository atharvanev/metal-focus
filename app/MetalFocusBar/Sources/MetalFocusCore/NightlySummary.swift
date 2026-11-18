import Foundation

/// The "nightly training finished" notification text, from hsmm.nightly's last stdout line
/// (one JSON object: status saved | skipped | ..., days, mu_minutes) and its exit code.
public enum NightlySummary {
    public static func message(lastLine: String?, exitCode: Int32) -> (title: String, body: String) {
        let j = lastLine.flatMap { $0.data(using: .utf8) }
            .flatMap { try? JSONSerialization.jsonObject(with: $0) as? [String: Any] }
        let status = j?["status"] as? String
        if exitCode != 0 || status == nil {
            return ("Nightly training failed", "Exit code \(exitCode). Details are in hsmm-nightly.err.log.")
        }
        let days = j?["days"] as? [String] ?? []
        if status == "skipped" {
            return ("Nightly training skipped", (j?["reason"] as? String).map { "Not enough data yet: \($0)." } ?? "Not enough data yet.")
        }
        var body = "Retrained on \(days.count) day\(days.count == 1 ? "" : "s")"
        if let first = days.first, let last = days.last { body += first == last ? " (\(first))." : " (\(first) to \(last))." }
        if let mu = (j?["mu_minutes"] as? [NSNumber])?.map(\.doubleValue), mu.count == 4 {
            body += " Typical: work \(Int(mu[0].rounded())) min, break \(Int(mu[1].rounded())) min, rabbit hole \(Int(mu[3].rounded())) min."
        }
        return ("Model retrained", body)
    }
}
