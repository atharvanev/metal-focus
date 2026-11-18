# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Repo status
As of 2026-11-13 `main` has: the Python activity logger (`logger/`, `data/db.py`; now the reference implementation and test oracle), windowing (`data/windows.py`), the rules categorizer, auto-labeler, label merge and check-in scheduler (`labels/`), and the HSMM in MLX with its live runner (`hsmm/`, GRU stub in `models_later/gru/`). As of 2026-11-14 sampling is done by the signed Swift menu bar app "Metal Rabbit" (`app/`; display name only, while the bundle id `com.metalfocus.menubar`, the `metal-focus` data dir/Keychain service and the `MetalFocusBar` targets keep their names). It writes the `samples` table directly; there is no Python bridge. Build with `app/build-app.sh [--install]`, re-render the icon with `app/make-icon.sh`, and test with `swift test --disable-keychain --package-path app/MetalFocusBar`. Also on main: the rules baseline (`baselines/rules.py`), weekly threshold tuning (`labels/tune.py`), the nudge engine (`nudge/`), nightly training (`hsmm/nightly.py`), the HMM baseline (`baselines/hmm.py`) and the eval harness (`eval/`).

## Environment and commands (uv only)
Python is managed **only by uv**: a uv-managed CPython 3.13 is pinned in `.python-version`, and `pyproject.toml` sets `python-preference = "only-managed"`. Never use pip, conda, brew Python or system Python for this repo. The project is not an installable package (`package = false`); top-level dirs are imported from the repo root (pytest `pythonpath = ["."]`).
- Set up or refresh the env: `uv sync`
- Add a dependency: `uv add <pkg>` (dev-only: `uv add --dev <pkg>`). Commit `uv.lock`.
- Run anything: `uv run python -m <module>`
- All tests: `uv run pytest`
- One test: `uv run pytest tests/path/test_file.py::test_name -v`
- Skip tests that hit real macOS APIs/permissions: `uv run pytest -m "not macos"`
- macOS permissions (Accessibility, Input Monitoring, Automation) attach to the launching binary. For this repo that is the uv-managed interpreter under `~/.local/share/uv/python/`, or the terminal/IDE that launches it.
- MLX (`mlx>=0.32.4`) is a dependency; device selection lives in `common/device.py`.

## Logger (v1, Python reference implementation)
- **Permission policy (user, 2026-11-13):** macOS permissions (Accessibility etc.) belong only to the signed Swift app, never to Python. Do **not** recommend `python -m logger install`; the LaunchAgent was removed. The Python logger stays as the reference implementation of the sampling contract and as a test oracle.
- **Contract the Swift sampler follows:** `data/schema.sql` (`samples`, `events`, plus the shared `checkins`, `nudge_responses`, `notify_outbox`, `not_working`), `logger/config.yaml` and `logger/financial.yaml` (exclusions), and the privacy semantics in `logger/sample.py:build_sample`. Announce any schema change to the app session first.
- `uv run python -m logger status`: sample count, last sample, recent events (read-only; never creates a key).
- Background jobs: `uv run python -m jobs deploy [--prompts | --no-prompts]` (default keeps the last setting) / `status` / `uninstall`. Deploys the committed `main` tree to `<data dir>/runtime/<sha>` (outside ~/Documents, so no Documents permission) with its own `uv sync` and writes `runtime/jobs.json`. The menu bar app (the only login item) runs those jobs as child processes: the HSMM runner, nightly update (once a day after 03:30) and (with `--prompts`) the check-in scheduler + nudge engine. They are spawned with responsibility disclaimed (`app/.../JobSupervisor.swift`, private `responsibility_spawnattrs_setdisclaim`), so they hold none of the app's permissions; `SourceGuardTests` enforces this. There are no LaunchAgents (deploy removes the old `com.metalfocus.*` ones, and the app pauses jobs while any remain). Redeploy after merging to main; the app restarts the jobs when `jobs.json` changes. Leave `--prompts` off until the menu bar app shows prompts.
- `uv run python -m dashboard [--port N] [--no-open]`: local read-only dashboard (timeline, labels, top apps/sites/titles, check-ins, live samples). Binds 127.0.0.1 only, needs the per-run token in the printed URL, checks the Host header, loads no external resources, and writes nothing.
- Data: `~/Library/Application Support/metal-focus/activity.db` (SQLCipher; override the dir with `METAL_FOCUS_HOME`). The key lives in Keychain (service `metal-focus`, account `sqlcipher-key`). If the DB exists but the key is gone, writers refuse to mint a new key; readers use `data.db.open_existing()`, which never creates a DB or key.
- **Privacy, set by the user (2026-11-13):** no keyboard or mouse hooks of any kind (no event taps, no global monitors, no Input Monitoring permission). Typing/click/scroll come only from macOS's "seconds since last event" timers, stored as 0/1 per-sample flags; `tests/logger/test_cli.py::test_no_input_hooks_in_source` guards this. Financial sites and apps are excluded automatically via `logger/financial.yaml` (always on). Extra exclusions and IBM repo title patterns go in `logger/config.local.yaml` (gitignored). Private/incognito windows store no title or domain; Safari can't report private windows, so Safari titles and domains are dropped unless `unknown_private_policy: keep`.

