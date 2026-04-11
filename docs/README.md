# BarroAiTrade — Knowledge Base

> AI 기반 트레이딩 플랫폼 프로젝트 산출물 저장소

이 Vault는 BarroAiTrade 프로젝트의 모든 PDCA 산출물을 Obsidian에서 탐색 가능한 지식 베이스로 관리합니다.

---

## 빠른 탐색

| 구분 | 링크 | 설명 |
|------|------|------|
| 피처 인덱스 | [[00-index/features-index]] | 전체 피처 목록 및 현황 |
| PDCA 대시보드 | [[00-index/status-dashboard]] | 단계별 산출물 현황 |
| 배포 정보 | [[deployment]] | 인프라 & 배포 가이드 |
| Paperclip Board | [[05-paperclip/issue-board]] | Paperclip 이슈 현황 |
| WBS | [[05-paperclip/wbs]] | 전체 구현 계획 + 스케줄 |

---

## PDCA 산출물 구조

```
docs/
├── 00-index/          # 인덱스 & 대시보드
├── 01-plan/           # Plan 산출물
│   └── features/
├── 02-design/         # Design 산출물
│   └── features/
├── 03-analysis/       # Analysis(Check) 산출물
├── 04-report/         # Report(Act) 산출물
└── 05-paperclip/      # Paperclip 연동 (Issue Board, WBS)
```

---

## 피처별 산출물

### BAR-17 — 실시간 대시보드

| 단계 | 문서 | 상태 |
|------|------|------|
| Plan | [[01-plan/features/bar-17-dashboard.plan]] | ✅ 완료 |
| Design | [[02-design/features/bar-17-dashboard.design]] | ✅ 완료 |
| Analysis | [[03-analysis/bar-17-dashboard.analysis]] | ✅ 완료 |
| Report | [[04-report/bar-17-dashboard.report]] | ✅ 완료 |

---

## 태그 구조

- `#plan` — Plan 산출물
- `#design` — Design 산출물
- `#analysis` — Gap/Check 분석
- `#report` — PDCA 완료 보고서
- `#paperclip` — Paperclip 연동 문서
- `#feature/bar-17` — BAR-17 관련 모든 문서
- `#status/done` — 완료된 산출물
- `#status/wip` — 진행 중인 산출물

---

## Paperclip 연동

- **Paperclip UI**: [http://127.0.0.1:3100](http://127.0.0.1:3100)
- **회사**: BarroQuant (이슈 접두어: BAR)
- **프로젝트**: BarroAiTrade
- Issue Board: [[05-paperclip/issue-board]]
- WBS 계획: [[05-paperclip/wbs]]

---

*BarroAiTrade PDCA Knowledge Base — 마지막 업데이트: 2026-04-11*
