# Authentication — final report (Phase 4A)

Completed and tested on 2026-10-06. Nothing has been committed or pushed.

## 1. Authentication architecture

```
Streamlit login form ──► POST /auth/login {student_id, password}
                              │  bcrypt.checkpw(password, students.password_hash)
                              │  (unknown ID → dummy bcrypt check: same time, same 401)
                              ▼
                  JWT HS256 {sub, iat, exp = +60 min, jti}  signed with JWT_SECRET
                              │
Every /ask, /audit, /auth/logout:  Authorization: Bearer <JWT>
                              │
                  get_token_claims() → signature + expiry + required claims + not revoked
                  get_current_student() → student still exists → AUTHENTICATED student ID
                              │
          run_question(question, as_of, authenticated_id)   ← the only way identity enters the graph
                              │
          LangGraph `authorize` (unchanged AUTHORIZATION) → tools / RAG / policy → citation check → answer
```

Authentication (who are you?) lives in `app/security/auth.py` and runs before the graph. Authorization (what may you see?) is the unchanged LangGraph `authorize` node. The two are separate, and the graph still refuses personal questions if no identity reaches it (defence in depth).

## 2. Files changed

| File | Change |
|---|---|
| `app/security/auth.py` | **New.** bcrypt hash/verify, JWT create/decode, `get_token_claims()`, `get_current_student()` |
| `app/api/routes.py` | `POST /auth/login`, `POST /auth/logout`; `/ask` and `/audit/{trace_id}` require a token; `X-Student-Id` parameter removed; `/audit` is owner-only |
| `app/models/schemas.py` | `LoginRequest` (extra fields forbidden), `LoginResponse` |
| `app/db/schema.sql`, `app/db/database.py` | `students.password_hash`, `revoked_tokens`, `security_log`; migration for older databases; `get_student()` never returns the hash |
| `app/config.py`, `.env.example` | `JWT_SECRET`, `JWT_EXPIRE_MINUTES`, `BCRYPT_ROUNDS`, `DEMO_STUDENT_PASSWORD`; `.env` reader now strips inline comments (bug found during this phase) |
| `app/main.py` | Warns at startup if `JWT_SECRET` is not set |
| `app/graph/state.py`, `nodes.py`, `workflow.py`, `app/services/understanding.py`, `app/tools/student_tools.py` | `header_student_id` → `auth_student_id`; comments state it comes only from a verified token. Logic unchanged |
| `scripts/load_students.py` | Students upserted in place (keeps hashes); demo or `credentials.csv` passwords hashed with bcrypt |
| `ui/streamlit_app.py` | Login screen, token in `st.session_state`, Bearer header, "Logged in as", Logout (revokes the token), expired session → back to login |
| `tests/conftest.py`, `tests/test_api.py` | Tests log in and send tokens; graph-level no-identity test kept |
| `tests/test_auth.py` | **New.** 15 authentication and security tests |
| `evaluation/run_eval.py`, `evaluation/questions.jsonl` | Evaluation logs in per student; Q26 is now sent with no token and must get 401 |
| `requirements.txt`, `docker-compose.yml`, `.github/workflows/ci.yml` | `PyJWT`, `bcrypt`; auth env vars; CI checks 401 without a token, then logs in |
| `README.md`, `docs/PHASE2_MVP_DESIGN.md` | Authentication section, demo credentials, curl demo, updated demo script; old design marked superseded for identity |
| `AUTH_INITIAL_STATUS.md` | Inspection report written before any change |

## 3. Database changes

| Table | Change |
|---|---|
| `students` | **+ `password_hash TEXT`** (nullable bcrypt hash; NULL = cannot log in). No column renamed or removed, so the judges' CSV schema still loads |
| `revoked_tokens` | New: `jti`, `expires_at` (logged-out tokens; expired rows cleaned up on each logout) |
| `security_log` | New: `created_at`, `event`, `student_id`, `detail` (no passwords, hashes or tokens) |

`init_db()` adds `password_hash` to existing databases with `ALTER TABLE`. This was verified on the existing local DB and on the Docker volume created before this phase. There is no second student database and no duplicated records.

## 4. API changes

| Endpoint | Before | Now |
|---|---|---|
| `POST /auth/login` | — | `{student_id, password}` → `{access_token, token_type: "bearer", expires_in, student_id}`; any failure → 401 `Invalid student ID or password.` |
| `POST /auth/logout` | — | Bearer token → 204; that token is revoked |
| `GET /auth/me` | — | Bearer token → the caller's own profile (ID, name, programme, batch, semester) for the UI; never the hash, never another student |
| `POST /ask` | Optional `X-Student-Id` header trusted | **Bearer token required** (401 otherwise); body = `{question, as_of_date?}` only; `X-Student-Id` ignored; `student_id` in body → 422 |
| `GET /audit/{trace_id}` | Open | Token required; only the caller's own records (others → 404) |
| `/ingest`, `/health`, `/sources`, `/metadata` | Open | Unchanged |

Behaviour change: guests can no longer ask general questions, because every `/ask` needs a login, as the Phase 4A brief requires.

## 5. Streamlit changes

Branded login card (Student ID + masked password, demo-accounts helper) → `POST /auth/login` → token stored in `st.session_state` (the password is never stored) → chat page with a profile card ("Logged in as: S1001", name and programme from `/auth/me`) and a welcome line → questions sent with `Authorization: Bearer <token>` only → Logout revokes the token and clears the session → a 401 (expired or revoked) sends the user back to the login screen.

## 6. Security controls

