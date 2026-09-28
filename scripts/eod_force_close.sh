#!/bin/bash
# EOD 강제청산 래퍼 — 실패 시 재시도.
#
# 배경 (2026-09-22 실사례):
#   15:20 크론이 intraday_buy_daemon 종료(15:20:22 BALANCE 스냅샷 + FILL-BACKFILL)와
#   같은 순간에 시작해 **토큰 발급을 동시 요청**했다. 토큰 파일락 15s 타임아웃 →
#   락 없이 진행 → OAuth 가 rc=3(8001) 을 돌려주고, `_issue` 는 429 만 재시도하므로
#   즉시 RuntimeError 로 크래시했다. 그날은 보유 0 이라 무해했지만, 포지션이 있었다면
#   **강제청산 실패 = 익일 갭 리스크 노출**이다.
#
# 대책 두 겹:
#   ① 크론 시각을 15:20 → 15:22 로 옮겨 데몬과의 경합 자체를 없앤다 (crontab)
#   ② 그래도 실패하면 이 래퍼가 재시도한다 (일시적 mock 서버 오류 대비)
#
# 사용: bash scripts/eod_force_close.sh
#   환경변수는 호출자(cron)가 .env.local 을 소싱해 넘긴다.
set -u
REPO="${REPO:-$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)}"
PY="${EOD_CLOSE_PY:-$REPO/.venv/bin/python}"
MAX_TRY="${EOD_CLOSE_MAX_TRY:-3}"
RETRY_WAIT="${EOD_CLOSE_RETRY_WAIT:-90}"

ts() { date '+%Y-%m-%d %H:%M:%S'; }

# [2026-09-28] --exclude-strategy 에 swing_38,ai_swing 추가 — 다일보유 전략 보호.
#   왜: evaluate_holdings.py 는 force_mode(--tp/--sl 지정)에서 PositionContext 를
#   만들지 않아(L229 `if not force_mode:`) ctx=None → _evaluate_basic() 가 돌고
#   전략 override(SL −8%)·min_hold 3일이 **전부 무시**된다. 실측: −4.0% ai_swing 보유가
#   `signal=take_profit 매도수량=100` 으로 전량 청산됐다(재현 확인).
#   데몬 내부 트림은 이미 _FORCE_CLOSE_EXEMPT_STRATEGIES={swing_38,ai_swing} 로 면제하는데
#   이 크론 경로만 누락돼 있었다. 되돌리기 = 아래 목록에서 ,swing_38,ai_swing 제거.
for try in $(seq 1 "$MAX_TRY"); do
  echo "==== [$(ts)] EOD 강제청산 시도 $try/$MAX_TRY ===="
  "$PY" "$REPO/scripts/evaluate_holdings.py" \
      --exclude-strategy supertrend,limit_up_chase,swing_38,ai_swing \
      --tp -100 --sl 100 --auto-sell --no-dry-run --telegram \
      --audit-log "$REPO/data/order_audit.csv" \
      --pos-log "$REPO/data/active_positions.json"
  rc=$?
  if [ "$rc" -eq 0 ]; then
    echo "==== [$(ts)] 성공 (시도 $try) ===="
    exit 0
  fi
  if [ "$try" -lt "$MAX_TRY" ]; then
    echo "==== [$(ts)] 실패 rc=$rc — ${RETRY_WAIT}s 후 재시도 ===="
    sleep "$RETRY_WAIT"
  else
    echo "==== [$(ts)] 실패 rc=$rc — 마지막 시도 ===="
  fi
done

echo "==== [$(ts)] 🔴 ${MAX_TRY}회 모두 실패 — 보유 포지션이 있다면 수동 청산 필요 ===="
exit 1
