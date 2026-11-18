import Foundation

/// Shows prompts (the check-in panel, nudge notifications). Implemented by the UI layer.
@MainActor public protocol PromptPresenter: AnyObject {
    func showCheckin(_ item: OutboxItem, deadline: Date)
    func closeCheckin()
    func showNudge(_ item: OutboxItem, deadline: Date)
    func closeNudge(outboxId: Int64)
}

/// Persists prompt lifecycle events (Store calls; test prompts write nothing to label tables).
@MainActor public protocol PromptSink: AnyObject {
    func ack(_ item: OutboxItem) async
    func answerCheckin(_ item: OutboxItem, _ answer: CheckinAnswer, respondedAt: Date) async
    func answerNudge(_ item: OutboxItem, _ answer: NudgeAnswer, respondedAt: Date) async
}

/// Prompt lifecycle: at most one check-in on screen (a newer one replaces it, the old one is dismissed),
/// any number of nudges keyed by outbox id. A check-in closes by min(shown + maxOnScreen, expires_at)
/// as `dismissed` with responded_at = that deadline; a nudge stays until answered or dismissed (user,
/// 2026-11-17). A prompt already past expires_at is never shown.
@MainActor public final class PromptCoordinator {
    let presenter: PromptPresenter, sink: PromptSink
    let now: () -> Date, maxOnScreen: TimeInterval
    public private(set) var currentCheckin: OutboxItem?
    private var checkinDeadline: Date?
    private var nudges: [Int64: (item: OutboxItem, deadline: Date)] = [:]
    private var seen = Set<Int64>()

    public init(presenter: PromptPresenter, sink: PromptSink, now: @escaping () -> Date = Date.init,
                maxOnScreen: TimeInterval = 60) {
        self.presenter = presenter; self.sink = sink; self.now = now; self.maxOnScreen = maxOnScreen
    }

    public var openNudgeIds: [Int64] { nudges.keys.sorted() }

    public func receive(_ item: OutboxItem) async {
        guard seen.insert(item.outboxId).inserted else { return }  // polled again before it was acked
        let t = now(), expires = Date(timeIntervalSince1970: item.expiresAt)
        guard expires > t else { return }  // e.g. woke from sleep: stale prompts are never shown
        let deadline = min(t.addingTimeInterval(maxOnScreen), expires)
        switch item.kind {
        case .checkin:
            if let old = currentCheckin {
                currentCheckin = nil; checkinDeadline = nil
                presenter.closeCheckin()
                await sink.answerCheckin(old, .dismissed, respondedAt: t)
            }
            currentCheckin = item; checkinDeadline = deadline
            await sink.ack(item)
            presenter.showCheckin(item, deadline: deadline)
        case .nudge:
            nudges[item.outboxId] = (item, .distantFuture)
            await sink.ack(item)
            presenter.showNudge(item, deadline: .distantFuture)
        }
    }

    public func userAnswered(_ answer: CheckinAnswer) async {
        guard let item = currentCheckin else { return }  // already answered or timed out
        currentCheckin = nil; checkinDeadline = nil
        presenter.closeCheckin()
        await sink.answerCheckin(item, answer, respondedAt: now())
    }

    public func userAnswered(nudge outboxId: Int64, _ answer: NudgeAnswer) async {
        guard let (item, _) = nudges.removeValue(forKey: outboxId) else { return }
        presenter.closeNudge(outboxId: outboxId)
        await sink.answerNudge(item, answer, respondedAt: now())
    }

    /// Call about once a second: closes prompts whose deadline has passed.
    public func tick() async {
        let t = now()
        if let item = currentCheckin, let d = checkinDeadline, d <= t {
            currentCheckin = nil; checkinDeadline = nil
            presenter.closeCheckin()
            await sink.answerCheckin(item, .dismissed, respondedAt: d)
        }
        for (id, entry) in nudges.sorted(by: { $0.key < $1.key }) where entry.deadline <= t {
            nudges.removeValue(forKey: id)
            presenter.closeNudge(outboxId: id)
            await sink.answerNudge(entry.item, .dismissed, respondedAt: entry.deadline)
        }
    }
}
