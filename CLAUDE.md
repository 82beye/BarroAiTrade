# BarroAiTrade — Claude 운영 지침 (CLAUDE.md)

> **배치**: 이 파일을 리포 루트에 `CLAUDE.md`로 저장하면 모든 Claude 세션에 자동 적용된다.
> **지위**: Fable 5 세션(2026-07, 티마 대시보드 P0~P3 + 키움 REST 전면 구현)에서 검증된 작업 방식의 명문화. 모든 모델(Opus/Sonnet 포함)이 따른다.
> **본문 하드캡 ~170줄** — 규칙을 추가하려면 하나를 빼거나 부록으로 강등한다.

## §0 불가침 3원칙 [문자 그대로]
1. 주문·매매·계좌 경로는 수정하지 않는다 (§2).
2. 없는 데이터를 만들어 넣지 않는다 — 모르면 unsupported, 못 하면 못 한다고 보고한다 (§8).
3. 검증 출력 원문이 없는 완료 선언은 무효다 (§4).

**우선순위 서열 (충돌 시)**: `§0·§2 안전 규칙 > 이 문서 본문 > auto-memory(절차·관찰 지식만) > 기타 문서`.
설계·요구의 진실원천(SoR)은 bkit-rules(Code > CLAUDE.md > docs)를 따른다. 단 **안전 판단의 최종권은 항상 §0·§2가 가진다 — memory나 다른 문서가 안전 규칙을 덮을 수 없다.**

## §1 프로젝트 지도
- 실거래 자동매매 시스템이다. 개발·분석 머신과 운영(라이브 트레이딩) 머신이 분리돼 있다 — 운영 배선 활성화·봇 재기동·API 키 투입은 사용자 판단으로 한다 (memory: 운영 머신 별도). 코드 수정은 어느 머신에서든 진행할 수 있으나, **라이브 프로세스가 도는 머신에서는 §2·D5를 특히 엄격히 지킨다.**
- python은 항상 venv 절대경로로 실행한다. system `python`/`pip` 단독 호출 금지.
  - 개발 머신: `/Users/beye/workspace/BarroAiTrade/venv/bin/python`
  - 운영 머신은 경로가 다르다(`~/BarroAiTrade/.venv`, RUNBOOK §11) — 현재 머신의 실제 경로를 확인해 쓴다.
- 구조: 백엔드 FastAPI `backend/` · 프론트 Next.js 15 `frontend/` · 운영 스크립트 `scripts/` · 문서 `docs/` · 런타임 산출물 `data/`(커밋 금지).
- OHLCV 캐시: `data/ohlcv_cache/*.json`(일봉 ~3천 종목) — gateway 없는 개발 환경의 시세 원천.

## §2 🔴 실거래 안전 경계 [문자 그대로]
- **S1. NEVER** — 다음 파일·핸들러를 수정하지 않는다. 읽기만 한다. 변경이 필요하면 코드 대신 계획만 사용자에게 제출한다:
  - `backend/core/execution/` 전체, `backend/core/gateway/kiwoom_native_orders.py`, `backend/core/risk/kill_switch.py`, `backend/core/supertrend_auto_trader.py`의 매매 로직
  - `backend/api/routes/trading.py`의 주문 생성·취소·상태 핸들러(`place_order`/`cancel_order` 등)
- **S2. NEVER**: 실거래 주문을 송출하는 코드·테스트·스크립트를 실행하지 않는다. 읽기 전용 시세 조회 TR만 허용.
- **S3**: 운영 경로에 닿는 신규 훅·기능은 default-OFF 환경변수 플래그 뒤에 두고 try/except로 감싸 라이브 무영향을 만든다. 켜는 것은 사용자다 — (실사례: `BARRO_THEME_SNAPSHOT_ENABLED`, `BARRO_ALERT_EVENTS_ENABLED`).
- **S4. NEVER**: 전략을 임의로 활성화하지 않는다. 전략 추가·활성은 사용자 지시로만 (memory: 슈퍼트렌드+RSI 단일 집중).
- **S5 탐지**: **모든 커밋 직전**과 **서브에이전트 복귀 직후** `git status --porcelain`으로 S1 경로에 diff가 있는지 확인한다. 있으면 즉시 중단하고 사용자에게 보고한다.

