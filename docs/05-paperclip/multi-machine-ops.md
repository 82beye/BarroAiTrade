---
tags: [runbook, ops, multi-machine, gitops]
---

# 분리된 환경 운영 가이드 (M4 개발 ↔ 인텔 2019 운영)

> 집 인텔 2019 = 24/7 운영 머신 / 외출 M4 = 개발 + 모니터링.
> GitHub 통한 코드 동기 + 텔레그램 통한 모니터링.
> [[runbook-ops|기본 RUNBOOK]] | [[security-rotation|보안 회전]] | [[../00-index/system-flow|시스템 흐름도]]

---

## 환경 분리 — 역할 정리

```
┌─────────────────────────┐         ┌──────────────────────────┐
│   M4 맥북 (개발)         │  GitHub  │  2019 인텔 (운영, 24/7)  │
│                          │ ←──────→ │                           │
│ ✓ 코드 작성               │         │ ✓ cron 실행               │
│ ✓ 시뮬 분석               │         │ ✓ 봇 데몬                 │
│ ✓ 정책 검토               │         │ ✓ 데이터 누적             │
│ ✓ git push                │         │ ✓ 텔레그램 알림 발송      │
│ ✓ 텔레그램 모니터          │         │ ✓ logs/data 보관          │
└─────────────────────────┘         └──────────────────────────┘
                ↓                                ↓
          GitHub                          모바일 텔레그램
```

핵심 원칙:
- **코드 = GitHub** 통해서만 동기 (M4 push → 인텔 pull)
- **데이터 = 인텔에만** (`data/`, `logs/`, `reports/`, `.env.local` — gitignored)
- **모니터링 = 텔레그램** (양쪽 기기에서 동일하게 확인)

---

## 1. 인텔 2019 (운영 머신) 초기 설정

### A) 저장소 클론 + 환경 구성 (1회)

```bash
# 인텔 머신에서
cd ~
git clone git@github.com:82beye/BarroAiTrade.git
cd BarroAiTrade

# Python 3.14 (인텔 호환)
brew install python@3.14
python3.14 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt

# 환경변수 — git 추적 X (운영 머신에만)
cat > .env.local <<'EOF'
KIWOOM_APP_KEY=...
KIWOOM_APP_SECRET=...
KIWOOM_BASE_URL=https://mockapi.kiwoom.com
TELEGRAM_BOT_TOKEN=...
TELEGRAM_CHAT_ID=...
EOF
chmod 600 .env.local

# 디렉토리
mkdir -p logs data reports
```

### B) 24/7 안정성 설정 (인텔 2019 특화)

```bash
# 1. 슬립 방지 (인텔 맥은 절전 모드 → cron 실행 안 됨)
sudo pmset -c sleep 0           # 전원 연결 시 슬립 X
sudo pmset -c displaysleep 30   # 디스플레이만 30분 후 꺼짐
sudo pmset -c disksleep 0       # 디스크 슬립 X
sudo pmset -c autorestart 1     # 정전 후 자동 재부팅

# 확인
pmset -g | grep -E "sleep|hibernate|autorestart"
```

### C) launchd 봇 데몬 등록 (자동 재시작)

```bash
# 환경변수 wrapper 스크립트
cat > ~/workspace/BarroAiTrade/scripts/run_bot_with_env.sh <<'EOF'
#!/bin/bash
cd /Users/beye/BarroAiTrade
set -a; . ./.env.local; set +a
exec ./.venv/bin/python scripts/run_telegram_bot.py
EOF
chmod +x ~/workspace/BarroAiTrade/scripts/run_bot_with_env.sh

# launchd plist
sudo tee ~/Library/LaunchAgents/com.barroai.telegram-bot.plist <<'EOF'
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key><string>com.barroai.telegram-bot</string>
    <key>ProgramArguments</key>
    <array>
        <string>/Users/beye/BarroAiTrade/scripts/run_bot_with_env.sh</string>
    </array>
    <key>WorkingDirectory</key><string>/Users/beye/BarroAiTrade</string>
    <key>RunAtLoad</key><true/>
    <key>KeepAlive</key><true/>
    <key>StandardOutPath</key><string>/Users/beye/BarroAiTrade/logs/telegram_bot.log</string>
    <key>StandardErrorPath</key><string>/Users/beye/BarroAiTrade/logs/telegram_bot.err</string>
</dict>
</plist>
EOF

# 등록 + 시작
launchctl load ~/Library/LaunchAgents/com.barroai.telegram-bot.plist

# 동작 확인
launchctl list | grep barroai
# → "PID  Status  com.barroai.telegram-bot" 형식
```

