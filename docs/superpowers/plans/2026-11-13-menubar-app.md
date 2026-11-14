# Menu Bar App + Bridge Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A SwiftUI menu bar app that shows check-ins (floating panel) and nudges (native notifications), writes the answers back as labels, and lets the user view what the logger collects. All DB access goes through a Python stdio bridge.

**Architecture:** `bridge/` (Python) is the only DB client. It opens the SQLCipher store via `data.db.open_existing()`, serves JSON-lines requests on stdin/stdout, and relays rows from `notify_outbox`. `app/MetalFocusBar` is a SwiftPM package. The `MetalFocusCore` library holds the protocol, `BridgeClient` and `PromptCoordinator`, all unit-tested. The `MetalFocusBar` executable holds the SwiftUI and AppKit UI. `app/build-app.sh` wraps it into an ad-hoc-signed `.app`.

**Tech Stack:** uv-managed CPython 3.13, `sqlcipher3` (via `data.db`), pytest. Swift 6.3 / Xcode 26.6, SwiftPM, SwiftUI `MenuBarExtra`, AppKit `NSPanel`, `UserNotifications`, `ServiceManagement`, Swift Testing. No third-party Swift packages.

**Spec:** `docs/superpowers/specs/2026-11-13-menubar-app-design.md` (read it first; this plan does not repeat its rationale).

## Global Constraints
- Python only through uv: `uv run pytest`, `uv run python -m bridge`. Never pip or system Python.
- **No network code anywhere.** The bridge never imports `socket`, `urllib`, `http`, `requests`, `asyncio`, `ssl` or `xmlrpc`. The app makes no URL requests.
- **No input hooks.** No `CGEventTap*`, `addGlobalMonitorForEvents`, `addLocalMonitorForEvents`, `IOHIDManager` or `CGRequestListenEventAccess` in `app/` or `bridge/`.
- **Never create a DB or key.** The only way to open the DB is `data.db.open_existing()`. Never call `get_or_create_key`, `init_schema` or `KeyringStore.set` from `bridge/`.
- The app never starts, stops or installs the logger or `hsmm.runner`.
- The `checkins` columns are exactly `id, asked_at, trigger, window_start, window_end, model_top_state, model_top_prob, response, responded_at, split`. Every time column is an epoch-seconds REAL.
- `nudge_responses.response` ∈ {back_to_work, on_break, snooze, dismissed}. `checkins.response` ∈ {Working, Short break, Drifting, Rabbit hole, Away, skip, dismissed}. (The state names match `hsmm/config.yaml` `states`, plus Away.)
- Response writes are first-write-wins: `UPDATE ... WHERE id=? AND response IS NULL`.
- Catch `store.DB_ERRORS = (sqlcipher3.Error, sqlite3.Error)` around every DB call. `sqlcipher3.Error` does not subclass `sqlite3.Error`.
- Check-in panel: never takes key or main status, never calls `NSApp.activate`, plays no sound, 60 s max on screen.
- Protocol version `1`. Bundle id `com.metalfocus.menubar`. macOS 14+.

## Review Focus
1. **The logger isn't installed yet (no DB file) when the app launches.** The bridge must keep serving `hello`/`status` with `db_state: "no_db"` and retry opening every 10 s, so the app recovers by itself once the logger starts. Test in Task 3 (`test_recovers_when_db_appears`).
2. **The bridge or app crashes while a check-in is on screen.** The check-in must still end as `dismissed`, not stay NULL forever. The bridge sweeps delivered rows past `expires_at + 30 s` with NULL responses. Test in Task 2 (`test_sweep_marks_shown_unanswered_dismissed`).
3. **A response arrives twice** (a timeout racing a click, or a retry after a bridge restart). The first answer wins and the second is a no-op with `{ok: true, applied: false}`. Test in Task 2 (`test_second_response_not_applied`).
4. **The bridge prints a non-JSON line or dies mid-request** (a Python traceback on stdout, uv printing to stdout). `BridgeClient` skips non-JSON lines, fails pending requests with `bridgeDied`, and restarts with backoff. Test in Task 5 (`skipsGarbageLines`, `pendingFailOnExit`).
5. **A check-in's `expires_at` has already passed by the time the app gets the event** (slow poll, sleep and wake). The app must not show it; the coordinator drops it. Test in Task 6 (`dropsAlreadyExpired`).

---

