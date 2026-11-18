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

    /// When the open "Not working" stretch started, or nil when the switch is off.
    public func notWorkingSince() throws -> Double? {
        try db.query("SELECT max(started_at) AS t FROM not_working WHERE ended_at IS NULL").first?["t"]?.double
    }

    /// Turn the "Not working" switch on (opens a stretch) or off (closes every open one).
    public func setNotWorking(_ on: Bool) throws {
        let t = now()
        try db.run("UPDATE not_working SET ended_at = ? WHERE ended_at IS NULL", [.double(t)])
        if on { try db.run("INSERT INTO not_working (started_at) VALUES (?)", [.double(t)]) }
    }
}

// MARK: - Outbox relay, answers, sweep (port of the v1 bridge/store.py, commit ef19dd0)

extension Store {
    public static let defaultQuestion = "What were you doing the last 10 min?"
    static let awayIdleS = 90.0
    static let readoutModel = "hsmm"

    /// Undelivered, unexpired prompts, oldest first.
    public func pendingOutbox() throws -> [OutboxItem] {
        try db.query("""
            SELECT id, kind, ref_id, expires_at, payload_json FROM notify_outbox
            WHERE delivered_at IS NULL AND expires_at > ? ORDER BY created_at, id
            """, [.double(now())]).compactMap { r in
            guard let id = r["id"]?.int, let kind = r["kind"]?.string.flatMap(PromptKind.init(rawValue:)),
                  let exp = r["expires_at"]?.double else { return nil }
            let payload = (r["payload_json"]?.string?.data(using: .utf8))
                .flatMap { try? JSONSerialization.jsonObject(with: $0) as? [String: Any] } ?? [:]
            return OutboxItem(outboxId: Int64(id), kind: kind, refId: r["ref_id"]?.int.map(Int64.init), expiresAt: exp,
                              question: payload["question"] as? String, body: payload["body"] as? String,
                              test: payload["test"] as? Bool ?? false)
        }
    }

    /// Mark shown; the referenced label row's asked_at becomes the shown time.
    @discardableResult
    public func ackDelivered(outboxId: Int64) throws -> Bool {
        let t = now()
        return try db.transaction {
            guard let row = try db.query("SELECT kind, ref_id FROM notify_outbox WHERE id = ?", [.int(outboxId)]).first,
                  let kind = row["kind"]?.string.flatMap(PromptKind.init(rawValue:)) else { return false }
            try db.run("UPDATE notify_outbox SET delivered_at = ? WHERE id = ?", [.double(t), .int(outboxId)])
            if let ref = row["ref_id"]?.int {
                try db.run("UPDATE \(kind.table) SET asked_at = ? WHERE id = ?", [.double(t), .int(Int64(ref))])
            }
            return true
        }
    }

    /// First answer wins: returns false if the check-in already has a response.
    @discardableResult
    public func checkinResponse(checkinId: Int64, response: CheckinAnswer, respondedAt: Double) throws -> Bool {
        try db.run("UPDATE checkins SET response = ?, responded_at = ? WHERE id = ? AND response IS NULL",
                   [.text(response.rawValue), .double(respondedAt), .int(checkinId)]) == 1
    }

    @discardableResult
    public func nudgeResponse(rowId: Int64, response: NudgeAnswer, respondedAt: Double) throws -> Bool {
        try db.run("""
            UPDATE nudge_responses SET response = ?, responded_at = ?, response_latency_s = ? - asked_at
            WHERE id = ? AND response IS NULL
            """, [.text(response.rawValue), .double(respondedAt), .double(respondedAt), .int(rowId)]) == 1
    }

    /// Shown-but-unanswered prompts past expires_at + grace become 'dismissed' (responded_at = expires_at).
    /// Covers a crash or quit while a prompt was on screen. Never-shown prompts are left alone.
    @discardableResult
    public func sweepExpired(graceS: Double = 30) throws -> Int {
        let cutoff = now() - graceS
        return try db.transaction {
            var n = 0
            for kind in [PromptKind.checkin] {  // nudges stay open until answered, so never swept
                let t = kind.table
                n += try db.run("""
                    UPDATE \(t) SET response = 'dismissed',
                      responded_at = (SELECT o.expires_at FROM notify_outbox o
                        WHERE o.kind = ? AND o.ref_id = \(t).id AND o.delivered_at IS NOT NULL
                        AND o.expires_at < ? ORDER BY o.expires_at DESC LIMIT 1)
                    WHERE response IS NULL AND id IN (SELECT ref_id FROM notify_outbox
                      WHERE kind = ? AND ref_id IS NOT NULL AND delivered_at IS NOT NULL AND expires_at < ?)
                    """, [.text(kind.rawValue), .double(cutoff), .text(kind.rawValue), .double(cutoff)])
            }
            return n
        }
    }

