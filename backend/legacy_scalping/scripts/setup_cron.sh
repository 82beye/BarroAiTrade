#!/bin/bash
# =============================================================================
# 주식단테 데이트레이딩 시스템 - Cron 스케줄 설정
# =============================================================================
#
# 스케줄 요약 (월~금):
#   08:00  계좌 현황 텔레그램 발송 (장 시작 전 점검)
#   08:25  모의매매 시스템 시작 (스캔 → 매매 → 14:50 청산 → 리포트)
#   15:30  OHLCV 캐시 업데이트 (장 마감 후 전종목 일봉 저장)
#   16:00  계좌 현황 텔레그램 발송 (마감 후 최종 확인)
#
# 사용법:
#   chmod +x scripts/setup_cron.sh
#   ./scripts/setup_cron.sh          # cron 등록
#   ./scripts/setup_cron.sh remove   # cron 제거
# =============================================================================

PROJECT_DIR="/Users/beye82/Workspace/ai-trade"
PYTHON="/usr/bin/python3"
LOG_FILE="${PROJECT_DIR}/logs/cron.log"

# 기존 ai-trade cron 제거
remove_cron() {
    crontab -l 2>/dev/null | grep -v "ai-trade" | crontab -
    echo "ai-trade cron 작업 제거 완료"
}

# cron 등록
install_cron() {
    # 기존 ai-trade 항목 제거 후 새로 추가
    (crontab -l 2>/dev/null | grep -v "ai-trade"; cat <<CRON
# === ai-trade 데이트레이딩 시스템 ===
# 08:00 장 시작 전 계좌 현황 점검
0 8 * * 1-5 cd ${PROJECT_DIR} && ${PYTHON} main.py --account-status >> ${LOG_FILE} 2>&1  # ai-trade
# 08:25 모의매매 시스템 시작 (스캔 → 장중매매 → 14:50 청산 → 리포트)
25 8 * * 1-5 cd ${PROJECT_DIR} && ${PYTHON} main.py --mode simulation >> ${LOG_FILE} 2>&1  # ai-trade
# 15:30 장 마감 후 OHLCV 캐시 업데이트
30 15 * * 1-5 cd ${PROJECT_DIR} && ${PYTHON} main.py --update-cache >> ${LOG_FILE} 2>&1  # ai-trade
# 16:00 마감 후 계좌 최종 현황
0 16 * * 1-5 cd ${PROJECT_DIR} && ${PYTHON} main.py --account-status >> ${LOG_FILE} 2>&1  # ai-trade
CRON
) | crontab -

    echo "ai-trade cron 작업 등록 완료"
    echo ""
    echo "등록된 스케줄:"
    crontab -l | grep "ai-trade"
}

# 메인
if [ "$1" = "remove" ]; then
    remove_cron
else
    install_cron
fi
