import Foundation
@testable import MetalFocusCore

/// Shared setup for the ports of the v1 bridge tests (tests/bridge in commit ef19dd0).
let NOW: Double = 1_763_000_000

struct StoreFixture {
    let store: Store, db: Database, clock: FakeClock

    init() throws {
        let clock = FakeClock(NOW)
        let db = try Database(url: TestRepo.tempDir().appendingPathComponent("activity.db"), key: TestDB.key)
        try db.applySchema(repo: TestRepo.root)
        self.clock = clock; self.db = db
        store = Store(db: db, runningThresholdS: 12, now: { clock.t })
    }

    func addSample(_ ts: Double, appName: String? = "Code", title: String? = "x.py", domain: String? = nil,
                   isPrivate: Int? = nil, excluded: Int = 0, idleS: Double = 0, bundleId: String? = "com.microsoft.VSCode") throws {
        try db.run("""
            INSERT INTO samples (ts, tz_offset_min, dt, bundle_id, app_name, window_title, domain, private, excluded,
              fullscreen, locked, idle_s, app_switches, key_active, click_active, scroll_active)
            VALUES (?, 0, 4.0, ?, ?, ?, ?, ?, ?, 0, 0, ?, 0, 1, 0, 0)
            """, [.double(ts), SQLValue(bundleId), SQLValue(appName), SQLValue(title), SQLValue(domain), SQLValue(isPrivate),
                  SQLValue(excluded), .double(idleS)])
    }

    func checkin(askedAt: Double = NOW - 50) throws -> Int64 {
        try db.run("INSERT INTO checkins (asked_at, trigger, split) VALUES (?, 'random', 'eval')", [.double(askedAt)])
        return db.lastInsertRowID()
    }

    func nudge(askedAt: Double = NOW - 50) throws -> Int64 {
        try db.run("INSERT INTO nudge_responses (nudge_id, asked_at) VALUES ('u1', ?)", [.double(askedAt)])
        return db.lastInsertRowID()
    }

    func outbox(_ kind: String, _ ref: Int64?, expiresAt: Double = NOW + 60, deliveredAt: Double? = nil,
                payload: String = "{}") throws -> Int64 {
        try db.run("""
            INSERT INTO notify_outbox (created_at, kind, ref_id, payload_json, delivered_at, expires_at)
            VALUES (?, ?, ?, ?, ?, ?)
            """, [.double(NOW - 1), .text(kind), ref.map { .int($0) } ?? .null, .text(payload), SQLValue(deliveredAt), .double(expiresAt)])
        return db.lastInsertRowID()
    }

    func get(_ table: String, _ id: Int64) throws -> [String: SQLValue] {
        try db.query("SELECT * FROM \(table) WHERE id = ?", [.int(id)]).first ?? [:]
    }
}
