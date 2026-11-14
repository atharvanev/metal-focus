import Foundation
import Testing
@testable import MetalFocusCore

@Suite struct PrivacyTests {
    @Test func sanitize() {
        #expect(Privacy.sanitizeURL("HTTPS://User:pw@WWW.Example.COM:8443/a?b=1#f") == "example.com")
        #expect(Privacy.sanitizeURL("  http://news.ycombinator.com/item?id=1 ") == "news.ycombinator.com")
        #expect(Privacy.sanitizeURL("https://www.www.x.com/") == "www.x.com")
        for bad in ["file:///Users/x", "chrome://settings", "about:blank", "", "not a url"] {
            #expect(Privacy.sanitizeURL(bad) == nil)
        }
        #expect(Privacy.sanitizeURL(nil) == nil)
    }

    @Test func suffix() {
        #expect(Privacy.domainMatches("secure.chase.com", "chase.com"))
        #expect(Privacy.domainMatches("chase.com", "chase.com"))
        #expect(!Privacy.domainMatches("notchase.com", "chase.com"))
    }

    @Test func titleWholeWord() throws {
        let c = try LoggerConfig.load(repo: TestRepo.root, includeLocal: false)
        #expect(Privacy.isExcluded(bundleId: nil, domain: nil, title: "My 401(k) summary", cfg: c))
        #expect(Privacy.isExcluded(bundleId: nil, domain: nil, title: "Online BANKING - Home", cfg: c))
        #expect(!Privacy.isExcluded(bundleId: nil, domain: nil, title: "bankrupt.py", cfg: c))
        #expect(Privacy.isExcluded(bundleId: nil, domain: "online.mysmalltownbank.com", title: nil, cfg: c))
        #expect(Privacy.isExcluded(bundleId: "com.apple.Passwords", domain: nil, title: nil, cfg: c))
    }
}
