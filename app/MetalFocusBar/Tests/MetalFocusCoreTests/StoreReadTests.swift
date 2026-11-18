import Foundation
import Testing
@testable import MetalFocusCore

/// Port of tests/bridge/test_store_reads.py (commit ef19dd0).
@Suite struct StoreReadTests {
    @Test func notWorkingSwitch() async throws {
        let f = try StoreFixture()
        #expect(try await f.store.notWorkingSince() == nil)
        try await f.store.setNotWorking(true)
        try await f.store.setNotWorking(true)  // still one open stretch
        #expect(try await f.store.notWorkingSince() != nil)
        #expect(try f.db.query("SELECT count(*) AS n FROM not_working WHERE ended_at IS NULL").first?["n"] == .int(1))
        try await f.store.setNotWorking(false)
        #expect(try await f.store.notWorkingSince() == nil)
    }

    @Test func checkinColumnsMatchSpec() throws {
        let f = try StoreFixture()
        let cols = try f.db.query("PRAGMA table_info(checkins)").compactMap { $0["name"]?.string }
        #expect(cols == ["id", "asked_at", "trigger", "window_start", "window_end", "model_top_state",
                         "model_top_prob", "response", "responded_at", "split"])
    }

    @Test func statusRunningAndCounts() async throws {
        let f = try StoreFixture()
        try f.addSample(NOW - 100)
        try f.addSample(NOW - 5, appName: nil, title: nil, excluded: 1)
        let s = try await f.store.status()
        #expect(s.loggerRunning && s.samplesToday == 2 && s.excludedToday == 1 && s.lastSampleTs == NOW - 5)
    }

    @Test func statusNotRunningWhenStale() async throws {
        let f = try StoreFixture()
        try f.addSample(NOW - 13)
        #expect(try await f.store.status().loggerRunning == false)
    }

    @Test func statusNoSamples() async throws {
        let s = try await StoreFixture().store.status()
        #expect(s.lastSampleTs == nil && !s.loggerRunning && s.lastEvent == nil)
    }

    @Test func statusLastEvent() async throws {
        let f = try StoreFixture()
        try f.db.run("INSERT INTO events (ts, kind, detail) VALUES (?, 'wake', NULL)", [.double(NOW - 3)])
        #expect(try await f.store.status().lastEvent == EventRow(id: nil, ts: NOW - 3, kind: "wake", detail: nil))
    }

    @Test func samplesRedactExcludedAndPrivate() async throws {
        let f = try StoreFixture()
        try f.addSample(NOW - 30, appName: "Chase", title: "Chase", domain: "chase.com", excluded: 1)
        try f.addSample(NOW - 20, appName: "Google Chrome", title: "secret", domain: "x.com", isPrivate: 1)
        try f.addSample(NOW - 10)
        let rows = try await f.store.samples(since: nil, limit: 200)
        #expect(rows.map(\.ts) == [NOW - 10, NOW - 20, NOW - 30])
        for r in rows.dropFirst() {
            #expect(r.windowTitle == nil && r.domain == nil && r.appName == nil && r.bundleId == nil)
        }
        #expect(rows[2].excluded == 1 && rows[0].windowTitle == "x.py")
        #expect(try await f.store.samples(since: nil, limit: 1).count == 1)
        #expect(try await f.store.samples(since: NOW - 20, limit: 200).map(\.ts) == [NOW - 10])
    }

    @Test func eventsNewestFirst() async throws {
        let f = try StoreFixture()
        for (i, k) in ["sleep", "wake", "lock"].enumerated() {
            try f.db.run("INSERT INTO events (ts, kind) VALUES (?, ?)", [.double(NOW - 10 + Double(i)), .text(k)])
        }
        #expect(try await f.store.events(since: nil, limit: 2).map(\.kind) == ["lock", "wake"])
    }

    @Test func timelineFromSamples() async throws {
        let f = try StoreFixture()
        for i in 0..<3 { try f.addSample(NOW - 100 + 4 * Double(i)) }
        try f.addSample(NOW - 88, appName: "Safari", idleS: 95)
        try f.addSample(NOW - 84, appName: nil, title: nil, excluded: 1)
        let segs = try await f.store.timeline(day: Date(timeIntervalSince1970: NOW))
        #expect(segs.map(\.label) == ["Code", "Away", "Excluded"])
        #expect(segs.allSatisfy { $0.source == "samples" })
        #expect(segs[0].start == NOW - 100 && segs[0].end == NOW - 92 + 4)
    }

    @Test func timelineOtherDayEmpty() async throws {
        let f = try StoreFixture()
        try f.addSample(NOW)
        #expect(try await f.store.timeline(day: Date(timeIntervalSince1970: 946_728_000)).isEmpty)
    }

    @Test func readoutMissingTableIsNil() async throws {
        #expect(try await StoreFixture().store.readout() == nil)
    }

    func readouts(_ f: StoreFixture, writtenAt: Double) throws {
        try f.db.exec("CREATE TABLE readouts (window_start REAL, model TEXT, day TEXT, written_at REAL, params TEXT, readout TEXT, PRIMARY KEY(window_start, model))")
        try f.db.run("INSERT INTO readouts VALUES (?, 'hsmm', '2025-11-13', ?, '', ?)",
                     [.double(NOW - 120), .double(writtenAt), .text(#"{"risk": 0.7, "away": false, "p_state": [0.1, 0.2, 0.3, 0.4], "top_state": 3}"#)])
    }

    @Test func readoutFresh() async throws {
        let f = try StoreFixture()
        try readouts(f, writtenAt: NOW - 10)
        let r = try #require(try await f.store.readout())
        #expect(r.risk == 0.7 && r.windowStart == NOW - 120 && r.writtenAt == NOW - 10)
        #expect(r.away == false && r.pState == [0.1, 0.2, 0.3, 0.4] && r.topState == 3)
    }

    @Test func dayActivityRanksDayAndHidesPrivate() async throws {
        let f = try StoreFixture()
        try f.addSample(NOW - 40, appName: "Code", title: "a.py", domain: "github.com")
        try f.addSample(NOW - 30, appName: "Code", title: "a.py", domain: "github.com")
        try f.addSample(NOW - 20, appName: "Safari", title: "Reddit", domain: "reddit.com", bundleId: "com.apple.Safari")
        try f.addSample(NOW - 10, appName: "Chase", title: "secret", domain: "chase.com", excluded: 1, bundleId: "com.chase")
        try f.addSample(NOW - 200_000, appName: "Yesterday")
        try f.checkin(askedAt: NOW - 15)
        try f.checkin(askedAt: NOW - 200_000)
        try f.db.run("INSERT INTO events (ts, kind, detail) VALUES (?, 'wake', 'lid')", [.double(NOW - 12)])

        let day = try await f.store.dayActivity(day: Date(timeIntervalSince1970: NOW))
        #expect(day.sampleCount == 4 && day.loggedSeconds == 16)
        #expect(day.apps.map(\.name) == ["Code", "Excluded", "Safari"])
        #expect(day.apps[0].seconds == 8)
        #expect(day.sites.map(\.name) == ["github.com", "reddit.com"])
        #expect(day.titles == [RankedTime(name: "a.py", seconds: 8, detail: "Code"),
                               RankedTime(name: "Reddit", seconds: 4, detail: "Safari")])
        #expect(day.checkins.count == 1 && day.events.map(\.kind) == ["wake"])
        #expect(day.samples.first?.ts == NOW - 10 && day.samples.first?.appName == nil)
        #expect(day.nudges.isEmpty)
        #expect(try await f.store.dayActivity(day: Date(timeIntervalSince1970: 946_728_000)).sampleCount == 0)
    }

    @Test func dayActivityReadsNudgesWhenLogged() async throws {
        let f = try StoreFixture()
        try f.db.exec("""
            CREATE TABLE nudge_log (window_start REAL PRIMARY KEY, day TEXT, decided_at REAL, nudge_id TEXT,
              risk REAL, holdout INTEGER, nudge_response_id INTEGER, body TEXT)
            """)
        let id = try f.nudge(askedAt: NOW - 5)
        try f.db.run("UPDATE nudge_responses SET response = 'back_to_work' WHERE id = ?", [.int(id)])
        try f.db.run("INSERT INTO nudge_log VALUES (?, 'd', ?, 'u1', 0.8, 0, ?, 'YouTube')",
                     [.double(NOW - 20), .double(NOW - 20), .int(id)])
        let nudges = try await f.store.dayActivity(day: Date(timeIntervalSince1970: NOW)).nudges
        #expect(nudges.count == 1 && nudges[0].risk == 0.8 && nudges[0].holdout == false)
        #expect(nudges[0].body == "YouTube" && nudges[0].response == "back_to_work")
    }

    @Test func readoutStaleIsNil() async throws {
        let f = try StoreFixture()
        try readouts(f, writtenAt: NOW - 301)
        #expect(try await f.store.readout() == nil)
    }

    @Test func riskSeriesMissingTableIsEmpty() async throws {
        #expect(try await StoreFixture().store.riskSeries(day: Date(timeIntervalSince1970: NOW)).isEmpty)
    }

    @Test func riskSeriesKeepsTheDayAndBreaksOnAway() async throws {
        let f = try StoreFixture()
        let day = Date(timeIntervalSince1970: NOW)
        let key = Store.localDay(day)
        try f.db.exec("CREATE TABLE readouts (window_start REAL, model TEXT, day TEXT, written_at REAL, params TEXT, readout TEXT, PRIMARY KEY(window_start, model))")
        func add(_ start: Double, _ day: String, _ json: String) throws {
            try f.db.run("INSERT INTO readouts VALUES (?, 'hsmm', ?, ?, '', ?)",
                         [.double(start), .text(day), .double(NOW), .text(json)])
        }
        try add(NOW - 240, key, #"{"risk": 0.2, "away": false}"#)
        try add(NOW - 120, key, #"{"risk": 0.8, "away": true}"#)
        try add(NOW, key, #"{"risk": 0.5}"#)
        try add(NOW - 60, "1999-01-01", #"{"risk": 0.9, "away": false}"#)
        let pts = try await f.store.riskSeries(day: day)
        #expect(pts.map(\.windowStart) == [NOW - 240, NOW - 120, NOW])
        #expect(pts.map(\.risk) == [0.2, nil, 0.5])
    }
}
