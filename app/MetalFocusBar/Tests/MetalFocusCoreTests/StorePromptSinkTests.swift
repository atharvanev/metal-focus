import Foundation
import Testing
@testable import MetalFocusCore

/// End to end through the real Store: outbox -> coordinator -> answers in the label tables.
@MainActor @Suite struct StorePromptSinkTests {
    @Test func realCheckinAnsweredAndTestPromptTouchesNothing() async throws {
        let f = try StoreFixture()
        let cid = try f.checkin()
        let oid = try f.outbox("checkin", cid, payload: #"{"question": "q"}"#)
        let presenter = FakePresenter()
        let coord = PromptCoordinator(presenter: presenter, sink: StorePromptSink(store: f.store),
                                      now: { Date(timeIntervalSince1970: NOW) })
        let pump = PromptPump(store: f.store, coordinator: coord)
        await pump.pump()
        #expect(presenter.log == ["showCheckin \(oid)"])
        #expect(try f.get("notify_outbox", oid)["delivered_at"] == .double(NOW))
        await coord.userAnswered(.rabbitHole)
        #expect(try f.get("checkins", cid)["response"] == .text("Rabbit hole"))

        _ = try await f.store.insertTestPrompt(kind: .checkin)
        await pump.pump()
        await coord.userAnswered(.working)
        #expect(try f.db.query("SELECT count(*) AS n FROM checkins WHERE response IS NOT NULL").first?["n"] == .int(1))
        await pump.pump()  // nothing pending any more
        #expect(presenter.log.filter { $0.hasPrefix("show") }.count == 2)
    }

    @Test func nudgeTimeoutWritesDismissed() async throws {
        let f = try StoreFixture()
        let nid = try f.nudge()
        _ = try f.outbox("nudge", nid, expiresAt: NOW + 20)
        final class Clock { var t = Date(timeIntervalSince1970: NOW) }
        let clock = Clock()
        let coord = PromptCoordinator(presenter: FakePresenter(), sink: StorePromptSink(store: f.store), now: { clock.t })
        await PromptPump(store: f.store, coordinator: coord).pump()
        clock.t = clock.t.addingTimeInterval(21)
        await coord.tick()
        let row = try f.get("nudge_responses", nid)
        #expect(row["response"] == .text("dismissed") && row["responded_at"] == .double(NOW + 20))
    }
}
