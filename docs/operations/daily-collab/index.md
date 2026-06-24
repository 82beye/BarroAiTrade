# 일일 협업 요약 (daily-collab)

> `scripts/_daily_collab_summary.py` 가 생성하는 `<YYYY-MM-DD>.md` 모음.
> 당일 **에이전트 협업 도출**(agent_room 결정) + **코드/설정 수정 적용**(git·policy.json) 통합 요약.
> 시스템 구조: [[../agent-collaboration|에이전트 협업 (텔레그램 그룹채팅)]]

---

## 생성

```bash
# 운영 머신 EOD(또는 수동) — data/agent_room 데이터가 있는 곳에서 실행
python scripts/_daily_collab_summary.py --date 2026-06-24
# → docs/operations/daily-collab/2026-06-24.md
```

- 입력: `data/agent_room/<date>.jsonl`·`decisions/<date>.jsonl` · 당일 git 커밋 · `data/policy.json` history
- read-only 집계(거래/주문 없음), 모든 소스 fail-open.
- 개발/분석 머신에서는 `agent_room` 데이터가 없어 협업 섹션이 비고 git·policy 섹션만 채워진다.

## 요약 구조 (각 `<date>.md`)

1. **협업 개요** — 메시지 수·type·참여 에이전트·주제
2. **당일 도출 결정** — summary/consensus/dissent/decision/confidence/recommendations
3. **당일 코드/설정 수정** — git 커밋(no-merge) + policy.json 변경
4. **후속 / HITL 대기** — `needs_human_approval` 결정

## 목록

운영 머신에서 생성된 `<date>.md` 가 이 폴더에 날짜순으로 쌓인다.
(Obsidian 에서 이 폴더를 열면 파일 탐색기로 일자별 탐색 가능.)
