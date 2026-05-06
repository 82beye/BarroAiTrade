-- infra/postgres/init.sql
-- BAR-56 — 컨테이너 첫 기동 시 자동 실행 (docker-entrypoint-initdb.d/).
-- pgvector extension 활성화 + 버전 게이트 + UTC 타임존.
-- 주의: extension 만 활성화. 벡터 컬럼·인덱스는 BAR-58 책임.

\c barro

-- 1. pgvector 확장 활성화 (idempotent)
CREATE EXTENSION IF NOT EXISTS vector;

-- 2. 버전 / 설치 검증 (≥ 0.8). 미만이면 즉시 실패.
DO $$
DECLARE
    v_extversion TEXT;
BEGIN
    SELECT extversion INTO v_extversion FROM pg_extension WHERE extname = 'vector';
    IF v_extversion IS NULL THEN
        RAISE EXCEPTION 'pgvector extension not installed';
    END IF;
    IF v_extversion < '0.8' THEN
        RAISE EXCEPTION 'pgvector version % < 0.8', v_extversion;
    END IF;
    RAISE NOTICE 'pgvector % installed', v_extversion;
END $$;

-- 3. 기본 timezone (UTC) — TIMESTAMPTZ 일관성
ALTER DATABASE barro SET TIMEZONE TO 'UTC';

-- 4. 애플리케이션 사용자 권한 — POSTGRES_USER 가 owner 라 별도 GRANT 불필요.
--    BAR-69 (RLS) 시 readonly / app 분리 사용자 도입 예정.