## Windowing and labels
- `data.windows.load_day(conn, day)`: completed 2-min windows for a local day, computed from `samples` (no windows table). Fields are a contract with `hsmm/io.py`; announce renames.
- `labels/categories.yaml`: app/site -> category rules (+ low-confidence flags); personal additions in `labels/categories.local.yaml` (gitignored).
- `labels/config.yaml`: auto-labeler thresholds and check-in scheduler settings (never hard-code them).
- `labels.merge.load_training_labels(conn, day)`: per-window (state, source), check-in > nudge > auto, eval check-ins excluded. `labels.checkins.load_eval(conn, start, end)`: the eval set.
- `uv run python -m labels.checkins run`: check-in scheduler loop (writes `checkins` + `notify_outbox`; the Swift app shows prompts and writes responses).
- `uv run python -m labels.tune suggest` / `apply KEY=V ...`: weekly threshold suggestions from train-split check-ins (needs 30+); `apply` is the user's approval step and logs to `labels/tuning_log.csv`.
- `baselines/rules.RulesBaseline`: the auto-labeler rules run live, implementing `common.model_api.LiveModel` (compare against the HSMM).
- Schema v2 added `samples.media_playing`; every opener runs `data.db.migrate` (Swift: `Database.migrate`), and both keep the same `ADDED_COLUMNS` list.
- `tests/fixtures/sample_cases.json` and `media_cases.json`: golden privacy/sample-assembly and video-detection cases for the Swift sampler; regenerate with `uv run python -m tests.fixtures.sample_cases` after changing logger rules.

## Framework decision: MLX everywhere
All model code (HSMM now, GRU/hybrid later) is written in **MLX** so the GRU port is the same framework, with shared array code, no NumPy/MLX conversion at the boundary, and one set of tests/utilities.
- **Device:** MLX defaults to the GPU, which is the wrong choice for the small, sequential live-inference ops (HSMM forward step is ~720 multiply-adds; GRU single-step forward is tiny). Kernel launch/dispatch overhead would dominate. So **live/online inference pins to CPU**: `mx.set_default_device(mx.cpu)` or `with mx.stream(mx.cpu):` scoped to the live path.
- **Batched work** (nightly training backward pass, GRU training over many sequences, threshold-tuning replays) may use the GPU stream **only if a benchmark shows it wins**. Default to CPU; benchmark before switching. Keep device selection in one config/helper (e.g. `common/device.py`), never scattered `mx.gpu`/`mx.cpu` literals.
- MLX is lazy: call `mx.eval(...)` at the end of each live step (before reading values, e.g. for the nudge rule) so latency is measurable and graphs don't accumulate.
- Gotchas to respect: no in-place mutation of arrays (use `.at[]` updates or rebuild), float32 default (normalize every step / use log space to avoid underflow, as already required), and no `float64` on GPU (CPU stream supports it if HSMM numerics need it).
- Tests on synthetic data run on the CPU stream so they are deterministic.
- **Benchmark (2026-11-13, M2 Pro):** GPU is ~2.7x slower than CPU for the HSMM nightly update, EM and live step at 7-56 days (per-window `mx.eval` overhead dominates), so `TRAIN_DEVICE` stays `mx.cpu`. Report: `docs/benchmarks/2026-11-13-hsmm-train-device.md`; re-run with `uv run python -m hsmm.bench`.

