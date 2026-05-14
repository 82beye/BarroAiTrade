#!/bin/bash
cd /Users/beye82/Workspace/BarroAiTrade

# 중복 실행 방지 (동일 봇 2개 → 409 Conflict)
LOCKFILE="/tmp/barroai-telegram-bot.lock"
if [ -f "$LOCKFILE" ]; then
    PID=$(cat "$LOCKFILE")
    if kill -0 "$PID" 2>/dev/null; then
        echo "이미 실행 중: PID=$PID"
        exit 0
    fi
fi
echo $$ > "$LOCKFILE"
trap "rm -f $LOCKFILE" EXIT

# 이전 long-polling 세션 만료 대기 (텔레그램 409 방지)
sleep 35

set -a; . ./.env.local; set +a
exec ./.venv/bin/python scripts/run_telegram_bot.py
