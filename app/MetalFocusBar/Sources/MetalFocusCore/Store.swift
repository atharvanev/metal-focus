import Foundation

/// The app's only DB client: one SQLCipher connection, serialized by the actor.
public actor Store: SampleSink {
    let db: Database
    let runningThresholdS: Double
    let now: @Sendable () -> Double

    public init(db: Database, runningThresholdS: Double, now: @escaping @Sendable () -> Double = { Date().timeIntervalSince1970 }) {
        self.db = db; self.runningThresholdS = runningThresholdS; self.now = now
    }

    /// Key (create only if no key AND no DB) -> open -> apply data/schema.sql.
    public static func open(repo: URL, defaults: UserDefaults = .standard, keys: KeyStore = KeychainKeyStore(),
                            runningThresholdS: Double) throws -> Store {
        let url = DataHome.dbURL(defaults: defaults)
        let key = try KeyRules.getOrCreateKey(dbURL: url, store: keys)
        let db = try Database(url: url, key: key)
        try db.applySchema(repo: repo)
        return Store(db: db, runningThresholdS: runningThresholdS)
    }

    public func write(sample: Sample) async throws {
        let cols = sample.columns
        try db.run("INSERT INTO samples (\(cols.map(\.0).joined(separator: ", "))) VALUES (\(cols.map { _ in "?" }.joined(separator: ", ")))",
                   cols.map(\.1))
    }

    public func write(event ts: Double, kind: String, detail: String?) async throws {
        try db.run("INSERT INTO events (ts, kind, detail) VALUES (?, ?, ?)", [.double(ts), .text(kind), SQLValue(detail)])
    }
}
