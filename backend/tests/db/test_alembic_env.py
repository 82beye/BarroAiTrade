"""BAR-56 — alembic env.py / metadata 정적 검증."""
from __future__ import annotations

import importlib.util
from pathlib import Path

from sqlalchemy import MetaData


def test_metadata_is_metadata_instance():
    from backend.db.models import metadata
    assert isinstance(metadata, MetaData)


def test_alembic_env_module_loads():
    """env.py 가 로드 가능 (구문 검증) — 실 마이그레이션은 실행하지 않음."""
    env_path = Path(__file__).resolve().parents[3] / "alembic" / "env.py"
    assert env_path.exists()

    src = env_path.read_text(encoding="utf-8")
    # 핵심 함수·구조 정적 점검
    assert "run_migrations_online" in src
    assert "run_migrations_offline" in src
    assert "target_metadata" in src
    assert "async_engine_from_config" in src


def test_alembic_ini_has_required_keys():
    ini_path = Path(__file__).resolve().parents[3] / "alembic.ini"
    assert ini_path.exists()
    content = ini_path.read_text(encoding="utf-8")
    assert "[alembic]" in content
    assert "script_location = alembic" in content
    assert "prepend_sys_path = ." in content
