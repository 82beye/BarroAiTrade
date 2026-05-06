"""trade_notes — BAR-65."""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0006"
down_revision = "0005"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    is_pg = bind.dialect.name == "postgresql"
    json_type = postgresql.JSONB if is_pg else sa.JSON
    ts_type = postgresql.TIMESTAMP(timezone=True) if is_pg else sa.Text

    op.create_table(
        "trade_notes",
        sa.Column("id", sa.BigInteger, primary_key=True, autoincrement=True),
        sa.Column("trade_id", sa.Text, nullable=False, unique=True),
        sa.Column("symbol", sa.Text, nullable=False),
        sa.Column("side", sa.Text, nullable=False),
        sa.Column("qty", sa.Float(precision=53), nullable=False),
        sa.Column("entry_price", sa.Float(precision=53), nullable=False),
        sa.Column("exit_price", sa.Float(precision=53)),
        sa.Column("pnl", sa.Float(precision=53)),
        sa.Column("entry_time", ts_type, nullable=False),
        sa.Column("exit_time", ts_type),
        sa.Column("emotion", sa.Text, nullable=False, server_default="neutral"),
        sa.Column("note", sa.Text, nullable=False, server_default=""),
        sa.Column(
            "tags",
            json_type,
            nullable=False,
            server_default=sa.text("'[]'::jsonb") if is_pg else sa.text("'[]'"),
        ),
    )
    op.create_index("idx_trade_notes_symbol", "trade_notes", ["symbol"])
    op.create_index("idx_trade_notes_entry_time", "trade_notes", ["entry_time"])


def downgrade() -> None:
    op.drop_index("idx_trade_notes_entry_time", table_name="trade_notes")
    op.drop_index("idx_trade_notes_symbol", table_name="trade_notes")
    op.drop_table("trade_notes")