# Project: Rabbit Hole Detector (working name TBD: Mettle / Metal Mind / Locus / Bare Metal)

## What this is
A fully on-device macOS app that notices when a distraction is turning into a rabbit hole and nudges me back **before** I lose the hour. All data, training, and inference stay on my Mac (Apple Silicon). No cloud calls except one-time model weight downloads.

Scope is intentionally narrow: not general "focus scoring," just rabbit hole detection.

## Core question the model answers
> "I'm N minutes into a distraction. What's the probability it becomes a rabbit hole?"

## Model strategy (read this first)
The **HSMM is the primary model** and gets built first. It has ~35 parameters, works with about a week of data, and is interpretable. The GRU has 2,000+ parameters and needs several weeks of data plus richer features before it beats the HSMM (in simulation it produced ~3x the false alarms with 3 days of training data and only matched the HSMM around 30 days). So:
1. Build the HSMM groundwork now, cleanly and well tested.
2. Leave a clear interface for the GRU/hybrid later. Do not build the GRU yet.

## Key definitions
- **Window:** a fixed 2-minute slice of time. The logger samples every 3-5 s; each window collapses to its dominant app category (most time). Also store per window: app switch count, idle seconds, and the fraction of samples with typing/click/scroll activity (no input hooks; see Logger (v1)).
- **Distraction session:** contiguous non-work windows, allowing a 1-window work blip.
- **Short break:** back to work within 10 min (5 windows).
- **Rabbit hole:** session lasts 20+ min (10 windows) OR chains through 3+ distinct distraction apps/sites without returning to work. (Thresholds tunable.)
- **Gray zone:** 10-20 min sessions with fewer than 3 apps stay unlabeled; the model decides them.
- **Drifting:** the first few windows of a rabbit hole (lead-in). Target zone for nudges, before the 20-min mark.
- **Away:** window with 90+ s idle (the longest unbroken stretch without input, not summed pauses), or laptop closed. Skip or label Away; never let it look like a rabbit hole.
- **Not working:** the popover's "Not working" switch (table `not_working`, written by the app). Windows mostly inside it are Away (`Window.not_working`), and nudges and check-ins are skipped while it is on, so leisure time never trains the model or triggers prompts.
  Idle time while the frontmost app plays video (it holds a `PreventUserIdleDisplaySleep` power assertion; `samples.media_playing`, see `logger/media.py`) is watching, not idle, so passive video can't hide as Away.

## Auto-labeler (rules, not ML)
Runs nightly in hindsight on categorized windows.
- Inputs: work categories (default {Coding, Docs}), BREAK_MAX = 5 windows, RABBIT_MIN = 10 windows, HOPS_MIN = 3, GAP_OK = 1.
- Short + returned to work -> Short break. Long or hoppy -> Drifting (first ~1/3, max 3 windows) then Rabbit hole. Gray zone and sessions cut off by end of day -> unlabeled. Remaining work windows -> Working.
- Check-ins and nudge responses override it (precedence: check-in > nudge response > auto-label). "Back to work" after a nudge -> label that session Drifting/Rabbit hole even if it was cut short; "I'm on break" -> Short break.
- Week 1: spot-check its labels against my own judgment; adjust thresholds if it disagrees with me more than ~1 in 5 times.
- The rules are fixed code, but all thresholds live in `labels/config.yaml` (BREAK_MAX, RABBIT_MIN, HOPS_MIN, GAP_OK, work categories). Never hard-code them.