## File Structure
```
bridge/__init__.py
bridge/__main__.py      CLI: serve (default) | test-checkin | test-nudge
bridge/schema.sql       notify_outbox, checkins, nudge_responses (IF NOT EXISTS)
bridge/store.py         Store: the only SQL; reads, writes, outbox, sweep
bridge/server.py        Server: JSON-lines dispatch, poll thread, locked stdout
tests/bridge/conftest.py   fixture: plain sqlite3 conn with data/schema.sql + bridge/schema.sql
tests/bridge/test_store_reads.py, test_store_writes.py, test_server.py, test_guards.py
app/MetalFocusBar/Package.swift
app/MetalFocusBar/Sources/MetalFocusCore/{JSONValue,Protocol,LineFramer,BridgeClient,BridgeLauncher,PromptCoordinator}.swift
app/MetalFocusBar/Sources/MetalFocusBar/{MetalFocusBarApp,AppModel,PopoverView,ViewerView,CheckinPanel,NudgeNotifier}.swift
app/MetalFocusBar/Tests/MetalFocusCoreTests/{ProtocolTests,LineFramerTests,BridgeClientTests,PromptCoordinatorTests,SourceGuardTests}.swift
app/MetalFocusBar/Tests/MetalFocusCoreTests/Fixtures/fake_bridge.sh
app/Info.plist.in, app/build-app.sh
```

---

### Task 1: Bridge store — schema and reads

**Files:**
- Create: `bridge/__init__.py`, `bridge/schema.sql`, `bridge/store.py`, `tests/bridge/__init__.py`, `tests/bridge/conftest.py`
- Test: `tests/bridge/test_store_reads.py`

**Interfaces:**
- Consumes: `data.db.open_existing(db_path=None, store=None)`, `data.db.KeyMissingError`, `data.db.WrongKeyError`, `logger.config.load_config().sample_interval_s` (4)
- Produces:
  - `DB_ERRORS: tuple[type[BaseException], ...]`.
  - `class Store`:
    - `__init__(conn, now=time.time)` (applies `schema.sql`) and `Store.open(db_path=None, key_store=None) -> Store`, which raises the three `open_existing` errors.
    - `status() -> dict`: keys `logger_running, last_sample_ts, samples_today, excluded_today, last_event` (`{ts, kind, detail}` or None).
    - `samples(since: float | None, limit: int = 200) -> list[dict]`: newest first.
    - `events(since, limit=100) -> list[dict]`.
    - `checkins(limit=50) -> list[dict]`.
    - `timeline(day: str) -> list[dict]`: each `{start, end, label, source}`.
    - `readout(max_age_s: float = 300) -> dict | None`.

`bridge/schema.sql`:
```sql
CREATE TABLE IF NOT EXISTS checkins (
  id INTEGER PRIMARY KEY, asked_at REAL NOT NULL,
  trigger TEXT NOT NULL CHECK (trigger IN ('uncertain','gray_zone','ambiguous_app','random','daily_review')),
  window_start REAL, window_end REAL, model_top_state TEXT, model_top_prob REAL,
  response TEXT CHECK (response IS NULL OR response IN
    ('Working','Short break','Drifting','Rabbit hole','Away','skip','dismissed')),
  responded_at REAL, split TEXT NOT NULL CHECK (split IN ('train','eval')));
CREATE TABLE IF NOT EXISTS nudge_responses (
  id INTEGER PRIMARY KEY, nudge_id TEXT, asked_at REAL NOT NULL, responded_at REAL,
  response TEXT CHECK (response IS NULL OR response IN ('back_to_work','on_break','snooze','dismissed')),
  response_latency_s REAL, window_start REAL, window_end REAL);
CREATE TABLE IF NOT EXISTS notify_outbox (
  id INTEGER PRIMARY KEY, created_at REAL NOT NULL,
  kind TEXT NOT NULL CHECK (kind IN ('checkin','nudge')),
  ref_id INTEGER, payload_json TEXT NOT NULL DEFAULT '{}',
  delivered_at REAL, expires_at REAL NOT NULL);
CREATE INDEX IF NOT EXISTS notify_outbox_pending ON notify_outbox(delivered_at, expires_at);
```

- [ ] **Step 1: Write failing tests.** The `conftest.py` fixture `store` runs `sqlite3.connect(":memory:")` + `data/schema.sql` and returns `Store(conn, now=lambda: NOW)` with `NOW = 1_763_000_000.0`. A helper `add_sample(conn, ts, **over)` inserts a row with defaults (`excluded=0, locked=0, app_switches=0, dt=4, tz_offset_min=0, app_name="Code", window_title="x.py", domain=None, idle_s=0`).
  - `test_schema_idempotent`: `Store(conn)` twice → no error; `checkins` columns == the exact CLAUDE.md list.
  - `test_status_running_and_counts`: samples at NOW-5 (excluded=1) and NOW-100 → `logger_running is True`, `samples_today == 2`, `excluded_today == 1`, `last_sample_ts == NOW-5`. With the only sample at NOW-13 → `logger_running is False`.
  - `test_status_no_samples`: `last_sample_ts is None`, `logger_running is False`.
  - `test_samples_redact_excluded_and_private`: an excluded row stored with title "Chase" → output has `window_title is None`, `domain is None`, `app_name is None`, `excluded == 1`. Same for `private == 1`. Rows are newest first, `limit` is respected, and `since` filters `ts > since`.
  - `test_timeline_from_samples`: Code×3, then a Safari row with `idle_s=95`, then an excluded row → segments labelled `["Code", "Away", "Excluded"]`, all with `source == "samples"`, and `start`/`end` = first ts and last ts + dt.
  - `test_readout_missing_table_is_none`; `test_readout_fresh_and_stale`: create a `readouts` table per the spec DDL, insert `readout='{"risk":0.7,"away":false}'`, `written_at=NOW-10` → dict with `risk == 0.7` and `window_start`; with `written_at=NOW-301` → None.
