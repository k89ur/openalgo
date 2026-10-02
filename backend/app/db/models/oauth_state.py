from __future__ import annotations

from datetime import datetime

from sqlalchemy import BigInteger, DateTime, ForeignKey, Identity, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from app.db.database import Base


class OAuthState(Base):
    """Short-lived server-side state for an OAuth/OIDC authorization attempt."""

    __tablename__ = "oauth_states"

    id: Mapped[int] = mapped_column(
        BigInteger,
        Identity(),
        primary_key=True,
    )
    state_hash: Mapped[str] = mapped_column(
        String(64),
        nullable=False,
        unique=True,
        index=True,
    )
    provider: Mapped[str] = mapped_column(
        String(32),
        nullable=False,
    )
    redirect_uri: Mapped[str] = mapped_column(
        Text,
        nullable=False,
    )
    nonce: Mapped[str | None] = mapped_column(
        String(128),
        nullable=True,
    )
    code_verifier: Mapped[str | None] = mapped_column(
        String(128),
        nullable=True,
    )
    session_hash: Mapped[str | None] = mapped_column(
        String(64),
        nullable=True,
        index=True,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )
    expires_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        index=True,
    )
    used_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
