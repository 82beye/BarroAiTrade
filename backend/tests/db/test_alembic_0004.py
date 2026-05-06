"""BAR-59 — alembic/versions/0004_themes.py 정적 검증 (3 cases)."""
from __future__ import annotations

import importlib.util
from pathlib import Path
from unittest.mock import MagicMock, patch


def _load_revision():
    path = (
        Path(__file__).resolve().parents[3]
        / "alembic"
        / "versions"
        / "0004_themes.py"
    )
    spec = importlib.util.spec_from_file_location("rev_0004", path)
    mod = importlib.util.module_from_spec(spec)  # type: ignore[arg-type]
    spec.loader.exec_module(mod)  # type: ignore[union-attr]
    return mod


def test_revision_id_and_down_revision():
    mod = _load_revision()
    assert mod.revision == "0004"
    assert mod.down_revision == "0003"


def test_upgrade_creates_three_tables_with_unique():
    mod = _load_revision()
    op_mock = MagicMock()
    bind_mock = MagicMock()
    bind_mock.dialect.name = "postgresql"
    op_mock.get_bind = MagicMock(return_value=bind_mock)
    with patch.object(mod, "op", op_mock):
        mod.upgrade()
    create_calls = [c for c in op_mock.method_calls if c[0] == "create_table"]
    assert len(create_calls) == 3
    table_names = [c[1][0] for c in create_calls]
    assert table_names == ["themes", "theme_keywords", "theme_stocks"]


def test_downgrade_reverse():
    mod = _load_revision()
    op_mock = MagicMock()
    with patch.object(mod, "op", op_mock):
        mod.downgrade()
    drop_calls = [c for c in op_mock.method_calls if c[0] == "drop_table"]
    assert len(drop_calls) == 3
    # reverse: theme_stocks → theme_keywords → themes
    assert drop_calls[0][1][0] == "theme_stocks"
    assert drop_calls[2][1][0] == "themes"
