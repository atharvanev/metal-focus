import Foundation

public enum PromptKind: String, Sendable, Equatable {
    case checkin, nudge
    /// The label table whose row `notify_outbox.ref_id` points at.
    var table: String { self == .checkin ? "checkins" : "nudge_responses" }
}

/// Exactly the `checkins.response` values allowed by data/schema.sql.
public enum CheckinAnswer: String, CaseIterable, Sendable {
    case working = "Working", shortBreak = "Short break", drifting = "Drifting", rabbitHole = "Rabbit hole"
    case away = "Away", skip = "skip", dismissed = "dismissed"
    /// The buttons shown to the user (dismissed is only ever set by timeout/sweep).
    public static let buttons: [CheckinAnswer] = [.working, .shortBreak, .drifting, .rabbitHole, .away, .skip]
}

/// Exactly the `nudge_responses.response` values allowed by data/schema.sql.
public enum NudgeAnswer: String, CaseIterable, Sendable {
    case backToWork = "back_to_work", onBreak = "on_break", snooze = "snooze", dismissed = "dismissed"
}

/// A pending `notify_outbox` row. `refId` is nil for test prompts (they touch no label table).
public struct OutboxItem: Sendable, Equatable {
    public var outboxId: Int64
    public var kind: PromptKind
    public var refId: Int64?
    public var expiresAt: Double
    public var question: String?
    public var body: String?
    public var test: Bool
    public init(outboxId: Int64, kind: PromptKind, refId: Int64?, expiresAt: Double,
                question: String? = nil, body: String? = nil, test: Bool = false) {
        self.outboxId = outboxId; self.kind = kind; self.refId = refId; self.expiresAt = expiresAt
        self.question = question; self.body = body; self.test = test
    }
}

public struct EventRow: Sendable, Equatable {
    public var id: Int64?
    public var ts: Double
    public var kind: String
    public var detail: String?
}

public struct Status: Sendable, Equatable {
    public var loggerRunning: Bool
    public var lastSampleTs: Double?
    public var samplesToday: Int
    public var excludedToday: Int
    public var lastEvent: EventRow?
}

/// A `samples` row as the viewer may show it: excluded/private rows carry no app, title or domain.
public struct SampleRow: Sendable, Equatable {
    public var ts: Double
    public var dt: Double
    public var bundleId: String?
    public var appName: String?
    public var windowTitle: String?
    public var domain: String?
    public var isPrivate: Int?
    public var excluded: Int
    public var fullscreen: Int?
    public var locked: Int
    public var idleS: Double?
    public var appSwitches: Int
    public var keyActive: Int?
    public var clickActive: Int?
    public var scrollActive: Int?
    public var mediaPlaying: Int?
}

public struct CheckinRow: Sendable, Equatable {
    public var id: Int64
    public var askedAt: Double
    public var trigger: String
    public var windowStart: Double?
    public var windowEnd: Double?
    public var modelTopState: String?
    public var modelTopProb: Double?
    public var response: String?
    public var respondedAt: Double?
    public var split: String
}

/// Consecutive samples with the same label (app name, Away or Excluded).
public struct Segment: Sendable, Equatable {
    public var start: Double
    public var end: Double
    public var label: String
    public var source: String
}

/// One ranked row on the day page (an app, a site, or a window title).
public struct RankedTime: Sendable, Equatable {
    public var name: String
    public var seconds: Double
    /// App name when `name` is a window title.
    public var detail: String?
}

/// A nudge the engine decided to show (or hold out) during a day.
public struct NudgeRow: Sendable, Equatable {
    public var windowStart: Double
    public var risk: Double
    public var holdout: Bool
    public var body: String?
    public var response: String?
}

/// Read-only day page: the same totals, lists, and rows the local dashboard shows.
public struct DayActivity: Sendable, Equatable {
    public var loggedSeconds: Double
    public var sampleCount: Int
    public var apps: [RankedTime]
    public var sites: [RankedTime]
    public var titles: [RankedTime]
    public var checkins: [CheckinRow]
    public var nudges: [NudgeRow]
    public var samples: [SampleRow]
    public var events: [EventRow]

    public static let empty = DayActivity(loggedSeconds: 0, sampleCount: 0, apps: [], sites: [], titles: [],
                                          checkins: [], nudges: [], samples: [], events: [])
}

/// One point on the viewer's day risk line. `risk` is nil for an Away window, which breaks the line.
public struct RiskPoint: Sendable, Equatable {
    public var windowStart: Double
    public var risk: Double?
}

/// The latest live-model readout (hsmm.runner `readouts` table), if fresh.
public struct ReadoutInfo: Sendable, Equatable {
    public var windowStart: Double
    public var writtenAt: Double
    public var risk: Double
    public var pState: [Double]?
    public var topState: Int?
    public var topProb: Double?
    public var minutesInState: Double?
    public var surprise: Double?
    public var away: Bool?
}
