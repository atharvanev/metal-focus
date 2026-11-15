import Foundation
import Testing
@testable import MetalFocusCore

@Suite struct JobManifestTests {
    @Test func decodesDeployOutput() throws {
        // Same JSON as tests/jobs/test_deploy.py::test_render_manifest.
        let dir = FileManager.default.temporaryDirectory.appendingPathComponent(UUID().uuidString)
        let rt = JobManifest.runtime(dataDir: dir)
        try FileManager.default.createDirectory(at: rt, withIntermediateDirectories: true)
        #expect(JobManifest.load(dataDir: dir) == nil)
        try """
        {"sha": "abc", "jobs": [
          {"name": "hsmm-runner", "args": ["hsmm.runner", "run", "--no-nudge"], "keepalive": true},
          {"name": "hsmm-nightly", "args": ["hsmm.nightly"], "keepalive": false, "daily": {"hour": 3, "minute": 30}}
        ]}
        """.write(to: rt.appendingPathComponent("jobs.json"), atomically: true, encoding: .utf8)
        let m = try #require(JobManifest.load(dataDir: dir))
        #expect(m.jobs.map(\.name) == ["hsmm-runner", "hsmm-nightly"])
        #expect(m.jobs[0].keepalive && m.jobs[0].daily == nil)
        #expect(m.jobs[1].daily == .init(hour: 3, minute: 30))
    }

    @Test func legacyAgents() throws {
        let home = FileManager.default.temporaryDirectory.appendingPathComponent(UUID().uuidString)
        let la = home.appendingPathComponent("Library/LaunchAgents")
        try FileManager.default.createDirectory(at: la, withIntermediateDirectories: true)
        #expect(JobManifest.legacyAgentsInstalled(home: home).isEmpty)
        try Data().write(to: la.appendingPathComponent("com.metalfocus.checkins.plist"))
        #expect(JobManifest.legacyAgentsInstalled(home: home) == ["com.metalfocus.checkins"])
    }

    @Test func dailyDue() {
        var cal = Calendar(identifier: .gregorian); cal.timeZone = TimeZone(identifier: "UTC")!
        let d = JobManifest.Daily(hour: 3, minute: 30)
        let at = { (h: Int, m: Int) in cal.date(from: DateComponents(year: 2026, month: 11, day: 14, hour: h, minute: m))! }
        #expect(!JobSchedule.dailyDue(d, now: at(3, 0), lastStart: nil, calendar: cal))     // before the slot
        #expect(JobSchedule.dailyDue(d, now: at(3, 30), lastStart: nil, calendar: cal))
        #expect(JobSchedule.dailyDue(d, now: at(9, 0), lastStart: at(3, 0) - 86_400, calendar: cal))  // slept through it
        #expect(!JobSchedule.dailyDue(d, now: at(9, 0), lastStart: at(3, 31), calendar: cal))  // already ran today
    }

    @Test func keepaliveRestart() {
        let t0 = Date(timeIntervalSince1970: 1000)
        #expect(JobSchedule.restartKeepalive(lastExit: nil, lastStart: nil, now: t0))
        #expect(!JobSchedule.restartKeepalive(lastExit: nil, lastStart: t0, now: t0 + 100))  // running
        #expect(!JobSchedule.restartKeepalive(lastExit: 0, lastStart: t0, now: t0 + 100))    // clean exit
        #expect(!JobSchedule.restartKeepalive(lastExit: 1, lastStart: t0, now: t0 + 10))     // throttled
        #expect(JobSchedule.restartKeepalive(lastExit: 1, lastStart: t0, now: t0 + 30))
    }
}
