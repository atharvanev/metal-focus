import Foundation
import os

/// Raw macOS observations. Implementations return nil (or throw) when a permission is missing.
public protocol Probes: Sendable {
    func screenLocked() throws -> Bool
    func frontmost() throws -> AppInfo?
    func window(pid: Int32) throws -> WindowInfo?
    func browser(bundleId: String) throws -> BrowserInfo?
    func idleSeconds() throws -> Double?
    func inputRecency() throws -> InputRecency?
    /// Is the frontmost app playing video (display-sleep power assertion)? See MediaPlayback.
    func mediaPlaying(app: AppInfo) throws -> Bool?
}

public protocol SampleSink: Sendable {
    func write(sample: Sample) async throws
    func write(event ts: Double, kind: String, detail: String?) async throws
}

let samplerLog = Logger(subsystem: "com.metalfocus.menubar", category: "sampler")

/// One sampling step (port of logger/sampler.py). Ticks never overlap: a tick requested while one is
/// running returns nil without doing anything.
public actor Sampler {
    let probes: Probes, switches: SwitchCounter, sink: SampleSink, cfg: LoggerConfig
    let clock: @Sendable () -> Double, tzOffset: @Sendable () -> Int
    private var last: Double?
    private var warned = Set<String>()
    public private(set) var isTicking = false

    public init(probes: Probes, switches: SwitchCounter, sink: SampleSink, cfg: LoggerConfig,
                clock: @escaping @Sendable () -> Double = { Date().timeIntervalSince1970 },
                tzOffset: @escaping @Sendable () -> Int = { TimeZone.current.secondsFromGMT() / 60 }) {
        self.probes = probes; self.switches = switches; self.sink = sink; self.cfg = cfg
        self.clock = clock; self.tzOffset = tzOffset
    }

    struct Observed: Sendable {
        var locked = false, app: AppInfo?, window: WindowInfo?, browser: BrowserInfo?
        var idle: Double?, recency: InputRecency?, media: Bool?, failed: [String] = []
    }

    /// Probe order: locked first (skip window/browser when locked), frontmost, window, browser (only for
    /// configured browsers), idle, input recency. A failing probe gives nil and never stops the tick.
    static func observe(_ p: Probes, browsers: [String: BrowserKind]) -> Observed {
        var o = Observed()
        func safe<T>(_ name: String, _ fn: () throws -> T?) -> T? {
            do { return try fn() } catch { o.failed.append(name); return nil }
        }
        o.locked = safe("screen_locked") { try p.screenLocked() } ?? false
        o.app = safe("frontmost") { try p.frontmost() }
        if !o.locked, let app = o.app {
            o.window = safe("window") { try p.window(pid: app.pid) }
            o.media = safe("media_playing") { try p.mediaPlaying(app: app) }
            if let bid = app.bundleId, browsers[bid] != nil { o.browser = safe("browser") { try p.browser(bundleId: bid) } }
        }
        o.idle = safe("idle_seconds") { try p.idleSeconds() }
        o.recency = safe("input_recency") { try p.inputRecency() }
        return o
    }

    @discardableResult
    public func tick() async -> Sample? {
        guard !isTicking else { return nil }
        isTicking = true
        defer { isTicking = false }

        let now = clock()
        var dt = cfg.sampleIntervalS
        if let last {
            let elapsed = now - last
            if elapsed > cfg.maxGapS || elapsed <= 0 {  // sleep/lid closed, or wall clock moved backwards
                do { try await sink.write(event: last, kind: "gap", detail: String(format: "%.1fs", elapsed)) }
                catch { samplerLog.error("failed to write gap event: \(error)") }
            } else {
                dt = elapsed
            }
        }
        last = now

        let probes = self.probes, browsers = cfg.browsers
        let o = await Task.detached { Sampler.observe(probes, browsers: browsers) }.value
        for name in o.failed where !warned.contains(name) {
            warned.insert(name)
            samplerLog.warning("probe \(name) failed; storing NULL")
        }
        let sample = SampleBuilder.build(
            ts: now, tzOffsetMin: tzOffset(), dt: dt, app: o.app, window: o.window, browser: o.browser,
            idleS: o.idle, recency: o.recency, locked: o.locked, appSwitches: switches.snapshotAndReset(), cfg: cfg,
            mediaPlaying: o.media)
        do { try await sink.write(sample: sample) } catch { samplerLog.error("failed to write sample: \(error)") }
        return sample
    }
}
