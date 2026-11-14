# Menu Bar App (Swift) - Design, v2

Date: 2026-11-13 (v2 same day). Branch/worktree: `menubar-app` (`.claude/worktrees/menubar-app`).
Spec context: `CLAUDE.md` sections "Key definitions", "Check-ins", "Notification", "Architecture", "Privacy rules".

**v2 change (user decision, 2026-11-13):**
- The signed Swift app now **does the activity sampling itself** and writes the encrypted DB directly. Accessibility and Automation belong only to the app, never to Python.
- The Python LaunchAgent logger is uninstalled. `logger/` stays in the repo as the reference implementation and test oracle.
- The v1 Python stdio bridge is **dropped**, so the app never spawns Python.
- Signing uses a stable self-signed certificate.

## Intent
One native macOS menu bar app that:
1. **Collects the activity data** with exactly the Python logger's semantics, every 4 s, into the shared SQLCipher DB, so `data.windows`, `labels/` and `hsmm/` keep working unchanged.
2. **Delivers check-ins and nudges** that Python components queue in `notify_outbox`, and writes the answers back as labels.
3. **Lets the user see what is collected**: live health, today's timeline, raw samples, events, check-ins.

Python stays the brain for decisions: check-in triggers and budget (`labels/`), the nudge rule (`nudge/` in the hsmm runner), the model, and training. These run as their own launchd jobs and need only DB + Keychain access, never TCC permissions.