- [ ] **Step 2:** `uv run pytest tests/bridge -v`. Expected: FAIL (ImportError).
- [ ] **Step 3: Implement `bridge/store.py`.**
  - `DB_ERRORS = (sqlcipher3.Error, sqlite3.Error)`.
  - Row → dict via `cursor.description`.
  - "Today" is local midnight from `datetime.fromtimestamp(now()).replace(hour=0, ...)`. `timeline(day)` uses that local day too.
  - Running threshold: `3 * load_config().sample_interval_s`, read once at `__init__`.
  - Redaction applies when `excluded == 1 or private == 1`.
  - Away when `locked == 1 or (idle_s or 0) >= 90`.
  - `timeline` builds sample segments only (`source="samples"`). Switching to windows and labels comes later, once `data.windows` and `labels.merge` exist. That's a follow-up, not this plan.
  - Readout: the spec's SQL; `sqlite3`/`sqlcipher3` `OperationalError` (no such table) → None.
- [ ] **Step 4:** `uv run pytest tests/bridge -v`. Expected: PASS.
- [ ] **Step 5:** Commit `feat(bridge): store schema and read queries`.

### Task 2: Bridge store — outbox, responses, sweep, test prompts

**Files:**
- Modify: `bridge/store.py`
- Test: `tests/bridge/test_store_writes.py`

**Interfaces:**
- Produces (on `Store`):
  - `pending_outbox() -> list[dict]`: undelivered rows with `expires_at > now`, oldest first. Each dict is `{outbox_id, kind, ref_id, expires_at, payload}`, with `payload` = decoded JSON.
  - `ack_delivered(outbox_id: int) -> bool`: sets `delivered_at=now`, and sets the referenced row's `asked_at=now` (checkins or nudge_responses, by kind) when `ref_id` is not None.
  - `checkin_response(checkin_id: int, response: str, responded_at: float) -> bool` and `nudge_response(nudge_id_row: int, response: str, responded_at: float) -> bool`. The nudge version also sets `response_latency_s = responded_at - asked_at`. Both return True if applied, and raise `ValueError` for a response outside the allowed set.
  - `sweep_expired(grace_s: float = 30) -> int`: for delivered outbox rows with `expires_at + grace_s < now` whose referenced row's response is NULL, writes `dismissed` with `responded_at = expires_at`. Returns the count.
  - `insert_test_prompt(kind: str, ttl_s: float = 60) -> int`: an outbox row with `ref_id NULL` and `payload {"test": true, "question" | "body": ...}`.

Wire-level note: the protocol's `nudge_response.nudge_id` param carries the `nudge_responses.id` (the outbox `ref_id`). The engine's own `nudge_id` text is display-only.

- [ ] **Step 1: Write failing tests**
  - `test_pending_excludes_delivered_and_expired`: three rows (fresh, delivered, `expires_at=NOW-1`) → only the fresh one is returned.
  - `test_ack_sets_asked_at_to_shown_time`: insert a checkin with `asked_at=NOW-50` + an outbox row → `ack_delivered` → checkin `asked_at == NOW` and outbox `delivered_at == NOW`.
  - `test_checkin_response_writes`: after the ack, `checkin_response(id, "Drifting", NOW+7)` → True; the row has `response == "Drifting"`, `responded_at == NOW+7`, and `split` unchanged.
  - `test_second_response_not_applied` (**Review Focus 3**): a second call with `"Working"` → False; the row is still `"Drifting"`.
  - `test_invalid_response_rejected`: `"rabbit"` → ValueError, row unchanged.
  - `test_nudge_latency`: ack at NOW, `nudge_response(id, "snooze", NOW+4)` → `response_latency_s == 4`.
  - `test_sweep_marks_shown_unanswered_dismissed` (**Review Focus 2**): a delivered checkin outbox row with `expires_at=NOW-31` and response NULL → `sweep_expired() == 1`, `response == "dismissed"`, `responded_at == expires_at`. A row with `expires_at=NOW-10` is untouched.
  - `test_sweep_ignores_never_shown`: undelivered and expired → `sweep_expired() == 0`, response stays NULL.
  - `test_test_prompt_touches_no_label_tables`: `insert_test_prompt("checkin")` → `checkins` and `nudge_responses` counts stay 0; `pending_outbox()[0]["payload"]["test"] is True`; `ref_id is None`.
- [ ] **Step 2:** run → FAIL.
- [ ] **Step 3:** Implement. Each write is one short transaction (`with conn:`).
- [ ] **Step 4:** `uv run pytest tests/bridge -v` → PASS.
- [ ] **Step 5:** Commit `feat(bridge): outbox relay, response writes, expiry sweep`.

### Task 3: Bridge server + CLI

