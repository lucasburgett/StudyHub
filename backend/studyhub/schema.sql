-- StudyHub local store. Applied idempotently at startup.

CREATE TABLE IF NOT EXISTS meta (
  key   TEXT PRIMARY KEY,
  value TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS courses (
  id                INTEGER PRIMARY KEY,
  code              TEXT NOT NULL,          -- display form, "CS 231N"
  code_key          TEXT NOT NULL UNIQUE,   -- matching form, "CS231N"
  title             TEXT,
  term              TEXT,
  term_start        TEXT,                   -- YYYY-MM-DD
  canvas_id         TEXT UNIQUE,
  gradescope_id     TEXT UNIQUE,
  granola_folder_id TEXT,
  site_url          TEXT,
  canvas_url        TEXT,
  created_at        TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS lectures (
  id        INTEGER PRIMARY KEY,
  course_id INTEGER NOT NULL REFERENCES courses(id) ON DELETE CASCADE,
  number    INTEGER,
  date      TEXT,                           -- YYYY-MM-DD, local time zone
  title     TEXT,
  UNIQUE (course_id, number),
  UNIQUE (course_id, date)
);

-- The course's lecture plan, from the syllabus or a CSV. Anchors lecture numbers and dates.
CREATE TABLE IF NOT EXISTS schedule (
  course_id INTEGER NOT NULL REFERENCES courses(id) ON DELETE CASCADE,
  number    INTEGER NOT NULL,
  date      TEXT,
  title     TEXT,
  PRIMARY KEY (course_id, number)
);

CREATE TABLE IF NOT EXISTS resources (
  id           INTEGER PRIMARY KEY,
  course_id    INTEGER NOT NULL REFERENCES courses(id) ON DELETE CASCADE,
  lecture_id   INTEGER REFERENCES lectures(id) ON DELETE SET NULL,
  source       TEXT NOT NULL,               -- canvas | gradescope | goodnotes | granola
  kind         TEXT NOT NULL,               -- slides | file | page | spec | notes | transcript | submission | announcement
  external_id  TEXT NOT NULL,
  title        TEXT NOT NULL,
  url          TEXT,
  occurred_at  TEXT,
  content_hash TEXT,
  file_path    TEXT,                        -- relative to the data dir
  page_count   INTEGER,
  duration_min INTEGER,
  markdown     TEXT,
  summary      TEXT,
  meta_json    TEXT NOT NULL DEFAULT '{}',
  synced_at    TEXT NOT NULL,
  UNIQUE (source, external_id)
);
CREATE INDEX IF NOT EXISTS resources_course ON resources(course_id, kind);
CREATE INDEX IF NOT EXISTS resources_lecture ON resources(lecture_id);

-- Search units: one per PDF page, one per transcript window, several per text document.
CREATE TABLE IF NOT EXISTS chunks (
  id          INTEGER PRIMARY KEY,
  resource_id INTEGER NOT NULL REFERENCES resources(id) ON DELETE CASCADE,
  seq         INTEGER NOT NULL,
  page        INTEGER,
  seconds     INTEGER,
  lecture_id  INTEGER REFERENCES lectures(id) ON DELETE SET NULL,
  header      TEXT NOT NULL,                -- "CS 231N · Lecture 3 · transcript · 41:12"
  text        TEXT NOT NULL,
  image_hash  TEXT,                         -- page image hash, for handwriting transcription
  transcribed INTEGER NOT NULL DEFAULT 0,   -- 1 when text came from Claude vision
  UNIQUE (resource_id, seq)
);
CREATE INDEX IF NOT EXISTS chunks_lecture ON chunks(lecture_id);

CREATE VIRTUAL TABLE IF NOT EXISTS chunks_fts USING fts5(
  header, text, content='chunks', content_rowid='id', tokenize='porter unicode61'
);
CREATE TRIGGER IF NOT EXISTS chunks_ai AFTER INSERT ON chunks BEGIN
  INSERT INTO chunks_fts(rowid, header, text) VALUES (new.id, new.header, new.text);
END;
CREATE TRIGGER IF NOT EXISTS chunks_ad AFTER DELETE ON chunks BEGIN
  INSERT INTO chunks_fts(chunks_fts, rowid, header, text) VALUES ('delete', old.id, old.header, old.text);
END;
CREATE TRIGGER IF NOT EXISTS chunks_au AFTER UPDATE ON chunks BEGIN
  INSERT INTO chunks_fts(chunks_fts, rowid, header, text) VALUES ('delete', old.id, old.header, old.text);
  INSERT INTO chunks_fts(rowid, header, text) VALUES (new.id, new.header, new.text);
END;

CREATE TABLE IF NOT EXISTS assignments (
  id               INTEGER PRIMARY KEY,
  course_id        INTEGER NOT NULL REFERENCES courses(id) ON DELETE CASCADE,
  source           TEXT NOT NULL,           -- canvas | gradescope
  external_id      TEXT NOT NULL,
  title            TEXT NOT NULL,
  due_at           TEXT,
  points           REAL,
  score            REAL,
  status           TEXT NOT NULL DEFAULT 'unknown',
  url              TEXT,
  spec_resource_id INTEGER REFERENCES resources(id) ON DELETE SET NULL,
  hidden           INTEGER NOT NULL DEFAULT 0, -- 1 when a Gradescope twin replaces this Canvas row
  synced_at        TEXT NOT NULL,
  UNIQUE (source, external_id)
);
CREATE INDEX IF NOT EXISTS assignments_course ON assignments(course_id, due_at);

CREATE TABLE IF NOT EXISTS feedback (
  id                INTEGER PRIMARY KEY,
  assignment_id     INTEGER NOT NULL REFERENCES assignments(id) ON DELETE CASCADE,
  seq               INTEGER NOT NULL,
  question          TEXT NOT NULL,
  score             REAL,
  max_score         REAL,
  rubric_items_json TEXT NOT NULL DEFAULT '[]',
  comment           TEXT,
  UNIQUE (assignment_id, seq)
);

CREATE TABLE IF NOT EXISTS sync_runs (
  id            INTEGER PRIMARY KEY,
  source        TEXT NOT NULL,
  started_at    TEXT NOT NULL,
  finished_at   TEXT,
  status        TEXT NOT NULL,              -- running | ok | error
  items_changed INTEGER NOT NULL DEFAULT 0,
  warnings_json TEXT NOT NULL DEFAULT '[]',
  error         TEXT
);

CREATE TABLE IF NOT EXISTS threads (
  id         INTEGER PRIMARY KEY,
  title      TEXT NOT NULL,
  scope_json TEXT NOT NULL DEFAULT '{}',
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS messages (
  id             INTEGER PRIMARY KEY,
  thread_id      INTEGER NOT NULL REFERENCES threads(id) ON DELETE CASCADE,
  role           TEXT NOT NULL,             -- user | assistant
  text           TEXT NOT NULL,
  api_json       TEXT NOT NULL DEFAULT '[]', -- Messages API turns produced by this message
  tools_json     TEXT NOT NULL DEFAULT '[]',
  citations_json TEXT NOT NULL DEFAULT '{}',
  created_at     TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS messages_thread ON messages(thread_id, id);