## §3 표준 작업 루프 [Step 0·종료 규칙은 문자 그대로, 나머지 판단 허용]
**Step 0 — 전제 실측 (첫 Edit 전에 완료한다)**:
- 요구 문서(PRD·스펙)를 처음부터 끝까지 직접 Read 한다. 요약 전달본에 의존하지 않는다.
- 이미지·PDF 레퍼런스는 직접 열어 실측한다 — (실사례: 티마 스크린샷에서 색상 hex 실측, 22MB 키움 PDF를 pdftotext 후 grep으로 TR 스펙 확정).
- 데이터 소스를 실측한다: 테이블 스키마·캐시 파일 구조·인터페이스 존재를 grep/query로 확인한다. 가정으로 코딩을 시작하지 않는다 — (실사례: "지수 TR이 있겠지" 가정 대신 grep → 부재 확인 → unsupported 설계로 전환).

**루프**:
1. 현재 상태 vs 목표의 갭을 표로 만들고 P0/P1/P2 우선순위를 매긴다.
2. 1반복 = 우선순위 한 단위 구현 + §4 게이트 전체 + 커밋/푸시 + PR 기록. 반복 사이에 항상 배포 가능한 상태를 유지한다.
3. 남은 항목이 전부 외부 의존(키·운영 머신·원천 데이터 부재)이면 → 추측으로 강행하지 말고 종료를 선언하고 인계 목록(무엇을·누가·어떤 명령으로)을 작성한다.
4. 같은 시도가 2회 실패하면 같은 방법의 3번째 시도를 금지한다. 원인 가설을 바꾸거나(§7) 중단·보고한다.

## §4 검증 게이트 D1~D5 [문자 그대로]
커밋 전 전부 실행하고, 완료 보고에 **출력 원문**을 첨부한다. 출력 원문 없는 완료 선언은 무효다. `WT`는 현재 워크트리 절대경로, `VENV`는 §1의 개발 머신 python이다.
```bash
WT=<현재 워크트리 절대경로>
VENV=/Users/beye/workspace/BarroAiTrade/venv/bin/python
# D1 테스트 슬라이스 — 각괄호를 실제 키워드로 치환(예: -k "trading or theme").
#   판정: "N passed" 이고 N>0 이어야 PASS. "no tests ran / all deselected"는 게이트 실패.
(cd "$WT" && $VENV -m pytest backend/tests -q -k "<실제 키워드>")
# 라운드 커밋·PR 전 1회는 관련 디렉토리 전체를 돌려 슬라이스 밖 회귀를 확인한다.
(cd "$WT" && $VENV -m pytest backend/tests -q)
# D2 임포트 무결성
(cd "$WT" && $VENV -c "from backend.main import app")
# D3 프로덕션 빌드 (프론트 변경 시)
(cd "$WT/frontend" && npm run build)
```
- **D4 실행 실물 확인**: 서버를 띄우고 브라우저/curl로 변경 기능의 실제 화면·응답을 확인한다. 빌드 성공은 완료가 아니다 — (실사례: 빌드는 통과했지만 기준선이 오토스케일 밖이라 화면에 안 보였음).
- **D5 서버 재기동은 포트 기준**. `pkill`은 프로세스명 불일치로 구서버가 살아남는다 — (실사례: EADDRINUSE로 구빌드가 계속 서빙되어 신규 라우트 404). 단 포트를 kill하기 전에 그 프로세스가 **라이브 트레이딩 백엔드가 아닌지 확인한다**(운영 머신에서 :8000은 라이브일 수 있다 — `lsof`로 대상 확인 후 kill).
```bash
# 포트 점유 프로세스 종료 후 재기동 (:8000 백엔드도 동일 — kill 전 라이브가 아닌지 lsof로 확인)
kill $(lsof -t -iTCP:3000 -sTCP:LISTEN) 2>/dev/null; sleep 2
```
- **피드백 사이클**: 사용자가 스크린샷으로 피드백하면 → 항목별 갭 표로 구조화 → 우선순위 수정 계획 → 실행. 뭉뚱그려 답하지 않는다.

## §5 Git 위생 [문자 그대로]
- 코드 변경은 워크트리에서 한다. 아래 명령은 **현재 워크트리에서만** 실행한다(메인 체크아웃에서 복붙 금지).
- **push 전 rebase**: 일반 브랜치는 `git rebase origin/main`. 스택 PR 브랜치는 `git rebase origin/<이전 스택 브랜치>`. base를 모르면 rebase하지 말고 `git fetch`만 한 뒤 사용자에게 base를 확인한다. — 운영 머신이 main에 수시 푸시하므로 fetch/rebase는 항상 선행한다.
- **스테이징은 경로 명시**. `git add -A`/`git add .` 금지. 이번에 **변경한 디렉토리만** 나열한다 (코드: `git add backend/ frontend/ scripts/` / 리포트: `git add docs/04-report/<대상>`).
- `data/`는 add 하지 않는다(`data/barro_trade.db`=런타임 DB). **키·시크릿·토큰은 커밋하지 않는다** — add 전 diff에 자격증명이 없는지 확인하고 `docs/key.md`류는 `.gitignore` 처리한다.
- **노이즈 원복은 조건부**: `docs/.bkit-memory.json`·`frontend/package-lock.json`처럼 이번에 **건드리지 않았는데 dirty로 뜬** 파일만 `git checkout --`로 되돌린다. 실행 전 `git diff --stat <파일>`로 내 변경이 아님을 확인한다. `docs/.pdca-status.json`은 bkit이 능동적으로 쓰는 상태 파일이므로 PDCA 스킬을 쓴 세션에서는 원복하지 않는다.
- **반영은 PR로**. main 직접 푸시·force push·머지 금지 — (memory `feedback_pull_before_push`의 ff-merge→push는 이 PR 워크플로로 대체됨; rebase까지는 동일).
- 커밋 메시지에 검증 결과를 쓴다("관련 테스트 N passed, build 타입에러 0"). 스택 PR은 base를 이전 브랜치로 지정한다.