**Files:**
- Create: `bridge/server.py`, `bridge/__main__.py`
- Test: `tests/bridge/test_server.py`, `tests/bridge/test_guards.py`

**Interfaces:**
- Consumes: `Store` (Tasks 1-2).
- Produces:
  - `class Server(inp: TextIO, out: TextIO, open_store: Callable[[], Store], poll_s: float = 2.0, reopen_s: float = 10.0, now=time.time)`.
    - `handle_line(line: str) -> dict | None`: the response; it does not write it.
    - `poll_once() -> list[dict]`: the events emitted (it also writes them).
    - `serve() -> int`: blocks until stdin EOF, returns 0.
  - Wire format:
    - responses are `{"id": <id>, "result": ...}` | `{"id": <id or null>, "error": {"code", "message"}}`;
    - events are `{"event": name, "data": {...}}`.
  - Error codes: `bad_request, unknown_method, no_db, key_missing, wrong_key, db_error`.
  - Methods (spec table):
    - `hello` → `{protocol: 1, db_state, schema_version}`, where `db_state` ∈ {ok, no_db, key_missing, wrong_key, error}.
    - `status` → `Store.status()` + `db_state`. It works without a DB, returning `db_state` only, with `logger_running: false`.
    - `samples`, `events`, `timeline`, `checkins`, `readout`, `ack_delivered`, `checkin_response`, `nudge_response`. The responses return `{ok: true, applied: bool}`. For a test prompt (no `ref_id`), the app sends `checkin_id: null`; the server logs `test response: ...` to stderr and returns `{ok: true, applied: false}`.
  - Events:
    - `show_checkin {outbox_id, checkin_id, question, expires_at, test}`; the question defaults to "What were you doing the last 10 min?";
    - `show_nudge {outbox_id, nudge_id, body, expires_at, test}`;
    - `status_changed {db_state}` when `db_state` changes.
  - CLI: `python -m bridge [serve|test-checkin|test-nudge]`. The test commands open the store, insert a test prompt, and print its outbox id.

- [ ] **Step 1: Write failing tests** (`io.StringIO` streams; `open_store` returns the fixture store or raises)
  - `test_hello_and_status_ok`; `test_unknown_method`; `test_malformed_line_keeps_running`: `handle_line("not json")` → error `bad_request` with `id is None`, and the next `hello` still works.
  - `test_no_db_serves_status`: `open_store` raises FileNotFoundError → `hello.db_state == "no_db"`, `samples` → error `no_db`. `KeyMissingError` → `key_missing`, and the test asserts the fake key store's `set` was never called.
  - `test_recovers_when_db_appears` (**Review Focus 1**): `open_store` raises first, then returns the store. Advancing `now` by 11 s and calling `poll_once()` → it emits `status_changed {db_state: "ok"}`, and `samples` then works.
  - `test_poll_emits_and_does_not_redeliver_unacked`: `poll_once` emits `show_checkin` once per outbox id per process (it remembers emitted ids) until acked. After an ack it is never emitted again.
  - `test_poll_runs_sweep`: the sweep from Task 2 runs inside `poll_once`.
  - `test_db_error_becomes_error_response`: monkeypatch `Store.samples` to raise `sqlite3.OperationalError` → error `db_error`; the server keeps running.
  - `test_serve_subprocess_smoke` (subprocess): with `METAL_FOCUS_HOME=tmp_path` (no DB), `uv run python -m bridge` gets `{"id":1,"method":"hello"}\n` and then EOF → it prints one line with `"db_state": "no_db"` and exits 0 within 10 s.
  - `tests/bridge/test_guards.py`:
    - `test_no_network_imports`: the source of every `bridge/*.py` contains none of `import socket`, `urllib`, `http.`, `import http`, `requests`, `asyncio`, `ssl`, `xmlrpc`;
    - `test_never_creates_key`: none of `get_or_create_key`, `init_schema`, `.set(`;
    - `test_no_input_hooks`: the forbidden symbol list from Global Constraints.
