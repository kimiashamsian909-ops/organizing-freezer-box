-- Freezer sample tracker schema (SQLite)

CREATE TABLE IF NOT EXISTS members (
  id      INTEGER PRIMARY KEY,
  name    TEXT NOT NULL UNIQUE,
  color   TEXT NOT NULL,
  active  INTEGER NOT NULL DEFAULT 1
);

CREATE TABLE IF NOT EXISTS freezers (
  id           INTEGER PRIMARY KEY,
  name         TEXT NOT NULL,
  location     TEXT NOT NULL DEFAULT '',
  temperature  TEXT NOT NULL DEFAULT ''
);

CREATE TABLE IF NOT EXISTS racks (
  id          INTEGER PRIMARY KEY,
  freezer_id  INTEGER NOT NULL REFERENCES freezers(id),
  name        TEXT NOT NULL,
  claimed_by  INTEGER REFERENCES members(id),
  claim_note  TEXT NOT NULL DEFAULT ''
);

CREATE TABLE IF NOT EXISTS boxes (
  id          INTEGER PRIMARY KEY,
  rack_id     INTEGER NOT NULL REFERENCES racks(id),
  name        TEXT NOT NULL,
  kind        TEXT NOT NULL CHECK (kind IN ('grid9', 'grid10', 'list')),
  claimed_by  INTEGER REFERENCES members(id),
  claim_note  TEXT NOT NULL DEFAULT ''
);

CREATE TABLE IF NOT EXISTS samples (
  id          INTEGER PRIMARY KEY,
  box_id      INTEGER NOT NULL REFERENCES boxes(id),
  row         INTEGER,
  col         INTEGER,
  name        TEXT NOT NULL,
  type        TEXT NOT NULL DEFAULT '',
  date        TEXT NOT NULL DEFAULT '',
  owner_id    INTEGER REFERENCES members(id),
  notes       TEXT NOT NULL DEFAULT '',
  created_at  TEXT NOT NULL,
  updated_at  TEXT NOT NULL
);
-- NULL row/col (list boxes) are treated as distinct, so only grid spots are unique.
CREATE UNIQUE INDEX IF NOT EXISTS samples_spot ON samples(box_id, row, col);

CREATE TABLE IF NOT EXISTS reservations (
  id          INTEGER PRIMARY KEY,
  box_id      INTEGER NOT NULL REFERENCES boxes(id),
  row         INTEGER NOT NULL,
  col         INTEGER NOT NULL,
  member_id   INTEGER NOT NULL REFERENCES members(id),
  note        TEXT NOT NULL DEFAULT '',
  created_at  TEXT NOT NULL,
  UNIQUE (box_id, row, col)
);

CREATE TABLE IF NOT EXISTS history (
  id           INTEGER PRIMARY KEY,
  ts           TEXT NOT NULL,
  user         TEXT NOT NULL DEFAULT '',
  action       TEXT NOT NULL,
  entity       TEXT NOT NULL,
  entity_id    INTEGER,
  label        TEXT NOT NULL DEFAULT '',
  before_json  TEXT,
  after_json   TEXT
);
CREATE INDEX IF NOT EXISTS history_entity ON history(entity, entity_id);
