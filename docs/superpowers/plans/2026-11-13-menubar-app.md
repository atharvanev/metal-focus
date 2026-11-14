# Menu Bar App v2 (Swift sampler + UI) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A signed SwiftUI menu bar app that samples activity with the Python logger's exact semantics into the shared SQLCipher DB, shows check-ins and nudges from `notify_outbox`, writes the answers, and shows what it collects.

**Architecture:**
- `MetalFocusCore` is the pure, unit-tested core: config, privacy, sample builder, sampler, DB/key, store, prompt coordinator.
- `MetalFocusBar` holds the macOS adapters (probes, workspace observer, permissions) and the UI.
- No Python is spawned. Python jobs talk to the app only through DB tables.

**Tech Stack:**
- Swift 6.3 / Xcode 26.6, SwiftPM (`--disable-keychain`).
- `SQLCipher.swift` 4.20.0 and `Yams` 5.x.
- SwiftUI `MenuBarExtra`, AppKit `NSPanel`, AX, `UserNotifications`, `ServiceManagement`, Swift Testing.
- Python side: uv + pytest (fixture oracle, interop check).

**Spec:** `docs/superpowers/specs/2026-11-13-menubar-app-design.md` (v2). Read it first; this plan does not repeat its rationale. Reference implementation: `logger/sample.py`, `logger/privacy.py`, `logger/config.py`, `logger/sampler.py`, `logger/macos/*.py`. Parity oracle: `tests/fixtures/sample_cases.json`.

## Global Constraints
- Every SwiftPM command uses `--disable-keychain` (`swift build --disable-keychain`, `swift test --disable-keychain`).
- **No input hooks:** none of `CGEventTap`, `addGlobalMonitorForEvents`, `addLocalMonitorForEvents`, `IOHIDManager`, `CGRequestListenEventAccess` anywhere in `app/`.
- **No network:** no `URLSession`, `URLRequest`, `NWConnection`.
- **Never spawn Python or uv from app sources.** The only `Process` executable allowed is `/usr/bin/osascript`.
- **Key rule:** a key is created only if no key exists AND no DB file exists. If the DB exists without a key, throw `DBError.keyMissing` and never write a key.
- **DB:**
  - path `<home>/activity.db`, where home = `MFHome` default or `~/Library/Application Support/metal-focus`;
  - dir 0700, files 0600;
  - `PRAGMA key = "x'<hex>'"` first, then `SELECT count(*) FROM sqlite_master`, then `PRAGMA journal_mode=WAL`;
  - Keychain service `metal-focus`, account `sqlcipher-key`;
  - schema = repo `data/schema.sql` executed on open.
- **Sampling constants come from config only** (`sample_interval_s` 4, `max_gap_s` 15, `browser_timeout_s` 1.5). AX messaging timeout 0.5 s.
- Response sets:
  - `checkins.response` ∈ {Working, Short break, Drifting, Rabbit hole, Away, skip, dismissed};
  - `nudge_responses.response` ∈ {back_to_work, on_break, snooze, dismissed}.
  - Answers are first-write-wins.
- **Check-in panel:** never key or main, never `NSApp.activate`, no sound, max 60 s on screen.
- Bundle id `com.metalfocus.menubar`, macOS 14+, signing identity `Metal Focus Dev`.

## Review Focus
1. **Python can't read a DB the app created** (cipher settings or key format mismatch): this would silently break windowing and HSMM. Test in Task 3 (`pythonReadsSwiftDB`).
2. **The DB exists but Keychain has no key** (Keychain reset): the app must refuse and never mint a key. Test in Task 3 (`dbExistsKeyMissingThrows`).
3. **A browser hangs** (a modal dialog in Chrome) **or Accessibility is revoked mid-run**: the tick must finish within roughly 2 s with NULL fields, and ticks must not pile up. Test in Task 2 (`slowProbeDoesNotOverlap`) and Task 4 (`osascriptTimeout`).
4. **The Mac sleeps for an hour:** the next tick writes a `gap` event at the previous tick's ts and uses `dt=4`. Test in Task 2 (`gapAfterSleep`).
5. **A prompt's `expires_at` has already passed when the app sees it** (wake from sleep): never show it. Test in Task 6 (`dropsAlreadyExpired`). Also: the app crashes while a prompt is up → the sweep marks it dismissed. Test in Task 5 (`sweepShownUnanswered`).

