"""
SQLAlchemy MetaData export — Alembic env.py 의 target_metadata 소스 (BAR-56).

본 BAR-56a 에서는 alembic op.create_table 로 직접 스키마 생성.
ORM 모델 도입은 후속 BAR (테이블 추가 시) 에서 점진적으로.
"""
from __future__ import annotations

from sqlalchemy import MetaData

metadata = MetaData()

__all__ = ["metadata"]