| # | Requirement | How |
|---|---|---|
| 1 | Password hashing | bcrypt, cost 12 (`BCRYPT_ROUNDS`), salted; max 72 bytes |
| 2 | Signed tokens | HS256 JWT with `JWT_SECRET`; `alg: none` and wrong-key tokens rejected |
| 3 | Expiration | `exp` = 60 min (`JWT_EXPIRE_MINUTES`); `sub`, `exp`, `iat`, `jti` required |
| 4 | Generic login error | One message and status for wrong password and unknown ID; a dummy bcrypt check keeps timing equal |
| 5 | Protected `/ask` | `Depends(get_current_student)` |
| 6 | No trust in client IDs | Header ignored, body field rejected, query ignored; IDs in the question only cause a refusal |
| 7 | No LLM-generated IDs | The LLM output only sets the question type, validated against a closed list |
| 8–10 | No passwords/hashes in logs or responses | Checked by tests and by scanning the API log; `get_student()` excludes the hash |
| 11 | Malformed/expired tokens | 401 + `WWW-Authenticate: Bearer`, logged as `unauthorized_request` |
| 12 | No-auth requests rejected | 401 |
| + | Logout really logs out | Token `jti` revoked server-side |
| + | Deleted student's token | Rejected (subject must still exist) |
| + | Audit privacy | `/audit` owner-only |

## 7. Test results (actually run)

| Check | Result |
|---|---|
| `pytest -q` | **73 passed** (16 auth tests + 57 existing tests updated to log in), exit code 0 |
| `ruff check .` | All checks passed |
| Evaluation, mock LLM | 29/30 correct, authorization 4/4 (Q26 → HTTP 401), unchanged from before |
| Evaluation, Ollama qwen2.5:3b | **30/30** correct; citations, abstention, authorization, tools, retrieval and conflicts all 100%; unchanged |
| curl cases 1–8 on the local API | 1 no token → 401 · 2 invalid token → 401 · 3 expired → 401 (pytest) · 4 S1001 token → S1001's data · 5 S1001 token + `X-Student-Id: S1002` → still S1001 (80%, never 77.5%) · 6 wrong password → 401 · 7 unknown student → 401, same message · 8 logout → 204, then /ask → 401 |
| API log scan for password, token, hash | 0 matches |
| Streamlit flow (AppTest against the live API) | Bad login refused; login shows "Logged in as: S1001"; password field masked (PASSWORD type) and not in session state; answer with citation; other-student question refused; Logout returns to the login screen with the token cleared |
| Docker (`docker compose up --build`) | Healthy; old volume migrated (40 hashes added); S1002 token + `X-Student-Id: S1001` → S1002's own result; no token → 401; UI 200 |

The new tests include all nine requested: `test_auth_login_success`, `test_auth_wrong_password`, `test_auth_unknown_student`, `test_auth_expired_token`, `test_auth_invalid_token`, `test_ask_requires_authentication`, `test_authenticated_student_identity`, `test_client_cannot_impersonate_other_student`, `test_audit_uses_authenticated_student_id`. They add tests for login case-insensitivity, malformed login, bcrypt storage, unknown-subject tokens, logout revocation, and a secret-free security log.

Re-verified on the running Docker stack after the UI redesign: cases 1–8 all pass, and the audit record of the impersonation attempt shows `S1001`. Fixed during that work: Docker Compose had picked up `.env`'s local `OLLAMA_BASE_URL`; the container now uses `DOCKER_OLLAMA_BASE_URL`.

## 8. Demo credentials

| Student ID | Password | Good for |
|---|---|---|
| S1001 | `Student@2026` | Attendance exactly 80%: eligible |
| S1002 | `Student@2026` | 77.5%: not eligible |
| S1008 | `Student@2026` | What-if placement |
| S1001–S1040 | `Student@2026` | All seeded students |

The demo password comes from `DEMO_STUDENT_PASSWORD` and is applied by `scripts/load_students.py` to students without a password. Only its bcrypt hash is stored.

## 9. Remaining limitations

- **One shared demo password** for all seeded students. That's fine for a demo; real use needs individual passwords (`credentials.csv` supports that already).
- **`JWT_SECRET` must be set** in `.env` for tokens to survive restarts. I generated a random one in the local `.env`, which is gitignored. If it's unset, a random per-process secret is used and a warning is logged.
- **No login rate limiting, lockout, password change or reset, and no admin/staff accounts.** `/ingest` stays open.
- **Tokens live in the Streamlit server session**, not in browser storage. Closing the tab ends the session; the token itself expires after 60 minutes or on logout.
- Revocation is stored in SQLite, so it works with one API instance, which is our deployment.
- An intermittent `libc++abi … recursive_mutex` message has been seen twice when a test process exits on macOS. It comes from the embedding runtime shutting down, after all tests have passed, and doesn't come from authentication.
- The GitHub Actions workflow is updated and its Docker check was simulated locally, but it hasn't run on GitHub (nothing pushed).

---

## AUTHENTICATION STATUS

- [x] Login works
- [x] Password hashing works (bcrypt)
- [x] JWT/session works (signed, expiring, revocable)
- [x] `/ask` protected
- [x] Student ID comes from authenticated identity
- [x] Impersonation prevented (header, body and query attempts all fail)
- [x] Streamlit login works
- [x] Logout works (client and server side)
- [x] Audit integration works (audit uses the authenticated ID; security events logged)
- [x] Tests pass (73/73)
- [x] README updated

**Phase 4A: COMPLETE.** All mandatory items were tested and pass.
