#!/bin/bash
# AI-Trade 실시간 오류 모니터링 + 프로세스 감시 스크립트
# - 장시간(08:25~15:35)에만 프로세스 감시 및 자동 재시작
# - 장외시간에는 로그 감시만 (재시작 안 함)
# - 주말(토/일) 자동 휴면
# - 당일 로그 파일 자동 탐지

PROJECT_DIR="/Users/beye82/Workspace/ai-trade"
LOG_DIR="${PROJECT_DIR}/logs"

# config/.env에서 텔레그램 설정 로드
if [ -f "${PROJECT_DIR}/config/.env" ]; then
    BOT_TOKEN=$(grep -E '^TELEGRAM_BOT_TOKEN=' "${PROJECT_DIR}/config/.env" | cut -d'=' -f2-)
    CHAT_ID=$(grep -E '^TELEGRAM_CHAT_ID=' "${PROJECT_DIR}/config/.env" | cut -d'=' -f2-)
fi
BOT_TOKEN="${BOT_TOKEN:-}"
CHAT_ID="${CHAT_ID:-}"

LAST_LINE=0
ERROR_COUNT=0
RESTART_COUNT=0
MAX_RESTARTS=3          # 하루 최대 재시작 횟수
CURRENT_LOG_DATE=""

# ─── 장시간 설정 (KST) ───
MARKET_START_H=8   MARKET_START_M=25    # 08:25 시작 (pre-market 스캔 포함)
MARKET_END_H=15    MARKET_END_M=35      # 15:35 종료 (청산 완료 대기)

send_telegram() {
    curl -s -X POST "https://api.telegram.org/bot${BOT_TOKEN}/sendMessage" \
        -d "chat_id=${CHAT_ID}" \
        -d "text=$1" \
        -d "parse_mode=HTML" > /dev/null 2>&1
}

is_weekday() {
    local dow=$(date '+%u')  # 1=월 ... 7=일
    [ "$dow" -le 5 ]
}

is_market_hours() {
    local now_m=$(( $(date '+%H') * 60 + $(date '+%M') ))
    local start_m=$(( MARKET_START_H * 60 + MARKET_START_M ))
    local end_m=$(( MARKET_END_H * 60 + MARKET_END_M ))
    [ "$now_m" -ge "$start_m" ] && [ "$now_m" -le "$end_m" ]
}

get_today_log() {
    echo "${LOG_DIR}/ai-trade_$(date '+%Y-%m-%d').log"
}

# ─── 시작 알림 ───
send_telegram "<b>AI-Trade 모니터 시작</b>
장시간 감시: ${MARKET_START_H}:${MARKET_START_M}~${MARKET_END_H}:${MARKET_END_M}
장외시간: 로그 감시만 (재시작 안 함)
최대 재시작: ${MAX_RESTARTS}회/일"

echo "[$(date '+%H:%M:%S')] 모니터링 시작"

while true; do
    # ── 주말 휴면 ──
    if ! is_weekday; then
        sleep 600  # 10분 간격 체크
        continue
    fi

    # ── 로그 파일 날짜 변경 감지 ──
    TODAY_LOG=$(get_today_log)
    if [ "$TODAY_LOG" != "$CURRENT_LOG_DATE" ]; then
        CURRENT_LOG_DATE="$TODAY_LOG"
        LAST_LINE=0
        ERROR_COUNT=0
        RESTART_COUNT=0
        echo "[$(date '+%H:%M:%S')] 로그 파일 전환: $(basename "$TODAY_LOG")"
    fi

    # ── 장시간: 프로세스 감시 + 자동 재시작 ──
    if is_market_hours; then
        if ! pgrep -f "main.py --mode simulation" > /dev/null 2>&1; then
            if [ "$RESTART_COUNT" -lt "$MAX_RESTARTS" ]; then
                RESTART_COUNT=$((RESTART_COUNT + 1))
                echo "[$(date '+%H:%M:%S')] 프로세스 없음 — 재시작 (${RESTART_COUNT}/${MAX_RESTARTS})"
                send_telegram "<b>AI-Trade 프로세스 종료 감지</b>