**Label merge (nightly, runs after the auto-labeler)**
The auto-labeler is never modified by human labels; a separate merge step overlays them:
1. Start from auto-labels.
2. Overwrite with nudge responses for the windows they cover.
3. Overwrite with check-ins for the windows they cover (highest precedence).
4. Output final labels + a source column (auto | nudge | checkin) used for clamping strength and weighting in training.

**Threshold tuning (weekly, suggestion only)**
- Replay the auto-labeler over the last 2-4 weeks with a grid of thresholds (e.g. RABBIT_MIN 6-14 windows, BREAK_MAX 3-7, HOPS_MIN 2-4).
- For each setting, measure agreement with train-split check-ins (exclude eval-split check-ins).
- Report the best setting and its agreement vs the current one (e.g. "18 min fits your check-ins at 86% vs 83% for 20 min"). Apply only after I approve; log every change with date and agreement numbers.
- Needs ~30+ check-ins before suggesting anything.

## Check-ins (asking me what state I'm in)
The app occasionally asks me directly which state I'm in. These are the highest-quality labels in the system: they correct the auto-labeler, teach the model the ambiguous cases, and form the evaluation set.

**The prompt**
- A small, non-modal macOS notification or menu bar popover. Never steals focus, auto-dismisses after 60 s.
- Question: "What were you doing the last 10 min?" One tap: [Working] [Short break] [Drifting] [Rabbit hole] [Away] [Skip].
- Also log: response latency, and whether it was dismissed or skipped (a skip is not a label).
- Retrospective ("the last 10 min"), not momentary, so answering doesn't depend on the exact second the prompt arrives.

