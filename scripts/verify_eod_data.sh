#!/usr/bin/env bash
# verify_eod_data.sh — EOD 데이터 무결성 자가검증 래퍼 (BAR-OPS).
#
# 장 마감(15:30 KST) + 이브닝 파이프라인 실행 이후 1회 실행 권장. fill_audit(브로커 체결)·
# EOD balance(장마감 후 정산)·buy_audit(EOD 보유 매수평단)가 당일 기록됐는지 점검해,
# 6/9~6/15 처럼 이브닝 파이프라인이 조용히 침묵하는 회귀를 즉시 감지한다.
#
# 사용:
#   bash scripts/verify_eod_data.sh            # 오늘(KST)
#   bash scripts/verify_eod_data.sh 2026-06-15 # 특정일
#
# 종료코드 = NG 건수(0=정상). cron 예시(이브닝 파이프라인 뒤, 평일 16:10):
#   10 16 * * 1-5  cd /Users/beye/BarroAiTrade && bash scripts/verify_eod_data.sh >> logs/verify_eod.log 2>&1
set -uo pipefail
cd "$(dirname "$0")/.." || exit 9
PY="./.venv/bin/python"; [ -x "$PY" ] || PY="./venv/bin/python"; [ -x "$PY" ] || PY=python3
exec "$PY" scripts/verify_eod_data.py "$@"
