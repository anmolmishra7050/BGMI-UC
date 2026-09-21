"""Admin authentication: PBKDF2 password hashing plus DB-backed sessions."""

from __future__ import annotations

import hashlib
import hmac
import secrets
from datetime import timedelta

from .db import Database, iso, parse_iso, utcnow

ALGORITHM = "pbkdf2_sha256"
ITERATIONS = 240_000
SALT_BYTES = 16

PASSWORD_KEY = "admin_password_hash"
USERNAME_KEY = "admin_username"


def hash_password(password: str) -> str:
    if not password:
        raise ValueError("password must not be empty")
    salt = secrets.token_bytes(SALT_BYTES)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, ITERATIONS)
    return f"{ALGORITHM}${ITERATIONS}${salt.hex()}${digest.hex()}"


def verify_password(password: str, encoded: str) -> bool:
    if not password or not encoded:
        return False
    try:
        algorithm, iterations, salt_hex, digest_hex = encoded.split("$")
        if algorithm != ALGORITHM:
            return False
        digest = hashlib.pbkdf2_hmac(
            "sha256", password.encode("utf-8"), bytes.fromhex(salt_hex), int(iterations)
        )
    except (ValueError, TypeError):
        return False
    return hmac.compare_digest(digest.hex(), digest_hex)


def ensure_admin_seed(db: Database, username: str, password: str) -> bool:
    """Store the initial admin credentials if none exist yet.

    Returns True when a new password hash was written.
    """
    if db.get_setting(PASSWORD_KEY):
        return False
    db.set_setting(PASSWORD_KEY, hash_password(password))
    db.set_setting(USERNAME_KEY, username or "admin")
    return True


def admin_username(db: Database, fallback: str = "admin") -> str:
    return db.get_setting(USERNAME_KEY, fallback) or fallback


def set_admin_password(db: Database, username: str, password: str) -> None:
    db.set_setting(USERNAME_KEY, username or "admin")
    db.set_setting(PASSWORD_KEY, hash_password(password))
    db.execute("DELETE FROM admin_sessions")


def check_credentials(db: Database, username: str, password: str) -> bool:
    expected_user = admin_username(db)
    stored = db.get_setting(PASSWORD_KEY, "") or ""
    ok_user = hmac.compare_digest(expected_user, (username or "").strip())
    ok_pass = verify_password(password or "", stored)
    # Always run the hash check so a wrong username costs the same as a wrong password.
    return ok_user and ok_pass


def create_session(db: Database, username: str, hours: int = 12) -> tuple[str, str]:
    token = secrets.token_urlsafe(32)
    created = utcnow()
    expires = created + timedelta(hours=max(1, int(hours)))
    db.execute(
        "INSERT INTO admin_sessions (token, username, created_at, expires_at) "
        "VALUES (?, ?, ?, ?)",
        [token, username, iso(created), iso(expires)],
    )
    return token, iso(expires)


def get_session(db: Database, token: str | None):
    if not token:
        return None
    row = db.query_one(
        "SELECT token, username, expires_at FROM admin_sessions WHERE token = ?", [token]
    )
    if row is None:
        return None
    expires = parse_iso(row["expires_at"])
    if expires is None or expires <= utcnow():
        delete_session(db, token)
        return None
    return row


def delete_session(db: Database, token: str | None) -> None:
    if token:
        db.execute("DELETE FROM admin_sessions WHERE token = ?", [token])


def purge_sessions(db: Database) -> int:
    cursor = db.execute("DELETE FROM admin_sessions WHERE expires_at <= ?", [iso()])
    return cursor.rowcount or 0
