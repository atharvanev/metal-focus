-- Bridge-created tables (design: docs/superpowers/specs/2026-11-13-menubar-app-design.md).
-- checkins / nudge_responses shapes are frozen; labels/ may later move this DDL into data/ unchanged.
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
