"""users — BAR-OPS-02."""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0007"
down_revision = "0006"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    is_pg = bind.dialect.name == "postgresql"
    ts_type = postgresql.TIMESTAMP(timezone=True) if is_pg else sa.Text

    op.create_table(
        "users",
        sa.Column("id", sa.BigInteger, primary_key=True, autoincrement=True),
        sa.Column("user_id", sa.Text, nullable=False, unique=True),
        sa.Column("email", sa.Text, nullable=False, unique=True),
        sa.Column("role", sa.Text, nullable=False, server_default="viewer"),
        sa.Column("password_hash", sa.Text, nullable=False),
        sa.Column("mfa_secret", sa.Text),
        sa.Column(
            "created_at",
            ts_type,
            nullable=False,
            server_default=sa.text("NOW()") if is_pg else sa.text("''"),
        ),
    )
    op.create_index("idx_users_email", "users", ["email"])


def downgrade() -> None:
    op.drop_index("idx_users_email", table_name="users")
    op.drop_table("users")
