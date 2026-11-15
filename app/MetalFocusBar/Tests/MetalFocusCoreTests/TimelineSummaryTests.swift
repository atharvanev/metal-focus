import Testing
@testable import MetalFocusCore

@Suite struct TimelineSummaryTests {
    @Test func paletteIndexIsStableAndInRange() {
        #expect(TimelineSummary.paletteIndex(for: "a", count: 1000) == Int(0xaf63_dc4c_8601_ec8c % UInt64(1000))) // FNV-1a test vector
        #expect(TimelineSummary.paletteIndex(for: "", count: 8) == Int(0xcbf2_9ce4_8422_2325 % UInt64(8)))
        for name in ["Xcode", "Safari", "Discord", "Ünïcode"] {
            #expect((0..<8).contains(TimelineSummary.paletteIndex(for: name, count: 8)))
        }
    }

    @Test func totalsSumPerLabelLargestFirst() {
        let segs = [Segment(start: 0, end: 10, label: "A", source: "s"), Segment(start: 10, end: 40, label: "B", source: "s"),
                    Segment(start: 40, end: 55, label: "A", source: "s"), Segment(start: 55, end: 60, label: "Away", source: "s")]
        let t = TimelineSummary.totals(segs)
        #expect(t.map(\.label) == ["B", "A", "Away"])
        #expect(t.map(\.seconds) == [30, 25, 5])
        #expect(TimelineSummary.totals([]).isEmpty)
    }
}
