-- Fixed schema from the hackathon guide (Annex C). Do not rename or remove columns:
-- judges load their own students into these exact tables.

CREATE TABLE IF NOT EXISTS students (
    student_id       TEXT PRIMARY KEY CHECK (student_id GLOB 'S[0-9][0-9][0-9][0-9]'),
    full_name        TEXT NOT NULL,
    programme        TEXT NOT NULL,
    batch_year       INTEGER NOT NULL,
    current_semester INTEGER NOT NULL CHECK (current_semester BETWEEN 1 AND 10),
    cgpa             REAL NOT NULL CHECK (cgpa BETWEEN 0 AND 10),
    active_backlogs  INTEGER NOT NULL CHECK (active_backlogs >= 0),
    password_hash    TEXT  -- our addition: bcrypt hash, never a plain password (NULL = cannot log in)
);

CREATE TABLE IF NOT EXISTS courses (
    course_code TEXT PRIMARY KEY,
    course_name TEXT NOT NULL,
    programme   TEXT NOT NULL,
    semester    INTEGER NOT NULL,
    credits     INTEGER NOT NULL
);

-- Attendance percentage is never stored; tools compute it.
CREATE TABLE IF NOT EXISTS attendance (
    student_id       TEXT NOT NULL REFERENCES students(student_id),
    course_code      TEXT NOT NULL REFERENCES courses(course_code),
    classes_held     INTEGER NOT NULL CHECK (classes_held > 0),
    classes_attended INTEGER NOT NULL CHECK (classes_attended >= 0 AND classes_attended <= classes_held),
    PRIMARY KEY (student_id, course_code)
);

CREATE TABLE IF NOT EXISTS results (
    student_id     TEXT NOT NULL REFERENCES students(student_id),
    course_code    TEXT NOT NULL REFERENCES courses(course_code),
    exam_session   TEXT NOT NULL,
    exam_type      TEXT NOT NULL CHECK (exam_type IN ('REGULAR', 'SUPPLEMENTARY')),
    internal_marks INTEGER NOT NULL,
    external_marks INTEGER NOT NULL,
    total_marks    INTEGER NOT NULL,
    max_marks      INTEGER NOT NULL,
    result         TEXT NOT NULL CHECK (result IN ('PASS', 'FAIL', 'ABSENT', 'DETAINED')),
    CHECK (total_marks = internal_marks + external_marks),
    CHECK (total_marks BETWEEN 0 AND max_marks),
    UNIQUE (student_id, course_code, exam_session, exam_type)
);

-- The only place policy thresholds live. Each rule links to a document clause.
CREATE TABLE IF NOT EXISTS rule_registry (
    rule_id          TEXT PRIMARY KEY,
    description      TEXT NOT NULL,
    parameter        TEXT NOT NULL,
    operator         TEXT NOT NULL,
    value            TEXT NOT NULL,
    scope_programmes TEXT NOT NULL DEFAULT 'ALL',
    scope_batches    TEXT NOT NULL DEFAULT 'ALL',
    effective_from   TEXT NOT NULL,
    effective_to     TEXT NOT NULL DEFAULT '',
    source_doc_id    TEXT NOT NULL,
    source_section   TEXT NOT NULL
);

-- Our additions (allowed by the guide): document list and audit trail.
CREATE TABLE IF NOT EXISTS source_register (
    doc_id           TEXT PRIMARY KEY,
    title            TEXT NOT NULL,
    issuer           TEXT NOT NULL,
    authority_level  INTEGER NOT NULL CHECK (authority_level BETWEEN 1 AND 5),
    doc_type         TEXT NOT NULL,
    version          TEXT NOT NULL,
    effective_from   TEXT NOT NULL,
    effective_to     TEXT NOT NULL DEFAULT '',
    supersedes       TEXT NOT NULL DEFAULT '',
    scope_programmes TEXT NOT NULL DEFAULT 'ALL',
    scope_batches    TEXT NOT NULL DEFAULT 'ALL',
    provenance       TEXT NOT NULL,
    retrieved_on     TEXT NOT NULL DEFAULT '',
    synthetic        TEXT NOT NULL DEFAULT 'N',
    file_name        TEXT NOT NULL DEFAULT '',
    content_hash     TEXT NOT NULL DEFAULT '',
    chunks_indexed   INTEGER NOT NULL DEFAULT 0,
    flagged_chunks   INTEGER NOT NULL DEFAULT 0,
    ingested_at      TEXT NOT NULL DEFAULT ''
);

-- No names, marks, attendance values or chain-of-thought are stored here.
CREATE TABLE IF NOT EXISTS audit_log (
    trace_id            TEXT PRIMARY KEY,
    created_at          TEXT NOT NULL,
    student_id          TEXT,
    as_of_date          TEXT NOT NULL,
    question_type       TEXT,
    answer_type         TEXT NOT NULL,
    retrieved           TEXT NOT NULL DEFAULT '[]',
    selected_documents  TEXT NOT NULL DEFAULT '[]',
    precedence_decision TEXT NOT NULL DEFAULT '[]',
    tools               TEXT NOT NULL DEFAULT '[]',
    applied_rules       TEXT NOT NULL DEFAULT '[]',
    citation_check      TEXT NOT NULL DEFAULT '',
    flagged_content     INTEGER NOT NULL DEFAULT 0,
    model               TEXT NOT NULL DEFAULT '',
    llm_calls           INTEGER NOT NULL DEFAULT 0,
    latency_ms          INTEGER NOT NULL DEFAULT 0
);

-- Logged-out tokens (by their random jti) stay rejected until they would have expired anyway.
CREATE TABLE IF NOT EXISTS revoked_tokens (
    jti        TEXT PRIMARY KEY,
    expires_at INTEGER NOT NULL
);

-- Security events. Never passwords, password hashes or tokens.
CREATE TABLE IF NOT EXISTS security_log (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    created_at TEXT NOT NULL,
    event      TEXT NOT NULL,   -- login_success | login_failure | logout | unauthorized_request
    student_id TEXT,
    detail     TEXT NOT NULL DEFAULT ''
);