    /// A prompt for trying the UI: outbox row only (ref_id NULL), so no label table is touched.
    @discardableResult
    public func insertTestPrompt(kind: PromptKind, ttlS: Double = 60) throws -> Int64 {
        let payload: [String: Any] = kind == .checkin
            ? ["test": true, "question": Self.defaultQuestion]
            : ["test": true, "body": "Test nudge: this looks like a rabbit hole. Head back?"]
        let json = String(decoding: try JSONSerialization.data(withJSONObject: payload, options: [.sortedKeys]), as: UTF8.self)
        let t = now()
        try db.run("INSERT INTO notify_outbox (created_at, kind, ref_id, payload_json, expires_at) VALUES (?, ?, NULL, ?, ?)",
                   [.double(t), .text(kind.rawValue), .text(json), .double(t + ttlS)])
        return db.lastInsertRowID()
    }
}

// MARK: - Viewer reads

extension Store {
    func todayStart() -> Double {
        Calendar.current.startOfDay(for: Date(timeIntervalSince1970: now())).timeIntervalSince1970
    }

    static func event(_ r: [String: SQLValue]) -> EventRow? {
        guard let ts = r["ts"]?.double, let kind = r["kind"]?.string else { return nil }
        return EventRow(id: r["id"]?.int.map(Int64.init), ts: ts, kind: kind, detail: r["detail"]?.string)
    }

    public func status() throws -> Status {
        let last = try db.query("SELECT max(ts) AS t FROM samples").first?["t"]?.double
        let counts = try db.query("SELECT count(*) AS n, coalesce(sum(excluded), 0) AS x FROM samples WHERE ts >= ?",
                                  [.double(todayStart())]).first
        let ev = try db.query("SELECT ts, kind, detail FROM events ORDER BY ts DESC LIMIT 1").first.flatMap(Self.event)
        return Status(loggerRunning: last.map { now() - $0 < runningThresholdS } ?? false, lastSampleTs: last,
                      samplesToday: counts?["n"]?.int ?? 0, excludedToday: counts?["x"]?.int ?? 0,
                      lastEvent: ev.map { EventRow(id: nil, ts: $0.ts, kind: $0.kind, detail: $0.detail) })
    }

    /// Newest first. Excluded and private rows have app, bundle, title and domain removed.
    public func samples(since: Double? = nil, limit: Int = 200) throws -> [SampleRow] {
        try db.query("SELECT * FROM samples WHERE ts > ? ORDER BY ts DESC LIMIT ?",
                     [.double(since ?? -Double.infinity), .int(Int64(limit))]).compactMap(Self.sampleRow)
    }

    static func sampleRow(_ r: [String: SQLValue]) -> SampleRow? {
        guard let ts = r["ts"]?.double, let dt = r["dt"]?.double else { return nil }
        var s = SampleRow(ts: ts, dt: dt, bundleId: r["bundle_id"]?.string, appName: r["app_name"]?.string,
                          windowTitle: r["window_title"]?.string, domain: r["domain"]?.string,
                          isPrivate: r["private"]?.int, excluded: r["excluded"]?.int ?? 0,
                          fullscreen: r["fullscreen"]?.int, locked: r["locked"]?.int ?? 0, idleS: r["idle_s"]?.double,
                          appSwitches: r["app_switches"]?.int ?? 0, keyActive: r["key_active"]?.int,
                          clickActive: r["click_active"]?.int, scrollActive: r["scroll_active"]?.int,
                          mediaPlaying: r["media_playing"]?.int)
        if s.excluded == 1 || s.isPrivate == 1 { s.appName = nil; s.bundleId = nil; s.windowTitle = nil; s.domain = nil }
        return s
    }

    static func checkinRow(_ r: [String: SQLValue]) -> CheckinRow? {
        guard let id = r["id"]?.int, let asked = r["asked_at"]?.double, let trig = r["trigger"]?.string,
              let split = r["split"]?.string else { return nil }
        return CheckinRow(id: Int64(id), askedAt: asked, trigger: trig, windowStart: r["window_start"]?.double,
                          windowEnd: r["window_end"]?.double, modelTopState: r["model_top_state"]?.string,
                          modelTopProb: r["model_top_prob"]?.double, response: r["response"]?.string,
                          respondedAt: r["responded_at"]?.double, split: split)
    }

