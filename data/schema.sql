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
