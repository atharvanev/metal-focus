import AppKit
import MetalFocusCore
import Observation
import os

let appLog = Logger(subsystem: "com.metalfocus.menubar", category: "app")

@MainActor @Observable
final class AppModel {
    var cfg: LoggerConfig?
    var store: Store?
    var startupError: String?
    var lastSample: Sample?
    var accessibility = Permissions.accessibility

    @ObservationIgnored private var sampler: Sampler?
    @ObservationIgnored private var observer: WorkspaceObserver?
    @ObservationIgnored private var loop: Task<Void, Never>?
    @ObservationIgnored let switches = SwitchCounter()

    /// Config (logger/*.yaml) and schema (data/schema.sql), copied from the repo into the bundle by
    /// app/build-app.sh, so the app needs no access to ~/Documents. The `MFRepoPath` default (a repo
    /// checkout) overrides it for development only.
    static var repoURL: URL? {
        if let path = UserDefaults.standard.string(forKey: "MFRepoPath") { return URL(fileURLWithPath: path) }
        guard let res = Bundle.main.resourceURL?.appendingPathComponent("repo"),
              FileManager.default.fileExists(atPath: res.appendingPathComponent("data/schema.sql").path) else { return nil }
        return res
    }

    func start() {
        guard let repo = Self.repoURL else {
            startupError = "Bundled config missing: rebuild with app/build-app.sh."
            return
        }
        do {
            let cfg = try LoggerConfig.load(repo: repo)
            let store = try Store.open(repo: repo, runningThresholdS: 3 * cfg.sampleIntervalS)
            self.cfg = cfg; self.store = store
            startSampling(cfg: cfg, store: store)
        } catch {
            startupError = "\(error)"
            appLog.error("startup failed: \(String(describing: error))")
        }
    }

    private func startSampling(cfg: LoggerConfig, store: Store) {
        let sampler = Sampler(probes: MacProbes(cfg: cfg), switches: switches, sink: store, cfg: cfg)
        self.sampler = sampler
        observer = WorkspaceObserver(switches: switches, sink: store)
        let trusted = accessibility
        loop = Task { [weak self] in
            let now = Date().timeIntervalSince1970
            try? await store.write(event: now, kind: "logger_start", detail: nil)
            if !trusted { try? await store.write(event: now, kind: "permission_missing", detail: "accessibility") }
            let clock = ContinuousClock()
            var next = clock.now
            while !Task.isCancelled {
                let sample = await sampler.tick()
                self?.lastSample = sample ?? self?.lastSample
                self?.accessibility = Permissions.accessibility
                next = next.advanced(by: .seconds(cfg.sampleIntervalS))
                if next < clock.now { next = clock.now.advanced(by: .seconds(cfg.sampleIntervalS)) }
                try? await clock.sleep(until: next)
            }
        }
    }

    /// Called from applicationWillTerminate: record logger_stop (bounded wait).
    func shutdown() {
        loop?.cancel()
        observer?.stop()
        guard let store else { return }
        let done = DispatchSemaphore(value: 0)
        Task.detached {
            try? await store.write(event: Date().timeIntervalSince1970, kind: "logger_stop", detail: nil)
            done.signal()
        }
        _ = done.wait(timeout: .now() + 1)
    }
}
