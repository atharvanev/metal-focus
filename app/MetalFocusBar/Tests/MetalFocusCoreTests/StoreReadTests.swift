import Foundation
import Testing
@testable import MetalFocusCore

/// Port of tests/bridge/test_store_reads.py (commit ef19dd0).
@Suite struct StoreReadTests {
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

    @Test func readoutStaleIsNil() async throws {
        let f = try StoreFixture()
        try readouts(f, writtenAt: NOW - 301)
        #expect(try await f.store.readout() == nil)
    }
}
