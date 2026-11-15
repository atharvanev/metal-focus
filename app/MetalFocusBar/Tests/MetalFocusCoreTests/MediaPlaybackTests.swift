import Foundation
import Testing
@testable import MetalFocusCore

/// Parity with logger/media.py on tests/fixtures/media_cases.json.
@Suite struct MediaPlaybackTests {
    @Test func fixtureCases() throws {
        let url = TestRepo.root.appendingPathComponent("tests/fixtures/media_cases.json")
        let fx = try #require(try JSONSerialization.jsonObject(with: Data(contentsOf: url)) as? [String: Any])
        let paths = Dictionary(uniqueKeysWithValues: (fx["paths"] as! [String: String]).map { (Int32($0.key)!, $0.value) })
        let cases = fx["cases"] as! [[String: Any]]
        #expect(cases.count >= 14)
        for c in cases {
            let got = MediaPlayback.frontmostPlays(
                assertions: c["assertions"] as! [[String: Any]], frontPid: Int32(fx["front_pid"] as! Int),
                frontBundlePath: fx["front_bundle_path"] as? String, pathOf: { paths[$0] })
            #expect(got == (c["expected"] as! Bool), "case \(c["name"]!)")
        }
    }

    @Test func pythonListMatchesSwift() throws {
        let src = try String(contentsOf: TestRepo.root.appendingPathComponent("logger/media.py"), encoding: .utf8)
        for name in MediaPlayback.displayAssertions { #expect(src.contains("\"\(name)\"")) }
        let db = try String(contentsOf: TestRepo.root.appendingPathComponent("data/db.py"), encoding: .utf8)
        for c in Database.addedColumns { #expect(db.contains("(\"\(c.table)\", \"\(c.column)\", \"\(c.type)\")")) }
    }
}
