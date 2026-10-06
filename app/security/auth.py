"""Authentication: WHO is this user? (Authorization — what they may see — stays in the graph.)

Student ID + password  ->  bcrypt check  ->  signed JWT {sub, iat, exp, jti}
Authorization: Bearer <JWT>  ->  get_current_student()  ->  verified student ID

The verified ID is the only identity the app uses. A student ID in a header, the request body,
a query parameter or the LLM's output is never trusted.
"""

import logging
import secrets
import time

import bcrypt
import jwt
from fastapi import Depends, HTTPException, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from app.config import settings
from app.db import database

log = logging.getLogger(__name__)
ALGORITHM = "HS256"
MAX_PASSWORD_BYTES = 72  # bcrypt only uses the first 72 bytes; longer passwords are refused
GENERIC_LOGIN_ERROR = "Invalid student ID or password."
GENERIC_TOKEN_ERROR = "Not authenticated. Please log in again."

_bearer = HTTPBearer(auto_error=False)
# Used when the student ID is unknown, so a failed login takes the same time either way.
_DUMMY_HASH = bcrypt.hashpw(b"not-a-real-password", bcrypt.gensalt(rounds=settings.bcrypt_rounds))



# ---------- passwords ----------

def hash_password(password: str) -> str:
    data = password.encode("utf-8")
    if not data or len(data) > MAX_PASSWORD_BYTES:
        raise ValueError(f"password must be 1-{MAX_PASSWORD_BYTES} bytes")
    return bcrypt.hashpw(data, bcrypt.gensalt(rounds=settings.bcrypt_rounds)).decode("ascii")


def verify_password(password: str, password_hash: str | None) -> bool:
    """Always runs one bcrypt check (against a dummy hash if needed), so timing doesn't reveal anything."""
    data = password.encode("utf-8")
    usable = password_hash is not None and 0 < len(data) <= MAX_PASSWORD_BYTES
    try:
        ok = bcrypt.checkpw(data[:MAX_PASSWORD_BYTES], password_hash.encode("ascii") if usable else _DUMMY_HASH)
    except ValueError:  # a corrupt stored hash
        ok = False
    return ok and usable


def authenticate(student_id: str, password: str) -> str | None:
    """Return the canonical student ID if the credentials are right, else None (no reason given)."""
    sid = student_id.strip().upper()
    if not verify_password(password, database.get_password_hash(sid)):
        return None
    return sid


# ---------- tokens ----------

def create_access_token(student_id: str) -> tuple[str, int]:
    """Return (token, lifetime in seconds)."""
    now = int(time.time())
    lifetime = settings.jwt_expire_minutes * 60
    claims = {"sub": student_id, "iat": now, "exp": now + lifetime, "jti": secrets.token_hex(16)}
    return jwt.encode(claims, settings.jwt_secret, algorithm=ALGORITHM), lifetime


def decode_token(token: str) -> dict:
    """Verify signature and expiry. Raises jwt.PyJWTError on any problem."""
    return jwt.decode(token, settings.jwt_secret, algorithms=[ALGORITHM],
                      options={"require": ["sub", "exp", "iat", "jti"]})


def _reject(request: Request, reason: str, student_id: str | None = None) -> HTTPException:
    database.log_security_event("unauthorized_request", student_id, f"{request.url.path}: {reason}")
    return HTTPException(status_code=401, detail=GENERIC_TOKEN_ERROR, headers={"WWW-Authenticate": "Bearer"})


def get_token_claims(request: Request,
                     credentials: HTTPAuthorizationCredentials | None = Depends(_bearer)) -> dict:
    """FastAPI dependency: the verified claims of the caller's token, or 401."""
    if credentials is None or credentials.scheme.lower() != "bearer":
        raise _reject(request, "missing token")
    try:
        claims = decode_token(credentials.credentials)
    except jwt.ExpiredSignatureError:
        raise _reject(request, "expired token") from None
    except jwt.PyJWTError:
        raise _reject(request, "invalid token") from None
    if database.is_token_revoked(claims["jti"]):
        raise _reject(request, "revoked token (logged out)", claims["sub"])
    return claims


def get_current_student(request: Request, claims: dict = Depends(get_token_claims)) -> str:
    """FastAPI dependency: the authenticated student ID (from the token's `sub`), checked to still exist."""
    student_id = claims["sub"]
    if not database.get_student(student_id):
        raise _reject(request, "student no longer exists", student_id)
    return student_id
