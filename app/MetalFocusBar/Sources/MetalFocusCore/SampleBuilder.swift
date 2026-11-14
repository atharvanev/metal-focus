import Foundation

/// Line-for-line port of logger/sample.py `build_sample`. Parity is pinned by tests/fixtures/sample_cases.json.
public enum SampleBuilder {
    static func flag(_ v: Bool?) -> Int? { v.map { $0 ? 1 : 0 } }
    static func active(_ secondsSince: Double?, _ dt: Double) -> Int? { secondsSince.map { $0 < dt ? 1 : 0 } }

    public static func build(
        ts: Double, tzOffsetMin: Int, dt: Double, app: AppInfo?, window: WindowInfo?, browser: BrowserInfo?,
        idleS: Double?, recency: InputRecency?, locked: Bool, appSwitches: Int, cfg: LoggerConfig,
        mediaPlaying: Bool? = nil
    ) -> Sample {
        var bundleId = app?.bundleId
        var appName = app?.name
        var title = window?.title
        var fullscreen = window.flatMap { flag($0.fullscreen) }
        var domain: String? = nil
        var isPrivate: Int? = nil
        var excluded = 0
        let isBrowser = bundleId.map { cfg.browsers[$0] != nil } ?? false
        if isBrowser, let b = browser { domain = Privacy.sanitizeURL(b.url) }

        if locked {
            bundleId = nil; appName = nil; title = nil; domain = nil; fullscreen = nil
        } else if Privacy.isExcluded(bundleId: bundleId, domain: domain, title: title, cfg: cfg) {
            // Checked on the raw domain/title, so a private bank tab is still "excluded".
            bundleId = nil; appName = nil; title = nil; domain = nil
            excluded = 1
        } else if isBrowser {
            let priv = browser?.isPrivate
            isPrivate = flag(priv)
            if priv == true || (priv == nil && cfg.unknownPrivatePolicy == .drop) { title = nil; domain = nil }
        }
        return Sample(
            ts: ts, tzOffsetMin: tzOffsetMin, dt: dt, bundleId: bundleId, appName: appName, windowTitle: title,
            domain: domain, isPrivate: isPrivate, excluded: excluded, fullscreen: fullscreen, locked: locked ? 1 : 0,
            idleS: idleS, appSwitches: appSwitches,
            keyActive: recency.flatMap { active($0.keyS, dt) },
            clickActive: recency.flatMap { active($0.clickS, dt) },
            scrollActive: recency.flatMap { active($0.scrollS, dt) },
            // A 0/1 flag with no content, kept for excluded apps (it only decides Away); unknown when locked.
            mediaPlaying: locked ? nil : flag(mediaPlaying))
    }
}