---

## File Structure
```
app/MetalFocusBar/Package.swift
app/MetalFocusBar/Sources/MetalFocusCore/
  LoggerConfig.swift  Privacy.swift  Observations.swift  SampleBuilder.swift
  Sampler.swift  SwitchCounter.swift  KeyStore.swift  Database.swift  Store.swift
  Models.swift  PromptCoordinator.swift  Paths.swift
app/MetalFocusBar/Sources/MetalFocusBar/
  MetalFocusBarApp.swift  AppModel.swift  MacProbes.swift  WorkspaceObserver.swift
  Permissions.swift  PopoverView.swift  ViewerView.swift  CheckinPanel.swift  NudgeNotifier.swift
app/MetalFocusBar/Tests/MetalFocusCoreTests/  (one file per Core unit + SourceGuardTests.swift)
app/Info.plist.in  app/build-app.sh  app/make-cert.sh
```
Removed: `bridge/`, `tests/bridge/`.

---

### Task 1: Package, config, privacy, sample builder (fixture parity)

**Files:**
- Delete: `bridge/`, `tests/bridge/` (`git rm -r`).
- Create: `Package.swift`, `Paths.swift`, `LoggerConfig.swift`, `Privacy.swift`, `Observations.swift`, `SampleBuilder.swift`, a placeholder `MetalFocusBarApp.swift` (`@main` with an empty `MenuBarExtra`), `Tests/MetalFocusCoreTests/{ConfigTests,PrivacyTests,SampleFixtureTests}.swift`.
- Modify: `.gitignore` (`app/MetalFocusBar/.build/`, `app/build/`).

**Interfaces (Produces):**
- `Package.swift`:
  - tools 6.0, `.macOS(.v14)`;
  - deps `SQLCipher.swift from: "4.20.0"`, `Yams from: "5.1.0"`;
  - `MetalFocusCore` depends on both;
  - the `MetalFocusBar` executable depends on Core;
  - the test target depends on Core.
- `enum Paths { static func repoRoot(from filePath: String = #filePath) -> URL }`: walks up to the dir containing `pyproject.toml`; tests only.
- `struct LoggerConfig: Sendable, Equatable`:
  - fields `sampleIntervalS, maxGapS, browserTimeoutS: Double`, `browsers: [String: BrowserKind]`, `unknownPrivatePolicy: PrivatePolicy`, `excludeBundleIds: Set<String>`, `excludeDomains: [String]`, `excludeDomainKeywords: [String]`, `excludeTitlePatterns: [NSRegularExpression]` (Equatable on the pattern strings);
  - `static func load(repo: URL, includeLocal: Bool = true) throws -> LoggerConfig`, which reads `logger/config.yaml`, `logger/financial.yaml` and (if present and `includeLocal`) `logger/config.local.yaml`;
  - merge exactly as `logger/config.py`;
  - throws `ConfigError.invalid(String)` on the same conditions;
  - financial `title_keywords` are wrapped as `(?<!\w)(?:frag)(?!\w)`;
  - every pattern is `.caseInsensitive`.
- `enum BrowserKind: String, Sendable { case safari, chromium, arc }`, `enum PrivatePolicy: String, Sendable { case drop, keep }`.
- `enum Privacy`:
  - `static func sanitizeURL(_ s: String?) -> String?` (via `URLComponents`; scheme http/https; `host` lowercased; strip one `www.`; nil otherwise);
  - `static func domainMatches(_ d: String, _ suffix: String) -> Bool`;
  - `static func isExcluded(bundleId: String?, domain: String?, title: String?, cfg: LoggerConfig) -> Bool`.
