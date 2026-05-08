"""BAR-OPS-02 — alembic 0007 정적 검증."""
from __future__ import annotations

import importlib.util
from pathlib import Path
from unittest.mock import MagicMock, patch


def _load():
    path = Path(__file__).resolve().parents[3] / "alembic" / "versions" / "0007_users.py"
    spec = importlib.util.spec_from_file_location("rev_0007", path)
    mod = importlib.util.module_from_spec(spec)  # type: ignore[arg-type]
    spec.loader.exec_module(mod)  # type: ignore[union-attr]
    return mod


def test_revision():
    mod = _load()
    assert mod.revision == "0007"
    assert mod.down_revision == "0006"


def test_upgrade_creates_users_table():
    mod = _load()
    op_mock = MagicMock()
    bind_mock = MagicMock()
    bind_mock.dialect.name = "postgresql"
    op_mock.get_bind = MagicMock(return_value=bind_mock)
    with patch.object(mod, "op", op_mock):
        mod.upgrade()
    create = [c for c in op_mock.method_calls if c[0] == "create_table"]
    assert create[0][1][0] == "users"
