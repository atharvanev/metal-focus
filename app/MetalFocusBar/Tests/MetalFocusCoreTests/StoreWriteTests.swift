import Foundation
import Testing
@testable import MetalFocusCore

enum TestDB {
    static let key = String(repeating: "ab", count: 32)
    static func open(now: @escaping @Sendable () -> Double = { 1_763_000_000 }) throws -> (Store, Database) {
        let db = try Database(url: TestRepo.tempDir().appendingPathComponent("activity.db"), key: key)
        try db.applySchema(repo: TestRepo.root)
        return (Store(db: db, runningThresholdS: 12, now: now), db)
    }
}

@Suite struct StoreWriteTests {
    @Test func sampleRoundTrip() async throws {
        let (store, db) = try TestDB.open()
        let full = Sample(ts: 10.5, tzOffsetMin: -300, dt: 4.2, bundleId: "com.google.Chrome", appName: "Google Chrome",
                          windowTitle: "t", domain: "example.com", isPrivate: 0, excluded: 0, fullscreen: 1, locked: 0,
                          idleS: 1.5, appSwitches: 2, keyActive: 1, clickActive: 0, scrollActive: 1)
        try await store.write(sample: full)
        var empty = full
        empty.bundleId = nil; empty.appName = nil; empty.windowTitle = nil; empty.domain = nil; empty.isPrivate = nil
        empty.fullscreen = nil; empty.idleS = nil; empty.keyActive = nil; empty.clickActive = nil; empty.scrollActive = nil
        empty.ts = 11
        try await store.write(sample: empty)
        let rows = try db.query("SELECT * FROM samples ORDER BY id", [])
        #expect(rows.count == 2)
        for (row, s) in zip(rows, [full, empty]) {
            for (k, v) in s.columns { #expect(row[k] == v, "column \(k)") }
        }
    }

    @Test func eventWrite() async throws {
        let (store, db) = try TestDB.open()
        try await store.write(event: 5, kind: "wake", detail: nil)
        try await store.write(event: 6, kind: "gap", detail: "20.0s")
        let rows = try db.query("SELECT ts, kind, detail FROM events ORDER BY id", [])
        #expect(rows.map { $0["kind"] } == [.text("wake"), .text("gap")])
        #expect(rows[1]["detail"] == .text("20.0s") && rows[0]["detail"] == .null)
    }

    @Test func eventKindChecked() async throws {
        let (store, _) = try TestDB.open()
        await #expect(throws: DBError.self) { try await store.write(event: 1, kind: "bogus", detail: nil) }
    }

    @Test func openCreatesKeyAndDB() throws {
        let home = TestRepo.tempDir()
        let defaults = UserDefaults(suiteName: "mf-test-\(UUID())")!
        defaults.set(home.path, forKey: "MFHome")
        let keys = MemoryKeyStore()
        _ = try Store.open(repo: TestRepo.root, defaults: defaults, keys: keys, runningThresholdS: 12)
        #expect(keys.setCount == 1)
        #expect(FileManager.default.fileExists(atPath: home.appendingPathComponent("activity.db").path))
        let emptyKeys = MemoryKeyStore()
        #expect(throws: DBError.keyMissing) {
            try Store.open(repo: TestRepo.root, defaults: defaults, keys: emptyKeys, runningThresholdS: 12)
        }
        #expect(emptyKeys.setCount == 0)
    }
}