- `Observations.swift`:
  - `struct AppInfo { bundleId: String?; name: String?; pid: Int32 }`;
  - `WindowInfo { title: String?; fullscreen: Bool? }`;
  - `BrowserInfo { url: String?; isPrivate: Bool? }`;
  - `InputRecency { keyS, clickS, scrollS: Double? }`;
  - `struct Sample: Equatable, Sendable` with exactly the `samples` columns minus `id`, in camelCase: `ts, tzOffsetMin, dt, bundleId, appName, windowTitle, domain, isPrivate: Int?, excluded: Int, fullscreen: Int?, locked: Int, idleS: Double?, appSwitches: Int, keyActive, clickActive, scrollActive: Int?`.
- `enum SampleBuilder { static func build(ts:tzOffsetMin:dt:app:window:browser:idleS:recency:locked:appSwitches:cfg:) -> Sample }`: a line-for-line port of `build_sample`.

- [ ] **Step 1: Write failing tests**
  - `ConfigTests.loadsRepoConfig`: `sampleIntervalS == 4`, `maxGapS == 15`, `browsers["com.google.Chrome"] == .chromium`, `excludeDomains.contains("chase.com")` and `"ibm.com"`, `excludeDomainKeywords.contains("bank")`, `excludeBundleIds.contains("com.apple.stocks")`.
  - `ConfigTests.localAppendsAndOverrides`: in a temp repo copy containing the 3 yaml files with local `exclude.domains: [example.org]` and `sample_interval_s: 3` → both domains are present and the interval is 3.
  - `ConfigTests.rejectsBadPolicy`: `unknown_private_policy: maybe` → throws.
  - `PrivacyTests`:
    - `sanitize`: `HTTPS://User:pw@WWW.Example.COM:8443/a?b` → `example.com`; `file:///x`, `chrome://settings`, `""`, nil → nil;
    - `suffix`: `secure.chase.com` matches `chase.com`, `notchase.com` doesn't;
    - `titleWholeWord`: "401(k) summary" is excluded; "bankrupt.py" is not excluded by `bank`.
  - `SampleFixtureTests.allCasesMatch`:
    - load `tests/fixtures/sample_cases.json` via `Paths.repoRoot()`;
    - config = `LoggerConfig.load(repo:, includeLocal: false)`;
    - for each case, build with `ts: 0, tzOffsetMin: 0` and compare every `expected` key to the Sample field (snake→camel; `private` → `isPrivate`);
    - on failure, report the case name and field;
    - assert the case count == fixture count (28).
- [ ] **Step 2:** `cd app/MetalFocusBar && swift test --disable-keychain` → FAIL (compile).
- [ ] **Step 3:** Implement the files above.
- [ ] **Step 4:** `swift test --disable-keychain` → PASS (all 28 fixture cases). `uv run pytest -q` → passes without `tests/bridge`.
- [ ] **Step 5:** Commit `feat(app): Swift config, privacy rules and sample builder with Python parity fixture`.

### Task 2: Sampler tick + switch counter

**Files:** Create `Sampler.swift`, `SwitchCounter.swift`, `Tests/.../SamplerTests.swift`.

**Interfaces:**
- Consumes: `SampleBuilder`, `LoggerConfig`, the observation types.
- Produces:
  - `protocol Probes: Sendable { func screenLocked() throws -> Bool; func frontmost() throws -> AppInfo?; func window(pid: Int32) throws -> WindowInfo?; func browser(bundleId: String) throws -> BrowserInfo?; func idleSeconds() throws -> Double?; func inputRecency() throws -> InputRecency? }`;
  - `protocol SampleSink: Sendable { func write(sample: Sample) async throws; func write(event ts: Double, kind: String, detail: String?) async throws }`;
  - `final class SwitchCounter: @unchecked Sendable { func increment(); func snapshotAndReset() -> Int }` (lock-protected);
  - `actor Sampler { init(probes:, switches:, sink:, cfg:, clock: @Sendable () -> Double = { Date().timeIntervalSince1970 }, tzOffset: @Sendable () -> Int = { TimeZone.current.secondsFromGMT() / 60 }); func tick() async -> Sample?; var isTicking: Bool }`.
    - `tick()` returns nil without doing anything if a tick is already running.
    - A probe that throws gives NULL (or `false` for `screenLocked`) and is logged once per probe name via `os.Logger`.
    - A sink error is logged and the tick continues.
