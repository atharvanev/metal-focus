# Neutral apps and the Spotify rule (short design)

## Intent
About 16% of logged time is "Other", mostly system apps and search. These are neither work nor
distraction (user decision, 2026-11-14), and they should stop triggering "ambiguous app" check-ins.
Spotify is neutral when it's briefly in front (picking a song) but a distraction when the user stays
on it (user: "if I'm on it a long time then it is distraction"; categorize as Social, after 3 windows / 6 min).

## Goals / non-goals
- Goal: a `neutral` rule list -> `Categorization("Other", low_confidence=False)`.
- Goal: Spotify (app `com.spotify.client`, site `spotify.com`) is neutral, but from the 3rd consecutive
  Spotify-dominant window onward that window's category becomes Social, which the HSMM sees.
- Non-goal: changing the category set, Window fields or the schema. Non-goal: audio as a media signal
  (Spotify idle in front still becomes Away after 90 s; agreed).

## Approaches
- **A (picked): post-pass in `data/windows.build_windows`.** After windows are built, count runs of
  consecutive non-Away windows whose `dominant` is an escalating app; the Nth onward get the escalation
  category. Only looks backwards, so live and nightly agree and it is deterministic per day.
- B: auto-labeler rule only. Rejected: HSMM would never see it.
- C: inside per-sample `categorize()`. Rejected: needs history, breaks the per-sample contract.

## Design
- `labels/categories.yaml`:
  - `neutral: {bundle_ids: [com.apple.SecurityAgent, com.apple.systempreferences, com.apple.ActivityMonitor,
    com.metalfocus.menubar], domains: [google.com]}`. Checked only after the normal domain/bundle rules
    miss, so `docs.google.com` stays Docs.
  - `escalate: {category: Social, after_windows: 3, bundle_ids: [com.spotify.client], domains: [spotify.com]}`.
    `spotify.com` is removed from Video and from low_confidence. Before escalation these rows are
    `Other`, high confidence.
  - `categories.local.yaml` can extend both lists.
- `labels/categorizer.Rules` gains `neutral_*`, `escalate_*` fields; `categorize()` keeps its signature.
  New helper `is_escalating(dominant, rules)`.
- `data/windows.build_windows`: after building, a run counter resets on any window that is Away or whose
  dominant isn't escalating; when the counter reaches `after_windows`, that and later windows in the run
  get `category=escalate.category`, `low_confidence=False` (via `dataclasses.replace`). Runs reset at
  local midnight (windows are per day); accepted.
- Tell the HSMM side that Social emissions will now include long Spotify stretches.

## Left to the builder
Exact test cases; the bundle ids above should be confirmed against real `samples`.