### D) cron 4건 등록

```bash
crontab -e
```
```cron
SHELL=/bin/bash
HOME=/Users/beye
REPO=/Users/beye/BarroAiTrade

# 09:30 매수
30 9 * * 1-5 cd $REPO && set -a; . ./.env.local; set +a; \
  ./.venv/bin/python scripts/simulate_leaders.py --top 5 --check-balance --execute --telegram \
  --log data/simulation_log.csv --audit-log data/order_audit.csv \
  >> logs/morning.log 2>&1

# 매시간 매도 평가
0 10-15 * * 1-5 cd $REPO && set -a; . ./.env.local; set +a; \
  ./.venv/bin/python scripts/evaluate_holdings.py --auto-sell --telegram \
  --audit-log data/order_audit.csv >> logs/eval.log 2>&1

# 15:20 강제 청산
20 15 * * 1-5 cd $REPO && set -a; . ./.env.local; set +a; \
  ./.venv/bin/python scripts/evaluate_holdings.py --tp -100 --sl 100 --auto-sell --telegram \
  --audit-log data/order_audit.csv >> logs/closing.log 2>&1

# 16:00 일일 리포트
0 16 * * 1-5 cd $REPO && set -a; . ./.env.local; set +a; \
  ./.venv/bin/python scripts/generate_daily_report.py data/simulation_log.csv \
  --output "reports/$(date +\%F).md" --telegram >> logs/report.log 2>&1
```

⚠️ **macOS Catalina+ Full Disk Access 권한 필수**:
- 시스템 환경설정 → 보안 및 개인정보 → 전체 디스크 접근 → `/usr/sbin/cron` 추가

---

## 2. M4 (개발 머신) 설정

### A) 저장소 클론 (개발 전용)

```bash
cd ~/workspace
git clone git@github.com:82beye/BarroAiTrade.git
cd BarroAiTrade

# .venv (M4 ARM 호환)
python3.14 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

### B) 개발용 .env.local (선택)

```bash
# 개발 전용 (실 거래 절대 X)
cat > .env.local <<'EOF'
KIWOOM_APP_KEY=별도-개발용
KIWOOM_BASE_URL=https://mockapi.kiwoom.com
LIVE_TRADING_ENABLED=             # ← 절대 true 안 함
TELEGRAM_BOT_TOKEN=별도-개발봇    # ← 운영봇과 분리
TELEGRAM_CHAT_ID=...
EOF
```

⚠️ M4 에서 봇 데몬 **실행 X** — 운영 봇은 인텔에서만.

### C) 개발 워크플로우

```bash
# 1. 코드 수정 + 회귀 테스트
.venv/bin/pytest backend/tests/ -q

# 2. 로컬 시뮬 검증 (dry_run)
.venv/bin/python scripts/simulate_leaders.py --top 3
# → 실 주문 X, 분석만

# 3. PR 생성 → 머지
git checkout -b feature/xxx
git push -u origin feature/xxx
gh pr create
gh pr merge --squash
```

### D) 운영 머신 동기 (PR 머지 후, 인텔 머신에서)

```bash
cd ~/workspace/BarroAiTrade
git pull origin main

# 봇 재시작 (코드 변경 반영)
launchctl unload ~/Library/LaunchAgents/com.barroai.telegram-bot.plist
launchctl load ~/Library/LaunchAgents/com.barroai.telegram-bot.plist
```

---

## 3. GitOps 흐름

```
M4 개발                        GitHub                    인텔 운영
  │                              │                          │
  ├─ 코드 수정                    │                          │
  ├─ pytest 통과                  │                          │
  ├─ git push (feature 브랜치)  →  │                          │
  ├─ gh pr create                 │                          │
  ├─ gh pr merge ────────────→  main 갱신                    │
  │                              │                          │
  │                              │  ← git pull              │
  │                              │     (수동 또는 자동)      │
  │                              │       ↓                  │
  │                              │       launchctl restart   │
  │                              │       (봇 재시작)          │
  │                              │                          │
  │                              │              ┌─ data/ ────┤ (운영 머신만)
  │                              │              ├─ logs/     │
  │                              │              ├─ reports/  │
  │                              │              └─ .env.local│
