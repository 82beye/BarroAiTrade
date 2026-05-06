"""news_items table — BAR-57.

Revision ID: 0002
Revises: 0001
Create Date: 2026-05-07
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    is_pg = bind.dialect.name == "postgresql"
    tags_type = postgresql.JSONB if is_pg else sa.JSON
    ts_type = postgresql.TIMESTAMP(timezone=True) if is_pg else sa.Text

    op.create_table(
        "news_items",
        sa.Column("id", sa.BigInteger, primary_key=True, autoincrement=True),
        sa.Column("source", sa.Text, nullable=False),
        sa.Column("source_id", sa.Text, nullable=False),
        sa.Column("title", sa.Text, nullable=False),
        sa.Column("body", sa.Text, nullable=False, server_default=""),
        sa.Column("url", sa.Text, nullable=False),
        sa.Column("published_at", ts_type, nullable=False),
        sa.Column("fetched_at", ts_type, nullable=False),
        sa.Column(
            "tags",
            tags_type,
            nullable=False,
            server_default=(
                sa.text("'[]'::jsonb") if is_pg else sa.text("'[]'")
            ),
        ),
        sa.UniqueConstraint("source", "source_id", name="uq_news_items_source_id"),
    )
    op.create_index("idx_news_items_source", "news_items", ["source"])
    op.create_index("idx_news_items_published_at", "news_items", ["published_at"])


def downgrade() -> None:
    op.drop_index("idx_news_items_published_at", table_name="news_items")
    op.drop_index("idx_news_items_source", table_name="news_items")
    op.drop_table("news_items")