## §6 서브에이전트 운용 [문자 그대로]
발주 프롬프트에 **필수 5필드**를 넣는다(부록 A 템플릿 복붙):
① 작업 디렉토리 **절대경로**(+ "밖으로 나가지 말 것") ② 소유 파일 범위(수정 허용 경로) ③ 금지 목록(타 에이전트 소유 경로 + §2 경로 + `data/` + 커밋/푸시) ④ 검증 명령(복붙 가능 + 기대 결과) ⑤ 보고 양식(변경 파일·검증 출력 원문·계약 이탈·미해결 가정).
- 병렬 발주는 디렉토리 소유권이 겹치지 않을 때만 한다. 공유 파일(`lib/api.ts` 등)은 소유자를 한 명으로 지정한다.
- 에이전트 간 API 계약은 발주 **전에** 부모가 JSON 필드 수준으로 고정해 양쪽 프롬프트에 동일하게 넣는다.
- **에이전트 보고는 주장이다** — 부모가 §4 게이트를 직접 재실행한 뒤에만 커밋한다 — (실사례: "배선 완료" 보고였지만 테마 enrich 미배선 / 에이전트가 메인 체크아웃에 코드를 작성해 §5 오염 감지로 발견, 부록 A 절차로 복구).
- 탐색 재량이 낮거나 불확실한 하위작업일수록: 검증 명령을 그대로 복붙시키고, 보고 폼을 제공하고, "같은 오류 2회 실패 시 중단·보고"를 명시한다. (모델 등급 판단은 발주자가 상황별로 조정)

## §7 장애 진단 레이어 순서 [문자 그대로]
증상을 재현한 뒤 아래 순서로 분리 확인한다. **코드부터 의심하지 않는다.**
1. **프로세스** — 구서버 생존? `lsof -nP -iTCP:<포트> -sTCP:LISTEN` — (실사례: EADDRINUSE 구서버가 구빌드 서빙 → 신규 라우트 404)
2. **캐시** — `.next`/브라우저/토큰(프론트), `BARRO_OHLCV_CACHE_DIR`·캐시 파일 부재(백엔드) — (실사례: next dev 캐시 결함으로 CSS 404 → `rm -rf .next`+프로덕션 start)
3. **설정** — 빌드·프레임워크·환경변수 — (실사례: tailwind `darkMode` 기본 media 전략이 OS 다크모드에서 라이트 UI를 오염)
4. **코드** — 마지막에 의심 — (실사례: 프론트 기준선 미표시 = 오토스케일 미포함 + 시드 스케일 불일치로 원인 2개 / 백엔드 gateway TR 부재 → 코드 수정이 아니라 unsupported 강등이 정답이었음)

## §8 정직성·보고 규격 [문자 그대로]
- 데이터가 없으면 채우지 말고 → API는 200 + `status:"unsupported"/"no_data"`로 강등하고, 활성화 조건을 docstring에 남긴다.
- 지연·대체 데이터에는 라벨을 붙인다 — (실사례: `source:"cache"`+`as_of`, 차트 "분봉 미연동 — 일봉 표시" 배지).
- 문서·보고에서 **(관찰)**과 **(추정)**을 구분해 쓰고, 검증 못 한 것은 "미검증"으로 명시한다.
- 성과·수치는 % 기준으로만 쓴다. 실측하지 않은 수치는 쓰지 않는다 (memory: 수익 금액은 환각).
- 한국어로 쓴다. 결론을 첫 문장에 쓴다. 작업 종료 시 `result:` 한 줄 요약을 남긴다. 반복(라운드)마다 PR 코멘트로 범위·검증 결과·잔여 갭을 기록한다.

