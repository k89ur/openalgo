"""Create OAuth authorization state storage.

Revision ID: 0005_create_oauth_states
Revises: 0004_create_oauth_accounts
Create Date: 2026-10-02
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "0005_create_oauth_states"
down_revision: Union[str, Sequence[str], None] = "0004_create_oauth_accounts"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "oauth_states",
        sa.Column("id", sa.BigInteger(), sa.Identity(), nullable=False),
        sa.Column("state_hash", sa.String(length=64), nullable=False),
        sa.Column("provider", sa.String(length=32), nullable=False),
        sa.Column("redirect_uri", sa.Text(), nullable=False),
        sa.Column("nonce", sa.String(length=128), nullable=True),
        sa.Column("code_verifier", sa.String(length=128), nullable=True),
        sa.Column("session_hash", sa.String(length=64), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("used_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("state_hash"),
    )
    op.create_index(
        "ix_oauth_states_state_hash",
        "oauth_states",
        ["state_hash"],
        unique=False,
    )
    op.create_index(
        "ix_oauth_states_session_hash",
        "oauth_states",
        ["session_hash"],
        unique=False,
    )
    op.create_index(
        "ix_oauth_states_expires_at",
        "oauth_states",
        ["expires_at"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index("ix_oauth_states_expires_at", table_name="oauth_states")
    op.drop_index("ix_oauth_states_session_hash", table_name="oauth_states")
    op.drop_index("ix_oauth_states_state_hash", table_name="oauth_states")
    op.drop_table("oauth_states")
