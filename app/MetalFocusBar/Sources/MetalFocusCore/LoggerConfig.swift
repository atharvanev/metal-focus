import Foundation
import Yams

public enum BrowserKind: String, Sendable { case safari, chromium, arc }
public enum PrivatePolicy: String, Sendable { case drop, keep }

public enum ConfigError: Error, Equatable, CustomStringConvertible {
    case invalid(String)
    public var description: String { if case .invalid(let s) = self { return s }; return "" }
}

/// Port of logger/config.py: config.yaml + financial.yaml (always) + config.local.yaml (lists append, scalars override).
public struct LoggerConfig: Sendable {
    public var sampleIntervalS: Double
    public var maxGapS: Double
    public var browserTimeoutS: Double
    public var browsers: [String: BrowserKind]
    public var unknownPrivatePolicy: PrivatePolicy
    public var excludeBundleIds: Set<String>
    public var excludeDomains: [String]
    public var excludeDomainKeywords: [String]
    public var excludeTitlePatterns: [NSRegularExpression]

    public static func load(repo: URL, includeLocal: Bool = true) throws -> LoggerConfig {
        let dir = repo.appendingPathComponent("logger")
        let base = try read(dir.appendingPathComponent("config.yaml"))
        let local = includeLocal ? try read(dir.appendingPathComponent("config.local.yaml")) : [:]
        let fin = try read(dir.appendingPathComponent("financial.yaml"))

        func scalar(_ k: String) -> Any? { local[k] ?? base[k] }
        func double(_ k: String) throws -> Double {
            if let d = scalar(k) as? Double { return d }
            if let i = scalar(k) as? Int { return Double(i) }
            throw ConfigError.invalid("\(k) must be a number")
        }
        func strings(_ v: Any?) -> [String] { (v as? [Any])?.map { "\($0)" } ?? [] }
        func excl(_ k: String) -> [String] {
            strings((base["exclude"] as? [String: Any])?[k]) + strings((local["exclude"] as? [String: Any])?[k])
        }

        let interval = try double("sample_interval_s")
        let maxGap = try double("max_gap_s")
        let timeout = try double("browser_timeout_s")
        if interval <= 0 || maxGap <= interval || timeout <= 0 {
            throw ConfigError.invalid("sample_interval_s, browser_timeout_s must be > 0 and max_gap_s > sample_interval_s")
        }
        guard let policyRaw = scalar("unknown_private_policy") as? String, let policy = PrivatePolicy(rawValue: policyRaw) else {
            throw ConfigError.invalid("unknown_private_policy must be 'drop' or 'keep'")
        }
        var browsers: [String: BrowserKind] = [:]
        for src in [base["browsers"], local["browsers"]] {
            for (bid, kind) in (src as? [String: Any]) ?? [:] {
                guard let k = BrowserKind(rawValue: "\(kind)") else { throw ConfigError.invalid("browser \(bid): unknown kind \(kind)") }
                browsers[bid] = k
            }
        }
        var patterns: [NSRegularExpression] = []
        do {
            patterns += try excl("title_patterns").map { try NSRegularExpression(pattern: $0, options: .caseInsensitive) }
            patterns += try strings(fin["title_keywords"]).map {
                try NSRegularExpression(pattern: "(?<!\\w)(?:\($0))(?!\\w)", options: .caseInsensitive)
            }
        } catch {
            throw ConfigError.invalid("bad title pattern: \(error)")
        }
        return LoggerConfig(
            sampleIntervalS: interval, maxGapS: maxGap, browserTimeoutS: timeout, browsers: browsers,
            unknownPrivatePolicy: policy,
            excludeBundleIds: Set(excl("bundle_ids") + strings(fin["bundle_ids"])),
            excludeDomains: (excl("domains") + strings(fin["domains"])).map { $0.lowercased() },
            excludeDomainKeywords: strings(fin["domain_keywords"]).map { $0.lowercased() },
            excludeTitlePatterns: patterns)
    }

    private static func read(_ url: URL) throws -> [String: Any] {
        guard FileManager.default.fileExists(atPath: url.path) else { return [:] }
        let text = try String(contentsOf: url, encoding: .utf8)
        return (try Yams.load(yaml: text) as? [String: Any]) ?? [:]
    }
}
