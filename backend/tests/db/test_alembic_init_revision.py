"""BAR-56 — alembic/versions/0001_init.py 정적 검증."""
from __future__ import annotations

import importlib.util
from pathlib import Path
from unittest.mock import MagicMock, patch


def _load_revision_module():
    path = (
        Path(__file__).resolve().parents[3]
        / "alembic"
        / "versions"
        / "0001_init.py"
    )
    spec = importlib.util.spec_from_file_location("rev_0001", path)
    mod = importlib.util.module_from_spec(spec)  # type: ignore[arg-type]
    spec.loader.exec_module(mod)  # type: ignore[union-attr]
    return mod


def test_revision_id_and_down_revision():
    mod = _load_revision_module()
    assert mod.revision == "0001"
    assert mod.down_revision is None


def test_upgrade_invokes_op_create_table_twice():
    """upgrade() 가 audit_log + trades 두 번 create_table 호출."""
    mod = _load_revision_module()
    op_mock = MagicMock()
    with patch.object(mod, "op", op_mock):
        mod.upgrade()
    # create_table 2회 (audit_log, trades)
    create_calls = [c for c in op_mock.method_calls if c[0] == "create_table"]
    assert len(create_calls) == 2
    table_names = [c[1][0] for c in create_calls]
    assert "audit_log" in table_names
    assert "trades" in table_names

    # 인덱스 3개 (idx_audit_log_event_type, idx_audit_log_created_at, idx_trades_symbol)
    index_calls = [c for c in op_mock.method_calls if c[0] == "create_index"]
    assert len(index_calls) == 3


def test_downgrade_drops_in_reverse_order():
    mod = _load_revision_module()
    op_mock = MagicMock()
    with patch.object(mod, "op", op_mock):
        mod.downgrade()
    # drop_table 2회
    drop_table_calls = [c for c in op_mock.method_calls if c[0] == "drop_table"]
    assert len(drop_table_calls) == 2
    # 마지막에 audit_log 가 drop 되도록 — trades 가 먼저
    assert drop_table_calls[0][1][0] == "trades"
    assert drop_table_calls[1][1][0] == "audit_log"