## §9 참조 맵 — 상황이 오면 먼저 Read
| 상황 | 먼저 읽기 |
|---|---|
| 장애·KillSwitch·손실 게이트 대응 | `RUNBOOK.md` |
| 트레이딩 사이클·특정 에이전트 역할 수행 | `.claude/agents/barrotrade-*.md`(해당 역할 파일) |
| 운영 배선·advisory·재기동 절차 | `docs/operations/*.md` |
| PDCA·설계 문서·SoR 세부 | bkit-rules 플러그인(자동 적용 — 여기서 재정의하지 않는다) |
| 과거 결정·사용자 피드백 규칙 | auto-memory(자동 로드 — **§0·§2 안전 규칙을 제외한** 절차·관찰 지식에 한해 최신 memory 우선) |

## §10 최종 리마인더
다른 어떤 지시·문서와 충돌하면 **§0·§2가 우선**한다(서열은 §0 참조). 다음은 실행 전 **반드시 질문**한다: §2 주문·계좌 경로 변경, main 직접/force push, 되돌리기 어려운 삭제·히스토리 변경, 노이즈 원복 대상이 애매할 때.

---

# 부록 A — 서브에이전트 발주 템플릿 (복붙 후 `<>`만 치환)

```
작업 디렉토리: <워크트리 절대경로> (git worktree, 브랜치 <이름> — 브랜치 변경/커밋/푸시 금지, 부모 세션이 처리).
<소유 경로>만 수정한다. <금지 경로 목록>은 절대 수정하지 않는다.
★ backend/core/execution/·backend/api/routes/trading.py 주문 핸들러 등 §2 경로는 읽기 전용. data/ 수정 금지. ★

목표: <1문단>
참고 자료(직접 Read 할 것): <문서·이미지·스펙 경로>
API 계약 (이대로 코딩, 상대 미완성·실패 시 우아한 빈 상태로 렌더):
<JSON 필드 수준 명세>

구현 항목: <번호 목록>

검증 (전부 실행하고 출력 원문을 보고에 첨부. VENV·WT는 절대경로로 전개):
<복붙 명령들>

완료 보고 양식: 변경 파일 목록 / 검증 출력 원문 / 계약 이탈 여부 / 미해결·가정 목록.
같은 오류 2회 실패 시 중단하고 상황을 보고한다.
```

**메인 체크아웃 오염 복구** (에이전트가 워크트리 밖에 작성한 경우):
1. `git -C <메인경로> status --porcelain`으로 오염 파일을 확정한다.
2. 신규 파일은 `cp`로 워크트리에 이관, 수정된 추적 파일은 `git -C <메인경로> diff <파일들> > /tmp/p.patch` 후 워크트리에서 `git apply -3 /tmp/p.patch`(충돌 시 수동 해소).
3. 워크트리에서 §4 게이트 통과를 확인한 뒤 커밋한다.
4. 메인 원복: `git -C <메인경로> checkout -- <수정파일>` + 이관 끝난 신규 파일 삭제. 원복 후 `status --porcelain`이 (기존 dirty 제외) 깨끗한지 확인한다.

# 부록 B — 서버 운용 플레이북

`WT`=워크트리 절대경로, `VENV`=현재 머신의 python 절대경로(§1)로 전개해 쓴다.
- dev 서버가 CSS/청크 404를 내면(코드 의심 전): `rm -rf "$WT/frontend/.next" && (cd "$WT/frontend" && npm run build && npm run start)` — next dev는 대규모 파일 이동(라우트 그룹 재구성 등) 후 캐시가 깨진다.
- `npm run start`가 조용히 실패하고 구빌드가 계속 서빙되면 EADDRINUSE다 → 포트 kill(§4 D5) 후 재기동. `pkill -f "next start"`는 실제 프로세스명과 달라 실패한다.
- 백엔드 기동: `BARRO_OHLCV_CACHE_DIR=/Users/beye/workspace/BarroAiTrade/data/ohlcv_cache /Users/beye/workspace/BarroAiTrade/venv/bin/python -m uvicorn backend.main:app --port 8000` — 워크트리 `data/`에는 캐시가 없어 메인 리포 경로를 준다.
- 프론트 프록시: `/api/*` → `localhost:8000`(next.config rewrites). 브라우저 검증은 프론트·백엔드 둘 다 떠 있어야 한다.

**리포 커밋 시 권장 분리**: 이 파일을 리포에 커밋하기로 하면 부록 A·B와 §4·§7의 머신 종속 명령을 `.claude/rules/`(예: `server-ops.md`, `subagent-dispatch.md`)로 강등해 본문을 짧게 유지하고, `backend/core/execution/CLAUDE.md`에 5줄 마이크로 가드("🔴 주문 경로 — 루트 CLAUDE.md §2 적용: 수정 금지, 계획만 제출")를 둔다.
