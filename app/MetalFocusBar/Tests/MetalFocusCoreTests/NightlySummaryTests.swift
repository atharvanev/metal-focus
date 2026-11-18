import Testing
@testable import MetalFocusCore

@Suite struct NightlySummaryTests {
    @Test func saved() {
        let m = NightlySummary.message(lastLine: #"{"status": "saved", "days": ["2026-11-13", "2026-11-16"], "mu_minutes": [30.8, 5.5, 6.5, 29.8]}"#, exitCode: 0)
        #expect(m.title == "Model retrained")
        #expect(m.body == "Retrained on 2 days (2026-11-13 to 2026-11-16). Typical: work 31 min, break 6 min, rabbit hole 30 min.")
    }

    @Test func skipped() {
        let m = NightlySummary.message(lastLine: #"{"status": "skipped", "reason": "only 2 day(s) with data (< 3)"}"#, exitCode: 0)
        #expect(m.title == "Nightly training skipped" && m.body.contains("only 2 day(s)"))
    }

    @Test func failed() {
        #expect(NightlySummary.message(lastLine: nil, exitCode: 1).title == "Nightly training failed")
        #expect(NightlySummary.message(lastLine: #"{"status": "saved"}"#, exitCode: 1).title == "Nightly training failed")
    }
}
