CREATE TABLE IF NOT EXISTS samples (
  id INTEGER PRIMARY KEY,
  ts REAL NOT NULL, tz_offset_min INTEGER NOT NULL, dt REAL NOT NULL,
  bundle_id TEXT, app_name TEXT, window_title TEXT, domain TEXT,
  private INTEGER, excluded INTEGER NOT NULL, fullscreen INTEGER, locked INTEGER NOT NULL,
  idle_s REAL, app_switches INTEGER NOT NULL,
  key_active INTEGER, click_active INTEGER, scroll_active INTEGER
);
CREATE INDEX IF NOT EXISTS samples_ts ON samples(ts);
CREATE TABLE IF NOT EXISTS events (
  id INTEGER PRIMARY KEY, ts REAL NOT NULL,
  kind TEXT NOT NULL CHECK (kind IN ('logger_start','logger_stop','sleep','wake',
        'screens_sleep','screens_wake','lock','unlock','gap','permission_missing')),
  detail TEXT
);
CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);
INSERT OR IGNORE INTO meta (key, value) VALUES ('schema_version', '1');
-- Shared with bridge/schema.sql (menubar-app). Keep the two copies identical (agreed 2026-11-13).
CREATE TABLE IF NOT EXISTS checkins (
  id INTEGER PRIMARY KEY, asked_at REAL NOT NULL,
  trigger TEXT NOT NULL CHECK (trigger IN ('uncertain','gray_zone','ambiguous_app','random','daily_review')),
  window_start REAL, window_end REAL, model_top_state TEXT, model_top_prob REAL,
  response TEXT CHECK (response IS NULL OR response IN ('Working','Short break','Drifting','Rabbit hole','Away','skip','dismissed')),
  responded_at REAL, split TEXT NOT NULL CHECK (split IN ('train','eval')));
CREATE TABLE IF NOT EXISTS nudge_responses (
  id INTEGER PRIMARY KEY, nudge_id TEXT, asked_at REAL NOT NULL, responded_at REAL,
  response TEXT CHECK (response IS NULL OR response IN ('back_to_work','on_break','snooze','dismissed')),
  response_latency_s REAL, window_start REAL, window_end REAL);
CREATE TABLE IF NOT EXISTS notify_outbox (
  id INTEGER PRIMARY KEY, created_at REAL NOT NULL,
  kind TEXT CHECK (kind IN ('checkin','nudge')), ref_id INTEGER,
  payload_json TEXT NOT NULL DEFAULT '{}', delivered_at REAL, expires_at REAL NOT NULL);
