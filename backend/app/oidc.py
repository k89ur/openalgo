from __future__ import annotations

import os
import secrets
import time
from datetime import datetime, timezone
from urllib.parse import urlencode

import requests
from authlib.jose import JsonWebKey, jwt
from sqlalchemy import select

from app import auth
from app.db.database import SessionLocal
from app.db.models import OAuthAccount, User
from app.oauth import authorization_query, consume_authorization_state, create_authorization_state


DISCOVERY = {
    "google": "https://accounts.google.com/.well-known/openid-configuration",
    "apple": "https://appleid.apple.com/.well-known/openid-configuration",
}
_DISCOVERY_CACHE: dict[str, tuple[float, dict[str, object]]] = {}
_DISCOVERY_TTL = 3600


def _env(name: str) -> str:
    return os.getenv(name, "").strip()


def _api_url() -> str:
    configured = _env("PIPSGOX_API_URL")
    if configured:
        return configured.rstrip("/")
    codespace = _env("CODESPACE_NAME")
    domain = _env("GITHUB_CODESPACES_PORT_FORWARDING_DOMAIN") or "app.github.dev"
    if codespace:
        return f"https://{codespace}-8000.{domain}"
    return "http://127.0.0.1:8000"


def _web_url() -> str:
    configured = _env("PIPSGOX_WEB_URL")
    if configured:
        return configured.rstrip("/")
    codespace = _env("CODESPACE_NAME")
    domain = _env("GITHUB_CODESPACES_PORT_FORWARDING_DOMAIN") or "app.github.dev"
    if codespace:
        return f"https://{codespace}-3001.{domain}"
    return "http://127.0.0.1:3001"


def provider_config(provider: str) -> dict[str, str]:
    provider = provider.lower()
    if provider == "google":
        return {
            "client_id": _env("GOOGLE_OIDC_CLIENT_ID"),
            "client_secret": _env("GOOGLE_OIDC_CLIENT_SECRET"),
            "redirect_uri": _env("GOOGLE_OIDC_REDIRECT_URI")
            or f"{_api_url()}/api/auth/oauth/google/callback",
        }
    if provider == "apple":
        return {
            "client_id": _env("APPLE_CLIENT_ID"),
            "client_secret": _env("APPLE_CLIENT_SECRET"),
            "team_id": _env("APPLE_TEAM_ID"),
            "key_id": _env("APPLE_KEY_ID"),
            "private_key": os.getenv("APPLE_PRIVATE_KEY", ""),
            "redirect_uri": _env("APPLE_REDIRECT_URI")
            or f"{_api_url()}/api/auth/oauth/apple/callback",
        }
    raise ValueError("Unsupported OAuth provider.")


def _discovery(provider: str) -> dict[str, object]:
    now = time.time()
    cached = _DISCOVERY_CACHE.get(provider)
    if cached and cached[0] > now:
        return cached[1]

    response = requests.get(
        DISCOVERY[provider],
        timeout=10,
        headers={"Accept": "application/json"},
    )
    response.raise_for_status()
    data = response.json()
    if not isinstance(data, dict):
        raise RuntimeError("Invalid OIDC discovery document.")
    _DISCOVERY_CACHE[provider] = (now + _DISCOVERY_TTL, data)
    return data


def _require_provider_config(provider: str) -> dict[str, str]:
    config = provider_config(provider)
    if not config.get("client_id"):
        raise RuntimeError(f"{provider.title()} OAuth client is not configured.")
    if provider == "google" and not config.get("client_secret"):
        raise RuntimeError("Google OAuth client secret is not configured.")
    if provider == "apple" and not config.get("client_secret"):
        raise RuntimeError("Apple OAuth client secret is not configured.")
    return config


def build_authorization_url(provider: str, session_token: str | None) -> str:
    provider = provider.lower()
    if provider not in DISCOVERY:
        raise ValueError("Unsupported OAuth provider.")

    config = _require_provider_config(provider)
    discovery = _discovery(provider)
    authorization_endpoint = str(discovery["authorization_endpoint"])
    state, nonce, _verifier, challenge = create_authorization_state(
        provider=provider,
        redirect_uri=config["redirect_uri"],
        session_token=session_token,
        use_pkce=True,
    )
    return authorization_query(
        authorization_endpoint=authorization_endpoint,
        client_id=config["client_id"],
        redirect_uri=config["redirect_uri"],
        state=state,
        nonce=nonce,
        code_challenge=challenge,
    )


def _token_exchange(provider: str, code: str, state_row) -> dict[str, object]:
    config = _require_provider_config(provider)
    discovery = _discovery(provider)
    token_endpoint = str(discovery["token_endpoint"])

    data = {
        "grant_type": "authorization_code",
        "code": code,
        "redirect_uri": state_row.redirect_uri,
        "client_id": config["client_id"],
        "code_verifier": state_row.code_verifier or "",
    }
    data["client_secret"] = config["client_secret"]

    response = requests.post(token_endpoint, data=data, timeout=10)
    if response.status_code >= 400:
        raise RuntimeError("OAuth token exchange failed.")
    payload = response.json()
    if not isinstance(payload, dict) or not payload.get("id_token"):
        raise RuntimeError("OAuth provider did not return an ID token.")
    return payload


