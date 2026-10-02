"""Create WebAuthn passkey storage.

Revision ID: 0006_create_passkeys
Revises: 0005_create_oauth_states
Create Date: 2026-10-02
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "0006_create_passkeys"
down_revision: Union[str, Sequence[str], None] = "0005_create_oauth_states"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "users",
        sa.Column("webauthn_user_id", sa.LargeBinary(length=64), nullable=True),
    )
    op.create_unique_constraint(
        "uq_users_webauthn_user_id",
        "users",
        ["webauthn_user_id"],
    )

    op.create_table(
        "passkeys",
        sa.Column("id", sa.BigInteger(), sa.Identity(), nullable=False),
        sa.Column("user_id", sa.BigInteger(), nullable=False),
        sa.Column("credential_id", sa.LargeBinary(), nullable=False),
        sa.Column("public_key", sa.LargeBinary(), nullable=False),
        sa.Column("sign_count", sa.BigInteger(), server_default="0", nullable=False),
        sa.Column("device_name", sa.String(length=120), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column("last_used_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("credential_id"),
    )
    op.create_index("ix_passkeys_user_id", "passkeys", ["user_id"], unique=False)

    op.create_table(
        "passkey_challenges",
        sa.Column("id", sa.BigInteger(), sa.Identity(), nullable=False),
        sa.Column("challenge", sa.LargeBinary(), nullable=False),
        sa.Column("user_id", sa.BigInteger(), nullable=True),
        sa.Column("ceremony", sa.String(length=16), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("used_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("challenge"),
    )
    op.create_index(
        "ix_passkey_challenges_user_id",
        "passkey_challenges",
        ["user_id"],
        unique=False,
    )
    op.create_index(
        "ix_passkey_challenges_expires_at",
        "passkey_challenges",
        ["expires_at"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index("ix_passkey_challenges_expires_at", table_name="passkey_challenges")
    op.drop_index("ix_passkey_challenges_user_id", table_name="passkey_challenges")
    op.drop_table("passkey_challenges")
    op.drop_index("ix_passkeys_user_id", table_name="passkeys")
    op.drop_table("passkeys")
    op.drop_constraint("uq_users_webauthn_user_id", "users", type_="unique")
    op.drop_column("users", "webauthn_user_id")