**When to ask (triggers, in priority order)**
1. **Uncertain:** the live HSMM's top state is below 55% for 2+ windows, or Short break vs Drifting are within 15 points of each other. This is active learning: ask where a label teaches the most.
2. **Gray zone:** a distraction session has passed 10 min but not 20 (the auto-labeler can't decide these).
3. **Ambiguous apps:** first 10+ min of the day on an app/site the categorizer marks low-confidence (e.g. YouTube without lecture keywords, Discord).
4. **Away return** (after Uncertain in the code): back from 16+ min Away that followed 4+ min of distraction (e.g. a phone rabbit hole). Asked once per such away, within 6 min of returning; covers the lead-in plus the away stretch; short aways never ask (`labels/config.yaml` `away_return`).
5. **Random:** a small number of prompts at uniformly random times, regardless of model state. These are kept separate and used **only for evaluation**, so the eval set isn't biased toward the cases the model finds hard.

**When NOT to ask**
- During a work stretch of 25+ min with steady typing (don't break real focus).
- Within 10 min of a nudge (the nudge response is already a label).
- In a meeting (calendar busy or video-call app frontmost), during quiet hours (default 12am-8am), or in fullscreen apps.

**Budget**
- Max 4 check-ins per day, at least 60 min apart. Of those, ~1 per day is a random (eval) prompt.
- Week 1: up to 6/day (cold start, labels matter most). After week 3: drop to 2/day. If I skip 3 in a row, back off to 1/day for the next 2 days.
- Daily review (optional, evening): a timeline of the day's auto-labeled sessions; tap any to correct it. Same label store.

**How check-in labels are used**
- Applied to the windows they cover (the last 5 windows for "last 10 min").
- Highest precedence: check-in > nudge response > auto-labeler. Clamped hard (1.0/0.0), unlike auto-labels (soft 0.9/0.1).
- Weighted 3x in the nightly counts relative to auto-labels.
- Random-trigger check-ins go to a held-out eval store and are never used for training.
- Track auto-labeler agreement with check-ins per week; if it drops below ~80%, flag that the thresholds need retuning.

**Storage (SQLite table `checkins`)**
`id, asked_at, trigger (uncertain|gray_zone|ambiguous_app|random|daily_review|away_return), window_start, window_end, model_top_state, model_top_prob, response (state|skip|dismissed), responded_at, split (train|eval)`

## HSMM specification
**States (K=4, +Away handling):** Working, Short break, Drifting, Rabbit hole.

**Observations per window:**
- App category o_t in {Coding, Docs, Chat, Video, Social} -> categorical emission B_j(o).
- App switch count binned {low, med, high} -> second categorical emission C_j(bin). Assumed independent of o_t given the state.
- Typing/click/scroll and context features are NOT HSMM inputs; keep them stored for the GRU.

**Parameters (fit nightly):**
- pi: starting probabilities (K).
- A: transitions K x K, diagonal fixed to 0 (staying is handled by durations).
- B: app emissions K x 5; C: switch-bin emissions K x 3.
- Durations: negative binomial per state, (mean mu_j, dispersion r_j), shifted so d >= 1, truncated at D = 30 windows (60 min) and renormalized.
- Hazard h_j(d) = p_j(d) / sum_{k>=d} p_j(k), with h_j(D) = 1. Derived, not stored as a parameter.

**Live inference (forward pass, every window, elapsed-duration formulation):**
- Belief table alpha_t(j, d), shape K x D.
- Step 1 (time passes): keep going alpha(j,d+1) += alpha(j,d)(1-h_j(d)); end and switch alpha(k,1) += sum_{j,d} alpha(j,d) h_j(d) A[j][k]. Window 1 uses pi instead.
- Step 2 (evidence): multiply by B_j(o_t) * C_j(bin_t).
- Step 3: normalize by c_t; keep log c_t (surprise, log-likelihood).
- Outputs: P(state), expected minutes in state, P(Drifting)+P(Rabbit hole) "risk", forecast k windows ahead (step 1 only), surprise.
- Nudge rule: risk > 0.5 for 2 consecutive windows, 10-window cooldown (user lowered it from 0.6 on 2026-11-17; `nudge/config.yaml`). Nudges stay on screen until answered or dismissed (no timeout; the app sets the Alerts notification style).

**Nightly training:**
- Forward pass + backward pass (beta) over each day; gamma = alpha * beta; xi for switches; n_j(d) for expected stretch lengths.
- Check-in windows are clamped hard (1.0/0.0) and weighted 3x; auto-labeled windows are clamped soft (0.9/0.1, so noisy auto-labels can be outvoted). Unlabeled windows use gamma. Eval-split check-ins are excluded from training.
- Evidence-weighted updates, today's tables as a prior: B tomorrow = (20*B_today + soft counts) / (20 + time in state); A with prior 10 switches; durations: add n_j(d) to 10*p_today(d), refit NB, recompute hazards; pi with prior 10 days. A state not visited that day must not change.
- Censoring: the stretch still running at end of day is excluded from duration counts.
- Use the last 2-4 weeks of data. Several random restarts if running unsupervised EM.

**Implementation notes:**
- Write in MLX from the start, pinned to the CPU stream (`mx.cpu`). Live updates are ~720 multiply-adds; GPU dispatch overhead would exceed the compute. Move batched training to the GPU stream only if benchmarks justify it (see "Framework decision: MLX everywhere").
- Normalize every step (or log space) to avoid underflow. Floor emissions so no cell is exactly 0.
- Validate on synthetic data sampled from known parameters before touching real logs: filtering accuracy, Viterbi accuracy, parameter recovery.

## Model lineup (compare on a held-out later week, never a random split)
1. Rules baseline (the auto-labeler's rules run live)
2. HMM (same states/emissions, geometric durations) - cheap baseline to justify the duration model
3. **HSMM (primary)**
4. LightGBM on hand-crafted features - later
5. GRU in MLX - later, after several weeks of data
6. Hybrid: GRU fed raw/context features + HSMM outputs (P(state), minutes in state, hazard, forecast, surprise) - later
Hybrid rules: use only the HSMM's live (past-only) outputs as features, and fit the HSMM on training days only.

## Feature split
| Feature | Goes to |
|---|---|
| App category | HSMM emission (and GRU) |
| App switches per window (binned) | HSMM emission (and GRU) |
| Idle seconds | Away handling (and GRU) |
| Typing, clicks, scrolling (fraction of active samples; no input hooks) | GRU only (store now) |
| Time of day, breaks this hour, minutes worked before, deadlines | GRU only for now (possible HSMM transition covariates later) |

## Evaluation
- Grade on my own labels, not on the auto-labeler's, to avoid rewarding the model for copying its rules. Primary eval set: random-trigger check-ins (unbiased). Secondary: nudge responses and daily review.
- Metrics: nudge precision, false alarms per day, lead time before the 20-min mark, rabbit holes caught, time lost to rabbit holes per week, log-likelihood on held-out days.
- Holdout: randomly skip ~10% of nudges so data on un-nudged behavior stays available.

## Notification
- Example: "This looks like a rabbit hole: YouTube -> Reddit -> YouTube, 12 min in. You usually don't come back from these. Head back to <last work context>?"
- Buttons: [Back to work] [I'm on break] [Snooze 20 min]. Responses are logged as labels.

## Architecture
| Piece | Hardware | Tech |
|---|---|---|
| Activity logger | CPU | Python + pyobjc (v1), SwiftUI menu bar app (v2) |
| Browser URLs | CPU | AppleScript polling (Safari, Chrome, Arc, Brave, Edge) |
| Storage | Disk | Local SQLite, encrypted |
| Categorizer (title/domain -> category) | rules first; later small embedding model on ANE (Core ML) or GPU (MLX) | |
| Windowing, auto-labeler | CPU | Python |
| HSMM (live + nightly) | CPU stream (`mx.cpu`) | MLX |
| GRU / hybrid (later) | CPU stream for live forward; GPU only for batched training if benchmarked | MLX |
| Nudge text (optional, later) | GPU | mlx-lm small local LLM |

## Privacy rules
- Skip private/incognito windows. Strip URL query strings; store domain + title by default.
- Never log password managers, banking, or work-confidential content (exclude IBM work apps/repos from anything demoed or open-sourced).
- Encrypt the SQLite store. Nothing leaves the machine.

## Build plan
1. **Week 1:** Logger + windowing running; collect data. Rules-based categorizer. Check-ins on (gray zone, ambiguous app and random triggers; up to 6/day). Build and test the HSMM on synthetic data in parallel.
2. **Week 2:** Auto-labeler + spot-check against check-ins; fit HSMM on real days; switch on the "uncertain" check-in trigger; rules baseline and HMM baseline; live nudges with feedback buttons.
3. **Week 3-4:** Nightly retraining loop; evaluation harness on a held-out week; tune nudge threshold.
4. **Later (several weeks of data):** LightGBM, GRU in MLX, hybrid; embedding categorizer; SwiftUI app; benchmarks.

## Suggested repo layout
```
logger/        sampler, browser URL capture, exclude lists
data/          SQLite schema, windowing (2-min windows + per-window stats)
labels/        config.yaml (thresholds), categorizer rules, auto-labeler, check-ins (triggers, budget, prompt UI hook),
               merge.py (overlay nudge responses + check-ins), tune.py (weekly threshold suggestions)
hsmm/          params.py (pi, A, B, C, NB durations, hazards)
               forward.py (live step, readouts, forecast)
               backward.py (beta, gamma, xi, n_j(d))
               train.py (nightly evidence-weighted update, clamping)
               simulate.py (sample synthetic days from known params)
baselines/     rules.py, hmm.py
nudge/         rule, cooldown, notification, response logging
eval/          held-out metrics, plots
models_later/  gru/ (empty stub with interface: features in -> P(rabbit hole) out)
tests/         synthetic recovery tests for hsmm/
```

## First task for Claude Code
1. Scaffold the repo with the layout above.
2. Write the v1 Python logger (frontmost app, window title, browser URL every 3-5 s; idle, switches, typing/click/scroll activity flags) writing to an encrypted local SQLite DB, with exclude lists for sensitive apps and private windows.
3. Write windowing (2-min windows with dominant category + stats), the auto-labeler (thresholds from config), the label merge step, and the check-in system (triggers, suppression rules, budget, `checkins` table; prompt via a simple macOS notification in v1) per the specs above.
4. Implement the HSMM in `hsmm/` per the spec: parameters, forward step, backward pass, nightly update, and a simulator. Add tests that sample synthetic days from known parameters and check filtering accuracy and parameter recovery.
5. Stub the GRU interface only.
