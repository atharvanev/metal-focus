import Testing
@testable import MetalFocusCore

@Suite struct PromptLabelsTests {
    @Test func checkinTitles() {
        #expect(CheckinAnswer.buttons.map(\.title) == ["Working", "Short break", "Drifting", "Rabbit hole", "Away", "Skip"])
    }

    @Test func nudgeActionMapping() {
        #expect(NudgeAnswer.buttons.map(\.title) == ["Back to work", "I'm on break", "Snooze 20 min"])
        #expect(NudgeAnswer(actionId: "back_to_work", isDismiss: false) == .backToWork)
        #expect(NudgeAnswer(actionId: "on_break", isDismiss: false) == .onBreak)
        #expect(NudgeAnswer(actionId: "snooze", isDismiss: false) == .snooze)
        #expect(NudgeAnswer(actionId: "anything", isDismiss: true) == .dismissed)
        #expect(NudgeAnswer(actionId: "com.apple.UNNotificationDefaultActionIdentifier", isDismiss: false) == nil)
        #expect(NudgeAnswer(actionId: "dismissed", isDismiss: false) == nil)
    }
}
