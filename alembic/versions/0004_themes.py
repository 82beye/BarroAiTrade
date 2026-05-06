"""themes / theme_keywords / theme_stocks tables — BAR-59.

Revision ID: 0004
Revises: 0003
Create Date: 2026-05-07
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0004"
down_revision = "0003"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    is_pg = bind.dialect.name == "postgresql"
    ts_type = postgresql.TIMESTAMP(timezone=True) if is_pg else sa.Text

    op.create_table(
        "themes",
        sa.Column("id", sa.BigInteger, primary_key=True, autoincrement=True),
        sa.Column("name", sa.Text, nullable=False, unique=True),
        sa.Column("description", sa.Text, nullable=False, server_default=""),
        sa.Column(
            "created_at",
            ts_type,
            nullable=False,
            server_default=sa.text("NOW()") if is_pg else sa.text("''"),
        ),
    )

    op.create_table(
        "theme_keywords",
        sa.Column("id", sa.BigInteger, primary_key=True, autoincrement=True),
        sa.Column(
            "theme_id",
            sa.BigInteger,
            sa.ForeignKey("themes.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("keyword", sa.Text, nullable=False),
        sa.UniqueConstraint(
            "theme_id", "keyword", name="uq_theme_keywords_theme_keyword"
        ),
    )

    op.create_table(
        "theme_stocks",
        sa.Column("id", sa.BigInteger, primary_key=True, autoincrement=True),
        sa.Column(
            "theme_id",
            sa.BigInteger,
            sa.ForeignKey("themes.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("symbol", sa.Text, nullable=False),
        sa.Column("score", sa.Float(precision=53), nullable=False),
        sa.UniqueConstraint(
            "theme_id", "symbol", name="uq_theme_stocks_theme_symbol"
        ),
    )
    op.create_index("idx_theme_stocks_symbol", "theme_stocks", ["symbol"])


def downgrade() -> None:
    op.drop_index("idx_theme_stocks_symbol", table_name="theme_stocks")
    op.drop_table("theme_stocks")
    op.drop_table("theme_keywords")
    op.drop_table("themes")