- [ ] **Step 1: Write failing tests** (a `FakeProbes` with settable values and throw flags; a `RecordingSink` actor)
  - `firstTickDt`: `dt == 4`, no gap event.
  - `normalDtIsElapsed`: clock 100 then 104.2 → `dt == 4.2`.
  - `gapAfterSleep` (**Review Focus 4**): 100 then 3700 → `dt == 4`, plus an event `(100, "gap", "3600.0s")`.
  - `clockBackwards`: 100 then 90 → `dt == 4`, gap detail `"-10.0s"`.
  - `lockedSkipsWindowAndBrowser`: locked → the fake records zero `window`/`browser` calls.
  - `browserOnlyForConfiguredBrowsers`: frontmost `com.microsoft.VSCode` → no browser call; Chrome → one call.
  - `probeFailureIsNull`: `window` throws → `windowTitle == nil`, the tick still writes a sample.
  - `switchesSnapshot`: increment ×3, tick → `appSwitches == 3`; the next tick → 0.
  - `slowProbeDoesNotOverlap` (**Review Focus 3**): `frontmost` sleeps 300 ms. Two concurrent `tick()` calls → exactly one sample is written and one call returns nil.
- [ ] **Step 2:** `swift test --disable-keychain` → FAIL. **Step 3:** Implement (port of `logger/sampler.py`). **Step 4:** PASS.
- [ ] **Step 5:** Commit `feat(app): sampler tick with gap handling and probe isolation`.

### Task 3: Key store + encrypted database (+ Python interop)

**Files:** Create `KeyStore.swift`, `Database.swift`, `Tests/.../DatabaseTests.swift`, `tests/test_swift_db_interop.py`.

**Interfaces:**
- Produces:
  - `protocol KeyStore: Sendable { func get() throws -> String?; func set(_ key: String) throws }`;
  - `struct KeychainKeyStore: KeyStore` (`SecItemCopyMatching`/`SecItemAdd`, `kSecClassGenericPassword`, service `metal-focus`, account `sqlcipher-key`, value as UTF-8);
  - `final class MemoryKeyStore: KeyStore` (for tests; counts `set` calls);
  - `enum DBError: Error, Equatable { case keyMissing, wrongKey, schemaMissing(String), sqlite(code: Int32, message: String) }`;
  - `enum DataHome { static func directory(defaults: UserDefaults = .standard) -> URL; static func dbURL(defaults:) -> URL }`; `directory` creates the dir with mode 0700;
  - `static func getOrCreateKey(dbURL: URL, store: KeyStore) throws -> String`:
    - a stored key is returned as-is;
    - if there is none and the file doesn't exist, it generates 32 random bytes via `SecRandomCopyBytes`, encodes them as lowercase hex, stores them, and returns them;
    - if there is none and the file exists, it throws `.keyMissing`;
  - `final class Database: @unchecked Sendable` (used only from inside `Store`):
    - `init(url: URL, key: String) throws` (the pragma order from Global Constraints; failure → `.wrongKey`; chmod 0600 on db/wal/shm);
    - `func exec(_ sql: String) throws`;
    - `func run(_ sql: String, _ args: [SQLValue]) throws -> Int` (changes);
    - `func query(_ sql: String, _ args: [SQLValue]) throws -> [[String: SQLValue]]`;
    - `func lastInsertRowID() -> Int64`;
    - `func transaction<T>(_ body: () throws -> T) throws -> T`;
    - `func applySchema(repo: URL) throws` (executes `data/schema.sql`; missing → `.schemaMissing`).
  - `enum SQLValue: Sendable, Equatable { case null, int(Int64), double(Double), text(String) }` with convenience accessors `.int`, `.double`, `.string`.
