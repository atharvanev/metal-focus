import Foundation

extension CheckinAnswer {
    /// Button text ("skip" is stored lowercase but shown as "Skip").
    public var title: String { self == .skip ? "Skip" : rawValue }
}

extension NudgeAnswer {
    /// The buttons a nudge offers; their raw values double as notification action ids.
    public static let buttons: [NudgeAnswer] = [.backToWork, .onBreak, .snooze]

    public var title: String {
        switch self {
        case .backToWork: "Back to work"
        case .onBreak: "I'm on break"
        case .snooze: "Snooze 20 min"
        case .dismissed: "Dismiss"
        }
    }

    /// Maps a notification action id to an answer. `isDismiss` is true for the system dismiss action;
    /// anything else that isn't a button (e.g. the default tap) is not an answer.
    public init?(actionId: String, isDismiss: Bool) {
        if isDismiss { self = .dismissed; return }
        guard let a = NudgeAnswer(rawValue: actionId), Self.buttons.contains(a) else { return nil }
        self = a
    }
}
