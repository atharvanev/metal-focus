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
    var status: Status?
    var readout: ReadoutInfo?
    /// Start of the open "Not working" stretch (the popover switch), nil when off.
    var notWorkingSince: Double?
    var notificationsAllowed = false
    var launchAtLogin = SystemStatus.launchAtLogin
    var browsers: [(id: String, name: String, automation: SystemStatus.Automation)] = []
    var legacyLoggerAgent = SystemStatus.legacyLoggerAgentExists
    var jobProblem: String?
    var promptError: String?
    // Popover-only: today's app strip (refreshed while the popover is open) and the risk meter's tick.
    var today: [Segment] = []
    var nudge = NudgeSettings.fallback

    @ObservationIgnored private var sampler: Sampler?
    @ObservationIgnored private var observer: WorkspaceObserver?
    @ObservationIgnored private var loop: Task<Void, Never>?
    @ObservationIgnored let switches = SwitchCounter()
    @ObservationIgnored private var promptUI: PromptUI?
    @ObservationIgnored private var pump: PromptPump?
    @ObservationIgnored private var promptLoop: Task<Void, Never>?
    @ObservationIgnored private let jobs = JobSupervisor()
    @ObservationIgnored private var jobLoop: Task<Void, Never>?

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
        startJobs()
        guard let repo = Self.repoURL else {
            startupError = "Bundled config missing: rebuild with app/build-app.sh."
            return
        }
        do {
            let cfg = try LoggerConfig.load(repo: repo)
            let store = try Store.open(repo: repo, runningThresholdS: 3 * cfg.sampleIntervalS)
            self.cfg = cfg; self.store = store
            nudge = NudgeSettings.load(repo: repo)
            startSampling(cfg: cfg, store: store)
            startPrompts(store: store)
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

    /// The background jobs (HSMM runner, nightly, check-ins) run as children of the app; see JobSupervisor.
    /// They wait and retry on their own when the DB or key isn't ready, so they start regardless.
    private func startJobs() {
        jobLoop = Task { [weak self] in
            while !Task.isCancelled {
                guard let self else { return }
                self.jobs.tick()
                if self.jobs.problem != self.jobProblem { self.jobProblem = self.jobs.problem }
                try? await Task.sleep(for: .seconds(5))
            }
        }
    }

    // MARK: prompts

    /// Outbox poll every 2 s, coordinator tick every 1 s, expiry sweep at start and every minute,
    /// popover status every 5 s. All on the main actor; the Store actor does the DB work.
    private func startPrompts(store: Store) {
        var coordinator: PromptCoordinator!
        let ui = PromptUI(
            onCheckinAnswer: { answer in Task { await coordinator.userAnswered(answer) } },
            onNudgeAnswer: { id, answer in Task { await coordinator.userAnswered(nudge: id, answer) } })
        coordinator = PromptCoordinator(presenter: ui, sink: StorePromptSink(store: store))
        let pump = PromptPump(store: store, coordinator: coordinator)
        promptUI = ui; self.pump = pump
        promptLoop = Task { [weak self] in
            self?.notificationsAllowed = await ui.requestNotificationAuthorization()
            await pump.sweep()
            var second = 0
            while !Task.isCancelled {
                await coordinator.tick()
                if second % 2 == 0 { await pump.pump() }
                if second % 5 == 0 { await self?.refreshStatus() }
                if second % 60 == 59 { await pump.sweep() }
                second += 1
                try? await Task.sleep(for: .seconds(1))
            }
        }
    }

    func refreshStatus() async {
        guard let store else { return }
        do {
            status = try await store.status()
            readout = try await store.readout()
            notWorkingSince = try await store.notWorkingSince()
            promptError = nil
        } catch { promptError = "\(error)" }
        accessibility = Permissions.accessibility
        launchAtLogin = SystemStatus.launchAtLogin
        legacyLoggerAgent = SystemStatus.legacyLoggerAgentExists
        if let ui = promptUI { notificationsAllowed = await ui.refreshNotificationAuthorization() }
        if let cfg {
            let running = SystemStatus.runningBrowsers(cfg)
            let checked = await Task.detached {
                running.map { (id: $0.0, name: $0.1, automation: SystemStatus.automation(bundleId: $0.0)) }
            }.value
            browsers = checked
        }
    }

    /// The "Not working" switch: that time gets no nudges or check-ins and isn't trained on.
    func setNotWorking(_ on: Bool) {
        guard let store else { return }
        Task {
            do { try await store.setNotWorking(on) } catch { promptError = "\(error)" }
            await refreshStatus()
        }
    }

    /// Menu bar icon colour: the live model's top state; plain while not working, away, unsure or stale.
    var menuBarTint: NSColor? {
        guard notWorkingSince == nil, let r = readout, r.away != true, let i = r.topState,
              MenuBarIcon.stateColors.indices.contains(i) else { return nil }
        return MenuBarIcon.stateColors[i]
    }

    /// Runs while the popover is open (driven by its `.task`): today's segments every 30 s.
    /// Kept out of the always-on prompt loop because it scans all of today's samples.
    func refreshTodayWhileVisible() async {
        while !Task.isCancelled {
            if let store, let segs = try? await store.timeline(day: Date()), segs != today { today = segs }
            try? await Task.sleep(for: .seconds(30))
        }
    }

    func testPrompt(_ kind: PromptKind) {
        guard let store, let pump else { return }
        Task {
            do { try await store.insertTestPrompt(kind: kind) } catch { promptError = "\(error)"; return }
            await pump.pump()
        }
    }

    func openViewer() {
        guard let store else { return }
        ViewerWindow.show(store: store, riskThreshold: nudge.riskThreshold)
    }

    func toggleLaunchAtLogin(_ on: Bool) {
        do { try SystemStatus.setLaunchAtLogin(on) } catch { promptError = "Launch at login: \(error.localizedDescription)" }
        launchAtLogin = SystemStatus.launchAtLogin
    }

    /// Called from applicationWillTerminate: record logger_stop (bounded wait).
    func shutdown() {
        promptLoop?.cancel()
        loop?.cancel()
        observer?.stop()
        jobLoop?.cancel()
        jobs.stopAll()
        guard let store else { return }
        let done = DispatchSemaphore(value: 0)
        Task.detached {
            try? await store.write(event: Date().timeIntervalSince1970, kind: "logger_stop", detail: nil)
            done.signal()
        }
        _ = done.wait(timeout: .now() + 1)
    }
}
