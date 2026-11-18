import Darwin
import Foundation
import MetalFocusCore

/// Runs the Python background jobs listed in `runtime/jobs.json` as children of the app, so the app
/// is the only login item (LaunchAgents each showed up as a separate unsigned "python" item).
///
/// Permissions stay with the app: a plain child would be "responsible" to the app and use its
/// Accessibility/Automation grants. Every job is spawned with responsibility disclaimed (the private
/// `responsibility_spawnattrs_setdisclaim`, as Chromium and Terminal use), so macOS treats it as its
/// own process with no grants. If that call is unavailable, nothing is spawned.
///
/// Each child gets only fds 0-2 (`POSIX_SPAWN_CLOEXEC_DEFAULT`): a stdin pipe the app holds open,
/// and its log files. If the app dies the pipe closes and the job exits (common/supervised.py), so
/// a relaunch never runs a second copy.
@MainActor final class JobSupervisor {
    private struct Running { let pid: pid_t; let stdinWrite: Int32; let exit: DispatchSourceProcess }

    private let dataDir = DataHome.directory()
    private var manifest: JobManifest?
    private var running: [String: Running] = [:]
    private var lastExit: [String: Int32] = [:]
    private var lastStart: [String: Date] = [:]
    /// Why jobs are paused (shown in the popover), or nil.
    private(set) var problem: String?
    /// Called on the main actor when a job exits by itself: (name, exit code). Not for signal deaths.
    var onExit: ((String, Int32) -> Void)?

    /// Called every few seconds from the app's loop.
    func tick(now: Date = Date()) {
        let legacy = JobManifest.legacyAgentsInstalled()
        problem = Self.disclaim == nil ? "macOS refused to start jobs without the app's permissions, so they're off."
            : legacy.isEmpty ? nil
            : "Old LaunchAgents are installed, so jobs are paused to avoid running twice. Run: uv run python -m jobs deploy"
        let latest = problem == nil ? JobManifest.load(dataDir: dataDir) : nil
        if latest != manifest {  // redeploy, uninstall, or a problem appeared: restart from scratch
            stopAll()
            manifest = latest
            lastExit = [:]
            lastStart = lastStart.filter { name, _ in latest?.jobs.contains { $0.name == name && $0.daily != nil } ?? false }
            if let sha = latest?.sha { appLog.info("jobs: running deploy \(sha.prefix(12), privacy: .public)") }
        }
        guard let manifest else { return }
        for job in manifest.jobs where running[job.name] == nil {  // a stopping job blocks its relaunch until reaped
            let due = job.daily.map { JobSchedule.dailyDue($0, now: now, lastStart: lastStart[job.name] ?? Self.persistedStart(job.name)) }
                ?? (job.keepalive && JobSchedule.restartKeepalive(lastExit: lastExit[job.name], lastStart: lastStart[job.name], now: now))
            if due { launch(job, now: now) }
        }
    }

    /// SIGTERM now, SIGKILL after 2 s if still running. Never blocks the main actor. (If the app exits
    /// first, the stdin pipe closes and the jobs exit on their own.)
    func stopAll() {
        for (_, r) in running {
            kill(r.pid, SIGTERM)
            let pid = r.pid
            DispatchQueue.main.asyncAfter(deadline: .now() + 2) { [weak self] in
                MainActor.assumeIsolated {
                    if self?.running.values.contains(where: { $0.pid == pid }) == true { kill(pid, SIGKILL) }
                }
            }
        }
    }

    private func launch(_ job: JobManifest.Job, now: Date) {
        lastStart[job.name] = now
        lastExit[job.name] = nil
        if job.daily != nil { UserDefaults.standard.set(now, forKey: "MFJobLastStart.\(job.name)") }
        let cur = JobManifest.runtime(dataDir: dataDir).appendingPathComponent("current")
        let python = cur.appendingPathComponent(".venv/bin/python").path
        guard FileManager.default.isExecutableFile(atPath: python) else { lastExit[job.name] = -1; return }
        var env = ProcessInfo.processInfo.environment
        env["PYTHONUNBUFFERED"] = "1"
        env["MF_SUPERVISED"] = "1"
        env["METAL_FOCUS_HOME"] = dataDir.path  // Swift reads the MFHome default, Python this env var
        do {
            let (pid, stdinWrite) = try Self.spawn(
                python, args: [python, "-m"] + job.args, env: env.map { "\($0.key)=\($0.value)" }, cwd: cur.path,
                out: dataDir.appendingPathComponent("\(job.name).out.log").path,
                err: dataDir.appendingPathComponent("\(job.name).err.log").path)
            running[job.name] = Running(pid: pid, stdinWrite: stdinWrite, exit: watchExit(job.name, pid))
        } catch {
            appLog.error("jobs: can't start \(job.name, privacy: .public): \(String(describing: error), privacy: .public)")
            lastExit[job.name] = -1
        }
    }