    public func events(since: Double? = nil, limit: Int = 100) throws -> [EventRow] {
        try db.query("SELECT id, ts, kind, detail FROM events WHERE ts > ? ORDER BY ts DESC LIMIT ?",
                     [.double(since ?? -Double.infinity), .int(Int64(limit))]).compactMap(Self.event)
    }

    public func checkins(limit: Int = 50) throws -> [CheckinRow] {
        try db.query("SELECT * FROM checkins ORDER BY asked_at DESC LIMIT ?", [.int(Int64(limit))]).compactMap(Self.checkinRow)
    }

    /// Totals and ranked lists for the local day containing `day`. Reads only. Missing `nudge_log` yields no nudges.
    public func dayActivity(day: Date) throws -> DayActivity {
        let start = Calendar.current.startOfDay(for: day)
        let lo = start.timeIntervalSince1970
        let hi = Calendar.current.date(byAdding: .day, value: 1, to: start)!.timeIntervalSince1970  // 23/25 h on DST days
        let bounds: [SQLValue] = [.double(lo), .double(hi)]
        let counts = try db.query(
            "SELECT count(*) AS n, coalesce(sum(dt), 0) AS s FROM samples WHERE ts >= ? AND ts < ?", bounds).first
        let apps = try ranked("""
            SELECT CASE WHEN excluded = 1 THEN 'Excluded'
                        WHEN locked = 1 OR COALESCE(idle_s, 0) >= \(Self.awayIdleS) THEN 'Away'
                        ELSE COALESCE(app_name, bundle_id, 'Unknown') END AS name,
                   SUM(dt) AS seconds
            FROM samples WHERE ts >= ? AND ts < ?
            GROUP BY name ORDER BY seconds DESC, name LIMIT 8
            """, bounds)
        let sites = try ranked("""
            SELECT domain AS name, SUM(dt) AS seconds FROM samples
            WHERE ts >= ? AND ts < ? AND domain IS NOT NULL AND excluded = 0 AND COALESCE(private, 0) = 0 AND locked = 0
            GROUP BY domain ORDER BY seconds DESC, name LIMIT 8
            """, bounds)
        let titles = try ranked("""
            SELECT window_title AS name, COALESCE(app_name, bundle_id, 'Unknown') AS detail, SUM(dt) AS seconds
            FROM samples
            WHERE ts >= ? AND ts < ? AND window_title IS NOT NULL AND excluded = 0 AND COALESCE(private, 0) = 0 AND locked = 0
            GROUP BY detail, window_title ORDER BY seconds DESC, name LIMIT 12
            """, bounds)
        let checkins = try db.query(
            "SELECT * FROM checkins WHERE asked_at >= ? AND asked_at < ? ORDER BY asked_at", bounds).compactMap(Self.checkinRow)
        let samples = try db.query(
            "SELECT * FROM samples WHERE ts >= ? AND ts < ? ORDER BY ts DESC LIMIT 20", bounds).compactMap(Self.sampleRow)
        let events = try db.query(
            "SELECT id, ts, kind, detail FROM events WHERE ts >= ? AND ts < ? ORDER BY ts DESC LIMIT 20", bounds).compactMap(Self.event)
        return DayActivity(loggedSeconds: counts?["s"]?.double ?? 0, sampleCount: counts?["n"]?.int ?? 0,
                           apps: apps, sites: sites, titles: titles, checkins: checkins, nudges: try nudges(bounds),
                           samples: samples, events: events)
    }

    private func ranked(_ sql: String, _ params: [SQLValue]) throws -> [RankedTime] {
        try db.query(sql, params).compactMap { r in
            guard let name = r["name"]?.string, let seconds = r["seconds"]?.double else { return nil }
            return RankedTime(name: name, seconds: seconds, detail: r["detail"]?.string)
        }
    }

