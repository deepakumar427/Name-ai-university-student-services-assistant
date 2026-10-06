# Authentication — initial status (Phase 4A inspection)

Inspected on 2026-10-06, before any change.

## 1. Current authentication mechanism

**There is none.** `POST /ask` reads an optional `X-Student-Id` header (`app/api/routes.py`, `ask()`) and trusts it as the student's identity. Nothing proves the caller *is* that student: anyone can send `X-Student-Id: S1002` and read S1002's attendance, results and eligibility.

What already exists, and must be kept, is **authorization**: once an identity is given, the graph refuses requests about other students, refuses personal questions with no identity, and tools never take a student ID argument.

## 2. Current student identity flow

```
Streamlit text box "Student ID"  ──►  X-Student-Id header
   └─► routes.ask(x_student_id)
        └─► workflow.run_question(question, as_of, student_id)
             └─► graph state "header_student_id"
                  ├─ nodes.understand  → understanding.understand(header_id): "other student mentioned?"
                  ├─ nodes.authorize   → database.get_student(header) → profile / refused / clarification
                  ├─ resolve_policy/run_tools → ToolContext(student_id from profile)
                  ├─ nodes.validate_citations → leaks_other_student(draft, header_student_id)
                  └─ workflow._audit   → audit_log.student_id
```

Identity never comes from the request body (`AskRequest` has `extra="forbid"`, so a `student_id` field is rejected with 422), query parameters, or the LLM (student IDs and names are detected by regex and only ever cause a refusal).

## 3. Files that need modification

| File | Change |
|---|---|
| `app/db/schema.sql`, `app/db/database.py` | `students.password_hash` column (+ migration for existing DBs), `revoked_tokens`, `security_log` tables, credential queries |
| `app/security/auth.py` (new) | bcrypt hashing, JWT create/verify, `get_current_student()` dependency |
| `app/config.py`, `.env.example` | `JWT_SECRET`, `JWT_EXPIRE_MINUTES`, `BCRYPT_ROUNDS`, `DEMO_STUDENT_PASSWORD` |
| `app/api/routes.py` | `POST /auth/login`, `POST /auth/logout`, `/ask` and `/audit` protected; `X-Student-Id` removed |
| `app/graph/state.py`, `nodes.py`, `workflow.py`, `app/services/understanding.py` | rename `header_student_id` → `auth_student_id` (it now always comes from a verified token) |
| `scripts/load_students.py` | set password hashes for loaded students; keep hashes when students are reloaded |
| `ui/streamlit_app.py` | login screen, token in session state, logout, Bearer header |
| `tests/*`, `evaluation/run_eval.py` | log in and use tokens instead of the header |
| `requirements.txt`, `README.md`, `.github/workflows/ci.yml`, `docker-compose.yml` | `PyJWT`, docs, CI/compose env vars |

## 4. Security risks in the current implementation

| # | Risk | Severity |
|---|---|---|
| R1 | Impersonation: any client can claim any student ID in `X-Student-Id` | Critical |
| R2 | `GET /audit/{trace_id}` is open: anyone with a trace ID sees whose request it was | Medium (IDs are random UUIDs) |
| R3 | `POST /ingest` is open: anyone can add documents | Medium (out of scope for 4A, documented) |
| R4 | The UI lets the user type any student ID | Critical (same as R1) |
| R5 | No record of failed or unauthenticated access | Low |

## 5. Proposed minimal design

```
POST /auth/login {student_id, password}
   └─ bcrypt.checkpw(password, students.password_hash)   (dummy check for unknown IDs: same timing, same 401)
   └─ JWT HS256 {sub: student_id, iat, exp (60 min), jti}  signed with JWT_SECRET
Authorization: Bearer <JWT>  ──►  get_current_student()
   └─ verify signature + expiry → jti not revoked → student still exists → student_id
   └─ /ask passes that ID to the unchanged graph; authorization logic unchanged
POST /auth/logout  → jti stored in revoked_tokens → the token stops working
```

- **Passwords:** bcrypt hashes in a new nullable `students.password_hash` column (the guide allows adding columns; the judges' CSV schema is unchanged). The loader gives every student without a hash the demo password (`DEMO_STUDENT_PASSWORD`), or a per-student password from an optional `credentials.csv`. Reloading a student keeps their existing hash.
- **`/ask` requires a token for every question.** Guests can no longer ask general questions; this follows the Phase 4A requirement "no token → 401".
- **`X-Student-Id` is ignored completely.** There is no test-only bypass: tests and the evaluation log in like the UI does.
- **`/audit/{trace_id}`** requires a token and returns only the caller's own records (others → 404).
- **Security events** (`login_success`, `login_failure`, `logout`, `unauthorized_request`) go to a `security_log` table, with no passwords, hashes or tokens.
- **Dependencies:** `bcrypt` (already installed via ChromaDB) and `PyJWT` (new, small, pure Python).
