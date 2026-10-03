"""Create TOTP two-factor authentication storage.

Revision ID: 0007_create_totp
Revises: 0006_create_passkeys
Create Date: 2026-10-03
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "0007_create_totp"
down_revision: Union[str, Sequence[str], None] = "0006_create_passkeys"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "totp_credentials",
        sa.Column("id", sa.BigInteger(), sa.Identity(), nullable=False),
        sa.Column("user_id", sa.BigInteger(), nullable=False),
        sa.Column("secret_encrypted", sa.String(length=1024), nullable=False),
        sa.Column("enabled", sa.Boolean(), server_default="false", nullable=False),
        sa.Column("confirmed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column("last_used_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("user_id"),
    )
    op.create_index("ix_totp_credentials_user_id", "totp_credentials", ["user_id"], unique=False)

    op.create_table(
        "totp_login_challenges",
        sa.Column("id", sa.BigInteger(), sa.Identity(), nullable=False),
        sa.Column("challenge_hash", sa.String(length=64), nullable=False),
        sa.Column("user_id", sa.BigInteger(), nullable=False),
        sa.Column("attempts", sa.Integer(), server_default="0", nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("challenge_hash"),
    )
    op.create_index(
        "ix_totp_login_challenges_challenge_hash",
        "totp_login_challenges",
        ["challenge_hash"],
        unique=False,
    )
    op.create_index(
        "ix_totp_login_challenges_user_id",
        "totp_login_challenges",
        ["user_id"],
        unique=False,
    )
    op.create_index(
        "ix_totp_login_challenges_expires_at",
        "totp_login_challenges",
        ["expires_at"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index("ix_totp_login_challenges_expires_at", table_name="totp_login_challenges")
    op.drop_index("ix_totp_login_challenges_user_id", table_name="totp_login_challenges")
    op.drop_index("ix_totp_login_challenges_challenge_hash", table_name="totp_login_challenges")
    op.drop_table("totp_login_challenges")
    op.drop_index("ix_totp_credentials_user_id", table_name="totp_credentials")
    op.drop_table("totp_credentials")
