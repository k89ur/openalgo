from __future__ import annotations

import base64
import hashlib
import os
import secrets
from datetime import datetime, timedelta, timezone
from urllib.parse import urlencode

from sqlalchemy import delete, select, update

from app.db.database import SessionLocal
from app.db.models import OAuthState


OAUTH_STATE_TTL_SECONDS = 10 * 60


def _require_db():
    if SessionLocal is None:
        raise RuntimeError("PostgreSQL is not configured. Set DATABASE_URL first.")
    return SessionLocal


def _sha256(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _base64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def create_pkce_pair() -> tuple[str, str]:
    """Return (code_verifier, S256 code_challenge)."""
    verifier = _base64url(secrets.token_bytes(32))
    challenge = _base64url(hashlib.sha256(verifier.encode("ascii")).digest())
    return verifier, challenge


def create_authorization_state(
    *,
    provider: str,
    redirect_uri: str,
    session_token: str | None = None,
    use_pkce: bool = True,
) -> tuple[str, str | None, str | None]:
    """Create one short-lived, single-use OAuth transaction.

    Returns the raw state plus optional nonce and PKCE verifier. Only the
    hashes/state transaction are persisted; the browser receives the raw
    state through the authorization URL.
    """
    state = secrets.token_urlsafe(32)
    nonce = secrets.token_urlsafe(32)
    code_verifier = create_pkce_pair()[0] if use_pkce else None
    now = datetime.now(timezone.utc)

    with _require_db()() as db:
        db.execute(
            delete(OAuthState).where(OAuthState.expires_at <= now)
        )
        db.add(
            OAuthState(
                state_hash=_sha256(state),
                provider=provider,
                redirect_uri=redirect_uri,
                nonce=nonce,
                code_verifier=code_verifier,
                session_hash=_sha256(session_token) if session_token else None,
                expires_at=now + timedelta(seconds=OAUTH_STATE_TTL_SECONDS),
            )
        )
        db.commit()

    return state, nonce, code_verifier


def consume_authorization_state(
    *,
    state: str,
    provider: str,
    session_token: str | None = None,
) -> OAuthState | None:
    """Validate and atomically consume a pending OAuth transaction."""
    if not state:
        return None

    now = datetime.now(timezone.utc)
    state_hash = _sha256(state)

    with _require_db()() as db:
        row = db.scalar(
            select(OAuthState).where(
                OAuthState.state_hash == state_hash,
                OAuthState.provider == provider,
                OAuthState.expires_at > now,
                OAuthState.used_at.is_(None),
            )
        )
        if row is None:
            return None

        expected_session_hash = row.session_hash
        actual_session_hash = _sha256(session_token) if session_token else None
        if expected_session_hash and not secrets.compare_digest(
            expected_session_hash,
            actual_session_hash or "",
        ):
            return None

        updated = db.execute(
            update(OAuthState)
            .where(
                OAuthState.id == row.id,
                OAuthState.used_at.is_(None),
            )
            .values(used_at=now)
        )
        if updated.rowcount != 1:
            return None

        db.commit()
        return row


def cleanup_expired_states() -> int:
    with _require_db()() as db:
        result = db.execute(
            delete(OAuthState).where(
                OAuthState.expires_at <= datetime.now(timezone.utc)
            )
        )
        db.commit()
        return int(result.rowcount or 0)


def authorization_query(
    *,
    authorization_endpoint: str,
    client_id: str,
    redirect_uri: str,
    state: str,
    nonce: str | None = None,
    code_challenge: str | None = None,
) -> str:
    params = {
        "response_type": "code",
        "client_id": client_id,
        "redirect_uri": redirect_uri,
        "scope": "openid email profile",
        "state": state,
    }
    if nonce:
        params["nonce"] = nonce
    if code_challenge:
        params["code_challenge"] = code_challenge
        params["code_challenge_method"] = "S256"
    return authorization_endpoint + "?" + urlencode(params)
