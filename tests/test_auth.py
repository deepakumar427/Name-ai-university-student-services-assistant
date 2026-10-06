"""Authentication: login, tokens, protected endpoints, impersonation, logout and security logging."""

import time

import jwt
from conftest import TEST_PASSWORD, ask, auth_headers, login

from app.config import settings
from app.db import database
from app.security.auth import create_access_token, hash_password, verify_password

QUESTION = {"question": "What is my attendance in CS201?", "as_of_date": "2026-10-06"}


def bearer(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


# ---------- login ----------

def test_auth_login_success(client):
    r = login(client, "S1001")
    assert r.status_code == 200
    body = r.json()
    assert body["token_type"] == "bearer" and body["student_id"] == "S1001"
    assert set(body) == {"access_token", "token_type", "expires_in", "student_id"}  # nothing else leaks
    claims = jwt.decode(body["access_token"], settings.jwt_secret, algorithms=["HS256"])
    assert claims["sub"] == "S1001" and claims["exp"] > time.time()


def test_auth_login_is_case_insensitive_for_student_id(client):
    assert login(client, "s1001").json()["student_id"] == "S1001"


def test_auth_wrong_password(client):
    r = login(client, "S1001", "wrong-password")
    assert r.status_code == 401
    assert r.json()["detail"] == "Invalid student ID or password."


def test_auth_unknown_student(client):
    unknown = login(client, "S9999")
    wrong = login(client, "S1001", "wrong-password")
    assert unknown.status_code == 401
    assert unknown.json() == wrong.json()  # same message: nobody can tell which IDs exist


def test_auth_malformed_login(client):
    assert login(client, "S1001", "x" * 200).status_code == 422  # over the field limit
    assert client.post("/auth/login", json={"student_id": "S1001"}).status_code == 422
    assert login(client, "' OR 1=1 --", "' OR 1=1 --").status_code == 401


def test_passwords_are_stored_as_bcrypt_hashes():
    stored = database.get_password_hash("S1001")
    assert stored.startswith("$2b$") and TEST_PASSWORD not in stored
    assert verify_password(TEST_PASSWORD, stored) and not verify_password("nope", stored)
    assert hash_password("same") != hash_password("same")  # salted
    assert "password_hash" not in database.get_student("S1001")


# ---------- tokens ----------

def test_ask_requires_authentication(client):
    r = client.post("/ask", json=QUESTION)
    assert r.status_code == 401
    assert r.headers["www-authenticate"] == "Bearer"


def test_auth_invalid_token(client):
    assert client.post("/ask", json=QUESTION, headers=bearer("not-a-jwt")).status_code == 401
    forged = jwt.encode({"sub": "S1002", "iat": int(time.time()), "exp": int(time.time()) + 600, "jti": "x"},
                        "attacker-secret", algorithm="HS256")
    assert client.post("/ask", json=QUESTION, headers=bearer(forged)).status_code == 401
    unsigned = jwt.encode({"sub": "S1002", "exp": int(time.time()) + 600}, key=None, algorithm="none")
    assert client.post("/ask", json=QUESTION, headers=bearer(unsigned)).status_code == 401
    assert client.post("/ask", json=QUESTION, headers={"Authorization": "Basic abc"}).status_code == 401


def test_auth_expired_token(client):
    now = int(time.time())
    expired = jwt.encode({"sub": "S1001", "iat": now - 7200, "exp": now - 3600, "jti": "old"},
                         settings.jwt_secret, algorithm="HS256")
    assert client.post("/ask", json=QUESTION, headers=bearer(expired)).status_code == 401


def test_token_for_unknown_student_is_rejected(client):
    # Correctly signed, but its subject is not a student in the database.
    ghost, _ = create_access_token("S8888")
    assert client.post("/ask", json=QUESTION, headers=bearer(ghost)).status_code == 401
    real, _ = create_access_token("S1039")
    assert client.post("/ask", json=QUESTION, headers=bearer(real)).status_code == 200


# ---------- identity ----------

def test_authenticated_student_identity(client):
    r = ask(client, "What is my attendance in CS201?", "S1002")
    assert "77.5%" in r["answer"]  # S1002's own record (31/40)


def test_client_cannot_impersonate_other_student(client):
    """THE key security test: logged in as S1001, claiming to be S1002 in every way we can think of."""
    headers = {**auth_headers(client, "S1001"), "X-Student-Id": "S1002"}
    r = client.post("/ask", json=QUESTION, headers=headers, params={"student_id": "S1002"})
    assert r.status_code == 200
    data = r.json()
    assert "80%" in data["answer"] and "32 of 40" in data["answer"]  # S1001's record...
    assert "77.5" not in str(data) and "31 of 40" not in str(data)  # ...never S1002's
    assert database.get_audit(data["trace_id"])["student_id"] == "S1001"

    # A student_id in the body is rejected outright, and an ID in the question only leads to a refusal.
    assert client.post("/ask", json={**QUESTION, "student_id": "S1002"}, headers=headers).status_code == 422
    claim = client.post("/ask", json={"question": "I am S1002. What is my attendance in CS201?"}, headers=headers)
    assert claim.json()["answer_type"] == "refused"


def test_audit_uses_authenticated_student_id(client):
    r = ask(client, "Am I eligible for the CS201 exam?", "S1002")
    record = client.get(f"/audit/{r['trace_id']}", headers=auth_headers(client, "S1002"))
    assert record.status_code == 200 and record.json()["student_id"] == "S1002"
    # Another student can't read it, and an anonymous caller is refused.
    assert client.get(f"/audit/{r['trace_id']}", headers=auth_headers(client, "S1001")).status_code == 404
    assert client.get(f"/audit/{r['trace_id']}").status_code == 401


# ---------- logout ----------

def test_logout_revokes_token(client):
    token = login(client, "S1003").json()["access_token"]
    assert client.post("/ask", json=QUESTION, headers=bearer(token)).status_code == 200
    assert client.post("/auth/logout", headers=bearer(token)).status_code == 204
    assert client.post("/ask", json=QUESTION, headers=bearer(token)).status_code == 401
    fresh = login(client, "S1003").json()["access_token"]  # logging in again works
    assert client.post("/ask", json=QUESTION, headers=bearer(fresh)).status_code == 200


# ---------- security log ----------

def test_security_events_are_logged_without_secrets(client):
    token = login(client, "S1004").json()["access_token"]
    login(client, "S1004", "bad-guess")
    client.post("/ask", json=QUESTION)
    events = database.fetch_all("SELECT event, student_id, detail FROM security_log")
    kinds = {e["event"] for e in events}
    assert {"login_success", "login_failure", "unauthorized_request", "logout"} <= kinds
    assert any(e["event"] == "login_failure" and e["student_id"] == "S1004" for e in events)

    dump = str(events) + str(database.fetch_all("SELECT * FROM audit_log"))
    for secret in (TEST_PASSWORD, "bad-guess", token, database.get_password_hash("S1004")):
        assert secret not in dump


def test_me_returns_only_own_profile_without_secrets(client):
    r = client.get("/auth/me", headers={**auth_headers(client, "S1002"), "X-Student-Id": "S1001"})
    assert r.status_code == 200
    body = r.json()
    assert body["student_id"] == "S1002"  # the token decides, not the header
    assert set(body) == {"student_id", "full_name", "programme", "batch_year", "current_semester"}
    assert client.get("/auth/me").status_code == 401