- [ ] **Step 1: Write failing tests** (temp dirs)
  - `firstRunCreatesKey`: no file, empty store → a 64-hex key, `set` called once.
  - `reusesKey`: two calls → same key, `set` once.
  - `dbExistsKeyMissingThrows` (**Review Focus 2**): touch the file, empty store → throws `.keyMissing`, `set` count 0.
  - `wrongKeyThrows`: create with key A, open with key B → `.wrongKey`.
  - `fileIsEncrypted`: after inserting a row with title "SECRET-TITLE", the raw file bytes don't contain it, and the first 16 bytes are not `"SQLite format 3\0"`.
  - `fileModes`: db 0600, dir 0700.
  - `schemaApplied`: tables `samples, events, meta, checkins, nudge_responses, notify_outbox` exist; applying twice is fine.
  - `pythonReadsSwiftDB` (**Review Focus 1**): the Swift test writes a DB to a temp path with a known key, then the **test** (not the app) runs `/usr/bin/env uv run --project <repo> python tests/test_swift_db_interop.py <db> <hexkey>`. It expects stdout `samples=1 title=interop`. The test is skipped with a message if `uv` isn't found.
  - `tests/test_swift_db_interop.py`: a script mode (`__main__`) that opens with `data.db.connect(Path(db), key)` and prints `samples=<count> title=<first title>`. Its pytest part only checks that the module imports.
- [ ] **Step 2:** run → FAIL. **Step 3:** Implement. **Step 4:** `swift test --disable-keychain` and `uv run pytest -q` → PASS.
- [ ] **Step 5:** Commit `feat(app): SQLCipher database and keychain key with Python interop test`.

### Task 4: Store sample/event writes, macOS probes, workspace observer, signed app that collects

**Files:**
- Create: `Store.swift` (writes part), `MacProbes.swift`, `WorkspaceObserver.swift`, `Permissions.swift`, `AppModel.swift` (sampling part), `app/make-cert.sh`, `app/build-app.sh`, `app/Info.plist.in`, `Tests/.../StoreWriteTests.swift`, `Tests/.../SourceGuardTests.swift`, `Tests/.../OsascriptTests.swift`.
- Modify: `MetalFocusBarApp.swift`.

**Interfaces:**
- Produces:
  - `actor Store: SampleSink`:
    - `init(db: Database, now: @Sendable () -> Double)`;
    - `static func open(repo: URL, defaults: UserDefaults = .standard, keys: KeyStore = KeychainKeyStore(), now: ...) throws -> Store` (getOrCreateKey → Database → applySchema);
    - `write(sample:)` inserts every column;
    - `write(event:kind:detail:)`.
  - `enum OSAScript { static func run(_ source: String, timeout: Double) -> String? }`: `Process` at `/usr/bin/osascript -e`; it kills the process on timeout and returns nil on timeout or nonzero exit.
  - `enum BrowserScripts { static func script(kind: BrowserKind, bundleId: String) -> String; static func parse(kind:, stdout:) -> BrowserInfo }`: ports of `logger/macos/browser.py`, same strings.
  - `struct MacProbes: Probes`:
    - `NSWorkspace.shared.frontmostApplication`;
    - AX focused window title + `AXFullScreen`, with `AXUIElementSetMessagingTimeout(app, 0.5)`, nil if `!AXIsProcessTrusted()`;
    - the browser via `OSAScript` with `cfg.browserTimeoutS`;
    - `CGEventSource.secondsSinceLastEventType(.hidSystemState, eventType:)` for `kCGAnyInputEventType` (`CGEventType(rawValue: ~0)!`), `.keyDown`, `.leftMouseDown`, `.scrollWheel`;
    - locked via `CGSessionCopyCurrentDictionary()["CGSSessionScreenIsLocked"]`.
  - `@MainActor final class WorkspaceObserver`: `NSWorkspace` notifications `didActivateApplication` → `switches.increment()`; `willSleep`/`didWake`/`screensDidSleep`/`screensDidWake`, plus the distributed `com.apple.screenIsLocked`/`Unlocked` → `store.write(event:)` with kinds `sleep, wake, screens_sleep, screens_wake, lock, unlock`.
  - `enum Permissions { static var accessibility: Bool; static func promptAccessibility() }`.
  - `AppModel.startSampling()`:
    - `Store.open` → `logger_start` event → if `!accessibility`, a `permission_missing` event with detail `accessibility` → a tick loop every `cfg.sampleIntervalS` on a detached task.
    - On `applicationWillTerminate` → a `logger_stop` event.
    - Errors are kept in `AppModel.dbError` (`keyMissing` → no sampling).
