import Foundation
import Testing
@testable import MetalFocusCore

@MainActor final class FakePresenter: PromptPresenter {
    var log: [String] = []
    var checkinDeadline: Date?
    func showCheckin(_ i: OutboxItem, deadline: Date) { log.append("showCheckin \(i.outboxId)"); checkinDeadline = deadline }
    func closeCheckin() { log.append("closeCheckin") }
    func showNudge(_ i: OutboxItem, deadline: Date) { log.append("showNudge \(i.outboxId)") }
    func closeNudge(outboxId: Int64) { log.append("closeNudge \(outboxId)") }
}

@MainActor final class FakePromptSink: PromptSink {
    var acks: [Int64] = []
    var checkins: [(Int64, CheckinAnswer, Date)] = []
    var nudges: [(Int64, NudgeAnswer, Date)] = []
    func ack(_ i: OutboxItem) async { acks.append(i.outboxId) }
    func answerCheckin(_ i: OutboxItem, _ a: CheckinAnswer, respondedAt: Date) async { checkins.append((i.outboxId, a, respondedAt)) }
    func answerNudge(_ i: OutboxItem, _ a: NudgeAnswer, respondedAt: Date) async { nudges.append((i.outboxId, a, respondedAt)) }
}

@MainActor @Suite struct PromptCoordinatorTests {
    let presenter = FakePresenter(), sink = FakePromptSink()
    final class Clock { var t = Date(timeIntervalSince1970: NOW) }
    let clock = Clock()

    func make() -> PromptCoordinator {
        let c = clock
        return PromptCoordinator(presenter: presenter, sink: sink, now: { c.t })
    }
    func checkin(_ id: Int64, expiresIn: Double = 60) -> OutboxItem {
        OutboxItem(outboxId: id, kind: .checkin, refId: id * 10, expiresAt: NOW + expiresIn, question: "q")
    }
    func nudge(_ id: Int64, expiresIn: Double = 60) -> OutboxItem {
        OutboxItem(outboxId: id, kind: .nudge, refId: id * 10, expiresAt: NOW + expiresIn, body: "b")
    }

    @Test func showsAndAcks() async {
        let c = make()
        await c.receive(checkin(1, expiresIn: 40))
        #expect(sink.acks == [1] && presenter.log == ["showCheckin 1"])
        #expect(presenter.checkinDeadline == Date(timeIntervalSince1970: NOW + 40))  // min(now+60, expires)
        await c.receive(checkin(2, expiresIn: 600))
        #expect(presenter.checkinDeadline == Date(timeIntervalSince1970: NOW + 60))
    }

    @Test func dropsAlreadyExpired() async {
        let c = make()
        await c.receive(checkin(1, expiresIn: -1))
        await c.receive(nudge(2, expiresIn: 0))
        #expect(sink.acks.isEmpty && presenter.log.isEmpty && c.currentCheckin == nil)
    }

    @Test func answerCloses() async {
        let c = make()
        await c.receive(checkin(1))
        clock.t = clock.t.addingTimeInterval(5)
        await c.userAnswered(.drifting)
        #expect(presenter.log == ["showCheckin 1", "closeCheckin"])
        #expect(sink.checkins.count == 1 && sink.checkins[0].1 == .drifting)
        #expect(sink.checkins[0].2 == Date(timeIntervalSince1970: NOW + 5) && c.currentCheckin == nil)
    }

    @Test func timeoutDismisses() async {
        let c = make()
        await c.receive(checkin(1))
        clock.t = clock.t.addingTimeInterval(59)
        await c.tick()
        #expect(c.currentCheckin != nil)
        clock.t = clock.t.addingTimeInterval(3)
        await c.tick()
        #expect(c.currentCheckin == nil && presenter.log.last == "closeCheckin")
        #expect(sink.checkins.map(\.1) == [.dismissed] && sink.checkins[0].2 == Date(timeIntervalSince1970: NOW + 60))
    }

    @Test func newerReplacesOlder() async {
        let c = make()
        await c.receive(checkin(1))
        await c.receive(checkin(2))
        #expect(c.currentCheckin?.outboxId == 2)
        #expect(sink.checkins.map(\.0) == [1] && sink.checkins[0].1 == .dismissed)
        #expect(presenter.log == ["showCheckin 1", "closeCheckin", "showCheckin 2"])
    }

    @Test func doubleAnswerIgnored() async {
        let c = make()
        await c.receive(checkin(1))
        await c.userAnswered(.working)
        await c.userAnswered(.away)
        await c.tick()
        #expect(sink.checkins.map(\.1) == [.working])
    }

    @Test func duplicateReceiveIgnored() async {
        let c = make()
        await c.receive(checkin(1))
        await c.receive(checkin(1))
        await c.userAnswered(.working)
        await c.receive(checkin(1))
        #expect(sink.acks == [1] && c.currentCheckin == nil)
    }

    @Test func nudgeStaysUntilAnswered() async {  // user, 2026-11-17: nudges linger until taken away
        let c = make()
        await c.receive(nudge(5, expiresIn: 30))
        clock.t = clock.t.addingTimeInterval(3600)
        await c.tick()
        #expect(sink.nudges.isEmpty && presenter.log == ["showNudge 5"] && c.openNudgeIds == [5])
        await c.userAnswered(nudge: 5, .dismissed)
        #expect(sink.nudges.map(\.1) == [.dismissed] && c.openNudgeIds.isEmpty)
    }

    @Test func nudgeAnswer() async {
        let c = make()
        await c.receive(nudge(5))
        await c.receive(nudge(6))
        await c.receive(checkin(7))
        await c.userAnswered(nudge: 6, .backToWork)
        await c.userAnswered(nudge: 6, .snooze)  // second answer ignored
        #expect(sink.nudges.map(\.0) == [6] && sink.nudges[0].1 == .backToWork)
        #expect(c.openNudgeIds == [5] && c.currentCheckin?.outboxId == 7)
    }
}
