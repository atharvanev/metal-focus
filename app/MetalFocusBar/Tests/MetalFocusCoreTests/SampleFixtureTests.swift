import Foundation
import Testing
@testable import MetalFocusCore

/// Parity with the Python logger: tests/fixtures/sample_cases.json is generated from logger/sample.py.
@Suite struct SampleFixtureTests {
    struct Fixture: Decodable { let cases: [Case] }
    struct Case: Decodable { let name: String; let inputs: Inputs; let expected: [String: JSONScalar] }
    struct Inputs: Decodable {
        struct App: Decodable { let bundle_id: String?; let name: String? }
        struct Win: Decodable { let title: String?; let fullscreen: Bool? }
        struct Browser: Decodable { let url: String?; let `private`: Bool? }
        struct Recency: Decodable { let key_s: Double?; let click_s: Double?; let scroll_s: Double? }
        let app: App?; let window: Win?; let browser: Browser?; let recency: Recency?
        let locked: Bool; let idle_s: Double?; let app_switches: Int; let dt: Double; let media_playing: Bool?
    }
    enum JSONScalar: Decodable, Equatable {
        case null, number(Double), string(String)
        init(from d: Decoder) throws {
            let c = try d.singleValueContainer()
            if c.decodeNil() { self = .null }
            else if let n = try? c.decode(Double.self) { self = .number(n) }
            else { self = .string(try c.decode(String.self)) }
        }
    }

    static func scalar(_ v: SQLValue) -> JSONScalar {
        switch v {
        case .null: .null
        case .int(let i): .number(Double(i))
        case .double(let d): .number(d)
        case .text(let s): .string(s)
        }
    }

    @Test func allCasesMatch() throws {
        let url = TestRepo.root.appendingPathComponent("tests/fixtures/sample_cases.json")
        let fx = try JSONDecoder().decode(Fixture.self, from: Data(contentsOf: url))
        let cfg = try LoggerConfig.load(repo: TestRepo.root, includeLocal: false)
        #expect(fx.cases.count >= 32)
        for c in fx.cases {
            let i = c.inputs
            let s = SampleBuilder.build(
                ts: 0, tzOffsetMin: 0, dt: i.dt,
                app: i.app.map { AppInfo(bundleId: $0.bundle_id, name: $0.name, pid: 1) },
                window: i.window.map { WindowInfo(title: $0.title, fullscreen: $0.fullscreen) },
                browser: i.browser.map { BrowserInfo(url: $0.url, isPrivate: $0.private) },
                idleS: i.idle_s,
                recency: i.recency.map { InputRecency(keyS: $0.key_s, clickS: $0.click_s, scrollS: $0.scroll_s) },
                locked: i.locked, appSwitches: i.app_switches, cfg: cfg, mediaPlaying: i.media_playing)
            let cols = Dictionary(uniqueKeysWithValues: s.columns)
            for (key, want) in c.expected {
                let got = cols[key].map(Self.scalar)
                #expect(got == want, "case \(c.name): column \(key) = \(String(describing: got)), want \(want)")
            }
        }
    }
}
