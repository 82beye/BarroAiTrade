#!/bin/bash
# 장마감(15:30) 이후 OHLCV 일봉+5분봉 업데이트 → 데스크탑 3아카이브 → 82beye@gmail.com 이메일.
# crontab 15:40 weekdays 등록.
# 이메일: scripts/eod_email_split_send.py — Mail Drop 회피(분할 14MB 일반첨부 + part.00 base64 .txt 우회)
#   다중 발송. (구 send_archive_email.applescript=Mail Drop 단일첨부는 -1712 상시실패로 폐기.)
set -u
REPO=/Users/beye82/Workspace/BarroAiTrade
AI=/Users/beye82/Workspace/ai-trade/data
DESK="$HOME/Desktop"
DT=$(date +%F)
cd "$REPO" || exit 9
set -a; . "$REPO/.env.local"; set +a
echo "==== EOD ARCHIVE+EMAIL START $(date '+%F %T') (DT=$DT) ===="
echo "-- 일봉 업데이트 --";   ./.venv/bin/python scripts/update_ohlcv_cache.py    2>&1 | tail -2
# [BAR-OPS-39] 일봉 갱신 검증 — tail -2 가 스킵 사실을 묻어 6/10 정지(2954 스킵)가 안 보였음
grep -q '"new_days_added": 0' "$AI/ohlcv_cache/meta.json" 2>/dev/null && echo "⚠️ 일봉 신규 0건 — 갱신 실패 의심(meta.json 확인 필요)"
echo "-- 5분봉 업데이트(최대 30분 캡) --"; perl -e 'alarm 1800; exec @ARGV' ./.venv/bin/python scripts/update_ohlcv_cache_5m.py --days 15 2>&1 | tail -3 || echo "  (5분봉 캡/오류 — 부분갱신으로 진행)"
D="ohlcv_cache_${DT}.tar.gz"; M="ohlcv_cache_5m_${DT}.tar.gz"; T="barroaitrade_trade_data_${DT}.tar.gz"
echo "-- 압축 → 데스크탑 --"
tar -czf "$DESK/$D" -C "$AI" ohlcv_cache && echo "  일봉 ok"
tar -czf "$DESK/$M" -C "$AI" ohlcv_cache_5m && echo "  5분봉 ok"
tar -czf "$DESK/$T" -C "$REPO" data && echo "  매매로그 ok"
for f in "$D" "$M" "$T"; do gzip -t "$DESK/$f" && echo "  gzip OK $f" || echo "  gzip FAIL $f"; done
echo "-- 이메일 발송(split+base64 우회, Mail Drop 미사용) --"
"$REPO/.venv/bin/python" "$REPO/scripts/eod_email_split_send.py" "$DT" "$DESK/$D" "$DESK/$M" "$DESK/$T" 2>&1
echo "==== DONE $(date '+%F %T') ===="