- [ ] **Step 1: Write failing tests**
  - `StoreWriteTests.sampleRoundTrip`: write a Sample with every field set; querying `samples` returns equal values. `nil` → SQL NULL.
  - `eventKindChecked`: kind `"bogus"` throws a sqlite error (CHECK constraint).
  - `OsascriptTests.osascriptTimeout` (**Review Focus 3**): `OSAScript.run("delay 5", timeout: 0.5)` returns nil in under 1.5 s. `OSAScript.run("return \"hi\"", timeout: 2) == "hi"`.
  - `OsascriptTests.parseReplies`: chromium `"https://x.com/a\nincognito"` → url + private true; `"missing value\nnormal"` → url nil, private false; arc `"u\ntrue"` → true; safari `"u"` → private nil.
  - `SourceGuardTests.noHooksNoNetworkNoPython`: walk `Sources/` and fail on any Global Constraints forbidden symbol, and on any `executableURL` / `launchPath` string other than `/usr/bin/osascript`, and on the substrings `"uv"`, `"python"` inside `Process`-related lines (the regex `Process\(|executableURL|arguments` lines must not contain `python` or `/uv`).
- [ ] **Step 2:** FAIL. **Step 3:** Implement. **Step 4:** `swift test --disable-keychain` → PASS; `swift build --disable-keychain` produces no warnings.
- [ ] **Step 5: Signing + bundle.**
  - `app/make-cert.sh`:
    - if `security find-identity -v -p codesigning | grep -q "Metal Focus Dev"`, exit 0;
    - else `openssl req -x509 -newkey rsa:2048 -days 3650 -nodes -subj "/CN=Metal Focus Dev" -addext "keyUsage=critical,digitalSignature" -addext "extendedKeyUsage=critical,codeSigning"` into a temp dir;
    - `openssl pkcs12 -export -legacy` (if supported; else without the flag) with a temp password;
    - `security import <p12> -k ~/Library/Keychains/login.keychain-db -P <pw> -T /usr/bin/codesign`;
    - `security add-trusted-cert -r trustRoot -p codeSign -k ~/Library/Keychains/login.keychain-db <cert.pem>` (password prompt).
  - `app/build-app.sh [--install]`:
    - `swift build -c release --disable-keychain --package-path app/MetalFocusBar`;
    - assemble the `.app`, render the plist (`CFBundleIdentifier`, `CFBundleName "Metal Focus"`, `CFBundleExecutable MetalFocusBar`, `CFBundlePackageType APPL`, `LSUIElement`, `LSMinimumSystemVersion 14.0`, `MFRepoPath`, `CFBundleShortVersionString 0.1.0`, `NSAppleEventsUsageDescription "Reads the current tab's domain and private-window state of the frontmost browser."`);
    - `codesign --force --timestamp=none -s "Metal Focus Dev"`;
    - `--install` → `ditto` to `~/Applications/MetalFocusBar.app`.
- [ ] **Step 6: Manual check** (results go in the commit body):
  - `app/make-cert.sh && app/build-app.sh --install && open ~/Applications/MetalFocusBar.app`;
  - grant Accessibility;
  - after 30 s, `uv run python -m logger status` (it reads via `data.db`) shows `samples: N>5` with a recent last sample.
  - Rebuild and relaunch → Accessibility is still granted (`codesign -dr - ~/Applications/MetalFocusBar.app` shows the cert leaf requirement, not a cdhash).