```

⚠️ **gitignored** (운영 머신에만 보관):
- `.env.local` (API 키)
- `data/*.csv` (시뮬·감사 누적)
- `data/policy.json` (학습된 정책)
- `logs/`
- `reports/`

---

## 4. 원격 모니터링 (M4 에서 인텔 점검)

### A) 텔레그램 — 가장 간단

같은 chat_id 면 어디서 명령해도 인텔의 봇이 응답:
- `/balance` → 인텔의 잔고
- `/audit` → 인텔의 거래
- `/history` → 인텔의 누적 시뮬

→ 별도 설정 X.

### B) SSH / Tailscale — 깊은 점검

```bash
# 옵션 1: 일반 SSH (시스템 환경설정 → 공유 → 원격 로그인)
ssh beye@<인텔-맥-IP>

# 옵션 2: Tailscale (자동 VPN, 추천)
brew install tailscale
sudo tailscale up
# → 어디서든 magic DNS 로 접근 (예: ssh beye@home-mac.tail-XXX.ts.net)
```

```bash
# M4 에서 인텔 로그 직접 보기
ssh beye@home-mac "tail -50 ~/workspace/BarroAiTrade/logs/morning.log"
ssh beye@home-mac "grep BLOCKED ~/workspace/BarroAiTrade/data/order_audit.csv | tail -20"
```

### C) 데이터 백업 — git 외 채널 (선택)

`data/` 는 gitignored. 백업 옵션:

| 옵션 | 명령 | 빈도 |
|------|------|------|
| **iCloud Drive** | 심볼릭 링크 | 자동 |
| rsync to M4 | `rsync -av beye@home:~/workspace/BarroAiTrade/data/ ~/Backup/` | 매주 cron |
| S3/Dropbox | aws s3 sync 또는 rclone | 매일 |

권장: **iCloud Drive 심볼릭 링크**:
```bash
# 인텔에서
mv ~/workspace/BarroAiTrade/data ~/Library/Mobile\ Documents/com~apple~CloudDocs/BarroAiTrade-data
ln -s ~/Library/Mobile\ Documents/com~apple~CloudDocs/BarroAiTrade-data ~/workspace/BarroAiTrade/data

# M4 에서 동일 위치 자동 동기 → 분석 가능
```

---

## 5. 일상 운영 루틴 — 기기별 분담

### 인텔 (집, 자동) — 손 안 댐

- 24/7 봇 실행 (launchctl KeepAlive)
- cron 4건 (09:30 / 매시간 / 15:20 / 16:00)
- 데이터 누적 (`data/`)

### M4 (이동 중, 사무실) — 모니터링

```
모바일 텔레그램          → 알림 + 명령 (가장 빠름)
M4 텔레그램 데스크탑       → 동일 + 큰 화면
M4 SSH                    → logs/audit 깊은 분석
M4 git pull + 코드 수정    → 정책/로직 변경 → push
```

### 시나리오별 어디서?

| 상황 | 위치 |
|------|------|
| 일상 알림 확인 | 모바일 텔레그램 |
| 잔고/매도 평가 | 모바일 명령 |
| 정책 추천 적용 (`/tune apply`) | 모바일 또는 M4 봇 |
| 코드 변경 / 버그 fix | M4 → push → 인텔 pull |
| logs 깊은 분석 | M4 SSH |
| 사고 — Kill Switch | 모바일 (`/cancel_order`) + SSH (봇 정지) |

---

## 6. 인텔 2019 특수 주의

### 메모리 / CPU
- 인텔 2019 16GB 가정 → 봇 + cron 정도는 충분 (각 ~100MB)
- 동시 다른 앱 무거운 것 X (Chrome 50탭 등 피하기)

### 네트워크 단절
- 와이파이 끊기면 텔레그램/키움 모두 차단
- → `keepalive` 또는 유선 LAN 권장
- 봇 데몬은 5초 후 자동 재시도 (구현됨)

### 절전 모드 함정
```bash
# 확인
pmset -g | grep -E "sleep|hibernate"

# 절대 sleep 0 / displaysleep 만 30
sudo pmset -c sleep 0 displaysleep 30 disksleep 0
sudo pmset -c autorestart 1
```

### 시간 동기 (cron 정확도)
```bash
sudo systemsetup -setusingnetworktime on
sudo systemsetup -setnetworktimeserver time.apple.com
```

---

## 7. 사고 방지 정책 (분리 환경 특화)

| 위험 | 방지 |
|------|------|
| M4 에서 실수로 실 주문 | M4 의 `.env.local` 에 `LIVE_TRADING_ENABLED` 없음 / mockapi 만 |
| M4 의 봇이 운영 봇과 충돌 | M4 는 별도 dev bot token (또는 봇 미실행) |
| 인텔 코드가 옛 버전 | git pull 후 launchctl 재시작 (체크리스트) |
| 인텔 슬립으로 cron 누락 | pmset 절대 점검 (위) |
| 양쪽 데이터 불일치 | 데이터는 인텔만 진실 (M4 는 읽기 전용) |
| GitHub 머지된 코드 인텔 미반영 | 매주 월요일 09:00 자동 git pull cron 추가 |

### 자동 git pull cron (인텔 추가, 선택)
```cron
# 매일 09:00 (cron 실행 전 30분 buffer) git pull
0 9 * * 1-5 cd /Users/beye/BarroAiTrade && git pull origin main >> logs/git-pull.log 2>&1 \
  && launchctl unload ~/Library/LaunchAgents/com.barroai.telegram-bot.plist 2>&1 \
  && launchctl load ~/Library/LaunchAgents/com.barroai.telegram-bot.plist 2>&1
```

⚠️ 주의: 자동 pull 이 머지 충돌 시 cron 실패. 안전 위해 **수동 pull** 권장 (PR 머지 후 직접).

---

## 8. 즉시 시작 체크리스트

### 인텔 2019 (집)
- [ ] git clone + .venv + requirements
- [ ] `.env.local` 키 입력 (모의 환경 먼저)
- [ ] `pmset` 절전 비활성
- [ ] launchd plist 등록 + 봇 데몬 시작
- [ ] cron 4건 등록 + Full Disk Access 권한
- [ ] `/ping` 모바일 응답 확인

### M4 (개발)
- [ ] git clone (워크스페이스용)
- [ ] `.venv` 구성
- [ ] `.env.local` 만들지 X 또는 dev 전용
- [ ] 텔레그램 봇은 인텔에서만 실행 (M4 미실행)
- [ ] SSH 또는 Tailscale 설치 (선택)

### 운영 검증 (1주)
- [ ] 매일 cron 정시 실행 (logs 점검)
- [ ] 텔레그램 알림 끊김 X
- [ ] `/diff` 데이터 누적 정상
- [ ] 봇 launchctl 자동 재시작 정상 (수동 kill 후 5초 내 부활 확인)

---

## 9. 권장 보강 (나중에 — 선택)

| 우선 | 도구 | 효과 |
|------|------|------|
| 🟢 즉시 | iCloud Drive 심볼릭 링크 | data/ 자동 백업 |
| 🟢 즉시 | Tailscale | 어디서나 SSH (M4 ↔ 인텔) |
| 🟡 1주 후 | UptimeRobot 텔레그램 | 봇 데몬 다운 즉시 알림 |
| 🟡 1개월 후 | Grafana + Prometheus | 시각적 대시보드 |
| 🔴 선택 | AWS EC2 이주 | 인텔 노트북 부담 0 (월 ~$10) |

---

## 10. 트러블슈팅 — 분리 환경 특화

| 증상 | 원인 | 조치 |
|------|------|------|
| 인텔 cron 실행 안 됨 | 슬립 모드 | `pmset -c sleep 0` 적용 |
| cron 권한 오류 | Full Disk Access X | 시스템 환경설정 → 권한 추가 |
| launchctl plist 무시 | 권한 또는 경로 오류 | `launchctl error <Label>` 확인 |
| M4 에서 명령했는데 인텔 응답 X | 텔레그램 봇 데몬 다운 | 인텔 SSH → 데몬 재시작 |
| git pull 충돌 | 인텔 working dir 변경 (실수) | `git stash` 후 pull, 정상화 |
| 봇 환경변수 못 읽음 | wrapper 스크립트 누락 | `run_bot_with_env.sh` 사용 |
| 인텔 부팅 후 봇 X | launchd 등록 실패 | `launchctl list` 확인 + 재등록 |

---

## 관련 문서

- [[runbook-ops|기본 운영 RUNBOOK]] — 단일 기기 기준
- [[security-rotation|보안 키 회전]]
- [[../00-index/system-flow|시스템 흐름도]] (Mermaid 9개)
- [[../00-index/ops-track-index|OPS 작업 인덱스]] (26 BAR)

---

*최종 업데이트: 2026-05-11 (분리 환경 운영 가이드 v1)*
