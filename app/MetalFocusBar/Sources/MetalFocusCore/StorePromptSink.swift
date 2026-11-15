import Foundation
import os

let promptLog = Logger(subsystem: "com.metalfocus.menubar", category: "prompts")

/// Persists the prompt lifecycle through the Store. Test prompts (ref_id NULL, payload.test) are
/// acknowledged in the outbox but never touch checkins / nudge_responses.
@MainActor public final class StorePromptSink: PromptSink {
    let store: Store
    public init(store: Store) { self.store = store }

    public func ack(_ item: OutboxItem) async {
        do { try await store.ackDelivered(outboxId: item.outboxId) }
        catch { promptLog.error("ack failed: \(String(describing: error))") }
    }

    public func answerCheckin(_ item: OutboxItem, _ answer: CheckinAnswer, respondedAt: Date) async {
        guard !item.test, let ref = item.refId else {
            promptLog.info("test check-in answered: \(answer.rawValue, privacy: .public)")
            return
        }
        do { try await store.checkinResponse(checkinId: ref, response: answer, respondedAt: respondedAt.timeIntervalSince1970) }
        catch { promptLog.error("check-in answer failed: \(String(describing: error))") }
    }

    public func answerNudge(_ item: OutboxItem, _ answer: NudgeAnswer, respondedAt: Date) async {
        guard !item.test, let ref = item.refId else {
            promptLog.info("test nudge answered: \(answer.rawValue, privacy: .public)")
            return
        }
        do { try await store.nudgeResponse(rowId: ref, response: answer, respondedAt: respondedAt.timeIntervalSince1970) }
        catch { promptLog.error("nudge answer failed: \(String(describing: error))") }
    }
}

/// Polls the outbox into the coordinator and keeps the expiry sweep running. The UI calls `pump()`
/// every couple of seconds and `coordinator.tick()` every second.
@MainActor public final class PromptPump {
    let store: Store
    public let coordinator: PromptCoordinator
    public init(store: Store, coordinator: PromptCoordinator) { self.store = store; self.coordinator = coordinator }

    public func pump() async {
        do {
            for item in try await store.pendingOutbox() { await coordinator.receive(item) }
        } catch { promptLog.error("outbox poll failed: \(String(describing: error))") }
    }

    @discardableResult
    public func sweep() async -> Int {
        do { return try await store.sweepExpired() }
        catch { promptLog.error("sweep failed: \(String(describing: error))"); return 0 }
    }
}
