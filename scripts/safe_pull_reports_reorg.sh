#!/usr/bin/env bash
#
# safe_pull_reports_reorg.sh — reports/ -> docs/04-report/daily 심링크 reorg 안전 pull (운영 머신용)
#
# 배경: 커밋 4d975f5 / b25ceaf 로 root reports/ 가 docs/04-report/daily 심링크가 됨.
#       운영 머신은 reports/ 에 매일 산출물(strategy_audit·daily md 등)을 쓰므로,
#       그냥 git pull 하면 git 이 "비어있지 않은 reports/ 디렉토리"를 심링크로 못 바꿔 실패한다.
#       이 스크립트는 reports/ 의 미커밋/미추적 산출물을 백업 -> 정리 -> pull -> 복원한다.
#
# 사용(운영 머신):
#   cd <REPO>
#   git fetch origin
#   git show origin/main:scripts/safe_pull_reports_reorg.sh > /tmp/safe_pull.sh
#   bash /tmp/safe_pull.sh             # 실제 실행
#   bash /tmp/safe_pull.sh --dry-run   # 미리보기(변경 없음)
#
# 특징: 멱등(이미 심링크면 단순 pull) · 비파괴(백업 후 진행) · bash 3.2(macOS 기본) 호환.

set -euo pipefail

DRY_RUN=0
[ "${1:-}" = "--dry-run" ] && DRY_RUN=1

c_b='\033[1;34m'; c_y='\033[1;33m'; c_r='\033[1;31m'; c_0='\033[0m'
log()  { printf "${c_b}[safe-pull]${c_0} %s\n" "$*"; }
warn() { printf "${c_y}[safe-pull WARN]${c_0} %s\n" "$*"; }
die()  { printf "${c_r}[safe-pull FAIL]${c_0} %s\n" "$*" >&2; exit 1; }

git rev-parse --show-toplevel >/dev/null 2>&1 || die "git 저장소가 아님. REPO 루트에서 실행하세요."
REPO="$(git rev-parse --show-toplevel)"
cd "$REPO"
BRANCH="$(git rev-parse --abbrev-ref HEAD)"
log "REPO=$REPO  BRANCH=$BRANCH  DRY_RUN=$DRY_RUN"

# 0) 이미 심링크 -> reorg 적용 완료. 단순 pull.
if [ -L reports ]; then
  log "reports 가 이미 심링크(-> $(readlink reports)) — 단순 git pull."
  [ "$DRY_RUN" = "1" ] && { log "(dry-run) git pull 생략."; exit 0; }
  git pull --ff-only origin "$BRANCH"
  log "완료(이미 마이그레이션됨)."
  exit 0
fi
# reports/ 없음 -> 단순 pull
if [ ! -e reports ]; then
  log "reports 없음 — 단순 git pull."
  [ "$DRY_RUN" = "1" ] && { log "(dry-run) git pull 생략."; exit 0; }
  git pull --ff-only origin "$BRANCH"
  exit 0
fi
[ -d reports ] || die "reports 가 디렉토리도 심링크도 아님: $(ls -ld reports)"

TS="$(date +%Y%m%d_%H%M%S)"
BACKUP="$REPO/.reports_premigration_backup_$TS"
UF="$(mktemp)"; MF="$(mktemp)"
trap 'rm -f "$UF" "$MF"' EXIT

# 1) 미커밋 상태 수집 (미추적 incl ignored + 수정-추적)
git ls-files --others -z -- reports/ > "$UF" || true
git diff  --name-only -z -- reports/ > "$MF" || true
NUM_U="$(tr -cd '\0' < "$UF" | wc -c | tr -d ' ')"
NUM_M="$(tr -cd '\0' < "$MF" | wc -c | tr -d ' ')"
log "reports/ 미추적 ${NUM_U}건 · 수정-추적 ${NUM_M}건"

if [ "$DRY_RUN" = "1" ]; then
  log "(dry-run) 아래를 백업->제거->pull->복원할 예정:"
  [ "$NUM_U" -gt 0 ] && { echo "--- 미추적(제거 후 복원) ---"; tr '\0' '\n' < "$UF" | sed 's/^/  /'; }
  [ "$NUM_M" -gt 0 ] && { echo "--- 수정-추적(되돌림 후 복원) ---"; tr '\0' '\n' < "$MF" | sed 's/^/  /'; }
  log "(dry-run) 변경 없음."
  exit 0
fi

# 2) 백업 (비파괴) — 미추적 + 수정-추적 모두
if [ "$NUM_U" -gt 0 ] || [ "$NUM_M" -gt 0 ]; then
  mkdir -p "$BACKUP"
  while IFS= read -r -d '' f; do
    [ -z "$f" ] && continue
    mkdir -p "$BACKUP/$(dirname "$f")"; cp -p "$f" "$BACKUP/$f"
  done < "$UF"
  while IFS= read -r -d '' f; do
    [ -z "$f" ] && continue
    [ -f "$f" ] || continue
    mkdir -p "$BACKUP/$(dirname "$f")"; cp -p "$f" "$BACKUP/$f"
  done < "$MF"
  log "백업 완료 -> $BACKUP"
else
  log "미커밋 산출물 없음 — 백업 불필요."
fi

# 3) reports/ 정리 (git 이 심링크로 교체할 수 있도록)
while IFS= read -r -d '' f; do [ -n "$f" ] && rm -f "$f"; done < "$UF"   # 미추적 제거(백업됨)
[ "$NUM_M" -gt 0 ] && { git checkout -- reports/ || true; }              # 수정-추적 HEAD 로 되돌림(백업됨)
find reports -type d -empty -delete 2>/dev/null || true                  # 빈(미추적) 디렉토리 정리

# 4) pull (reports/ -> 심링크로 교체됨)
log "git pull --ff-only origin $BRANCH ..."
git pull --ff-only origin "$BRANCH" || die "pull 실패. 백업 보존: $BACKUP (수동 복구 필요)"

# 5) 심링크 확인
[ -L reports ] || die "pull 후에도 reports 가 심링크가 아님. 백업: $BACKUP"
TARGET="$(readlink reports)"
log "reports -> $TARGET (심링크 적용됨)"

# 6) 백업분 복원 (reports/ 심링크 경유 -> 새 위치 docs/04-report/daily)
if [ -d "$BACKUP" ]; then
  log "백업 산출물 복원..."
  ( cd "$BACKUP" && find . -type f -print0 ) | while IFS= read -r -d '' rel; do
    rel="${rel#./}"
    mkdir -p "$(dirname "$rel")"      # reports/... -> 심링크 경유 새 위치에 디렉토리 생성
    cp -p "$BACKUP/$rel" "$rel"
  done
  log "복원 완료. 백업 보관: $BACKUP (정상 확인 후 삭제 가능)"
fi

# 7) 검증
[ -e "reports/." ] && log "reports/ 접근 OK (-> $TARGET)"
if [ -f scripts/verify_deploy.sh ]; then
  log "verify_deploy.sh 실행..."
  bash scripts/verify_deploy.sh || warn "verify_deploy 비정상 — 로그 확인."
fi
log "✅ 안전 pull 완료. reports -> $TARGET · 미커밋 산출물 복원됨."
