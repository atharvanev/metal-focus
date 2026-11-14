import Foundation
import Testing
@testable import MetalFocusCore

@Suite struct OsascriptTests {
    @Test func osascriptTimeout() {
        let start = Date()
        #expect(OSAScript.run("delay 5", timeout: 0.5) == nil)
        #expect(Date().timeIntervalSince(start) < 1.5)
    }

    @Test func osascriptReturnsOutput() {
        #expect(OSAScript.run("return \"hi\"", timeout: 3) == "hi\n")
        #expect(OSAScript.run("error \"x\"", timeout: 3) == nil)
    }

    @Test func scripts() {
        #expect(BrowserScripts.script(kind: .chromium, bundleId: "com.google.Chrome")
            == "tell application id \"com.google.Chrome\" to return (URL of active tab of front window) & linefeed & (mode of front window)")
        #expect(BrowserScripts.script(kind: .safari, bundleId: "com.apple.Safari")
            == "tell application id \"com.apple.Safari\" to return URL of current tab of front window")
        #expect(BrowserScripts.script(kind: .arc, bundleId: "a").contains("incognito of front window as text"))
    }

    @Test func parseReplies() {
        #expect(BrowserScripts.parse(kind: .chromium, stdout: "https://x.com/a\nincognito\n") == BrowserInfo(url: "https://x.com/a", isPrivate: true))
        #expect(BrowserScripts.parse(kind: .chromium, stdout: "missing value\nnormal") == BrowserInfo(url: nil, isPrivate: false))
        #expect(BrowserScripts.parse(kind: .chromium, stdout: "u\nweird") == BrowserInfo(url: "u", isPrivate: nil))
        #expect(BrowserScripts.parse(kind: .arc, stdout: "u\ntrue") == BrowserInfo(url: "u", isPrivate: true))
        #expect(BrowserScripts.parse(kind: .safari, stdout: "u") == BrowserInfo(url: "u", isPrivate: nil))
        #expect(BrowserScripts.parse(kind: .safari, stdout: "") == BrowserInfo(url: nil, isPrivate: nil))
    }
}
