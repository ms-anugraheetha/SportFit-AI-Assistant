"""staff sessions

Revision ID: 0002
Revises: 0001
Create Date: 2026-10-18 12:00:00
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "0002"
down_revision: Union[str, None] = "0001"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "staff_sessions",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("token_hash", sa.String(length=64), nullable=False),
        sa.Column("csrf_token", sa.String(length=64), nullable=False),
        sa.Column("username", sa.String(length=64), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_staff_sessions_token_hash"), "staff_sessions", ["token_hash"], unique=True)
    op.create_index(op.f("ix_staff_sessions_expires_at"), "staff_sessions", ["expires_at"], unique=False)


def downgrade() -> None:
    op.drop_index(op.f("ix_staff_sessions_expires_at"), table_name="staff_sessions")
    op.drop_index(op.f("ix_staff_sessions_token_hash"), table_name="staff_sessions")
    op.drop_table("staff_sessions")
