import Foundation
import Testing
@testable import MetalFocusCore

final class MemoryKeyStore: KeyStore, @unchecked Sendable {
    var key: String?
    private(set) var setCount = 0
    init(_ key: String? = nil) { self.key = key }
    func get() throws -> String? { key }
    func set(_ key: String) throws { setCount += 1; self.key = key }
}

@Suite struct DatabaseTests {
    let keyA = String(repeating: "ab", count: 32)
    let keyB = String(repeating: "cd", count: 32)

    func dbURL() -> URL { TestRepo.tempDir().appendingPathComponent("activity.db") }

    @Test func firstRunCreatesKey() throws {
        let store = MemoryKeyStore()
        let k = try KeyRules.getOrCreateKey(dbURL: dbURL(), store: store)
        #expect(k.count == 64 && k.allSatisfy { "0123456789abcdef".contains($0) })
        #expect(store.setCount == 1 && store.key == k)
    }

    @Test func reusesKey() throws {
        let store = MemoryKeyStore(), url = dbURL()
        let a = try KeyRules.getOrCreateKey(dbURL: url, store: store)
        let b = try KeyRules.getOrCreateKey(dbURL: url, store: store)
        #expect(a == b && store.setCount == 1)
    }

    @Test func dbExistsKeyMissingThrows() throws {
        let url = dbURL()
        FileManager.default.createFile(atPath: url.path, contents: Data())
        let store = MemoryKeyStore()
        #expect(throws: DBError.keyMissing) { try KeyRules.getOrCreateKey(dbURL: url, store: store) }
        #expect(store.setCount == 0 && store.key == nil)
    }

    @Test func rejectsMalformedKey() {
        #expect(throws: DBError.wrongKey) { try Database(url: dbURL(), key: "nothex") }
    }

    @Test func wrongKeyThrows() throws {
        let url = dbURL()
        let db = try Database(url: url, key: keyA)
        try db.applySchema(repo: TestRepo.root)
        db.close()
        #expect(throws: DBError.wrongKey) { try Database(url: url, key: keyB) }
    }

    func insertSample(_ db: Database, title: String) throws {
        let s = Sample(ts: 1, tzOffsetMin: 0, dt: 4, bundleId: "b", appName: "A", windowTitle: title, domain: nil,
                       isPrivate: nil, excluded: 0, fullscreen: nil, locked: 0, idleS: 0, appSwitches: 0,
                       keyActive: nil, clickActive: nil, scrollActive: nil)
        let cols = s.columns
        try db.run("INSERT INTO samples (\(cols.map(\.0).joined(separator: ","))) VALUES (\(cols.map { _ in "?" }.joined(separator: ",")))",
                   cols.map(\.1))
    }

    @Test func fileIsEncrypted() throws {
        let url = dbURL()
        let db = try Database(url: url, key: keyA)
        try db.applySchema(repo: TestRepo.root)
        try insertSample(db, title: "SECRET-TITLE-123")
        db.close()
        let bytes = try Data(contentsOf: url)
        #expect(!bytes.prefix(16).elementsEqual(Array("SQLite format 3\0".utf8)))
        #expect(bytes.range(of: Data("SECRET-TITLE-123".utf8)) == nil)
    }

    @Test func fileModes() throws {
        let dir = TestRepo.tempDir().appendingPathComponent("home")
        let defaults = UserDefaults(suiteName: "mf-test-\(UUID())")!
        defaults.set(dir.path, forKey: "MFHome")
        let url = DataHome.dbURL(defaults: defaults)
        #expect(url == dir.appendingPathComponent("activity.db"))
        let db = try Database(url: url, key: keyA)
        try db.applySchema(repo: TestRepo.root)
        db.close()
        func mode(_ p: String) -> Int { (try! FileManager.default.attributesOfItem(atPath: p)[.posixPermissions] as! NSNumber).intValue }
        #expect(mode(dir.path) == 0o700)
        #expect(mode(url.path) == 0o600)
    }

    @Test func schemaApplied() throws {
        let db = try Database(url: dbURL(), key: keyA)
        try db.applySchema(repo: TestRepo.root)
        try db.applySchema(repo: TestRepo.root)
        let names = Set(try db.query("SELECT name FROM sqlite_master WHERE type='table'", []).compactMap { $0["name"]?.string })
        #expect(names.isSuperset(of: ["samples", "events", "meta", "checkins", "nudge_responses", "notify_outbox"]))
        #expect(throws: DBError.self) { try db.applySchema(repo: TestRepo.tempDir()) }
    }

    @Test func queryAndTransaction() throws {
        let db = try Database(url: dbURL(), key: keyA)
        try db.applySchema(repo: TestRepo.root)
        try db.transaction { try insertSample(db, title: "t1") }
        #expect(db.lastInsertRowID() == 1)
        let rows = try db.query("SELECT window_title, dt, private FROM samples WHERE id = ?", [.int(1)])
        #expect(rows.first?["window_title"] == .text("t1") && rows.first?["dt"] == .double(4) && rows.first?["private"] == .null)
        struct Boom: Error {}
        #expect(throws: Boom.self) { try db.transaction { try insertSample(db, title: "t2"); throw Boom() } }
        #expect(try db.query("SELECT count(*) AS n FROM samples", []).first?["n"] == .int(1))
    }

    @Test func pythonReadsSwiftDB() throws {
        let uv = ["\(NSHomeDirectory())/.local/bin/uv", "/opt/homebrew/bin/uv", "/usr/local/bin/uv"]
            .first { FileManager.default.isExecutableFile(atPath: $0) }
        guard let uv else { Issue.record("uv not found; interop not verified"); return }
        let url = dbURL()
        let db = try Database(url: url, key: keyA)
        try db.applySchema(repo: TestRepo.root)
        try insertSample(db, title: "interop")
        db.close()
        let p = Process()
        p.executableURL = URL(fileURLWithPath: uv)
        p.arguments = ["run", "--project", TestRepo.root.path, "python", "tests/test_swift_db_interop.py", url.path, keyA]
        p.currentDirectoryURL = TestRepo.root
        var env = ProcessInfo.processInfo.environment
        env.removeValue(forKey: "VIRTUAL_ENV")
        p.environment = env
        let out = Pipe(), err = Pipe()
        p.standardOutput = out; p.standardError = err
        try p.run(); p.waitUntilExit()
        let text = String(decoding: out.fileHandleForReading.readDataToEndOfFile(), as: UTF8.self)
        let errText = String(decoding: err.fileHandleForReading.readDataToEndOfFile(), as: UTF8.self)
        #expect(p.terminationStatus == 0, "python failed: \(errText)")
        #expect(text.trimmingCharacters(in: .whitespacesAndNewlines) == "samples=1 title=interop")
    }
}