## Goals
- **Sampling parity with `logger/`** (contract from the logger session, 2026-11-13):
  - **Probe order per tick:** locked first; when locked, skip the window and browser probes. Then frontmost, then the AX window, then the browser (only if the bundle id is in `browsers`), then idle and input recency.
  - **Failing probes:** a failing probe gives NULL plus one warning per probe; it never stops the tick.
  - **`dt`:**
    - On the first tick, when elapsed > `max_gap_s` (15), or when elapsed ≤ 0: `dt = sample_interval_s` (4).
    - In the gap and clock-backwards cases, also write a `gap` event at the *previous* tick's ts with detail `"<elapsed, 1 decimal>s"`.
    - Otherwise, `dt` = elapsed.
  - **Privacy rule order:** locked → excluded (checked on the raw sanitized domain + raw title, for every app) → browser private handling. Under `unknown_private_policy` `drop` (the default), private=1 or unknown means title and domain become NULL.
  - **Field details:**
    - Non-browser apps: domain and private are NULL.
    - Activity flags: `int(seconds_since < dt)`.
    - `idle_s` = seconds since any input.
    - `app_switches` = NSWorkspace activations since the previous tick.
  - **URL sanitizing:** http/https only, lowercased host, no port or userinfo, one leading `www.` stripped.
  - **Matching:** domain suffix match is exact or on a dot boundary. Domain keywords are plain substrings. Financial title keywords become `(?<!\w)(?:frag)(?!\w)`, case-insensitive. User `title_patterns` are case-insensitive regexes.
  - **Config:** `logger/config.yaml` + `logger/financial.yaml` (always appended; it can't be disabled) + optional `logger/config.local.yaml` (lists append, scalars override). These are parsed **at runtime** from the repo, the single source of truth. Validation matches Python.
  - **Timeouts:** AX messaging 0.5 s, browser query (osascript) 1.5 s.
  - **Parity proof:** the Swift builder reproduces every case in `tests/fixtures/sample_cases.json`, which is generated from the Python `build_sample` and kept in sync by a Python test.
- **Events:** `logger_start`/`logger_stop` (app start/quit), `sleep`, `wake`, `screens_sleep`, `screens_wake`, `lock`, `unlock`, `gap`, and `permission_missing` (once per launch when Accessibility is not granted, detail `accessibility`).
- **Check-ins:** a **floating check-in panel** (user decision) with six one-tap answers: [Working] [Short break] [Drifting] [Rabbit hole] [Away] [Skip].
  - It is non-activating and never takes key or main status. It plays no sound.
  - It shows a 60 s countdown, then closes as `dismissed`.
- **Nudges:** native notifications with [Back to work] [I'm on break] [Snooze 20 min]. If notifications are denied, the floating panel is used instead.
- **Popover:**
  - Sampler health: last sample age, and permissions with "Grant" buttons (Accessibility; Automation per running browser, shown as granted or not).
  - DB state, today's counts, the model line.
  - [Open viewer], [Test check-in], Launch at login, Quit.
  - A warning if `~/Library/LaunchAgents/com.metalfocus.logger.plist` exists, because two writers would duplicate samples.
- **Viewer:** tabs for Today (timeline), Samples, Events, Check-ins.

## Non-goals (v2)
- No model, labeling, check-in trigger or nudge-decision logic in Swift.
- No daily-review editing UI.
- No App Store, notarization or Developer ID; this is for personal use on this Mac.
- No Screen Recording, no Input Monitoring.

## Anti-goals
- **Any data leaving the machine.** The app makes no network requests. (Downloading packages at build time is fine.)
- **Minting a key when the DB exists.** A key is created only if *no key exists AND no DB file exists*. If the DB exists without a key, the app refuses to sample, and the popover says "Database key missing from Keychain — refusing to create a new one."
- **Any keyboard or mouse hook:** no `CGEventTap*`, NSEvent global or local monitors, `IOHIDManager` or `CGRequestListenEventAccess`. Input activity comes only from `CGEventSourceSecondsSinceLastEventType`.
- **Python inheriting the app's TCC grants.** The app never launches Python or `uv`. Its only child process is `/usr/bin/osascript` for browser queries, whose Apple Events are *meant* to count as the app's. A source-guard test enforces this.
- **Check-ins that steal focus,** play sounds, or stay up longer than 60 s.
- **Swift re-encoding windowing or label rules.**

## Approaches (v2)
- **A. The app samples and owns DB I/O directly (chosen by user).** SQLCipher via SPM. The app never spawns Python, so nothing can inherit permissions. Cost: two third-party Swift packages, and Python readers get a one-time Keychain "Always Allow" prompt for the key item the app created.
- **B. A Python bridge launched by launchd, over a Unix socket.** Rejected: more moving parts, and Swift still needs SQLCipher to write samples.
- **C. The stdio bridge spawned with responsibility disclaimed.** Rejected: it relies on a private API.
- v1's "Python bridge over stdio" was right while Python sampled. With sampling in Swift, the same argument (one program owns DB and key access) now favours Swift.

## Architecture
```
MetalFocusBar.app (Swift, LSUIElement, signed "Metal Focus Dev")
  Sampler (4 s, off-main) ── MacProbes (NSWorkspace, AX, osascript, CGEventSource, CGSession)
         └─ SampleBuilder (pure; fixture-tested) ─> Store.writeSample
  WorkspaceObserver ── switch counter + sleep/wake/lock events ─> Store.writeEvent
  Store (actor; one SQLCipher connection, WAL)
     writes samples, events, prompt answers; polls notify_outbox every 2 s; sweeps expired
     reads status / samples / events / timeline / checkins / readouts for the UI
  PromptCoordinator ─> CheckinPanel (NSPanel) / NudgeNotifier (UNUserNotificationCenter)
  MenuBarExtra popover + Viewer window (SwiftUI)

Python (separate launchd jobs, no TCC): labels.checkins, hsmm.runner (+ nudge/) write
notify_outbox + checkins/nudge_responses; read via data.db.open_existing.
```

### DB and key (must match `data/db.py` exactly)
- **Path:** `~/Library/Application Support/metal-focus/activity.db` (dir 0700; file and `-wal`/`-shm` 0600). The `MFHome` user default overrides the dir (the Swift equivalent of `METAL_FOCUS_HOME`).
- **Cipher:** SQLCipher 4 community defaults. The first statement is `PRAGMA key = "x'<64 lowercase hex>'"`, then `SELECT count(*) FROM sqlite_master` (failure → wrong key), then `PRAGMA journal_mode=WAL`.
- **Keychain:** a generic password with service `metal-focus`, account `sqlcipher-key`, and the 64 hex chars as its value. It's created via `SecItemAdd` only under the anti-goal rule. Python's `keyring` reads the same item.
- **Schema:** the app executes the repo's `data/schema.sql` (all `IF NOT EXISTS`) on open, read at runtime from `MFRepoPath`, so the schema has one source. It includes `checkins`, `notify_outbox` and `nudge_responses`.
- **Writes:** short transactions. Python processes share the WAL file concurrently.

### Prompts: the outbox contract (agreed with metal-focus-d1 and metal-focus-fd)
- **How Python queues a prompt:** it inserts the label row (`checkins`, response NULL, trigger + split set; or `nudge_responses`, response NULL, uuid in `nudge_id`). In the same transaction it inserts `notify_outbox` (`kind`, `ref_id` = label row id, `expires_at = created_at + 60`, `payload_json` = `{question}` / `{body, ...}`).
- **Polling:** the app polls undelivered, unexpired rows every 2 s. A row is never shown after `expires_at`.
- **When a prompt is shown:** `delivered_at = now`, and the label row's `asked_at = now`.
- **Answers:**
  - Writes are first-write-wins (`WHERE response IS NULL`).
  - `checkins.response` ∈ {Working, Short break, Drifting, Rabbit hole, Away, skip, dismissed}.
  - `nudge_responses.response` ∈ {back_to_work, on_break, snooze, dismissed}, plus `response_latency_s = responded_at - asked_at`.
- **Sweep:** delivered rows whose `expires_at + 30 s` has passed with a NULL response become `dismissed`, with `responded_at = expires_at` (covers a crash while a prompt was on screen). Rows that were never shown stay NULL.
- **Test prompts** (popover button): an outbox row with `ref_id NULL` and `payload.test = true`. Answers are only logged; they never touch label tables.
- **Readout:** the latest `readouts` row with `model='hsmm'`, decoded from the `readout` JSON. It's null when the table is missing, the table is empty, or `written_at` is more than 5 min old; the UI then shows "model not running". `away=true` shows as "Away / paused". Everything but `risk` may be null. `forecast_risk` keys are strings.

### Timeline (viewer "Today")
Consecutive same-label samples in the local day:
- label = app name, "Excluded" (excluded=1), or "Away" (locked=1 or idle_s ≥ 90);
- `source="samples"`.

Window and label-based segments come later and are not in scope.

## Swift side
- SwiftPM package `app/MetalFocusBar/`, macOS 14+, Swift 6 language mode.
- Dependencies (verified to build 2026-11-13):
  - `https://github.com/sqlcipher/SQLCipher.swift` (product `SQLCipher`, 4.20.0 community);
  - `https://github.com/jpsim/Yams` (5.x).
  - SwiftPM runs with `--disable-keychain`, so it never asks for GitHub credentials.
- Targets:
  - **`MetalFocusCore`** (library, unit-tested): `LoggerConfig`, `Privacy`, `SampleBuilder` (+ observation types), `Sampler` (probes behind a protocol), `SwitchCounter`, `KeyStore` + `KeychainKeyStore`, `Database` (SQLCipher wrapper + open/create rules), `Store`, `PromptCoordinator`, and the UI models.
  - **`MetalFocusBar`** (executable): `MacProbes`, `WorkspaceObserver`, `Permissions`, `AppModel`, and the views/controllers.
  - **`MetalFocusCoreTests`**: golden-fixture parity, config, privacy, sampler, DB/key rules, store and outbox (ported from the v1 Python bridge tests), coordinator, and source guards.
- **Threading:** `Store` is an actor owning the one connection. Sampler ticks run off-main (AX and osascript can block 0.5 s / 1.5 s), and ticks never overlap.
- **Login:** `SMAppService.mainApp` is registered **on first launch** (my call: it is now the data logger, so collection should survive reboots). There's a toggle in the popover.
- **Signing:**
  - `app/make-cert.sh` creates the self-signed code-signing identity "Metal Focus Dev" in the login keychain (openssl → p12 → `security import`, then `security add-trusted-cert -p codeSign`, which prompts once for the user's password). It is idempotent.
  - `app/build-app.sh` builds release, assembles `app/build/MetalFocusBar.app` (Info.plist: `CFBundleIdentifier com.metalfocus.menubar`, `LSUIElement`, `MFRepoPath`), and runs `codesign --force -s "Metal Focus Dev"`. If the identity is missing, it fails with a hint. `--install` copies the app to `~/Applications`.
  - TCC grants then stick across rebuilds.

## Python side
- Remove `bridge/` and `tests/bridge/`; their logic and tests are ported to Swift `Store`.
- `logger/` is unchanged (it's the reference).
- `CLAUDE.md`: the app is the logger now, plus its commands, and don't run `python -m logger install` alongside it.

## Privacy
- Same rules as the Python logger, proven by the shared fixture.
- The viewer redacts excluded and private rows (title, domain, app and bundle are null).
- No network APIs in the app (source guard: `URLSession`, `URLRequest`, `NWConnection`).

## Left to the builder
View layout and colors, SF Symbols, error copy, viewer pagination, and the tick scheduling mechanism (it must not overlap ticks).
