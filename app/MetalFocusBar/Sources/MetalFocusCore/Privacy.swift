import Foundation

/// Port of logger/privacy.py. Pure functions.
public enum Privacy {
    /// Bare domain (lowercase, no www., port, userinfo, path or query), or nil for non-http(s) URLs.
    public static func sanitizeURL(_ url: String?) -> String? {
        guard let raw = url?.trimmingCharacters(in: .whitespacesAndNewlines), !raw.isEmpty,
              let comps = URLComponents(string: raw),
              let scheme = comps.scheme?.lowercased(), scheme == "http" || scheme == "https",
              let host = comps.host?.lowercased(), !host.isEmpty
        else { return nil }
        return host.hasPrefix("www.") ? String(host.dropFirst(4)) : host
    }

    public static func domainMatches(_ domain: String, _ suffix: String) -> Bool {
        domain == suffix || domain.hasSuffix("." + suffix)
    }

    public static func isExcluded(bundleId: String?, domain: String?, title: String?, cfg: LoggerConfig) -> Bool {
        if let b = bundleId, cfg.excludeBundleIds.contains(b) { return true }
        if let d = domain {
            if cfg.excludeDomains.contains(where: { domainMatches(d, $0) }) { return true }
            if cfg.excludeDomainKeywords.contains(where: { d.contains($0) }) { return true }
        }
        if let t = title {
            let range = NSRange(t.startIndex..., in: t)
            if cfg.excludeTitlePatterns.contains(where: { $0.firstMatch(in: t, range: range) != nil }) { return true }
        }
        return false
    }
}
