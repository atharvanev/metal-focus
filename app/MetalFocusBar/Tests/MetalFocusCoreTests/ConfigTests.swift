import Foundation
import Testing
@testable import MetalFocusCore

@Suite struct ConfigTests {
    @Test func loadsRepoConfig() throws {
        let c = try LoggerConfig.load(repo: TestRepo.root, includeLocal: false)
        #expect(c.sampleIntervalS == 4)
        #expect(c.maxGapS == 15)
        #expect(c.browserTimeoutS == 1.5)
        #expect(c.browsers["com.google.Chrome"] == .chromium)
        #expect(c.browsers["com.apple.Safari"] == .safari)
        #expect(c.unknownPrivatePolicy == .drop)
        #expect(c.excludeDomains.contains("chase.com") && c.excludeDomains.contains("ibm.com"))
        #expect(c.excludeDomainKeywords.contains("bank"))
        #expect(c.excludeBundleIds.contains("com.apple.stocks"))
        #expect(c.excludeBundleIds.contains("com.1password.1password"))
    }

    @Test func localAppendsAndOverrides() throws {
        let repo = TestRepo.repoCopy(local: "sample_interval_s: 3\nexclude:\n  domains: [example.org]\n")
        let c = try LoggerConfig.load(repo: repo)
        #expect(c.sampleIntervalS == 3)
        #expect(c.excludeDomains.contains("example.org") && c.excludeDomains.contains("ibm.com"))
    }

    @Test func financialAlwaysLoaded() throws {
        let repo = TestRepo.repoCopy(local: "exclude:\n  domains: []\n  bundle_ids: []\n")
        let c = try LoggerConfig.load(repo: repo)
        #expect(c.excludeDomains.contains("chase.com"))
        #expect(c.excludeDomainKeywords.contains("bank"))
    }

    @Test func rejectsBadPolicy() {
        let repo = TestRepo.repoCopy(local: "unknown_private_policy: maybe\n")
        #expect(throws: ConfigError.self) { try LoggerConfig.load(repo: repo) }
    }

    @Test func rejectsGapNotAboveInterval() {
        let repo = TestRepo.repoCopy(local: "max_gap_s: 4\n")
        #expect(throws: ConfigError.self) { try LoggerConfig.load(repo: repo) }
    }

    @Test func rejectsUnknownBrowserKind() {
        let repo = TestRepo.repoCopy(local: "browsers:\n  org.mozilla.firefox: gecko\n")
        #expect(throws: ConfigError.self) { try LoggerConfig.load(repo: repo) }
    }
}

@Suite struct NudgeSettingsTests {
    @Test func readsRepoThreshold() {
        #expect(NudgeSettings.load(repo: TestRepo.root).riskThreshold == 0.5)
    }

    @Test func missingFileFallsBack() {
        let empty = FileManager.default.temporaryDirectory.appendingPathComponent(UUID().uuidString)
        #expect(NudgeSettings.load(repo: empty) == .fallback)
    }
}
