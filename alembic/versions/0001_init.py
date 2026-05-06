"""init — audit_log + trades tables (1:1 from SQLite, BAR-56).

Revision ID: 0001
Revises:
Create Date: 2026-05-07
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    # audit_log
    op.create_table(
        "audit_log",
        sa.Column("id", sa.BigInteger, primary_key=True, autoincrement=True),
        sa.Column("event_type", sa.Text, nullable=False),
        sa.Column("symbol", sa.Text),
        sa.Column("market_type", sa.Text),
        sa.Column("quantity", sa.Float(precision=53)),
        sa.Column("price", sa.Float(precision=53)),
        sa.Column("pnl", sa.Float(precision=53)),
        sa.Column("strategy_id", sa.Text),
        sa.Column(
            "metadata",
            postgresql.JSONB,
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column(
            "created_at",
            postgresql.TIMESTAMP(timezone=True),
            nullable=False,
            server_default=sa.text("NOW()"),
        ),
    )
    op.create_index("idx_audit_log_event_type", "audit_log", ["event_type"])
    op.create_index("idx_audit_log_created_at", "audit_log", ["created_at"])

    # trades
    op.create_table(
        "trades",
        sa.Column("id", sa.BigInteger, primary_key=True, autoincrement=True),
        sa.Column("symbol", sa.Text, nullable=False),
        sa.Column("side", sa.Text, nullable=False),
        sa.Column("order_type", sa.Text, nullable=False),
        sa.Column("quantity", sa.Float(precision=53), nullable=False),
        sa.Column("price", sa.Float(precision=53), nullable=False),
        sa.Column("strategy_id", sa.Text),
        sa.Column("order_id", sa.Text),
        sa.Column("status", sa.Text),
        sa.Column(
            "created_at",
            postgresql.TIMESTAMP(timezone=True),
            nullable=False,
            server_default=sa.text("NOW()"),
        ),
    )
    op.create_index("idx_trades_symbol", "trades", ["symbol"])


def downgrade() -> None:
    op.drop_index("idx_trades_symbol", table_name="trades")
    op.drop_table("trades")
    op.drop_index("idx_audit_log_created_at", table_name="audit_log")
    op.drop_index("idx_audit_log_event_type", table_name="audit_log")
    op.drop_table("audit_log")
