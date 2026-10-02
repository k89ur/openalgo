from __future__ import annotations

from datetime import datetime

from sqlalchemy import BigInteger, DateTime, ForeignKey, Identity, LargeBinary, String, func
from sqlalchemy.orm import Mapped, mapped_column

from app.db.database import Base


class PasskeyChallenge(Base):
    """Short-lived, single-use WebAuthn ceremony challenge."""

    __tablename__ = "passkey_challenges"

    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    challenge: Mapped[bytes] = mapped_column(LargeBinary, nullable=False, unique=True)
    user_id: Mapped[int | None] = mapped_column(
        BigInteger,
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=True,
        index=True,
    )
    ceremony: Mapped[str] = mapped_column(String(16), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    expires_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, index=True
    )
    used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