- [ ] **Step 2:** run → FAIL.
- [ ] **Step 3: Implement.**
  - `serve()` uses a single `queue.Queue`:
    - a daemon reader thread pushes stdin lines, then an EOF sentinel;
    - a daemon timer thread pushes a POLL sentinel every `poll_s`;
    - the main thread drains the queue and does **all** DB work, so the connection never crosses threads (`open_existing` doesn't expose `check_same_thread`). Every write to `out` happens on the main thread.
  - Logging goes to stderr only. Nothing except protocol JSON ever goes to stdout.
- [ ] **Step 4:** `uv run pytest tests/bridge -v` → PASS; then `uv run pytest` (full suite) → all pass.
- [ ] **Step 5:** Commit `feat(bridge): JSON-lines stdio server and CLI`.

### Task 4: Swift package, protocol models, line framer

**Files:**
- Create: `app/MetalFocusBar/Package.swift`, `Sources/MetalFocusCore/{JSONValue,Protocol,LineFramer}.swift`, a placeholder `Sources/MetalFocusBar/MetalFocusBarApp.swift` (an empty `@main` App with a `MenuBarExtra("Metal Focus", systemImage: "scope") { Text("…") }`), and `Tests/MetalFocusCoreTests/{ProtocolTests,LineFramerTests}.swift`.
- Modify: `.gitignore`: add `app/MetalFocusBar/.build/`, `app/build/`.

**Interfaces:**
- Produces:
  - `Package.swift`: swift-tools-version 6.0, `platforms: [.macOS(.v14)]`, a library `MetalFocusCore`, an executable `MetalFocusBar` (depends on Core), and a test target `MetalFocusCoreTests` (resources: `.copy("Fixtures")`).
  - `enum JSONValue: Codable, Sendable, Equatable` (null, bool, number(Double), string, array, object).
  - `enum Incoming: Sendable { case response(id: Int, result: JSONValue); case error(id: Int?, code: String, message: String); case event(name: String, data: JSONValue) }` with `static func parse(_ line: Data) -> Incoming?` (nil for non-JSON or an unrecognized shape).
  - `struct Request: Encodable { let id: Int; let method: String; let params: JSONValue }`.
  - Models (`Decodable, Sendable`, snake_case keys via `JSONDecoder.keyDecodingStrategy = .convertFromSnakeCase`), with every field the spec marks nullable as optional:
    - `Status(dbState, loggerRunning, lastSampleTs?, samplesToday?, excludedToday?, lastEvent?)`;
    - `SampleRow(id, ts, appName?, domain?, windowTitle?, idleS?, appSwitches, excluded, private?, keyActive?, clickActive?, scrollActive?)`;
    - `EventRow(id, ts, kind, detail?)`;
    - `Segment(start, end, label, source)`;
    - `CheckinRow(id, askedAt, trigger, response?, respondedAt?, split)`;
    - `ReadoutInfo(risk, pState?: [Double], topState?: Int, topProb?, minutesInState?, surprise?, away?, writtenAt?)`;
    - `ShowCheckin(outboxId, checkinId?, question, expiresAt, test)`;
    - `ShowNudge(outboxId, nudgeId?, body, expiresAt, test)`.
  - `extension JSONValue { func decode<T: Decodable>(_: T.Type) throws -> T }`.
  - `struct LineFramer { mutating func append(_ data: Data) -> [Data] }`: splits on `\n`, keeps the partial tail, and drops empty lines.
  - `enum CheckinAnswer: String, CaseIterable, Sendable`: working "Working", shortBreak "Short break", drifting "Drifting", rabbitHole "Rabbit hole", away "Away", skip "skip", dismissed "dismissed". `enum NudgeAnswer: String`: backToWork "back_to_work", onBreak "on_break", snooze "snooze", dismissed "dismissed".
- [ ] **Step 1: Write failing tests** (Swift Testing, `import Testing`)
  - `parsesResponse`: `{"id":3,"result":{"ok":true}}`.
  - `parsesErrorWithNullId`.
  - `parsesEvent`: `{"event":"show_checkin","data":{"outbox_id":1,"checkin_id":null,"question":"q","expires_at":10.5,"test":true}}`, then decode as `ShowCheckin` → `checkinId == nil`, `test == true`.
  - `garbageIsNil`: `"Traceback (most recent call last):"` → nil.
  - `readoutAllowsNulls`: `{"risk":0.2}` decodes with the other fields nil.
  - `checkinAnswerRawValues`: matches the Global Constraints sets exactly.
  - `LineFramerTests.splitsAcrossChunks`: `"{\"a\":1}\n{\"b"` then `"\":2}\n"` → 2 lines total; an empty line is dropped.
- [ ] **Step 2:** `cd app/MetalFocusBar && swift test` → FAIL (compile errors).
- [ ] **Step 3:** Implement.
- [ ] **Step 4:** `swift test` → PASS. `swift build` → the executable builds.
- [ ] **Step 5:** Commit `feat(app): SwiftPM package with bridge protocol models`.

### Task 5: BridgeClient + launcher

**Files:**
- Create: `Sources/MetalFocusCore/{BridgeClient,BridgeLauncher}.swift`, `Tests/MetalFocusCoreTests/BridgeClientTests.swift`, `Tests/MetalFocusCoreTests/Fixtures/fake_bridge.sh` (executable)

**Interfaces:**
- Consumes: `Incoming`, `Request`, `LineFramer`, `JSONValue` (Task 4).
- Produces:
  - `struct BridgeCommand: Sendable { executable: URL; arguments: [String]; cwd: URL; stderrLog: URL? }`.
  - `enum BridgeLauncher { static func locateUV(defaults: UserDefaults = .standard, fileExists: (String) -> Bool = FileManager.default.isExecutableFile(atPath:)) -> URL?; static func command(repo: URL, uv: URL) -> BridgeCommand }`.
    - The search order is `MFUvPath` default, then `~/.local/bin/uv`, `/opt/homebrew/bin/uv`, `/usr/local/bin/uv`.
    - The arguments are `["run", "--project", repo.path, "python", "-m", "bridge"]`.
    - The stderr log is `~/Library/Logs/metal-focus/bridge.err.log`.
  - `enum BridgeError: Error, Sendable { case notRunning, bridgeDied, timeout, remote(code: String, message: String) }`.
  - `actor BridgeClient`:
    - `init(command: BridgeCommand, requestTimeout: Duration = .seconds(10), backoff: [Duration] = [1,2,4,8,16,32,60].map(Duration.seconds))`.
    - `func start()`.
    - `func stop()`.
    - `func call(_ method: String, _ params: JSONValue = .object([:])) async throws -> JSONValue`.
    - `nonisolated let events: AsyncStream<(name: String, data: JSONValue)>`.
    - `nonisolated let states: AsyncStream<BridgeState>`, where `enum BridgeState: Sendable { case starting, running, restarting(after: Duration), stopped }`.
- [ ] **Step 1: Write failing tests**
  - `fake_bridge.sh`: a bash `while IFS= read -r line` loop that extracts `id` and `method` with `sed`.
    - For `ping` it replies `{"id":N,"result":"pong"}`.
    - For `boom` it replies `{"id":N,"error":{"code":"db_error","message":"x"}}`.
    - For `garbage` it prints `not json` then the pong.
    - For `die` it exits 1.
    - For `slow` it doesn't reply.
    - At start it prints `{"event":"status_changed","data":{"db_state":"ok"}}`.
  - Tests (each builds the command from `Bundle.module.url(forResource:"fake_bridge", withExtension:"sh", subdirectory:"Fixtures")`, with `/bin/bash` as the executable):
    - `roundTrip`: `call("ping") == .string("pong")`.
    - `remoteError`: `boom` throws `.remote(code: "db_error", _)`.
    - `skipsGarbageLines` (**Review Focus 4**): `garbage` still returns pong.
    - `receivesEvents`: the first event's name is `status_changed`.
    - `timeoutFires`: `slow` with a 200 ms timeout → `.timeout`.
    - `pendingFailOnExit` (**Review Focus 4**): `die` → `.bridgeDied`.
    - `restartsAfterDeath`: after `die`, using backoff `[.milliseconds(50)]`, a later `ping` succeeds within 2 s.
    - `locateUVOrder`: with a fake `fileExists`, the first existing path wins, and `MFUvPath` overrides.
- [ ] **Step 2:** `swift test` → FAIL.
- [ ] **Step 3: Implement.**
  - `Process` with stdin/stdout `Pipe`s.
  - stdout `readabilityHandler` → `LineFramer` → `Incoming.parse` → hops into the actor.
  - stderr is appended to the log file (the directory is created on first use), or discarded if `stderrLog` is nil.
  - `terminationHandler` fails all pending requests with `.bridgeDied`, emits `.restarting`, sleeps `backoff[min(n, last)]`, and relaunches unless `stop()` was called. The restart counter resets after 60 s of uptime.
  - The ids are a monotonically increasing `Int`.
  - Pending requests are `[Int: CheckedContinuation<JSONValue, Error>]`, plus a timeout `Task` per request.
- [ ] **Step 4:** `swift test` → PASS.
- [ ] **Step 5:** Commit `feat(app): bridge process client with restart and timeouts`.

### Task 6: PromptCoordinator (prompt lifecycle logic, UI-free)

**Files:**
- Create: `Sources/MetalFocusCore/PromptCoordinator.swift`, `Tests/MetalFocusCoreTests/PromptCoordinatorTests.swift`

**Interfaces:**
- Consumes: `ShowCheckin`, `ShowNudge`, `CheckinAnswer`, `NudgeAnswer` (Task 4).
- Produces:
  - `@MainActor protocol PromptPresenter: AnyObject { func showCheckin(_ p: ShowCheckin, deadline: Date); func closeCheckin(); func showNudge(_ p: ShowNudge, deadline: Date); func closeNudge(outboxId: Int) }`.
  - `@MainActor protocol PromptSink: AnyObject { func ack(outboxId: Int) async; func answerCheckin(checkinId: Int?, _ a: CheckinAnswer, shownAt: Date, respondedAt: Date) async; func answerNudge(nudgeId: Int?, _ a: NudgeAnswer, shownAt: Date, respondedAt: Date) async }`.
  - `@MainActor final class PromptCoordinator`:
    - `init(presenter:, sink:, now: @escaping () -> Date = Date.init, maxOnScreen: TimeInterval = 60)`.
    - `func receive(checkin: ShowCheckin) async`.
    - `func receive(nudge: ShowNudge) async`.
    - `func userAnswered(_ a: CheckinAnswer) async`.
    - `func userAnswered(nudgeOutboxId: Int, _ a: NudgeAnswer) async`.
    - `func tick() async`: the app calls it every 1 s; it handles timeouts.
    - `var currentCheckin: ShowCheckin? { get }`.
- [ ] **Step 1: Write failing tests** (fake presenter and sink record calls; `now` is a mutable closure)
  - `showsAndAcks`: receive → presenter `showCheckin` with `deadline = min(now+60, expiresAt)`, and the sink `ack(outboxId)`.
  - `dropsAlreadyExpired` (**Review Focus 5**): `expiresAt` < now → no show, no ack.
  - `answerSendsLatencyInputs`: shown at t0, answered at t0+7 → `answerCheckin(id, .drifting, shownAt: t0, respondedAt: t0+7)`, and the presenter is closed.
  - `timeoutDismisses`: advance past the deadline, `tick()` → `answerCheckin(..., .dismissed, respondedAt: deadline)`, closed.
  - `newerReplacesOlder`: a second checkin while one is open → the first gets `.dismissed`, the second is shown and acked.
  - `doubleAnswerIgnored`: after one answer, a second `userAnswered` sends nothing.
  - `nudgeTimeoutDismisses` and `nudgeAnswer`, the same for nudges keyed by outbox id. Several nudges may be open at once.
- [ ] **Step 2:** `swift test` → FAIL. **Step 3:** Implement. **Step 4:** `swift test` → PASS.
- [ ] **Step 5:** Commit `feat(app): prompt lifecycle coordinator`.

### Task 7: The app — model, popover, viewer, check-in panel, nudge notifications

**Files:**
- Create: `Sources/MetalFocusBar/{AppModel,PopoverView,ViewerView,CheckinPanel,NudgeNotifier}.swift`
- Modify: `Sources/MetalFocusBar/MetalFocusBarApp.swift`

**Interfaces:**
- Consumes: `BridgeClient`, `BridgeLauncher`, models, `PromptCoordinator`, `PromptPresenter`, `PromptSink`.
- Produces:
  - `@MainActor @Observable final class AppModel: PromptSink`:
    - published state: `bridgeState`, `status: Status?`, `readout: ReadoutInfo?`, `samples`, `events`, `timeline`, `checkins`, `notificationsDenied: Bool`, `lastError: String?`;
    - `func refreshStatus() async` (every 5 s: status + readout);
    - `func refreshViewer(tab:) async`;
    - `func sendTestCheckin()` (runs `uv run python -m bridge test-checkin` as a one-off `Process`).
  - The repo is read from `Bundle.main.object(forInfoDictionaryKey: "MFRepoPath")`, overridable by the `MFRepoPath` user default. If it's missing, `lastError` explains how to set it.
- [ ] **Step 1: Build the UI** (no unit tests; logic is in Core and tested there):
  - **App scenes.** `MetalFocusBarApp`: `MenuBarExtra(.window)` with an SF Symbol, `scope`, or `exclamationmark.triangle` when `status.dbState != "ok"` or the logger isn't running. Plus a `Window("Metal Focus", id: "viewer")`.
  - **Popover.** `PopoverView` rows follow the spec mock:
    - **Logger:** "running (last sample Ns ago)" or "not running — run `uv run python -m logger install`".
    - **Today:** sample and excluded counts.
    - **Model** line:
      - "model not running" if `readout == nil`;
      - "Away / paused" if `away == true`;
      - otherwise `stateName(topState) topProb% · risk x.xx · N min in state`.
    - **Errors:** `db_state` errors (`key_missing` → "Database key missing from Keychain — the bridge will not create a new one."), and "notifications off" if denied.
    - **Controls:** [Open viewer], [Test check-in], a "Launch at login" toggle (`SMAppService.mainApp.register()/unregister()`), [Quit].
  - **Viewer.** `ViewerView`: a `TabView` with Today (horizontal strip of `Segment`s, colored per label: one hue per app category or app name hash, gray for Away, striped/neutral for Excluded, plus a list), Samples (`Table`), Events (`Table`), Check-ins (`Table`). Refresh on appear and every 10 s while visible.
  - **Check-in panel.** `CheckinPanel`:
    - `final class CheckinPanel: NSPanel` with `styleMask: [.nonactivatingPanel, .borderless]`, `canBecomeKey/Main` → false, `level = .floating`, `collectionBehavior = [.canJoinAllSpaces, .fullScreenAuxiliary]`, `hidesOnDeactivate = false`, `isMovableByWindowBackground = false`.
    - It hosts a SwiftUI view: the question, a countdown, and six buttons in a 2-column grid, labelled exactly `Working, Short break, Drifting, Rabbit hole, Away, Skip`, plus a "(test)" tag when `test`.
    - It's positioned under the status item via the `NSStatusBarButton`'s window frame when available, else the top-right of `NSScreen.main.visibleFrame` with 12 pt insets.
    - `CheckinPanelController: PromptPresenter` (it also presents nudges in the panel when `notificationsDenied`).
  - **Nudges.** `NudgeNotifier`:
    - `UNUserNotificationCenter` delegate; category `NUDGE` with actions `back_to_work` "Back to work", `on_break` "I'm on break", `snooze` "Snooze 20 min"; no sound.
    - `didReceive` → `coordinator.userAnswered(nudgeOutboxId:, …)`. The default action (body tap) → `.dismissed`.
    - It requests authorization on launch and sets `notificationsDenied` on denial.
    - `closeNudge` → `removeDeliveredNotifications(withIdentifiers: ["nudge-\(id)"])`.
  - **Wiring.** At launch: locate uv → `BridgeClient.start()` → `call("hello")`. A loop over `client.events` routes `show_checkin`/`show_nudge` to the coordinator and `status_changed` to `refreshStatus`. A 1 s timer drives `coordinator.tick()`. `PromptSink` methods call `ack_delivered` / `checkin_response` / `nudge_response`, with times sent as epoch seconds.
- [ ] **Step 2:** `swift build` → succeeds with zero warnings under Swift 6 strict concurrency.
- [ ] **Step 3:** Commit `feat(app): menu bar popover, viewer, check-in panel, nudge notifications`.

### Task 8: App bundle, source guards, docs, end-to-end check

**Files:**
- Create: `app/Info.plist.in`, `app/build-app.sh`, `Tests/MetalFocusCoreTests/SourceGuardTests.swift`
- Modify: `CLAUDE.md`: under "Environment and commands", add an "App" bullet list.

**Interfaces:**
- Consumes: everything above.
- [ ] **Step 1: Write the failing guard test** `SourceGuardTests.noHooksNoNetwork`. It walks `Sources/` (found from `#filePath`) and asserts that no file contains any of: `CGEventTap`, `addGlobalMonitorForEvents`, `addLocalMonitorForEvents`, `IOHIDManager`, `CGRequestListenEventAccess`, `URLSession`, `URLRequest`, `NWConnection`, `NSApp.activate`, `NSApplication.shared.activate`, `activate(ignoringOtherApps`. Run `swift test` and confirm it passes against the current code. If it fails, fix the code, not the list.
- [ ] **Step 2: Write `app/build-app.sh`** (`set -euo pipefail`; repo = `git rev-parse --show-toplevel`).
  - It runs `swift build -c release --package-path app/MetalFocusBar`.
  - It creates `app/build/MetalFocusBar.app/Contents/{MacOS,Resources}`, copies the binary, and renders `Info.plist.in`. The plist contains: `CFBundleIdentifier com.metalfocus.menubar`, `CFBundleName "Metal Focus"`, `CFBundleExecutable MetalFocusBar`, `CFBundlePackageType APPL`, `LSUIElement true`, `LSMinimumSystemVersion 14.0`, `MFRepoPath <repo>`, `CFBundleShortVersionString 0.1.0`.
  - It runs `codesign --force -s - app/build/MetalFocusBar.app`.
  - With `--install`, it copies the app to `~/Applications/`.
- [ ] **Step 3:** `app/build-app.sh && codesign -v app/build/MetalFocusBar.app && plutil -lint app/build/MetalFocusBar.app/Contents/Info.plist`. Expected: all succeed.
- [ ] **Step 4: Run the end-to-end manual check and record the results in the commit body.**
  1. `open app/build/MetalFocusBar.app` → the menu bar icon appears; the popover shows the logger state, either "running" or the install hint.
  2. `uv run python -m bridge test-checkin` → the panel appears within about 3 s. The frontmost app (e.g. VS Code) stays frontmost (check with `osascript -e 'tell application "System Events" to name of first process whose frontmost is true'` before and after clicking "Drifting"). `~/Library/Logs/metal-focus/bridge.err.log` shows the test response.
  3. Run test-checkin again and wait 60 s → the panel closes on its own.
  4. `uv run python -m bridge test-nudge` → a notification appears with 3 actions.
  5. The viewer shows samples and events, if the logger is running.

  If the DB isn't present (logger not installed), steps 1-4 still work with `db_state no_db`: the test prompts need the DB to insert into, so then skip 2-4 and note it.
- [ ] **Step 5:** Add to `CLAUDE.md`:
  - build the app with `app/build-app.sh [--install]`;
  - Swift tests with `cd app/MetalFocusBar && swift test`;
  - try the UI with `uv run python -m bridge test-checkin|test-nudge`;
  - the app talks only through `python -m bridge` (stdio) and never starts the logger or runner.
- [ ] **Step 6:** `uv run pytest -q` and `(cd app/MetalFocusBar && swift test)` → all pass. Commit `feat(app): app bundle script, source guards, docs`.

### Task 9: Merge to main
- [ ] `git fetch . main:main 2>/dev/null; git merge main` (other sessions may have merged since). Resolve any conflicts. If one touches a shared contract (`checkins`/`nudge_responses`/`notify_outbox`), message the owning session (`metal-focus-d1` / `metal-focus-fd`) before resolving.
- [ ] `uv run pytest -q` and `swift test` → all pass.
- [ ] From the main checkout: `git -C /Users/atharva/Documents/GitHub/metal-focus merge --no-ff menubar-app`. Run `uv run pytest -q` there.
- [ ] Message `metal-focus-d1` and `metal-focus-fd`: menubar-app merged, `bridge/schema.sql` tables live, and how to emit prompts (insert the label row, then the outbox row with `expires_at = created_at + 60`).