- [ ] **Step 7:** Commit `feat(app): signed menu bar app that samples into the encrypted DB`.

### Task 5: Store — outbox, answers, sweep, reads

**Files:** Modify `Store.swift`; create `Models.swift`, `Tests/.../StoreOutboxTests.swift`, `Tests/.../StoreReadTests.swift`.

**Interfaces:**
- Produces (on `Store`, port of v1 `bridge/store.py` from commit `ef19dd0`; read it with `git show ef19dd0:bridge/store.py`):
  - `pendingOutbox() -> [OutboxItem]`;
  - `ackDelivered(outboxId:) -> Bool`;
  - `checkinResponse(checkinId:response:respondedAt:) -> Bool`;
  - `nudgeResponse(rowId:response:respondedAt:) -> Bool`;
  - `sweepExpired(graceS: Double = 30) -> Int`;
  - `insertTestPrompt(kind: PromptKind, ttlS: Double = 60) -> Int64`;
  - `status() -> Status`;
  - `samples(since:limit:) -> [SampleRow]`;
  - `events(since:limit:) -> [EventRow]`;
  - `checkins(limit:) -> [CheckinRow]`;
  - `timeline(day: Date) -> [Segment]`;
  - `readout(maxAgeS: Double = 300) -> ReadoutInfo?`.
  - All of them throw `DBError`.
- `Models.swift`:
  - `OutboxItem(outboxId, kind: PromptKind, refId: Int64?, expiresAt, question?, body?, test: Bool)`;
  - `enum PromptKind: String { case checkin, nudge }`;
  - `Status(loggerRunning, lastSampleTs?, samplesToday, excludedToday, lastEvent?)`;
  - `SampleRow`, `EventRow`, `CheckinRow`, `Segment(start, end, label, source)`;
  - `ReadoutInfo(risk, pState?, topState?, topProb?, minutesInState?, surprise?, away?, writtenAt)`;
  - `enum CheckinAnswer: String, CaseIterable` (exact set), `enum NudgeAnswer: String` (exact set).
- [ ] **Step 1: Write failing tests:** port every test in `git show ef19dd0:tests/bridge/test_store_reads.py` and `test_store_writes.py` to Swift Testing, with the same names in camelCase and the same values (`NOW = 1_763_000_000`). That includes **`sweepShownUnanswered`** (Review Focus 5), `secondResponseNotApplied`, `testPromptTouchesNoLabelTables`, `samplesRedactExcludedAndPrivate`, `timelineFromSamples`, and the readout fresh/stale/missing cases. `loggerRunning` uses `3 × cfg.sampleIntervalS` (12 s).
- [ ] **Step 2:** FAIL. **Step 3:** Implement. **Step 4:** PASS.
- [ ] **Step 5:** Commit `feat(app): outbox relay, prompt answers, expiry sweep and viewer reads`.

### Task 6: PromptCoordinator

**Files:** Create `PromptCoordinator.swift`, `Tests/.../PromptCoordinatorTests.swift`.

**Interfaces:**
- Consumes: `OutboxItem`, `CheckinAnswer`, `NudgeAnswer`.
- Produces:
  - `@MainActor protocol PromptPresenter: AnyObject { func showCheckin(_ i: OutboxItem, deadline: Date); func closeCheckin(); func showNudge(_ i: OutboxItem, deadline: Date); func closeNudge(outboxId: Int64) }`;
  - `@MainActor protocol PromptSink: AnyObject { func ack(_ i: OutboxItem) async; func answerCheckin(_ i: OutboxItem, _ a: CheckinAnswer, respondedAt: Date) async; func answerNudge(_ i: OutboxItem, _ a: NudgeAnswer, respondedAt: Date) async }`;
  - `@MainActor final class PromptCoordinator`:
    - `init(presenter:sink:now:maxOnScreen: TimeInterval = 60)`;
    - `receive(_ i: OutboxItem) async`;
    - `userAnswered(_ a: CheckinAnswer) async`;
    - `userAnswered(nudge outboxId: Int64, _ a: NudgeAnswer) async`;
    - `tick() async`;
    - `currentCheckin: OutboxItem?`;
    - it ignores an outbox id it has already received.