    private func nudges(_ bounds: [SQLValue]) throws -> [NudgeRow] {
        let sql = """
            SELECT l.window_start, l.risk, l.holdout, l.body, r.response
            FROM nudge_log l LEFT JOIN nudge_responses r ON r.id = l.nudge_response_id
            WHERE l.window_start >= ? AND l.window_start < ? ORDER BY l.window_start
            """
        do {
            return try db.query(sql, bounds).compactMap { r in
                guard let ws = r["window_start"]?.double, let risk = r["risk"]?.double else { return nil }
                return NudgeRow(windowStart: ws, risk: risk, holdout: r["holdout"]?.int == 1,
                                body: r["body"]?.string, response: r["response"]?.string)
            }
        } catch DBError.sqlite(_, let msg) where msg.contains("no such table") {
            return []
        }
    }

    /// Segments of consecutive same-label samples for the local day containing `day`.
    public func timeline(day: Date) throws -> [Segment] {
        let lo = Calendar.current.startOfDay(for: day)
        let hi = Calendar.current.date(byAdding: .day, value: 1, to: lo)!
        var segs: [Segment] = []
        for r in try db.query("SELECT ts, dt, app_name, excluded, locked, idle_s FROM samples WHERE ts >= ? AND ts < ? ORDER BY ts",
                              [.double(lo.timeIntervalSince1970), .double(hi.timeIntervalSince1970)]) {
            guard let ts = r["ts"]?.double, let dt = r["dt"]?.double else { continue }
            let label: String
            if r["excluded"]?.int == 1 { label = "Excluded" }
            else if r["locked"]?.int == 1 || (r["idle_s"]?.double ?? 0) >= Self.awayIdleS { label = "Away" }
            else { label = r["app_name"]?.string ?? "Unknown" }
            if let last = segs.last, last.label == label { segs[segs.count - 1].end = ts + dt }
            else { segs.append(Segment(start: ts, end: ts + dt, label: label, source: "samples")) }
        }
        return segs
    }

    /// The newest HSMM readout if written within `maxAgeS`; nil if stale, missing, or the runner never ran.
    public func readout(maxAgeS: Double = 300) throws -> ReadoutInfo? {
        let rows: [[String: SQLValue]]
        do {
            rows = try db.query("SELECT window_start, written_at, readout FROM readouts WHERE model = ? ORDER BY window_start DESC LIMIT 1",
                                [.text(Self.readoutModel)])
        } catch DBError.sqlite(_, let msg) where msg.contains("no such table") { return nil }
        guard let r = rows.first, let ws = r["window_start"]?.double, let written = r["written_at"]?.double,
              now() - written <= maxAgeS,
              let data = r["readout"]?.string?.data(using: .utf8),
              let j = try JSONSerialization.jsonObject(with: data) as? [String: Any],
              let risk = (j["risk"] as? NSNumber)?.doubleValue else { return nil }
        return ReadoutInfo(windowStart: ws, writtenAt: written, risk: risk,
                           pState: (j["p_state"] as? [NSNumber])?.map(\.doubleValue), topState: (j["top_state"] as? NSNumber)?.intValue,
                           topProb: (j["top_prob"] as? NSNumber)?.doubleValue,
                           minutesInState: (j["minutes_in_state"] as? NSNumber)?.doubleValue,
                           surprise: (j["surprise"] as? NSNumber)?.doubleValue, away: j["away"] as? Bool)
    }

    /// Local `YYYY-MM-DD`, the same key `readouts.day` stores.
    static func localDay(_ day: Date, calendar: Calendar = .current) -> String {
        let c = calendar.dateComponents([.year, .month, .day], from: day)
        return String(format: "%04d-%02d-%02d", c.year ?? 0, c.month ?? 0, c.day ?? 0)
    }

    /// HSMM risk for one local day, oldest first. Away (and a readout with no risk) is nil so the
    /// line breaks, the same rule as the dashboard. Empty when `readouts` does not exist yet.
    public func riskSeries(day: Date) throws -> [RiskPoint] {
        let rows: [[String: SQLValue]]
        do {
            rows = try db.query(
                "SELECT window_start, readout FROM readouts WHERE model = ? AND day = ? ORDER BY window_start",
                [.text(Self.readoutModel), .text(Self.localDay(day))])
        } catch DBError.sqlite(_, let msg) where msg.contains("no such table") { return [] }
        return rows.compactMap { r in
            guard let ws = r["window_start"]?.double,
                  let data = r["readout"]?.string?.data(using: .utf8),
                  let j = try? JSONSerialization.jsonObject(with: data) as? [String: Any] else { return nil }
            let away = (j["away"] as? Bool) ?? false
            return RiskPoint(windowStart: ws, risk: away ? nil : (j["risk"] as? NSNumber)?.doubleValue)
        }
    }
}
