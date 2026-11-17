from __future__ import annotations

import hashlib
import secrets
import time
from collections import defaultdict, deque
from datetime import timedelta

from argon2 import PasswordHasher
from argon2.exceptions import Argon2Error, InvalidHashError
from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from .config import get_settings
from .db import utcnow
from .models import AuthSession, Membership, User, Workspace

COOKIE = "openkg_session"
_ph = PasswordHasher()
_DUMMY = _ph.hash("openkg-timing-equalizer")


def hash_password(password: str) -> str:
    return _ph.hash(password)


def verify_password(hash_: str | None, password: str) -> bool:
    try:
        return _ph.verify(hash_ or _DUMMY, password) and hash_ is not None
    except (Argon2Error, InvalidHashError):
        return False


def _digest(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def create_session(s: Session, user: User) -> tuple[str, AuthSession]:
    token = secrets.token_urlsafe(32)
    row = AuthSession(token_hash=_digest(token), csrf_token=secrets.token_urlsafe(24), user_id=user.id,
                      expires_at=utcnow() + timedelta(hours=get_settings().session_ttl_hours))
    s.add(row)
    s.flush()
    return token, row


def lookup_session(s: Session, token: str | None) -> AuthSession | None:
    if not token:
        return None
    row = s.scalar(select(AuthSession).where(AuthSession.token_hash == _digest(token)))
    if row is None or row.expires_at < utcnow():
        return None
    return row


def destroy_session(s: Session, token: str | None) -> None:
    if token:
        s.execute(delete(AuthSession).where(AuthSession.token_hash == _digest(token)))


def create_user(s: Session, email: str, password: str | None, name: str = "", is_demo: bool = False) -> tuple[User, Workspace]:
    user = User(email=email.strip().lower(), name=name or email.split("@")[0],
                password_hash=hash_password(password) if password else "!", is_demo=is_demo)
    ws = Workspace(name=f"{user.name}'s workspace")
    s.add_all([user, ws])
    s.flush()
    s.add(Membership(workspace_id=ws.id, user_id=user.id, role="owner"))
    s.flush()
    return user, ws


class RateLimiter:
    """Small in-memory sliding window (per process). Front with a real limiter in production."""

    def __init__(self, limit: int, window: float):
        self.limit, self.window, self.hits = limit, window, defaultdict(deque)

    def allow(self, key: str) -> bool:
        now, q = time.monotonic(), self.hits[key]
        while q and q[0] < now - self.window:
            q.popleft()
        if len(q) >= self.limit:
            return False
        q.append(now)
        return True
