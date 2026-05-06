"""market_events table — BAR-61.

Revision ID: 0005
Revises: 0004
Create Date: 2026-05-07
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0005"
down_revision = "0004"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    is_pg = bind.dialect.name == "postgresql"
    json_type = postgresql.JSONB if is_pg else sa.JSON
    date_type = postgresql.TIMESTAMP(timezone=True) if is_pg else sa.Text

    op.create_table(
        "market_events",
        sa.Column("id", sa.BigInteger, primary_key=True, autoincrement=True),
        sa.Column("event_type", sa.Text, nullable=False),
        sa.Column("symbol", sa.Text),
        sa.Column("event_date", date_type, nullable=False),
        sa.Column("title", sa.Text, nullable=False),
        sa.Column("source", sa.Text, nullable=False, server_default="manual"),
        sa.Column(
            "metadata",
            json_type,
            nullable=False,
            server_default=sa.text("'{}'::jsonb") if is_pg else sa.text("'{}'"),
        ),
        sa.UniqueConstraint(
            "symbol",
            "event_date",
            "event_type",
            name="uq_market_events_symbol_date_type",
        ),
    )
    op.create_index(
        "idx_market_events_event_date", "market_events", ["event_date"]
    )
    op.create_index("idx_market_events_symbol", "market_events", ["symbol"])


def downgrade() -> None:
    op.drop_index("idx_market_events_symbol", table_name="market_events")
    op.drop_index("idx_market_events_event_date", table_name="market_events")
    op.drop_table("market_events")