시각: $(date '+%H:%M:%S')
재시작 시도 (${RESTART_COUNT}/${MAX_RESTARTS})"

                cd "$PROJECT_DIR" && python3 main.py --mode simulation >> /tmp/ai-trade-restart.log 2>&1 &
                NEW_PID=$!
                sleep 5

                if kill -0 "$NEW_PID" 2>/dev/null; then
                    send_telegram "<b>프로세스 재시작 성공</b> (PID: ${NEW_PID})"
                else
                    send_telegram "<b>프로세스 재시작 실패</b> — 즉시 종료됨"
                fi
                sleep 30
                continue
            else
                echo "[$(date '+%H:%M:%S')] 최대 재시작 횟수 초과"
                # 초과 알림은 1회만
                if [ "$RESTART_COUNT" -eq "$MAX_RESTARTS" ]; then
                    RESTART_COUNT=$((RESTART_COUNT + 1))  # 다시 안 보내도록
                    send_telegram "<b>AI-Trade 재시작 한도 초과</b>
${MAX_RESTARTS}회 재시작 모두 실패.
수동 확인 필요."
                fi
            fi
        fi
    fi
    # 장외시간: 프로세스 감시 안 함 (재시작 안 함)

    # ── 로그 파일 감시 ──
    if [ -f "$TODAY_LOG" ]; then
        CURRENT_LINE=$(wc -l < "$TODAY_LOG" 2>/dev/null || echo 0)
        if [ "$CURRENT_LINE" -gt "$LAST_LINE" ]; then
            NEW_LOGS=$(tail -n +$((LAST_LINE + 1)) "$TODAY_LOG" | head -n $((CURRENT_LINE - LAST_LINE)))
            LAST_LINE=$CURRENT_LINE

            # CRITICAL / Traceback 감지
            if echo "$NEW_LOGS" | grep -qE "\[CRITICAL\]|Traceback \(most recent|치명적 오류"; then
                ERROR_MSG=$(echo "$NEW_LOGS" | grep -E "\[CRITICAL\]|Traceback|Exception:|치명적" | head -3)
                echo "[$(date '+%H:%M:%S')] CRITICAL: $ERROR_MSG"
                send_telegram "<b>AI-Trade CRITICAL 오류</b>
시각: $(date '+%H:%M:%S')
<code>$(echo "$ERROR_MSG" | head -3)</code>"
            fi

            # ERROR 감지 (Rate Limit / 텔레그램 제외)
            if echo "$NEW_LOGS" | grep -E "\[ERROR\]" | grep -qvE "429|Rate limit|텔레그램 발송 실패"; then
                ERROR_LINE=$(echo "$NEW_LOGS" | grep -E "\[ERROR\]" | grep -vE "429|Rate limit|텔레그램 발송 실패" | tail -1)
                ERROR_COUNT=$((ERROR_COUNT + 1))
                echo "[$(date '+%H:%M:%S')] ERROR(${ERROR_COUNT}): $ERROR_LINE"
                if [ $((ERROR_COUNT % 5)) -eq 0 ]; then
                    send_telegram "<b>AI-Trade 반복 오류 (${ERROR_COUNT}회)</b>
<code>$(echo "$ERROR_LINE" | head -1)</code>"
                fi
            fi

            # 매매 이벤트 감지
            if is_market_hours; then
                if echo "$NEW_LOGS" | grep -qE "매수 주문 성공|매도 주문 성공|긴급 청산"; then
                    TRADE_MSG=$(echo "$NEW_LOGS" | grep -E "매수 주문|매도 주문|긴급 청산" | tail -1)
                    echo "[$(date '+%H:%M:%S')] 매매: $TRADE_MSG"
                fi
            fi

            # 장 마감 리포트 감지
            if echo "$NEW_LOGS" | grep -qE "일일 결산|시스템 종료 완료"; then
                echo "[$(date '+%H:%M:%S')] 장 마감 감지"
            fi
        fi
    fi

    sleep 30
done
