<!--
🚨 BarroAiTrade PR 템플릿 (마스터 플랜 v1 §0 운영 원칙)

PDCA 1 사이클 = 1 BAR 티켓. main 직접 push 금지, BAR-XX 브랜치 → PR → 셀프/팀 리뷰.
자금흐름·보안·동시성 PR 은 자동 머지 금지 — 사람 게이트키퍼 의무.

마스터 플랜: docs/01-plan/MASTER-EXECUTION-PLAN-v1.md
-->

## Summary

<!-- 1~3줄. 무엇을·왜. BAR-XX 티켓 또는 phase 명시 -->

## 변경 파일

<!-- 주요 변경/신규 파일. 디렉터리 단위 OK -->

- `path/to/file` — ...

## 라벨 (해당 항목 체크)

### Area (필수, 1개 이상)
- [ ] `area:money` — **자금흐름** (주문·포지션·가격 계산·자산) → 사람 게이트키퍼 의무
- [ ] `area:security` — **보안** (인증/인가·암호화·감사로그·RLS) → 사람 게이트키퍼 의무
- [ ] `area:strategy` — 전략 엔진 (Strategy v2, F존/SF존/골드존/38스윙/합의)
- [ ] `area:data` — 시장 데이터·뉴스·테마·일정
- [ ] `area:risk` — 리스크 엔진·Kill Switch·Circuit Breaker
- [ ] `area:ui` — 프론트엔드 (Next.js / React)
- [ ] `area:repo` — 리포지토리·인프라·CI·문서

### Phase
- [ ] `phase:0` — 기반 정비 (BAR-40~44)
- [ ] `phase:1` — 전략 엔진 통합 (BAR-45~51)
- [ ] `phase:2` — NXT 통합 (BAR-52~55)
- [ ] `phase:3` — 테마 인텔리전스 (BAR-56~62)
- [ ] `phase:4` — 자동매매 운영 (BAR-63~66)
- [ ] `phase:5` — 보안 강화 (BAR-67~70)
- [ ] `phase:6` — 운영 고도화 (BAR-71~78)

### Priority
- [ ] `priority:p0` (최우선) / `priority:p1` (중간) / `priority:p2` (후순위)

### 기타
- [ ] `ai-generated` — AI 생성 코드 (Phase 5.4 BAR-70 부터 Semgrep/Bandit 통과 의무)

---

## 자금흐름 PR 체크리스트 (`area:money` 시 필수)

- [ ] 모든 가격·수량·잔고 변수에 `Decimal` 사용 (float 금지)
- [ ] 반올림 정책 명시 (`ROUND_HALF_UP` / `ROUND_DOWN` 등)
- [ ] 외부 입력(API 응답, 사용자 입력) 검증 (Pydantic v2 model)
- [ ] 동시성 처리 — `asyncio.Lock` 또는 트랜잭션 격리
- [ ] 부분 실패 시 보상 트랜잭션·롤백 경로 존재
- [ ] 주문·포지션 변경 모두 감사 로그(`audit_repo`) 기록
- [ ] **사람 게이트키퍼 1인 이상 승인 (자동 머지 금지)**

## 보안 PR 체크리스트 (`area:security` 시 필수)

- [ ] OWASP Top 10 자동 스캔 통과 (BAR-70 도입 후)
- [ ] 인증/인가 로직에 우회 가능 경로 부재 검증
- [ ] 비밀(키·토큰) 평문 노출 0건 (`.env` 외 위치 검토)
- [ ] 세션·JWT 만료 정책 (Access ≤1h, Refresh ≤7d)
- [ ] RLS 정책 누수 부재 (다른 user_id 데이터 접근 0%)
- [ ] **사람 게이트키퍼 (security teammate 또는 동등 권한자) 승인**

## AI 생성 코드 체크리스트 (`ai-generated` 시 필수, BAR-70 부터)

- [ ] Semgrep 스캔 결과 첨부 — P0/P1 0건
- [ ] Bandit 스캔 결과 첨부 — HIGH 0건
- [ ] 자금 흐름 함수 검토 (Decimal · 반올림 정책 — 위 자금흐름 체크리스트와 중복 적용)
- [ ] 인증/인가 체크 우회 가능성 점검
- [ ] 동시성 코드 race condition / asyncio cancel handling 점검

> 본 PR 이 자금흐름·보안·동시성 영역 모두 포함 시 **모든 체크리스트** 적용.

---

## Test plan

- [ ] 단위 테스트 추가/갱신 (`pytest backend/tests/...`)
- [ ] 신규 코드 라인 커버리지 ≥ 70%
- [ ] 백테스트 회귀 (BAR-51 도입 후 — 베이스라인 ±5% 이내)
- [ ] 모의투자 N주 검증 (Phase 1: 1주, Phase 4: 3주 — 해당 시)
- [ ] V1~Vn 검증 시나리오 결과 (design §5 참조)

## PDCA 사이클

- [ ] Plan: `docs/01-plan/features/{slug}.plan.md` 머지됨
- [ ] Design: `docs/02-design/features/{slug}.design.md` 머지됨
- [ ] Do: 본 PR
- [ ] (다음) Analyze: gap-detector Match Rate ≥ 90%
- [ ] (다음) Report: `docs/04-report/{slug}.report.md`

## Test plan 결과

<!-- 검증 명령 출력·스크린샷·매치율 등 -->

## 비고

<!-- BAR-51 번호 충돌 같은 후속 처리 사항, design 보완사항 등 -->

🤖 Generated with [Claude Code](https://claude.com/claude-code)
