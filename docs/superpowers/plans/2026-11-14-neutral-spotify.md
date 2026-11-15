# Neutral Apps and Spotify Rule Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** System apps and search are neutral (Other, high confidence); Spotify is neutral until it is the dominant app for 3 consecutive windows, then those windows become Social.

**Architecture:** New `neutral` and `escalate` blocks in `labels/categories.yaml`, loaded into `Rules`. `categorize()` returns `Other/False` for both. A backward-looking post-pass in `data/windows.build_windows` turns the Nth+ window of a Spotify run into the escalation category.

**Tech Stack:** Python 3.13 (uv), PyYAML, pytest.

**Spec:** `docs/superpowers/specs/2026-11-14-neutral-spotify-design.md`

## Global Constraints
- `categorize(bundle_id, domain, title, rules, excluded=False) -> Categorization` signature unchanged.
- Category set unchanged: Coding, Docs, Chat, Video, Social, Other, Excluded. Window fields unchanged.
- Thresholds/lists live in yaml, never in code. `categories.local.yaml` extends `neutral` and `escalate` lists.
- Values: neutral bundle ids `com.apple.SecurityAgent, com.apple.systempreferences, com.apple.ActivityMonitor, com.metalfocus.menubar`; neutral domain `google.com`; escalate `category: Social, after_windows: 3, bundle_ids: [com.spotify.client], domains: [spotify.com]`.
- `spotify.com` removed from `domains.Video` and `low_confidence.domains`.
- Run tests with `uv run pytest`.

## Review Focus
- `docs.google.com` / `mail.google.com` must keep their own categories (neutral is checked only after normal rules miss). Test in Task 1.
- An Away window inside a Spotify run resets the counter. Test in Task 2.
- A Spotify-heavy window whose category is not Other (e.g. Coding wins on time) resets the counter. Test in Task 2.
- Excluded rows are never neutral/escalated (`dominant` is None). Test in Task 2.
- `open.spotify.com` (subdomain) counts as Spotify. Tests in Tasks 1 and 2.

---

### Task 1: Neutral and escalate rules in the categorizer

**Files:**
- Modify: `labels/categories.yaml`, `labels/categorizer.py`
- Test: `tests/labels/test_categorizer.py`

**Interfaces:**
- Produces: `Rules` gains `neutral_bundle_ids: frozenset[str]`, `neutral_domains: tuple[str, ...]`, `escalate_category: str | None`, `escalate_after: int`, `escalate_bundle_ids: frozenset[str]`, `escalate_domains: tuple[str, ...]`. New `is_escalating(dominant: str | None, rules: Rules) -> bool` (True if dominant equals an escalate bundle id or `domain_matches` an escalate domain).

- [ ] **Step 1: Write failing tests.** Add parametrized cases to `test_categorize`:
  `("com.apple.SecurityAgent", None, "x", Categorization("Other", False))`, `("com.apple.systempreferences", None, "Wi-Fi", Categorization("Other", False))`, `("com.apple.ActivityMonitor", None, "x", Categorization("Other", False))`, `("com.metalfocus.menubar", None, None, Categorization("Other", False))`, `("com.google.Chrome", "www.google.com", "q - Google Search", Categorization("Other", False))`, `("com.google.Chrome", "mail.google.com", "Inbox", Categorization("Chat", False))`, `("com.spotify.client", None, "Song", Categorization("Other", False))`, `("com.google.Chrome", "open.spotify.com", "Song", Categorization("Other", False))`.
  New `test_is_escalating(rules)`: True for `"com.spotify.client"` and `"open.spotify.com"`, False for `None`, `"com.microsoft.VSCode"`, `"youtube.com"`; `rules.escalate_category == "Social"` and `rules.escalate_after == 3`.
  New `test_local_rules_extend_neutral(tmp_path)`: local yaml `neutral:\n  bundle_ids: [com.apple.finder]\n` makes Finder `Other/False`.
- [ ] **Step 2:** `uv run pytest tests/labels/test_categorizer.py -v` -> new cases FAIL.
- [ ] **Step 3: Implement.** yaml blocks per Global Constraints. In `categorize`: after the domain loop misses, return `Other/False` if domain matches a neutral or escalate domain, else `Other/True`. After the bundle rules miss, return `Other/False` if bundle id is neutral or escalate. Validate `escalate.category` is in `CATEGORIES` (ValueError otherwise). Local file appends to the lists; `category`/`after_windows` come from base unless local overrides.
- [ ] **Step 4:** `uv run pytest tests/labels -v` -> PASS.
- [ ] **Step 5:** Commit `feat(labels): neutral apps and escalating (Spotify) rules`.

### Task 2: Escalation post-pass in windowing

**Files:**
- Modify: `data/windows.py` (`build_windows`)
- Test: `tests/data/test_windows.py`

**Interfaces:**
- Consumes: `is_escalating`, `rules.escalate_category`, `rules.escalate_after` from Task 1.

- [ ] **Step 1: Write failing tests** (use the existing `fill`/`build` helpers, 30 samples x 4 s = one full window):
  - `test_spotify_escalates_from_third_window`: 5 consecutive Spotify windows -> categories `["Other", "Other", "Social", "Social", "Social"]`, escalated windows have `low_confidence False`, `dominant == "com.spotify.client"`.
  - `test_spotify_web_counts`: 3 windows of Chrome on `open.spotify.com` -> last is Social.
  - `test_away_resets_spotify_run`: Spotify, Spotify, Away (idle_s=120), Spotify, Spotify -> no Social.
  - `test_other_category_window_resets_run`: Spotify, Spotify, Coding, Spotify, Spotify -> no Social.
  - `test_short_spotify_stays_neutral`: 2 Spotify windows -> both Other.
  - `test_excluded_never_escalates`: 3 windows of `excluded=1, bundle_id=None, title=None` -> all Excluded.
- [ ] **Step 2:** `uv run pytest tests/data/test_windows.py -v` -> new tests FAIL.
- [ ] **Step 3: Implement** a private `_escalate(windows: list[Window], rules: Rules) -> list[Window]` applied to `out` at the end of `build_windows`. Counter increments on a window with `not is_away and category == "Other" and is_escalating(dominant, rules)`, else resets to 0; when counter >= `rules.escalate_after`, `dataclasses.replace(w, category=rules.escalate_category, low_confidence=False)`. No-op when `escalate_category` is None.
- [ ] **Step 4:** `uv run pytest` (full suite, including `tests/data/test_windows_hsmm_contract.py`) -> PASS.
- [ ] **Step 5:** Commit `feat(windows): long Spotify stretches count as Social`.