- [ ] **Step 1: Write failing tests:**
  - `showsAndAcks` (deadline = min(now+60, expiresAt));
  - `dropsAlreadyExpired` (**Review Focus 5**);
  - `answerCloses`;
  - `timeoutDismisses` (respondedAt = deadline);
  - `newerReplacesOlder` (the old one is `.dismissed`);
  - `doubleAnswerIgnored`;
  - `duplicateReceiveIgnored`;
  - `nudgeTimeoutDismisses`;
  - `nudgeAnswer` (several open nudges keyed by outbox id).
- [ ] **Step 2:** FAIL. **Step 3:** Implement. **Step 4:** PASS. **Step 5:** Commit `feat(app): prompt lifecycle coordinator`.

### Task 7: UI — popover, viewer, check-in panel, nudge notifications, wiring

**Files:** Create `PopoverView.swift`, `ViewerView.swift`, `CheckinPanel.swift`, `NudgeNotifier.swift`. Modify `AppModel.swift`, `MetalFocusBarApp.swift`.

- [ ] **Step 1: Build** per spec "Goals" and the v1 plan's Task 7 (`git show a63caf8:docs/superpowers/plans/2026-11-13-menubar-app.md`) for the panel, notifier and viewer details. Differences from v1: data comes from `Store` directly, not the bridge.
  - **`AppModel` timers:**
    - a 2 s loop does `sweepExpired` → `pendingOutbox` → `coordinator.receive` for each item;
    - a 1 s timer drives `coordinator.tick()`;
    - a 5 s loop refreshes `status`/`readout`.
  - **`PromptSink`:** `ack` → `store.ackDelivered`. Answers → `store.checkinResponse`/`nudgeResponse`, or `os_log` only when `refId == nil` (test).
  - **Popover:**
    - Accessibility row, with [Grant] → `Permissions.promptAccessibility()` + opening `x-apple.systempreferences:com.apple.preference.security?Privacy_Accessibility`;
    - the old-LaunchAgent warning (if `~/Library/LaunchAgents/com.metalfocus.logger.plist` exists);
    - [Test check-in] → `store.insertTestPrompt(.checkin)`, and a hidden ⌥-click → nudge;
    - Launch at login (registered on first launch, stored in the `MFLoginRegistered` default).
- [ ] **Step 2:** `swift build --disable-keychain` → no warnings. `swift test --disable-keychain` → PASS.
- [ ] **Step 3: Manual check.**
  - Rebuild and install.
  - [Test check-in] → the panel appears within about 3 s, and the frontmost app stays frontmost (`osascript -e 'tell application "System Events" to name of first process whose frontmost is true'` before and after the click).
  - A second test with no click → it closes at 60 s.
  - ⌥-click → the nudge notification has 3 actions.
  - The viewer shows samples and events.
  - Record the results in the commit body.
- [ ] **Step 4:** Commit `feat(app): popover, viewer, check-in panel and nudge notifications`.

### Task 8: Docs + merge
- [ ] Update `CLAUDE.md` "Environment and commands" with an **App** list:
  - `app/make-cert.sh` (once);
  - `app/build-app.sh --install`;
  - `cd app/MetalFocusBar && swift test --disable-keychain`;
  - the app is the logger (Accessibility and Automation belong to `com.metalfocus.menubar`);
  - do not run `python -m logger install` alongside it;
  - Python jobs reach the app only via `notify_outbox`.
- [ ] `git merge main`. Resolve conflicts; if any touches `data/schema.sql` or the outbox contract, message `metal-focus-d1`/`metal-focus-fd` first.
- [ ] `uv run pytest -q` and `swift test --disable-keychain` → all pass.
- [ ] In the main checkout: `git merge --no-ff menubar-app`, then `uv run pytest -q`.
- [ ] Message `metal-focus-d1`, `metal-focus-fd`, `metal-focus-ec`: merged, and the app is the logger.
