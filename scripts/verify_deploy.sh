#!/usr/bin/env bash
# BAR-OPS-39 배포 검증 — 운영 머신에서 'git pull' 직후 1회 실행.
#
# 배경: BAR-OPS-38/39 의 리스크 가드·비용 현실화가 코드(origin/main)에는 머지됐으나
#   운영 머신에 pull 되지 않아 4거래일(6/9~6/12) 라이브 미적용이 반복됐다
#   (buy_audit.csv 부재·일봉 캐시 6/10 정지·strategy_id 빈칸·중복매도 FAILED 로 발견).
#   이 스크립트는 "코드가 실제로 운영에 반영됐는지"를 자동 점검해 그 반복을 끊는다.
#
# 사용:
#   cd /Users/beye/BarroAiTrade
#   git pull origin main
#   bash scripts/verify_deploy.sh
#   # 밀린 일봉 1회 백필(EOD 잡 누락분):
#   ./.venv/bin/python scripts/update_ohlcv_cache.py
#
# cron 기반 경로(simulate_leaders 09:30 / evaluate_holdings 매시간)는 pull 만으로
# 다음 실행부터 최신 코드가 적용된다(재기동 불필요). 단 supertrend 를 상시 프로세스
# (run_telegram_bot, SUPERTREND_AUTO_ENABLED=1)로 돌리는 경우 그 프로세스는 재기동 필요.
set -uo pipefail
cd "$(dirname "$0")/.." || exit 9
REPO=$(pwd)
PY="./.venv/bin/python"; [ -x "$PY" ] || PY="./venv/bin/python"; [ -x "$PY" ] || PY=python3
pass=0; fail=0; warn=0
ok(){ echo "  [OK] $1"; pass=$((pass+1)); }
ng(){ echo "  [NG] $1"; fail=$((fail+1)); }
wn(){ echo "  [!!] $1"; warn=$((warn+1)); }

echo "==== BAR-OPS-39 배포 검증 ($(date '+%F %T')) ===="
echo "REPO=$REPO"

echo "-- 1) 코드 버전 (origin/main 최신 여부) --"
git fetch origin -q 2>/dev/null || wn "git fetch 실패(네트워크?) — 로컬 기준으로만 점검"
LOCAL=$(git rev-parse HEAD 2>/dev/null || echo "?")
REMOTE=$(git rev-parse origin/main 2>/dev/null || echo "?")
behind=$(git rev-list --count HEAD..origin/main 2>/dev/null || echo "?")
if [ "$LOCAL" = "$REMOTE" ]; then ok "HEAD == origin/main (${LOCAL:0:8}) — 최신"
else ng "origin/main 보다 $behind 커밋 뒤처짐 — 'git pull origin main' 먼저 실행"; fi

echo "-- 2) BAR-OPS-39 코드 마커 --"
[ -f backend/core/trading_costs.py ] && ok "trading_costs.py 존재 (비용 단일 진실원천)" || ng "trading_costs.py 없음 — BAR-OPS-39 미반영"
grep -q "_zone_entry_cutoff_passed" scripts/intraday_buy_daemon.py 2>/dev/null && ok "데몬 진입 컷오프(14:30) 코드 존재" || ng "진입 컷오프 미반영"
grep -q "_eod_buy_snapshot" scripts/intraday_buy_daemon.py 2>/dev/null && ok "EOD 매수 스냅샷(buy_audit) 코드 존재" || ng "buy_snapshot 미반영"
grep -q "strategy_id=best_strategy" scripts/simulate_leaders.py 2>/dev/null && ok "simulate_leaders strategy_id 전파 존재" || ng "strategy_id 전파 미반영(audit 빈칸 재발 위험)"
grep -q "gap < 1" scripts/update_ohlcv_cache.py 2>/dev/null && ok "일봉 캐시 gap<1 수정 존재" || ng "일봉 캐시 정지 버그 미수정"

echo "-- 3) 비용 기본값(실측 0.175%) --"
if $PY -c "from backend.core.backtester.intraday_simulator import IntradaySimulator as S; import sys; sys.exit(0 if abs(float(S()._commission)-0.00175)<1e-9 else 1)" 2>/dev/null; then
  ok "IntradaySimulator 비용 default = 0.175% (실측)"
else ng "비용 default 미반영 (여전히 0=gross 선정 버그)"; fi

echo "-- 4) 일봉 캐시 신선도 --"
META=data/ohlcv_cache/meta.json
if [ -f "$META" ]; then
  grep -o '"updated":[^,}]*' "$META" | head -1 | sed 's/^/     /'
  if grep -q '"new_days_added": *0' "$META"; then
    wn "new_days_added=0 — 당일 일봉 미갱신(장중 실행이면 정상, 장 마감 후면 update_ohlcv_cache.py 점검)"
  else ok "일봉 신규 추가 있음"; fi
else wn "meta.json 없음 — 캐시 갱신 이력 미확인"; fi

echo "-- 5) 회귀 테스트 (BAR-OPS-39 + risk) --"
if $PY -m pytest backend/tests/test_bar_ops_39.py backend/tests/risk/ -q >/tmp/_vd_test.log 2>&1; then
  ok "핵심 테스트 통과 ($(grep -Eo '[0-9]+ passed' /tmp/_vd_test.log | head -1))"
else ng "테스트 실패 — /tmp/_vd_test.log 확인"; fi

echo "-- 6) 권장 env (.env.local, 경고만) --"
ENV=.env.local
if [ -f "$ENV" ]; then
  grep -q "^BARRO_COMMISSION_RATE" "$ENV" 2>/dev/null && echo "     · BARRO_COMMISSION_RATE 설정됨(요율 협의 반영)" || wn "BARRO_COMMISSION_RATE 미설정 — 협의 전이면 실측 0.00175 기본값 사용(정상)"
else wn ".env.local 없음 — 운영 환경변수 확인 필요"; fi

echo ""
echo "==== 결과: PASS $pass / FAIL $fail / WARN $warn ===="
if [ "$fail" -eq 0 ]; then
  echo "[OK] 코드 배포 정상 — cron 경로는 다음 거래일부터 BAR-OPS-39 적용(재기동 불필요)."
  echo "     supertrend 상시 프로세스 운영 시 그 프로세스만 재기동하세요."
else
  echo "[NG] 미배포/문제 $fail 건 — 위 [NG] 항목 조치 후 재실행."
fi
exit "$fail"
