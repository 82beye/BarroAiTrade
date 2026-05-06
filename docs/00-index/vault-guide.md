---
tags: [index, vault, guide]
---

# Obsidian Vault 사용 가이드

> BarroAiTrade `docs/` 가 Obsidian Vault. 본 문서는 vault 설정·사용법 정리.

---

## 1. Vault 설정 (`docs/.obsidian/`)

| 파일 | 역할 | git 추적 |
|------|------|:---:|
| `app.json` | 일반 설정 (링크 형식, attachments 폴더 등) | ✅ 공유 |
| `appearance.json` | 테마 / 폰트 | ✅ 공유 |
| `core-plugins.json` | 코어 플러그인 활성 목록 | ✅ 공유 |
| `daily-notes.json` | 일일 노트 (`00-index/daily/YYYY-MM-DD.md`) | ✅ 공유 |
| `templates.json` | 템플릿 폴더 (`99-templates/`) | ✅ 공유 |
| `hotkeys.json` | 단축키 | ✅ 공유 |
| `graph.json` | 그래프 뷰 사용자 상태 | ❌ ignore |
| `workspace.json` | UI 패널 레이아웃 | ❌ ignore |
| `cache/`, `plugins/*/data.json` | 사용자별 캐시 | ❌ ignore |

`.gitignore` 룰: `docs/.obsidian/{graph,workspace}.json` + `cache/` + `plugins/*/data.json` 만 차단.

---

## 2. 단축키 (`hotkeys.json`)

| 단축키 | 액션 |
|---|---|
| `Cmd+O` | 파일 빠른 전환 |
| `Cmd+Shift+F` | 전체 검색 |
| `Cmd+G` / `Cmd+Shift+G` | 그래프 뷰 / 로컬 그래프 |
| `Cmd+Shift+B` | 백링크 |
| `Cmd+L` | Outline |
| `Cmd+Shift+P` | 명령 팔레트 |
| `Cmd+T` | 템플릿 삽입 |
| `Cmd+Alt+D` | 일일 노트 열기 |
| `Cmd+Shift+E` | 파일 탐색기에서 현재 파일 |

---

## 3. 폴더 구조

```
docs/                              ← Vault root
├── .obsidian/                     ← 설정
├── 00-index/                      ← 인덱스 진입점
│   ├── features-index.md
│   ├── status-dashboard.md
│   ├── vault-guide.md             ← 본 문서
│   └── daily/                     ← 일일 노트 (YYYY-MM-DD.md)
├── 01-plan/                       ← Plan 산출물
│   ├── MASTER-EXECUTION-PLAN-v2.md
│   ├── MASTER-EXECUTION-PLAN-v1.md  (보존)
│   ├── _index.md
│   ├── analysis/                  ← 분석 자산 (Plan 입력)
│   └── features/
│       └── bar-XX-{slug}.plan.md
├── 02-design/
│   ├── _index.md
│   └── features/
│       └── bar-XX-{slug}.design.md
├── 03-analysis/
│   ├── _index.md
│   └── bar-XX-{slug}.analysis.md
├── 04-report/
│   ├── _index.md
│   ├── PHASE-N-summary.md
│   ├── PHASE-N-baseline-YYYY-MM.md
│   └── bar-XX-{slug}.report.md
├── 05-paperclip/                  ← Paperclip 연동
└── 99-templates/                  ← Obsidian 템플릿 5종
    ├── daily-note.md
    ├── plan.md
    ├── design.md
    ├── analysis.md
    └── report.md
```

---

## 4. 템플릿 5종 (`99-templates/`)

| 템플릿 | 사용 | 단축키 |
|---|---|---|
| `daily-note.md` | 일일 노트 자동 적용 | `Cmd+Alt+D` |
| `plan.md` | `01-plan/features/bar-XX-{slug}.plan.md` 신규 시 | `Cmd+T` |
| `design.md` | `02-design/features/bar-XX-{slug}.design.md` 신규 시 | `Cmd+T` |
| `analysis.md` | `03-analysis/bar-XX-{slug}.analysis.md` 신규 시 | `Cmd+T` |
| `report.md` | `04-report/bar-XX-{slug}.report.md` 신규 시 | `Cmd+T` |

각 PDCA 템플릿은 `{{title}}`, `{{date}}`, `{{time}}` 플레이스홀더 활용.

---

## 5. 태그 체계

| 카테고리 | 태그 | 용도 |
|---|---|---|
| PDCA 단계 | `#plan` `#design` `#analysis` `#report` | 산출물 분류 |
| 피처 | `#feature/bar-17` `#feature/bar-44` 등 | 피처별 모든 문서 |
| Phase | `#phase/0` ~ `#phase/6` | 마스터 플랜 단계 |
| 영역 | `#area/repo` `#area/strategy` `#area/security` `#area/data` `#area/ui` `#area/risk` | 도메인 분류 |
| 상태 | `#status/in_progress` `#status/done` `#status/wip` | 진행 상태 |
| 마일스톤 | `#milestone/phase-N-종료` | 게이트 통과 |
| 일일 | `#daily` | 일일 노트 |
| 인덱스 | `#index` | _index, status-dashboard |

검색 예시:
- `#phase/0 -tag:#index` — Phase 0 산출물 (인덱스 제외)
- `#feature/bar-44` — BAR-44 모든 문서
- `#status/done #phase/0` — Phase 0 완료 산출물

---

## 6. 권장 Community Plugins (수동 설치)

Obsidian 앱에서 Settings → Community Plugins → Browse 로 설치:

| 플러그인 | 용도 | 우선순위 |
|---|---|---|
| **Dataview** | _index.md 의 표를 동적 쿼리 | High |
| **Templater** | 고급 템플릿 (현재 빌트인 templates 보다 강력) | High |
| **Tag Wrangler** | 태그 일괄 변경·관리 | Medium |
| **Recent Files** | 사이드바에 최근 파일 | Medium |
| **Linter** | 마크다운 자동 정리 | Medium |
| **Style Settings** | 테마 커스터마이즈 | Low |

설치 후 Settings → Hotkeys 에서 단축키 적용.

---

## 7. 일일 노트 사용

`Cmd+Alt+D` 또는 캘린더 → 오늘 클릭:
- 자동 생성 위치: `00-index/daily/YYYY-MM-DD.md`
- 템플릿 적용: `99-templates/daily-note.md`
- 빈 칸 채우기: 오늘의 BAR / PDCA 진행 / 완료 / 메모 / 관련 링크

---

## 8. PDCA 사이클 + Vault 연동 워크플로

```
1. /pdca plan BAR-XX (CLI)
   ↓ 자동 생성 (template 우회: 직접 작성)
2. docs/01-plan/features/bar-XX-{slug}.plan.md
   ↓ git PR 머지
3. Obsidian 재로드 (또는 자동 file-watch)
   ↓ vault 에 등장
4. _index.md 갱신 (수동, BAR-44 PR 패턴 참조)
   ↓ wikilink 누적
5. 그래프 뷰에서 BAR-XX 노드 확인
```

---

## 9. Vault Sync (선택)

Obsidian 자체 Sync 미사용 (`core-plugins.json` 의 `"sync": false`). 대신 git 사용:

```bash
# vault 변경 후
cd /Users/beye/workspace/BarroAiTrade
git add docs/
git commit -m "docs: ..."
git push
```

다른 worktree/기기에서:
```bash
git pull
# Obsidian 재로드 (Cmd+R)
```

---

*[[../README|🏠 Home]] | [[features-index|📋 피처 인덱스]] | [[status-dashboard|📊 PDCA 대시보드]] | 최종 업데이트: 2026-05-06*
