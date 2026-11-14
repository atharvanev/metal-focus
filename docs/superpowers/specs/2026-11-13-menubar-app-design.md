# Menu Bar App (Swift) - Design

Date: 2026-11-13. Branch/worktree: `menubar-app` (`.claude/worktrees/menubar-app`).
Spec context: `CLAUDE.md` sections "Check-ins", "Notification", "Architecture", "Privacy rules".

## Intent
A native macOS menu bar app that is the user-facing front end of the Python logger. It has two jobs:
1. **Deliver check-ins and nudges** as actionable notifications and send each tap back as a label. Check-ins are the highest-quality labels in the system, so this is what makes training data possible.
2. **Let the user see what is being collected.** The user can confirm the logger is running and capturing the right things (and nothing it shouldn't), and see the day so far.

Python stays the brain: it decides *when* to ask or nudge, owns the encrypted DB, and runs the model. Swift is display and input only.

## Goals
- A menu bar item that is always present, with a popover showing logger health, today's counts, and the current model readout if one exists.
- Check-ins appear in a **floating check-in panel** (user decision, 2026-11-13) with all six answers visible for one tap: [Working] [Short break] [Drifting] [Rabbit hole] [Away] [Skip]. It sits under the menu bar icon. It is non-modal and never takes focus or key status. It shows a 60 s countdown, then closes and is recorded as `dismissed`. Response latency is recorded.
- Nudge notifications with [Back to work] [I'm on break] [Snooze 20 min], recorded as nudge responses.
- A viewer window with:
  - Today: a timeline strip of the day.
  - Samples: the most recent samples (time, app, domain, title, idle, switches, activity flags). Excluded and private samples are shown as such.
  - Events: logger start and stop, sleep and wake, lock, gaps, and missing permissions.
  - Check-ins: history of what was asked and answered.
- Robust to Python being down: the app shows "bridge not running" or "logger not running", restarts the bridge with backoff, and never crashes.

## Non-goals (v1)
- No logging in Swift (the v2 Swift logger is a separate, later project).
- No model code in Swift. No direct DB or Keychain access from Swift.
- No daily-review editing UI (where you tap a session to relabel it). The viewer is read-only apart from notification answers. Daily review comes later and will reuse the same bridge.
- No Xcode project file, App Store or notarization. The app is built with SwiftPM plus a script, and ad-hoc signed.

## Anti-goals (failure even if it works)
- Any data leaving the machine: no network code at all in the app or the bridge.
- The app causing a new SQLCipher key to be minted, or touching the Keychain item at all. If the bridge hits `KeyMissingError`, it reports `key_missing` and stops serving data. The popover shows this error.
- Any keyboard or mouse hook: no `NSEvent` global monitors and no event taps in the app (user privacy rule; enforced by a source-grep test like the logger's).
- The app starting, stopping or installing the logger. The `com.metalfocus.logger` LaunchAgent owns it. The popover only reports whether samples are fresh, and when they aren't it shows the command to run (`uv run python -m logger install`).
- Check-ins that steal focus, play sounds, or persist on screen past 60 s.
- Swift and Python both encoding the windowing or label rules. That logic is defined once, in Python.

## Approaches
- **A. Python bridge over stdio (chosen).** The app spawns `uv run python -m bridge` and talks newline-delimited JSON over stdin/stdout. Python alone opens the DB, using the logger's `KeyringStore` + `data.db.connect`. Chosen because:
  - notifications need a channel from Python regardless (triggers, budget and nudge rule are Python);
  - it avoids Keychain ACL prompts on every ad-hoc-signed rebuild;
  - the windowing and label schema is still in flux and stays private to Python;
  - check-in writes (split, precedence) live in one place.
  - Cost: Python must be running, and the protocol must be versioned.
- **B. Swift reads SQLCipher directly** (SQLCipher via SPM plus Keychain read). Rejected: it adds Keychain prompts, puts schema knowledge in two places, and still needs a separate notification channel.
- **C. Local HTTP server in Python + Swift client.** Rejected: it opens a port, which is unnecessary attack surface on a privacy app. stdio gives the same thing with no socket.

## Architecture

```
MetalFocusBar.app (Swift, LSUIElement)
  BridgeClient ──spawn──> uv run python -m bridge   (cwd = repo root)
      stdin  : requests  {"id":7,"method":"samples","params":{...}}
      stdout : responses {"id":7,"result":...} | {"id":7,"error":{...}}
               events    {"event":"show_checkin","data":{...}}
  NotificationController (UNUserNotificationCenter)
  MenuBarExtra popover + Viewer window (SwiftUI)

bridge (Python)
  reads   samples / events / checkins / readouts   (via data.db.connect)
  polls   notify_outbox every 2 s -> emits show_checkin / show_nudge
  writes  checkins.response, nudge_responses, notify_outbox.delivered_at
```

### Notification source: an outbox table
Python components never talk to Swift directly. Anything that wants to notify inserts a row into `notify_outbox`: the check-in scheduler (labels/), the nudge engine (nudge/), and a test CLI. The bridge polls the outbox, forwards undelivered rows as events, and marks them delivered. This decouples the logger, check-in and nudge work (other branches) from the app. The UI also works today, before those exist: `uv run python -m bridge test-checkin` / `test-nudge` insert a row.

### Tables involved (only `notify_outbox` is created by the bridge, in `bridge/schema.sql`; see Ownership)
- `checkins`: exactly the CLAUDE.md column list: `id, asked_at, trigger, window_start, window_end, model_top_state, model_top_prob, response, responded_at, split`. The scheduler inserts the row when it asks (`response` NULL). The bridge sets `asked_at` to the shown time and fills `response`/`responded_at`.
- `nudge_responses`: `id, nudge_id, asked_at, responded_at, response (back_to_work|on_break|snooze|dismissed), response_latency_s, window_start, window_end`.
- `notify_outbox`: `id, created_at, kind (checkin|nudge), ref_id, payload_json, delivered_at, expires_at`. `ref_id` points to the `checkins.id` or the nudge id. `payload_json` carries display text (nudge body, e.g. "YouTube -> Reddit -> YouTube, 12 min in").
- Ownership (agreed with the logger session `metal-focus-d1`, 2026-11-13):
  - **`bridge/schema.sql` creates all three tables for now** with `CREATE TABLE IF NOT EXISTS`. Logger's `labels/` will later move the `checkins` and `nudge_responses` DDL into `data/` *unchanged*, so their shapes are frozen by this doc.
  - `checkins` has **exactly** the CLAUDE.md columns. There is no extra latency column. Latency is derived as `responded_at - asked_at`: the scheduler sets `asked_at` when it inserts the outbox row, and the bridge overwrites it with the moment the notification was actually shown.
  - `nudge_responses` adds `response_latency_s REAL`. `response` ∈ {back_to_work, on_break, snooze, dismissed}. `dismissed` is not a label, and `labels/merge` ignores it. (Agreed with d1.)
  - Every time column is an epoch-seconds REAL.
  - The `split` column (train|eval) is set by whoever *asks* (the scheduler), never by the bridge. Eval-split check-ins must never reach training.
  - **Expiry (agreed with d1).** The scheduler sets `expires_at = created_at + 60`.
    - **Shown and never answered by `expires_at`:** the notification is withdrawn and `response='dismissed'` is written. `dismissed` and `skip` are distinct values, and the "skip 3 in a row" back-off needs that distinction.
    - **Expired before it was ever shown** (app or bridge down): the bridge writes no response and leaves `response` NULL, because the user was never asked. *(My call.)*
    - `labels/merge` ignores `dismissed`, `skip` and NULL. Nudge cooldown still counts a dismissed nudge.
  - **Late delivery.** An outbox row is never shown after its `expires_at`. The 10-min look-back was fixed at ask time, so showing it late would label the wrong windows. Budget accounting is the scheduler's concern.
  - **Nudge rows.** The nudge engine pre-inserts the `nudge_responses` row with `response` NULL, like check-ins, and puts its `id` in `ref_id`. `nudge_id` is the engine's own identifier; there is no separate `nudges` table. *(My call.)*
  - **Test prompts never touch the label tables.** `test-checkin` / `test-nudge` insert only an outbox row with `ref_id` NULL and `payload_json.test = true`. Answers to test prompts go to the bridge log only, so test data can never reach merge, training or eval. *(My call.)*
  - **No decision logic in the bridge.** When to ask, the budget, suppression and the nudge rule live in `labels/` and `nudge/`. The bridge only relays prompts and writes responses. Write transactions are short, because the logger LaunchAgent writes the same WAL-mode file concurrently.

### Protocol v1 (JSON lines)
Requests use `method` + `params`. A response echoes `id`. Each message is one JSON object per line, UTF-8. Timestamps are Unix seconds (float), matching `samples.ts`.

| method | params | result |
|---|---|---|
| `hello` | `{client_version}` | `{protocol: 1, db_path, schema_version}` |
| `status` | - | `{logger_running, last_sample_ts, samples_today, excluded_today, last_event, permissions?}` |
| `samples` | `{since?, limit=200}` | list of sample rows. `window_title`/`domain` are null when excluded |
| `events` | `{since?, limit=100}` | list of event rows |
| `timeline` | `{day: "YYYY-MM-DD"}` | list of `{start, end, label, source}` segments; see below |
| `checkins` | `{limit=50}` | list of check-in rows |
| `readout` | - | latest model `Readout` as a dict, or null |
| `checkin_response` | `{checkin_id, response, shown_at, responded_at}` | `{ok}` |
| `nudge_response` | `{nudge_id, response, shown_at, responded_at}` | `{ok}` |
| `ack_delivered` | `{outbox_id}` | `{ok}` |

Events (no `id`): `show_checkin {outbox_id, checkin_id, question, expires_at}`, `show_nudge {outbox_id, nudge_id, body}`, `status_changed {...}`.

`logger_running` is derived from the age of `last_sample_ts`: newer than 3 x `sample_interval_s` means running. No process inspection is needed.

`timeline`, staged:
- Until `data/windows.py` lands: segments come from raw samples, grouped by consecutive app (label = app name or "Excluded"/"Away" based on idle).
- Once windowing exists: 2-min windows with dominant category.
- Once `labels/merge.py` exists: final labels + `source`.
- The bridge picks whichever is available via guarded imports. Swift renders a list of segments either way and does not know which stage it is.

`readout`: the source is the `readouts` table, owned and created by `hsmm/runner.py` on hsmm-mlx (`uv run python -m hsmm.runner`, its own process):
```
readouts(window_start REAL, model TEXT, day TEXT, written_at REAL, params TEXT, readout TEXT,
         PRIMARY KEY(window_start, model))
```
- The bridge runs `SELECT window_start, day, written_at, readout FROM readouts WHERE model='hsmm' ORDER BY window_start DESC LIMIT 1` and decodes `readout` (the JSON of `asdict(Readout)`). It uses plain SQL, not an import of `hsmm`, to keep cross-branch imports to `data.db` only. *(My call.)*
- The result is null, and the UI shows "model not running", when the table is missing, empty, or `written_at` is older than 5 min.
- The bridge and app never start the runner. Like the logger, it runs on its own. *(My call.)* The fields are `dataclasses.asdict(Readout)` from `common.model_api`:
- `risk` is the only guaranteed field; every other field may be null (e.g. for the later GRU).
- `p_state` order is [Working, Short break, Drifting, Rabbit hole].
- `minutes_in_state` applies to the top state only.
- `forecast_risk` keys arrive as strings ("1", "3", "5").
- `away=true` is shown as "Away / paused", not as a risk value.

## Swift side
- SwiftPM package at `app/MetalFocusBar/`, macOS 14+, Swift 6 strict concurrency, no third-party dependencies.
- Targets:
  - `MetalFocusCore` (library): protocol models (Codable), `BridgeClient` (actor; `Process` + pipes; line framing; request/response correlation with timeouts; event `AsyncStream`; restart with exponential backoff capped at 60 s), view models.
  - `MetalFocusBar` (executable): SwiftUI `App` with `MenuBarExtra(.window)` + a `Window` scene for the viewer, plus `NotificationController`.
  - `MetalFocusCoreTests`.
- Check-in panel (`CheckinPanelController`):
  - an `NSPanel` subclass with `.nonactivatingPanel` + `.borderless` (or `.hudWindow`) style, `canBecomeKey = false` and `canBecomeMain = false`, at `.floating` level, `collectionBehavior` `[.canJoinAllSpaces, .fullScreenAuxiliary]`, hosting a SwiftUI view.
  - Positioned just below the status item (fallback: the top-right of the main screen). No sound.
  - Clicking a button never activates the app (`NSApp.activate` is never called). The frontmost app keeps focus; that can be checked by hand with `NSWorkspace.frontmostApplication` before and after.
  - Shows a 60 s countdown. On timeout it closes and sends `checkin_response` with `dismissed`. `shown_at` is when the panel appeared, and latency is `responded_at - shown_at`.
  - Only one panel at a time. If a second check-in arrives while one is open, the newer one replaces it, and the old one is recorded as `dismissed`. *(My call.)*
- Nudges stay native notifications:
  - `UNUserNotificationCenter` category `NUDGE` with 3 actions. The default banner style is fine for these.
  - Tapping the body with no action counts as `dismissed`.
  - Notification permission is requested on first launch. If it is denied, nudges fall back to the same floating panel with 3 buttons.
- `app/build-app.sh`:
  - runs `swift build -c release`;
  - assembles `build/MetalFocusBar.app` with `Info.plist` (`LSUIElement=1`, bundle id `com.metalfocus.menubar`, `MFRepoPath` = repo root baked at build time);
  - ad-hoc signs with `codesign -s -`.
  - Optional `--install` copies it to `~/Applications`.
- Launching the bridge:
  - `uv` is located from, in order: the `MFUvPath` user default, `~/.local/bin/uv`, `/opt/homebrew/bin/uv`, `/usr/local/bin/uv`.
  - It is run as `uv run --project <repo> python -m bridge` with `cwd = repo`, so the uv-managed interpreter (which holds the Keychain item and TCC grants) is the one that opens the DB.
  - The bridge's stderr is appended to `~/Library/Logs/metal-focus/bridge.err.log`.
- Launch at login: `SMAppService.mainApp` toggle in the popover. It is off by default.

## Python side (`bridge/`)
- `bridge/__main__.py`: CLI with three modes:
  - `serve` (the default, stdio loop);
  - `test-checkin`;
  - `test-nudge`.
- `bridge/server.py`: request dispatch, an outbox poll thread, and stdout writes behind one lock. Unknown method → error response. Malformed line → error response with `id: null`, keep running.
- `bridge/store.py`: the only module that touches SQL.
  - `open_store()` uses `data.db` (`KeyringStore`, `get_or_create_key` **in read-or-fail mode: never create a key**. If the DB doesn't exist, report `no_db` in `status` rather than creating it), then applies `bridge/schema.sql`.
  - Tests inject a plain `sqlite3` connection with the logger schema loaded.
- Tests (`tests/bridge/`): protocol round-trips, outbox delivery and ack, check-in response writes (latency, dismissed is not a label), excluded samples redacted in output, never-create-key behaviour, malformed input survival.

## Privacy
- The viewer shows titles and domains already stored. It never shows anything for excluded samples beyond "Excluded".
- The app has no network entitlement use, makes no URL requests, and the bridge imports no networking modules (a test greps for `socket`, `urllib`, `http`, `requests`).

## Dependencies on other branches
- `logger` branch: `data.db` (`connect`, `KeyringStore`, `KeyMissingError`), `common.paths`, schema v1. The bridge imports `data.db` lazily. Until the logger branch merges, its tests run on plain sqlite3, and `serve` against the real DB needs the logger branch merged first.
- `hsmm-mlx` branch: `common.model_api.Readout` field names (used only as dict keys for display).

## Left to the builder
View layout details, colors (one color per category, plus excluded and away), icons (SF Symbols), the popover's exact contents, error copy, backoff constants, and how the viewer paginates samples.
