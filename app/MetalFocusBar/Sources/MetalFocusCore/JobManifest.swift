import Foundation

/// `<data dir>/runtime/jobs.json`, written by `jobs deploy` (shape: jobs/deploy.py
/// `render_manifest`). The app runs these jobs as child processes; it is the only login item.
public struct JobManifest: Decodable, Equatable, Sendable {
    public struct Daily: Decodable, Equatable, Sendable { public let hour: Int, minute: Int }
    public struct Job: Decodable, Equatable, Sendable {
        public let name: String
        public let args: [String]  // module and args (JobSupervisor runs them with `-m`)
        public let keepalive: Bool
        public let daily: Daily?
    }
    public let sha: String
    public let jobs: [Job]

    public static func runtime(dataDir: URL) -> URL { dataDir.appendingPathComponent("runtime") }

    /// nil when nothing is deployed (or the file is mid-write / unreadable): run no jobs.
    public static func load(dataDir: URL) -> JobManifest? {
        let url = runtime(dataDir: dataDir).appendingPathComponent("jobs.json")
        return (try? Data(contentsOf: url)).flatMap { try? JSONDecoder().decode(JobManifest.self, from: $0) }
    }

    /// LaunchAgents from earlier deploys. While any exists the app runs nothing, or jobs would run twice.
    public static let legacyAgentLabels = ["hsmm-runner", "hsmm-nightly", "checkins"].map { "com.metalfocus.\($0)" }

    public static func legacyAgentsInstalled(home: URL = FileManager.default.homeDirectoryForCurrentUser) -> [String] {
        legacyAgentLabels.filter {
            FileManager.default.fileExists(atPath: home.appendingPathComponent("Library/LaunchAgents/\($0).plist").path)
        }
    }
}

public enum JobSchedule {
    /// Like launchd's StartCalendarInterval (which runs at wake if the Mac slept through it): due once
    /// the day's slot has passed and the job hasn't been started since. `hsmm.nightly` itself refuses
    /// a second run on the same day, so a spurious start is harmless.
    public static func dailyDue(_ d: JobManifest.Daily, now: Date, lastStart: Date?, calendar: Calendar = .current) -> Bool {
        guard let slot = calendar.date(bySettingHour: d.hour, minute: d.minute, second: 0, of: now), now >= slot else { return false }
        return (lastStart ?? .distantPast) < slot
    }

    /// Keepalive restart, like launchd `KeepAlive {SuccessfulExit: false}` with `ThrottleInterval 30`:
    /// restart after a failure (not a clean exit), at most once per `throttle` seconds.
    public static func restartKeepalive(lastExit: Int32?, lastStart: Date?, now: Date, throttle: TimeInterval = 30) -> Bool {
        guard let lastStart else { return true }  // never started
        guard let lastExit else { return false }  // still running
        return lastExit != 0 && now.timeIntervalSince(lastStart) >= throttle
    }
}
