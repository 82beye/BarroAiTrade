#!/usr/bin/env bash
# setup_daily_collab_cron.sh — 일일 협업 요약 cron/launchd 배선 (운영 머신)
#
# 기본 동작: 점검 + 등록 명령 출력만(HITL 원칙 — 아무것도 설치 X).
# 실제 설치는 opt-in 플래그로:
#   --install-cron      crontab 에 월-금 EOD 라인 추가(멱등)
#   --install-launchd   ~/Library/LaunchAgents 에 plist 설치 후 load (macOS)
#
# 스케줄 override: COLLAB_HOUR(기본 16) · COLLAB_MIN(기본 20)
# 여러 번 실행해도 안전(멱등).
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

HOUR="${COLLAB_HOUR:-16}"
MIN="${COLLAB_MIN:-20}"
WRAP="scripts/run_daily_collab_summary.sh"
LOG="logs/daily_collab_summary.log"
CRON_LINE="$MIN $HOUR * * 1-5 cd $ROOT && bash $WRAP >> $LOG 2>&1"

PLIST_SRC="infra/com.barro.daily-collab.plist.example"
PLIST_DST="$HOME/Library/LaunchAgents/com.barro.daily-collab.plist"

install_cron=0; install_launchd=0
for a in "$@"; do
  case "$a" in
    --install-cron) install_cron=1 ;;
    --install-launchd) install_launchd=1 ;;
    -h|--help)
      echo "usage: $0 [--install-cron] [--install-launchd]"
      echo "  env: COLLAB_HOUR(기본 16) COLLAB_MIN(기본 20)"
      exit 0 ;;
    *) echo "알 수 없는 인자: $a"; exit 2 ;;
  esac
done

echo "════════ 일일 협업 요약 cron 배선 ════════"
echo "repo  : $ROOT"
echo "스케줄: 월-금 ${HOUR}:$(printf '%02d' "$MIN") KST (장 마감 15:30 + EOD 여유)"

# 1) 디렉터리 보장
mkdir -p logs docs/operations/daily-collab
echo "✓ 디렉터리: logs/ · docs/operations/daily-collab/"

# 2) python + 컴파일 점검
PY="./.venv/bin/python"; [ -x "$PY" ] || PY="./venv/bin/python"; [ -x "$PY" ] || PY="python3"
echo "✓ python: $PY"
if "$PY" -m py_compile scripts/_daily_collab_summary.py 2>/dev/null; then
  echo "✓ _daily_collab_summary.py 컴파일 OK"
else
  echo "✗ _daily_collab_summary.py 컴파일 실패"; exit 1
fi
chmod +x "$WRAP" 2>/dev/null || true

# 3) 스모크(요약 1회, 파일 미생성 — stdout)
echo "── 스모크(--stdout, 상위 6줄) ──"
"$PY" scripts/_daily_collab_summary.py --stdout 2>/dev/null | head -6 || true

# 4) crontab 등록 안내/설치
echo
echo "════════ crontab 등록 (택1·A) ════════"
echo "$CRON_LINE"
if [ "$install_cron" = "1" ]; then
  tmp="$(mktemp)"
  (crontab -l 2>/dev/null | grep -vF "$WRAP" || true) > "$tmp"
  echo "$CRON_LINE" >> "$tmp"
  crontab "$tmp"; rm -f "$tmp"
  echo "✓ crontab 등록됨:"; crontab -l | grep -F "$WRAP" || true
fi

# 5) launchd 등록 안내/설치 (macOS 대안)
echo
echo "════════ launchd 등록 (택1·B, macOS) ════════"
echo "cp $PLIST_SRC $PLIST_DST    # USERNAME/경로 치환 필요"
echo "launchctl load -w $PLIST_DST"
if [ "$install_launchd" = "1" ]; then
  if [ ! -f "$PLIST_SRC" ]; then
    echo "✗ $PLIST_SRC 없음"; exit 1
  fi
  mkdir -p "$HOME/Library/LaunchAgents"
  sed "s#/Users/USERNAME/workspace/BarroAiTrade#$ROOT#g; s#USERNAME#$(whoami)#g" \
    "$PLIST_SRC" > "$PLIST_DST"
  launchctl unload "$PLIST_DST" 2>/dev/null || true
  launchctl load -w "$PLIST_DST"
  echo "✓ launchd 등록됨: $PLIST_DST"
  launchctl list | grep -F "barro.daily-collab" || true
fi

echo
echo "검증: tail -f $LOG   ·   ls -1 docs/operations/daily-collab/"
echo "수동 1회: bash $WRAP --date \$(date +%F)"
echo "════════ 완료 ════════"
