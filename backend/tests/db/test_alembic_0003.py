"""BAR-58 — alembic/versions/0003_embeddings.py 정적 검증 (3 cases)."""
from __future__ import annotations

import importlib.util
from pathlib import Path
from unittest.mock import MagicMock, patch


def _load_revision():
    path = (
        Path(__file__).resolve().parents[3]
        / "alembic"
        / "versions"
        / "0003_embeddings.py"
    )
    spec = importlib.util.spec_from_file_location("rev_0003", path)
    mod = importlib.util.module_from_spec(spec)  # type: ignore[arg-type]
    spec.loader.exec_module(mod)  # type: ignore[union-attr]
    return mod


def test_revision_id_and_down_revision():
    mod = _load_revision()
    assert mod.revision == "0003"
    assert mod.down_revision == "0002"


def test_upgrade_creates_embeddings_with_unique_constraint():
    mod = _load_revision()
    op_mock = MagicMock()
    bind_mock = MagicMock()
    bind_mock.dialect.name = "postgresql"
    op_mock.get_bind = MagicMock(return_value=bind_mock)

    with patch.object(mod, "op", op_mock):
        mod.upgrade()

    create_calls = [c for c in op_mock.method_calls if c[0] == "create_table"]
    assert len(create_calls) == 1
    assert create_calls[0][1][0] == "embeddings"

    # pgvector — execute 3회 (CREATE EXTENSION + ALTER COLUMN + ivfflat)
    execute_calls = [c for c in op_mock.method_calls if c[0] == "execute"]
    assert len(execute_calls) == 3


def test_downgrade_reverse():
    mod = _load_revision()
    op_mock = MagicMock()
    with patch.object(mod, "op", op_mock):
        mod.downgrade()

    drop_table_calls = [c for c in op_mock.method_calls if c[0] == "drop_table"]
    drop_index_calls = [c for c in op_mock.method_calls if c[0] == "drop_index"]
    assert len(drop_table_calls) == 1
    assert drop_table_calls[0][1][0] == "embeddings"
    assert len(drop_index_calls) >= 2