    /// One owner reaps each child: the exit source, or the immediate check if it already exited.
    private func watchExit(_ name: String, _ pid: pid_t) -> DispatchSourceProcess {
        let src = DispatchSource.makeProcessSource(identifier: pid, eventMask: .exit, queue: .main)
        src.setEventHandler { [weak self] in MainActor.assumeIsolated { self?.reap(name, pid) } }
        src.resume()
        DispatchQueue.main.async { [weak self] in MainActor.assumeIsolated { self?.reap(name, pid) } }
        return src
    }

    private func reap(_ name: String, _ pid: pid_t) {
        guard let r = running[name], r.pid == pid else { return }
        var status: Int32 = 0
        guard waitpid(pid, &status, WNOHANG) == pid else { return }  // still running
        r.exit.cancel()
        close(r.stdinWrite)  // the only close of this fd
        running[name] = nil
        let code = status & 0x7f == 0 ? (status >> 8) & 0xff : -(status & 0x7f)  // exit code, or -signal
        lastExit[name] = code
        if code != 0 { appLog.error("jobs: \(name, privacy: .public) exited \(code)") }
        if code >= 0 { onExit?(name, code) }  // a signal means we stopped it (redeploy, quit)
    }

    private static func persistedStart(_ name: String) -> Date? {
        UserDefaults.standard.object(forKey: "MFJobLastStart.\(name)") as? Date
    }

    // MARK: spawn

    private typealias DisclaimFn = @convention(c) (UnsafeMutablePointer<posix_spawnattr_t?>, Int32) -> Int32
    private static let disclaim: DisclaimFn? = {
        // RTLD_DEFAULT is ((void *)-2).
        guard let sym = dlsym(UnsafeMutableRawPointer(bitPattern: -2), "responsibility_spawnattrs_setdisclaim") else { return nil }
        return unsafeBitCast(sym, to: DisclaimFn.self)
    }()

    struct SpawnError: Error, CustomStringConvertible { let description: String }

    /// Returns the pid and the write end of the child's stdin pipe.
    private static func spawn(_ path: String, args: [String], env: [String], cwd: String, out: String, err: String) throws -> (pid_t, Int32) {
        guard let disclaim else { throw SpawnError(description: "responsibility_spawnattrs_setdisclaim unavailable") }
        var attr: posix_spawnattr_t?
        posix_spawnattr_init(&attr)
        defer { posix_spawnattr_destroy(&attr) }
        guard disclaim(&attr, 1) == 0 else { throw SpawnError(description: "disclaim failed") }
        // Only fds 0-2 reach the child; signals the app ignores (SIGTERM) go back to default.
        var none = sigset_t(), all = sigset_t()
        sigemptyset(&none); sigfillset(&all)
        posix_spawnattr_setsigmask(&attr, &none)
        posix_spawnattr_setsigdefault(&attr, &all)
        posix_spawnattr_setflags(&attr, Int16(POSIX_SPAWN_CLOEXEC_DEFAULT | POSIX_SPAWN_SETSIGMASK | POSIX_SPAWN_SETSIGDEF))

        var p: [Int32] = [0, 0]
        guard pipe(&p) == 0 else { throw SpawnError(description: "pipe: \(errno)") }
        _ = fcntl(p[1], F_SETFD, FD_CLOEXEC)
        let outFd = open(out, O_WRONLY | O_APPEND | O_CREAT | O_CLOEXEC, 0o600)
        let errFd = open(err, O_WRONLY | O_APPEND | O_CREAT | O_CLOEXEC, 0o600)
        defer { close(p[0]); if outFd >= 0 { close(outFd) }; if errFd >= 0 { close(errFd) } }
        guard outFd >= 0, errFd >= 0 else { close(p[1]); throw SpawnError(description: "can't open logs: \(errno)") }

        var fa: posix_spawn_file_actions_t?
        posix_spawn_file_actions_init(&fa)
        defer { posix_spawn_file_actions_destroy(&fa) }
        posix_spawn_file_actions_adddup2(&fa, p[0], 0)
        posix_spawn_file_actions_adddup2(&fa, outFd, 1)
        posix_spawn_file_actions_adddup2(&fa, errFd, 2)
        posix_spawn_file_actions_addchdir_np(&fa, cwd)

        var pid: pid_t = 0
        let rc = withCStrings(args) { argv in withCStrings(env) { envp in posix_spawn(&pid, path, &fa, &attr, argv, envp) } }
        guard rc == 0 else { close(p[1]); throw SpawnError(description: "posix_spawn: \(rc)") }
        return (pid, p[1])
    }

    private static func withCStrings<R>(_ strings: [String], _ body: ([UnsafeMutablePointer<CChar>?]) -> R) -> R {
        let ptrs = strings.map { strdup($0) } + [nil]
        defer { ptrs.forEach { free($0) } }
        return body(ptrs)
    }
}
