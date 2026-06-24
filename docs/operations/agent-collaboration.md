# 에이전트 협업 (텔레그램 그룹채팅) — 시스템 구조

> 7역할 에이전트가 텔레그램 그룹채팅으로 다자토론하며 매매 인사이트를 도출하는 협업 시스템.
> **진실원천은 JSONL 로그**(`data/agent_room/`), 텔레그램은 **비권위 미러(display-only)**.
> 일일 도출·수정 요약은 [[#일일 요약본]] 의 `_daily_collab_summary.py` 로 생성.

---

## 개요

에이전트들이 사람 개입 없이도 장중에 자율적으로 토론하고, 그 결과가 텔레그램 그룹채팅(`@barroAiTrade_agents_bot`)에 실시간 미러된다. 모든 메시지·결정은 append-only JSONL 로 기록되며, 텔레그램은 사람이 흐름을 관찰하기 위한 표시용일 뿐 권위가 없다(주문 경로와 단절).

핵심 컴포넌트:

| 컴포넌트 | 파일 | 역할 |
|----------|------|------|
| 메시지 버스 | `backend/core/agents/room_bus.py` | 진실원천 JSONL append + 텔레그램 미러 |
| 다자토론 엔진 | `scripts/agent_room_discuss.py` | 7역할 × N라운드 토론 → 합의 합성 |
| 코디네이터 | `scripts/agent_room_coordinator.py` | 트리거 감지(자율/질문) + 결정 합성·게시 |

---

## 아키텍처 · 데이터 흐름

```
장중 09:00~15:30 (KST)
   │
   ├─[트리거1] BARRO_ROOM_AUTO_INTERVAL_MIN 주기 도래 → 장중 자율토론
   └─[트리거2] 버스에 human/question 메시지 → 질문 응답토론
        │
        ▼
   agent_room_coordinator
        │  _live_snapshot(): advisory.json(regime) + active_positions + closing_bet_positions
        ▼
   agent_room_discuss  (R1, R2, …)
        │  라운드마다 7역할 × 로컬 claude CLI
        │  각 의견 → room_bus.post(type=proposal|finding, refs=[동료 msg_id])
        ▼
   합의 합성 (coordinator)
        │  토론 전문 → decision{summary/consensus/dissent/decision/confidence/recommendations}
        ▼
   기록 + 미러
        ├─ data/agent_room/<date>.jsonl            (메시지 진실원천)
        ├─ data/agent_room/decisions/<date>.jsonl  (합의 결정 감사로그)
        └─ Telegram @barroAiTrade_agents_bot       (비권위 미러)
```

추가로 `loss-watch`(손실 감시)·`advisory-writer`(verdict 생산자)도 발견을 `room_bus.post()` 로 게시해 같은 그룹채팅에 미러된다.

---

## 7역할

| 역할 | 책임 |
|------|------|
| `market-analyst` | 시장국면·지수·거시/심리 |
| `risk-officer` | 리스크·노출·손절·드로다운·포지션 사이징 |
| `execution-trader` | 체결품질·진입 타이밍·API 운영 |
| `strategy-quant` | 전략 성과·종목 선정·백테스트 정합성 |
| `macro-specialist` | 美거시(Growth·Inflation)·글로벌 지수·섹터 회전 |
| `trend-expert` | 기술적 추세(EMA/ADX/MACD)·추세 강도 |
| `devils-advocate` | 반론·낙관 견제·groupthink 방지 |

(`barrotrade` 스킬의 17 사이클 에이전트와는 별개의 운영 다자토론 역할군이다. → `.claude/skills/barrotrade/SKILL.md` 참고)

---

## 트리거

| 트리거 | 환경변수 | 조건 |
|--------|----------|------|
| 장중 자율토론 | `BARRO_ROOM_AUTO_INTERVAL_MIN`(>0) | 평일 09:00~15:30 KST, 주기 경과 시 |
| 사람/질문 응답 | `BARRO_ROOM_AUTO_DISCUSS=1` | 버스에 human/question 메시지 감지 |
| 라운드 수 | `BARRO_DISCUSS_ROUNDS`(기본 2) | 토론 라운드 |

코디네이터 실행:
```bash
python scripts/agent_room_coordinator.py --interval 60   # 상시 루프
python scripts/agent_room_coordinator.py --once --dry    # 합성만(테스트)
```

---

## 텔레그램 미러

`room_bus._tg_mirror()` 가 각 메시지를 별도 봇으로 그룹채팅에 게시한다.

| 환경변수 | 용도 |
|----------|------|
| `BARRO_AGENTS_BOT_TOKEN` | 전용 봇(`@barroAiTrade_agents_bot`) 토큰 |
| `BARRO_AGENTS_CHAT_ID` | 그룹 채팅(채널) ID |

메시지 포맷(HTML): `{icon} <b>{agent}</b>[{symbol}] · {topic}` + 본문 + `<code>{type}/{priority} id={id}</code>`. 아이콘: 🔎finding · 📋proposal · 🗳vote · ✅decision · ❓question · 🧑human. 4096자 제한(3500자 본문 절단), HTML 실패 시 plain text 폴백.

미설정(토큰/chat 없음) 시 미러만 비활성화되고 버스(JSONL)는 정상 동작한다.

---

## 데이터 경로 · 스키마

| 경로 | 내용 |
|------|------|
| `data/agent_room/<YYYY-MM-DD>.jsonl` | 메시지 진실원천(append-only) |
| `data/agent_room/decisions/<YYYY-MM-DD>.jsonl` | 합의 결정 감사로그 |
| `data/agent_room/.cursor_<agent>.json` | 에이전트/코디네이터 진행 커서 |

`BARRO_DATA_DIR` 로 데이터 루트를 재지정할 수 있다(기본 `data/`).

**메시지(RoomMessage)**: `from_agent · type(finding/proposal/vote/decision/question/human) · topic · payload.text · priority(critical/high/normal/low) · symbol · refs[] · id · ts`

**결정(decision)**: `ts · by · topic · mode · summary · consensus · dissent · decision · confidence · needs_human_approval · recommendations[] · rounds · participants[]`

---

## 안전 불변식

- **텔레그램은 비권위 미러** — 표시용일 뿐 결정 권위 없음. 진실원천은 JSONL.
- **주문 경로 단절** — room_bus·discuss·coordinator 는 매수/매도 함수를 호출하지 않는다. 실행은 `advisory.json` 소비측 + 사람 승인(HITL)으로만.
- **킬스위치** — `BARRO_AGENT_ROOM_ENABLED=0`(기본) 이면 `post()` 무동작.
- **Fail-open** — 텔레그램/LLM 실패는 로그만 남기고 거래에 무영향.
- **감사성** — 모든 결정은 `decisions/<date>.jsonl` append-only 로 보존.

---

## 일일 요약본

당일 협업 결정 + 코드/설정 수정을 한 문서로 모은 요약을 생성한다.

```bash
# 운영 머신 EOD(또는 수동) 실행 — data/agent_room 데이터가 있는 곳에서
python scripts/_daily_collab_summary.py --date 2026-06-24
```

- **입력**: `data/agent_room/<date>.jsonl`(메시지) · `decisions/<date>.jsonl`(결정) · 당일 git 커밋 · `data/policy.json` history(당일 정책 변경)
- **출력**: `docs/operations/daily-collab/<date>.md` (협업 개요 → 당일 도출 결정 → 당일 코드/설정 수정 → HITL 대기)
- read-only 집계 — 거래/주문 일절 없음. 데이터 없으면 "데이터 없음(운영 EOD 생성)" 으로 graceful.

> ℹ️ `data/agent_room/` 는 운영 머신에만 쌓이므로, 개발/분석 머신에서는 빈 요약(헤더+안내)만 생성된다.

---

## cron 자동화 배선 (운영 머신)

EOD(장 마감 15:30 KST 이후)에 일일 요약을 자동 생성하도록 배선한다. **운영 머신에서** 아래를 실행한다.

### 빠른 배선 (셋업 스크립트)

```bash
cd ~/workspace/BarroAiTrade          # 운영 머신 repo 경로
bash scripts/setup_daily_collab_cron.sh                 # ① 점검 + 등록 명령 출력만(설치 X)
bash scripts/setup_daily_collab_cron.sh --install-cron  # ② crontab 등록(월-금 16:20, 멱등)
# 또는 launchd(macOS):
bash scripts/setup_daily_collab_cron.sh --install-launchd
```

- 기본 실행은 **점검만**(디렉터리 보장 + 컴파일 + 스모크 + 등록 라인 출력). 실제 설치는 `--install-*` 플래그로 opt-in(HITL).
- 시각 변경: `COLLAB_HOUR=16 COLLAB_MIN=30 bash scripts/setup_daily_collab_cron.sh --install-cron`

### 구성 요소

| 파일 | 역할 |
|------|------|
| `scripts/run_daily_collab_summary.sh` | cron/launchd 가 호출하는 래퍼 — `.env.local` 로딩 · venv fallback · 중복실행 lock · 로그 |
| `scripts/setup_daily_collab_cron.sh` | 배선 셋업(점검 + opt-in crontab/launchd 설치, 멱등) |
| `infra/com.barro.daily-collab.plist.example` | launchd 템플릿(macOS, 월-금 16:20) |

### 수동 등록 (crontab 직접)

기존 cron 4건과 동일 패턴([[../05-paperclip/runbook-ops|runbook-ops]]):
```cron
20 16 * * 1-5 cd /Users/USERNAME/workspace/BarroAiTrade && bash scripts/run_daily_collab_summary.sh >> logs/daily_collab_summary.log 2>&1
```

### 검증 · 운영

```bash
bash scripts/run_daily_collab_summary.sh --date $(date +%F)   # 수동 1회
tail -f logs/daily_collab_summary.log                         # 실행 로그
ls -1 docs/operations/daily-collab/                           # 생성된 <date>.md
```

- 중복 실행은 `/tmp/barro-daily-collab.lock` 으로 방지(fail-open: 실행 중이면 skip).
- read-only 집계 — 거래/주문 경로 미접촉. 데이터 없으면 graceful(헤더+안내).

---

## 관련 문서

- [[agent-advisory-runbook]] — advisory.json verdict 운영
- [[team-agent-tmux]] — 에이전트 tmux 운영
- `.claude/skills/barrotrade/SKILL.md` — 17 사이클 오케스트레이션(별개 트랙)
- [[daily-collab/index|일일 협업 요약 목록]]
