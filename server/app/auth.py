"""Accounts + token sessions. PBKDF2-HMAC-SHA256, opaque bearer tokens."""
from __future__ import annotations

import hashlib
import hmac
import secrets

from fastapi import Depends, HTTPException, Request

from .db import get_conn, now, tx

PBKDF2_ITERS = 200_000


def hash_password(password: str) -> str:
    salt = secrets.token_bytes(16)
    dk = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, PBKDF2_ITERS)
    return f"pbkdf2${PBKDF2_ITERS}${salt.hex()}${dk.hex()}"


def verify_password(password: str, stored: str) -> bool:
    try:
        _, iters, salt_hex, dk_hex = stored.split("$")
        dk = hashlib.pbkdf2_hmac("sha256", password.encode(), bytes.fromhex(salt_hex), int(iters))
        return hmac.compare_digest(dk.hex(), dk_hex)
    except (ValueError, TypeError):
        return False


def create_user(username: str, password: str) -> int:
    username = username.strip()
    if len(username) < 2 or len(password) < 6:
        raise HTTPException(400, "Username must be 2+ chars, password 6+ chars")
    with tx() as conn:
        try:
            cur = conn.execute(
                "INSERT INTO users (username, password_hash, created_at) VALUES (?,?,?)",
                (username, hash_password(password), now()),
            )
        except Exception:
            raise HTTPException(409, "Username already taken")
        return cur.lastrowid


def login(username: str, password: str) -> str:
    row = get_conn().execute("SELECT * FROM users WHERE username=?", (username.strip(),)).fetchone()
    if not row or not verify_password(password, row["password_hash"]):
        raise HTTPException(401, "Invalid username or password")
    return issue_token(row["id"])


def issue_token(user_id: int) -> str:
    token = secrets.token_urlsafe(32)
    with tx() as conn:
        conn.execute("INSERT INTO sessions (token, user_id, created_at) VALUES (?,?,?)",
                     (token, user_id, now()))
    return token


def current_user(request: Request) -> dict:
    auth = request.headers.get("authorization", "")
    token = auth.removeprefix("Bearer ").strip() if auth.startswith("Bearer ") else request.query_params.get("token", "")
    if not token:
        raise HTTPException(401, "Not authenticated")
    row = get_conn().execute(
        "SELECT u.id, u.username FROM sessions s JOIN users u ON u.id = s.user_id WHERE s.token=?",
        (token,),
    ).fetchone()
    if not row:
        raise HTTPException(401, "Invalid or expired session")
    return {"id": row["id"], "username": row["username"]}


CurrentUser = Depends(current_user)
