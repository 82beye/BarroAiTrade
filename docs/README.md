# BarroAiTrade — Knowledge Base

> AI 기반 트레이딩 플랫폼 — Master Plan v2 (40 BAR) + OPS 트랙 (26 BAR) 완료. mockapi.kiwoom.com 운영 가능 단계.

이 Vault 는 모든 PDCA 산출물 + 운영 자동화 자료를 Obsidian 에서 탐색 가능한 지식 베이스로 관리합니다.

---

## 🚀 빠른 탐색

### 운영 시작 (지금 가능)

| 링크 | 용도 |
|------|------|
| [[05-paperclip/runbook-ops\|운영 시작 RUNBOOK]] | cron 4건 + 봇 데몬 시작 + 1~2주 검증 |
| [[05-paperclip/security-rotation\|보안 회전 가이드]] | 키 + 토큰 회전 (5분) |
| [[05-paperclip/deployment\|배포 정보]] | 인프라 & 배포 가이드 |
| [[00-index/system-flow\|시스템 흐름도]] | Mermaid 9 다이어그램 |

### 인덱스

| 링크 | 범위 |
|------|------|
| [[00-index/features-index\|피처 인덱스]] | 전체 BAR 목록 + PDCA 진행 |
| [[00-index/ops-track-index\|OPS 트랙 인덱스]] | OPS-01~35 (26 BAR) 운영 자동화 |
| [[00-index/status-dashboard\|상태 대시보드]] | 단계별 산출물 현황 |
| [[00-index/vault-guide\|Vault 가이드]] | Obsidian 사용법 |

### Master Plan

| 링크 | 상태 |
|------|------|
| [[01-plan/MASTER-EXECUTION-PLAN-v2\|마스터 실행 계획 v2]] | ✅ Phase 0~6 / BAR-40~79 완료 (40/40) |
| [[01-plan/MASTER-EXECUTION-PLAN-v1\|v1 보존]] | 📦 supersede by v2 |

---

## 📊 진행 현황

```
Master Plan v2 (BAR-40~79):     40/40 BAR ✅ 100%
OPS 트랙 (BAR-OPS-01~35):       26/26 BAR ✅ 100%
누적 PR:                          170+
누적 테스트:                      830 passed, 0 fail
키움 API 통합:                    11 TR-ID
Telegram 명령:                    19개
운영 가능 여부:                   ✅ mockapi 즉시
```

---

## 📁 디렉토리 구조

```
docs/
├── 00-index/              # 인덱스 & 대시보드
│   ├── features-index.md  # 피처 인덱스 (BAR-40~79)
│   ├── ops-track-index.md # OPS 트랙 인덱스 (OPS-01~35)
│   ├── system-flow.md     # 시스템 흐름도 (Mermaid 9개)
│   ├── status-dashboard.md
│   ├── vault-guide.md
│   └── daily/             # 일일 노트
├── 01-plan/               # Plan 산출물
│   ├── MASTER-EXECUTION-PLAN-v2.md
│   ├── analysis/
│   └── features/          # BAR-40~79 plan
├── 02-design/             # Design 산출물
│   ├── features/          # BAR-40~79 design
│   └── _legacy/           # 폐기된 옛 설계 (BAR-28, 29)
├── 03-analysis/           # Gap analysis
├── 04-report/             # PDCA 완료 보고서
│   ├── features/          # BAR-OPS-01~35 + BAR-40~79
│   ├── PHASE-0~6 통합 회고
│   └── analyze/
├── 05-paperclip/          # 운영 + 사이드 문서
│   ├── runbook-ops.md     # 운영 시작 RUNBOOK
│   ├── security-rotation.md
│   ├── deployment.md
│   ├── issue-board.md
│   └── wbs.md
├── 99-templates/          # PDCA 템플릿
└── operations/            # 인프라 운영
```

---

## 🎯 주요 마일스톤

### Phase 0~6 — Master Plan v2 (40 BAR) ✅ 완료

| Phase | 범위 | 회고 |
|-------|------|------|
| Phase 0 (BAR-40~44) | 기반 정비 | [[04-report/PHASE-0-summary]] / [[04-report/PHASE-0-baseline-2026-05]] |
| Phase 1 (BAR-45~51) | 전략 엔진 + 5 전략 | 04-report/features/ |
| Phase 2 (BAR-52~55) | NXT 통합 + SOR | 동상 |
| Phase 3 (BAR-56~62) | 테마 인텔리전스 | 동상 |
| Phase 4 (BAR-63~66) | 자동매매 + 매매일지 | 동상 |
| Phase 5 (BAR-67~70) | 보안 강화 | 동상 |
| Phase 6 (BAR-71~78) | 운영 고도화 | 동상 |

### OPS 트랙 (26 BAR) ✅ 완료

| Layer | 범위 | 핵심 |
|-------|------|------|
| 1. 인증·기본 | OPS-01~07 | JWT + bcrypt + Kiwoom OAuth |
| 2. 시뮬·전략 | OPS-08~09 | IntradaySimulator |
| 3. 키움 자체 OpenAPI | OPS-10~12 | api.kiwoom.com 어댑터 |
| 4. 영속·정책·게이트 | OPS-13~17 | LiveOrderGate 4중 안전 |
| 5. End-to-End + 매도 | OPS-18~20 | 매수·매도 자동화 |
| 6. Telegram | OPS-21~25 | 5 알림 + 양방향 봇 |
| 7. Confirm 패턴 | OPS-26~27 | 7중 보안 layer |
| 8. 학습 루프 | OPS-28~32 | /tune apply → policy.json |
| 9. 미체결 + 정확도 | OPS-33~35 | 트레이딩뷰 등급 시뮬 |

→ 상세: [[00-index/ops-track-index]]

---

## 🏷️ 태그 구조

- `#plan` `#design` `#analysis` `#report` — PDCA 단계별
- `#runbook` `#ops` — 운영 자료
- `#feature/bar-XX` — BAR 피처별
- `#phase/N` — Phase 분류
- `#area/repo|strategy|security|money` — 영역
- `#status/done|in_progress` — 진행 상태
- `#index` `#architecture` `#mermaid` — 메타

---

## 🔗 외부 링크

- **Paperclip UI**: [http://127.0.0.1:3100](http://127.0.0.1:3100) (회사: BarroQuant, 이슈 접두어: BAR)
- **GitHub**: [82beye/BarroAiTrade](https://github.com/82beye/BarroAiTrade)

---

*BarroAiTrade PDCA Knowledge Base — 마지막 업데이트: 2026-05-09 (OPS-35 완료, 운영 가능 단계)*
