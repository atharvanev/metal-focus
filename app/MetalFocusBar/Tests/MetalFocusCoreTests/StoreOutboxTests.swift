import Foundation
import Testing
@testable import MetalFocusCore

/// Port of tests/bridge/test_store_writes.py (commit ef19dd0).
@Suite struct StoreOutboxTests {
    @Test func pendingExcludesDeliveredAndExpired() async throws {
        let f = try StoreFixture()
        let fresh = try f.outbox("checkin", nil, payload: #"{"question": "q"}"#)
        _ = try f.outbox("checkin", nil, deliveredAt: NOW - 5)
        _ = try f.outbox("checkin", nil, expiresAt: NOW - 1)
        let p = try await f.store.pendingOutbox()
        #expect(p.map(\.outboxId) == [fresh])
        #expect(p[0].question == "q" && p[0].kind == .checkin && p[0].expiresAt == NOW + 60 && !p[0].test)
    }

    @Test func ackSetsAskedAtToShownTime() async throws {
        let f = try StoreFixture()
        let cid = try f.checkin()
        let oid = try f.outbox("checkin", cid)
        #expect(try await f.store.ackDelivered(outboxId: oid))
        #expect(try f.get("checkins", cid)["asked_at"] == .double(NOW))
        #expect(try f.get("notify_outbox", oid)["delivered_at"] == .double(NOW))
        #expect(try await f.store.pendingOutbox().isEmpty)
        #expect(try await f.store.ackDelivered(outboxId: 9999) == false)
    }

    @Test func ackNudgeSetsAskedAt() async throws {
        let f = try StoreFixture()
        let nid = try f.nudge()
        try await f.store.ackDelivered(outboxId: try f.outbox("nudge", nid))
        #expect(try f.get("nudge_responses", nid)["asked_at"] == .double(NOW))
    }

    @Test func checkinResponseWrites() async throws {
        let f = try StoreFixture()
        let cid = try f.checkin()
        try await f.store.ackDelivered(outboxId: try f.outbox("checkin", cid))
        #expect(try await f.store.checkinResponse(checkinId: cid, response: .drifting, respondedAt: NOW + 7))
        let row = try f.get("checkins", cid)
        #expect(row["response"] == .text("Drifting") && row["responded_at"] == .double(NOW + 7) && row["split"] == .text("eval"))
    }

    @Test func secondResponseNotApplied() async throws {
        let f = try StoreFixture()
        let cid = try f.checkin()
        try await f.store.checkinResponse(checkinId: cid, response: .drifting, respondedAt: NOW + 7)
        #expect(try await f.store.checkinResponse(checkinId: cid, response: .working, respondedAt: NOW + 9) == false)
        #expect(try f.get("checkins", cid)["response"] == .text("Drifting"))
    }

    /// v1 test_invalid_response_rejected: invalid strings can't be expressed in Swift (typed enums), so check
    /// instead that the enums match the CHECK constraints in data/schema.sql exactly.
    @Test func answerSetsMatchSchema() throws {
        let sql = try String(contentsOf: TestRepo.root.appendingPathComponent("data/schema.sql"), encoding: .utf8)
        func checkSet(_ table: String) -> Set<String> {
            let tail = sql.components(separatedBy: "CREATE TABLE IF NOT EXISTS \(table)")[1]
            let inner = tail.components(separatedBy: "response IN (")[1].components(separatedBy: ")")[0]
            return Set(inner.components(separatedBy: ",").map { $0.trimmingCharacters(in: .whitespaces).trimmingCharacters(in: CharacterSet(charactersIn: "'")) })
        }
        #expect(Set(CheckinAnswer.allCases.map(\.rawValue)) == checkSet("checkins"))
        #expect(Set(NudgeAnswer.allCases.map(\.rawValue)) == checkSet("nudge_responses"))
        #expect(!CheckinAnswer.buttons.contains(.dismissed))
    }

    @Test func nudgeLatency() async throws {
        let f = try StoreFixture()
        let nid = try f.nudge()
        try await f.store.ackDelivered(outboxId: try f.outbox("nudge", nid))
        #expect(try await f.store.nudgeResponse(rowId: nid, response: .snooze, respondedAt: NOW + 4))
        let row = try f.get("nudge_responses", nid)
        #expect(row["response"] == .text("snooze") && row["response_latency_s"]?.double == 4)
    }

    @Test func sweepShownUnanswered() async throws {
        let f = try StoreFixture()
        let old = try f.checkin()
        _ = try f.outbox("checkin", old, expiresAt: NOW - 31, deliveredAt: NOW - 80)
        let recent = try f.checkin()
        _ = try f.outbox("checkin", recent, expiresAt: NOW - 10, deliveredAt: NOW - 60)
        let nid = try f.nudge()
        _ = try f.outbox("nudge", nid, expiresAt: NOW - 40, deliveredAt: NOW - 90)
        #expect(try await f.store.sweepExpired() == 2)
        #expect(try f.get("checkins", old)["response"] == .text("dismissed"))
        #expect(try f.get("checkins", old)["responded_at"] == .double(NOW - 31))
        #expect(try f.get("checkins", recent)["response"] == .null)
        #expect(try f.get("nudge_responses", nid)["response"] == .text("dismissed"))
        #expect(try await f.store.sweepExpired() == 0)
    }

    @Test func sweepIgnoresNeverShown() async throws {
        let f = try StoreFixture()
        let cid = try f.checkin()
        _ = try f.outbox("checkin", cid, expiresAt: NOW - 100)
        #expect(try await f.store.sweepExpired() == 0)
        #expect(try f.get("checkins", cid)["response"] == .null)
    }

    @Test func testPromptTouchesNoLabelTables() async throws {
        let f = try StoreFixture()
        let oid = try await f.store.insertTestPrompt(kind: .checkin)
        let p = try await f.store.pendingOutbox()
        #expect(p[0].outboxId == oid && p[0].refId == nil && p[0].test && p[0].expiresAt == NOW + 60)
        #expect(p[0].question == Store.defaultQuestion)
        _ = try await f.store.insertTestPrompt(kind: .nudge)
        #expect(try f.db.query("SELECT count(*) AS n FROM checkins").first?["n"] == .int(0))
        #expect(try f.db.query("SELECT count(*) AS n FROM nudge_responses").first?["n"] == .int(0))
    }

    @Test func pythonPayloadsParse() async throws {
        let f = try StoreFixture()
        _ = try f.outbox("nudge", try f.nudge(), payload: #"{"body": "Rabbit hole?", "nudge_id": "n1", "window_start": 1, "window_end": 2, "risk": 0.7}"#)
        let p = try await f.store.pendingOutbox()
        #expect(p.first?.body == "Rabbit hole?" && p.first?.kind == .nudge && p.first?.refId != nil)
    }
}
