#!/bin/bash
# ai-trade 크론탭 설치 스크립트
# 실행: bash scripts/install_cron.sh

DIR="/Users/beye82/Workspace/ai-trade"
PY="/usr/bin/python3"
LOG="$DIR/logs/cron.log"

crontab - << EOF
# === ai-trade 데이트레이딩 시스템 ===
# 08:00 장 시작 전 계좌 현황 점검
0 8 * * 1-5 cd $DIR && $PY main.py --account-status >> $LOG 2>&1  # ai-trade
# 08:25 모의매매 시스템 시작 (스캔 → 장중매매 → 14:50 청산 → 리포트)
25 8 * * 1-5 cd $DIR && $PY main.py --mode simulation >> $LOG 2>&1  # ai-trade
# 15:30 장 마감 후 OHLCV 캐시 업데이트
30 15 * * 1-5 cd $DIR && $PY main.py --update-cache >> $LOG 2>&1  # ai-trade
# 16:00 마감 후 계좌 최종 현황
0 16 * * 1-5 cd $DIR && $PY main.py --account-status >> $LOG 2>&1  # ai-trade
# 21:00 장 마감 후 매매 분석 + 전략 팀 에이전트 고도화
0 21 * * 1-5 cd $DIR && $PY main.py --post-market >> $LOG 2>&1  # ai-trade
EOF

echo "크론탭 설치 완료:"
crontab -l
