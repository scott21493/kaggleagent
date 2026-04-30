CREATE TABLE IF NOT EXISTS runs (
  run_id TEXT PRIMARY KEY,
  started_at TEXT NOT NULL,
  finished_at TEXT,
  status TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS experiments (
  experiment_id TEXT PRIMARY KEY,
  run_id TEXT NOT NULL,
  score REAL,
  metric_name TEXT NOT NULL,
  status TEXT NOT NULL
);
