import Foundation
import Testing
@testable import MetalFocusCore

final class FakeProbes: Probes, @unchecked Sendable {
    let lock = NSLock()
    var locked = false
    var app: AppInfo? = AppInfo(bundleId: "com.microsoft.VSCode", name: "Code", pid: 42)
    var win: WindowInfo? = WindowInfo(title: "main.swift", fullscreen: false)
    var browserInfo: BrowserInfo? = BrowserInfo(url: "https://example.com/x", isPrivate: false)
    var windowThrows = false
    var frontmostDelay: TimeInterval = 0
    private(set) var windowCalls = 0
    private(set) var browserCalls = 0
    struct Boom: Error {}

    func screenLocked() throws -> Bool { locked }
    func frontmost() throws -> AppInfo? {
        if frontmostDelay > 0 { Thread.sleep(forTimeInterval: frontmostDelay) }
        return app
    }
    func window(pid: Int32) throws -> WindowInfo? {
        lock.withLock { windowCalls += 1 }
        if windowThrows { throw Boom() }
        return win
    }
    func browser(bundleId: String) throws -> BrowserInfo? { lock.withLock { browserCalls += 1 }; return browserInfo }
    func idleSeconds() throws -> Double? { 0.5 }
    func inputRecency() throws -> InputRecency? { InputRecency(keyS: 1, clickS: 100, scrollS: nil) }
}

actor RecordingSink: SampleSink {
    var samples: [Sample] = []
    var events: [(Double, String, String?)] = []
    func write(sample: Sample) async throws { samples.append(sample) }
    func write(event ts: Double, kind: String, detail: String?) async throws { events.append((ts, kind, detail)) }
}

final class FakeClock: @unchecked Sendable {
    var t: Double
    init(_ t: Double) { self.t = t }
}

@Suite struct SamplerTests {
    let cfg = try! LoggerConfig.load(repo: TestRepo.root, includeLocal: false)

    func make(_ probes: FakeProbes = FakeProbes(), clock: FakeClock = FakeClock(100))
        -> (Sampler, RecordingSink, SwitchCounter, FakeClock, FakeProbes) {
        let sink = RecordingSink(), sw = SwitchCounter()
        let s = Sampler(probes: probes, switches: sw, sink: sink, cfg: cfg, clock: { clock.t }, tzOffset: { -300 })
        return (s, sink, sw, clock, probes)
    }

    @Test func firstTickDt() async {
        let (s, sink, _, _, _) = make()
        let sample = await s.tick()
        #expect(sample?.dt == 4)
        #expect(sample?.ts == 100 && sample?.tzOffsetMin == -300)
        #expect(await sink.events.isEmpty)
        #expect(await sink.samples.count == 1)
    }

    @Test func normalDtIsElapsed() async {
        let (s, _, _, clock, _) = make()
        _ = await s.tick()
        clock.t = 104.25
        #expect(await s.tick()?.dt == 4.25)
    }

    @Test func gapAfterSleep() async {
        let (s, sink, _, clock, _) = make()
        _ = await s.tick()
        clock.t = 3700
        #expect(await s.tick()?.dt == 4)
        let ev = await sink.events
        #expect(ev.count == 1 && ev[0].0 == 100 && ev[0].1 == "gap" && ev[0].2 == "3600.0s")
    }

    @Test func clockBackwards() async {
        let (s, sink, _, clock, _) = make()
        _ = await s.tick()
        clock.t = 90
        #expect(await s.tick()?.dt == 4)
        #expect(await sink.events.first?.2 == "-10.0s")
    }

    @Test func lockedSkipsWindowAndBrowser() async {
        let p = FakeProbes(); p.locked = true
        p.app = AppInfo(bundleId: "com.google.Chrome", name: "Google Chrome", pid: 7)
        let (s, _, _, _, _) = make(p)
        let sample = await s.tick()
        #expect(p.windowCalls == 0 && p.browserCalls == 0)
        #expect(sample?.locked == 1 && sample?.bundleId == nil)
    }

    @Test func browserOnlyForConfiguredBrowsers() async {
        let p = FakeProbes()
        let (s, _, _, clock, _) = make(p)
        _ = await s.tick()
        #expect(p.browserCalls == 0)
        p.app = AppInfo(bundleId: "com.google.Chrome", name: "Google Chrome", pid: 7)
        clock.t += 4
        let sample = await s.tick()
        #expect(p.browserCalls == 1)
        #expect(sample?.domain == "example.com" && sample?.isPrivate == 0)
    }

    @Test func probeFailureIsNull() async {
        let p = FakeProbes(); p.windowThrows = true
        let (s, sink, _, _, _) = make(p)
        let sample = await s.tick()
        #expect(sample?.windowTitle == nil && sample?.appName == "Code")
        #expect(await sink.samples.count == 1)
    }

    @Test func activityFlags() async {
        let (s, _, _, _, _) = make()
        let sample = await s.tick()
        #expect(sample?.keyActive == 1 && sample?.clickActive == 0 && sample?.scrollActive == nil)
        #expect(sample?.idleS == 0.5)
    }

    @Test func switchesSnapshot() async {
        let (s, _, sw, clock, _) = make()
        sw.increment(); sw.increment(); sw.increment()
        #expect(await s.tick()?.appSwitches == 3)
        clock.t += 4
        #expect(await s.tick()?.appSwitches == 0)
    }

    @Test func slowProbeDoesNotOverlap() async {
        let p = FakeProbes(); p.frontmostDelay = 0.3
        let (s, sink, _, _, _) = make(p)
        async let a = s.tick()
        async let b = s.tick()
        let results = await [a, b]
        #expect(results.compactMap { $0 }.count == 1)
        #expect(await sink.samples.count == 1)
    }
}
