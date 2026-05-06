"""embeddings table — BAR-58.

Revision ID: 0003
Revises: 0002
Create Date: 2026-05-07
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0003"
down_revision = "0002"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    is_pg = bind.dialect.name == "postgresql"
    ts_type = postgresql.TIMESTAMP(timezone=True) if is_pg else sa.Text

    op.create_table(
        "embeddings",
        sa.Column("id", sa.BigInteger, primary_key=True, autoincrement=True),
        sa.Column("news_id", sa.BigInteger, nullable=False),
        sa.Column("model", sa.Text, nullable=False),
        sa.Column("vector", sa.Text, nullable=False),  # placeholder
        sa.Column(
            "created_at",
            ts_type,
            nullable=False,
            server_default=sa.text("NOW()") if is_pg else sa.text("''"),
        ),
        sa.UniqueConstraint(
            "news_id", "model", name="uq_embeddings_news_model"
        ),
    )
    op.create_index("idx_embeddings_news_id", "embeddings", ["news_id"])
    op.create_index("idx_embeddings_model", "embeddings", ["model"])

    if is_pg:
        # pgvector — extension + 자료형 + ivfflat 인덱스
        op.execute("CREATE EXTENSION IF NOT EXISTS vector")
        op.execute(
            "ALTER TABLE embeddings ALTER COLUMN vector "
            "TYPE vector(768) USING vector::vector"
        )
        op.execute(
            "CREATE INDEX IF NOT EXISTS idx_embeddings_vector_cos "
            "ON embeddings USING ivfflat (vector vector_cosine_ops) "
            "WITH (lists=100)"
        )


def downgrade() -> None:
    op.drop_index("idx_embeddings_model", table_name="embeddings")
    op.drop_index("idx_embeddings_news_id", table_name="embeddings")
    op.drop_table("embeddings")
