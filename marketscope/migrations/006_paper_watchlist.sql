-- Separate paper ledger. Existing signals table and its outcome tracker are unchanged.
CREATE TABLE IF NOT EXISTS paper_watch_trades (
 id TEXT PRIMARY KEY, day TEXT NOT NULL, symbol TEXT NOT NULL,
 status TEXT NOT NULL CHECK(status IN ('PENDING','OPEN','CLOSED','EXPIRED')),
 payload TEXT NOT NULL
);
CREATE UNIQUE INDEX IF NOT EXISTS paper_watch_one_active ON paper_watch_trades(day,symbol)
 WHERE status IN ('PENDING','OPEN');
CREATE TABLE IF NOT EXISTS paper_watch_events (
 id TEXT PRIMARY KEY, day TEXT NOT NULL, at TEXT NOT NULL, kind TEXT NOT NULL,
 symbol TEXT NOT NULL, payload TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS paper_watch_event_day ON paper_watch_events(day,at);
CREATE TABLE IF NOT EXISTS paper_watch_reports (
 day TEXT PRIMARY KEY, generated_at TEXT NOT NULL, payload TEXT NOT NULL
);
