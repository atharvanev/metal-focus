import Foundation

/// App-switch counter fed by NSWorkspace activation notifications (port of logger/counters.py).
/// Deliberately the only thing counted: there is no keyboard or mouse counter.
public final class SwitchCounter: @unchecked Sendable {
    private let lock = NSLock()
    private var n = 0
    public init() {}
    public func increment() { lock.withLock { n += 1 } }
    public func snapshotAndReset() -> Int { lock.withLock { defer { n = 0 }; return n } }
}