def _verify_id_token(provider: str, id_token: str, nonce: str | None) -> dict[str, object]:
    discovery = _discovery(provider)
    jwks_uri = str(discovery["jwks_uri"])
    response = requests.get(jwks_uri, timeout=10, headers={"Accept": "application/json"})
    response.raise_for_status()
    jwks = response.json()

    claims_options = {
        "iss": {"essential": True, "value": str(discovery["issuer"])},
        "aud": {"essential": True, "value": provider_config(provider)["client_id"]},
        "exp": {"essential": True},
    }
    claims = jwt.decode(
        id_token,
        JsonWebKey.import_key_set(jwks),
        claims_options=claims_options,
    )
    claims.validate()

    if nonce:
        token_nonce = str(claims.get("nonce") or "")
        if not token_nonce or not secrets.compare_digest(token_nonce, nonce):
            raise RuntimeError("OIDC nonce validation failed.")

    return dict(claims)


def _verified_email(claims: dict[str, object]) -> str | None:
    email = str(claims.get("email") or "").strip().lower()
    if not email:
        return None
    verified = claims.get("email_verified")
    if verified is False or str(verified).lower() == "false":
        return None
    return email


def _username_base(email: str | None, provider: str, subject: str) -> str:
    if email:
        local = email.split("@", 1)[0]
        safe = "".join(ch if ch.isalnum() or ch in "._-" else "-" for ch in local).strip(".-_")
        if safe:
            return safe[:48]
    return f"{provider}-{subject[:24]}"


def _unique_username(db, base: str) -> str:
    candidate = base or "user"
    if db.scalar(select(User.id).where(User.username == candidate)) is None:
        return candidate
    for _ in range(20):
        candidate = f"{base[:48]}-{secrets.token_hex(2)}"
        if db.scalar(select(User.id).where(User.username == candidate)) is None:
            return candidate
    raise RuntimeError("Could not generate a unique username.")


def _upsert_user(provider: str, claims: dict[str, object]) -> int:
    subject = str(claims.get("sub") or "").strip()
    if not subject:
        raise RuntimeError("OIDC identity did not contain a subject.")

    email = _verified_email(claims)
    display_name = str(
        claims.get("name")
        or claims.get("preferred_username")
        or ""
    ).strip() or None
    avatar_url = str(claims.get("picture") or "").strip() or None
    now = datetime.now(timezone.utc)

    with SessionLocal() as db:
        account = db.scalar(
            select(OAuthAccount).where(
                OAuthAccount.provider == provider,
                OAuthAccount.provider_subject == subject,
            )
        )
        if account is not None:
            user = db.get(User, account.user_id)
            if user is None:
                raise RuntimeError("OAuth account references a missing user.")
            account.email = email or account.email
            account.last_used_at = now
        else:
            user = None
            if email:
                user = db.scalar(select(User).where(User.email == email))
                if user is not None and not user.email_verified:
                    # Only link an existing account when the provider has
                    # supplied a verified email claim.
                    user.email_verified = True

            if user is None:
                user = User(
                    email=email,
                    username=_unique_username(db, _username_base(email, provider, subject)),
                    display_name=display_name,
                    avatar_url=avatar_url,
                    status="active",
                    email_verified=bool(email),
                )
                db.add(user)
                db.flush()

            db.add(
                OAuthAccount(
                    user_id=user.id,
                    provider=provider,
                    provider_subject=subject,
                    email=email,
                    created_at=now,
                    last_used_at=now,
                )
            )

        if display_name and not user.display_name:
            user.display_name = display_name
        if avatar_url and not user.avatar_url:
            user.avatar_url = avatar_url
        user.last_login_at = now
        db.commit()
        return int(user.id)


def complete_callback(
    *,
    provider: str,
    state: str,
    code: str,
    session_token: str | None,
    error: str | None = None,
) -> str:
    if error:
        # An OAuth denial still consumes the state so it cannot be replayed.
        consume_authorization_state(
            state=state,
            provider=provider,
            session_token=session_token,
        )
        raise RuntimeError("OAuth authorization was denied.")

    state_row = consume_authorization_state(
        state=state,
        provider=provider,
        session_token=session_token,
    )
    if state_row is None:
        raise RuntimeError("Invalid or expired OAuth state.")

    tokens = _token_exchange(provider, code, state_row)
    claims = _verify_id_token(provider, str(tokens["id_token"]), state_row.nonce)
    user_id = _upsert_user(provider, claims)
    return auth.create_session_for_user(
        user_id,
        ip_address=None,
        user_agent=None,
    )
