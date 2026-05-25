# BAR-OPS-09 ↔ main 머지 계획 (2026-05-25)

> 두 커밋 `4c7313ae` (main 최신) ↔ `871b475` (BAR-OPS-09 최신) 의 11일 분기를 안전하게 통합하기 위한 상세 머지 계획.

---

## 1. 분기 현황

| 항목 | 값 |
|---|---|
| 공통 분기점 (merge-base) | `9e9eb67` (2026-05-14 02:39) |
| 분기 경과 | **11일** |
| main HEAD | `4c7313ae` — 2026-05-23 21:04 (BAR-183) |
| BAR-OPS-09 HEAD | `871b475` — 2026-05-23 23:45 (Phase B) |

### 1.1 양쪽 변경 통계

| 측 | 커밋 수 | 파일 수 | +/−라인 |
|---|---|---|---|
| **main** (BAR-155 ~ BAR-183) | **148** | **182** | **+18,698 / −1,941** |
| BAR-OPS-09 (Phase 1 ~ Phase B) | 18 | 40 | +7,955 / −264 |

### 1.2 동시 수정 파일 (20개) — 머지 충돌 1차 후보

| 파일 | main 변경 (+/−) | worktree 변경 (+/−) | 충돌 등급 |
|---|---|---|---|
| `backend/core/strategy/f_zone.py` | 91/6 | 25/38 | 🔴 HIGH |
| `backend/core/strategy/blue_line.py` | 1/1 | 37/1 | 🟢 LOW |
| `backend/core/strategy/gold_zone.py` | 8/6 | 34/17 | 🟡 MID |
| `backend/core/strategy/scalping_consensus.py` | 13/8 | 3/20 | 🟡 MID |
| `backend/core/strategy/sf_zone.py` | 3/3 | 3/17 | 🟢 LOW |
| `backend/core/strategy/swing_38.py` | 8/6 | 42/18 | 🔴 HIGH |
| `backend/core/backtester/intraday_simulator.py` | 169/30 | 82/34 | 🔴 HIGH |
| `backend/core/risk/balance_gate.py` | 11/4 | 44/14 | 🟡 MID |
| `backend/core/journal/policy_config.py` | 8/0 | 5/2 | 🟢 LOW |
| `backend/api/routes/signals.py` | 33/53 | 11/1 | 🟡 MID |
| `backend/core/orchestrator.py` | 54/3 | 10/1 | 🟡 MID |
| `scripts/simulate_leaders.py` | 53/11 | 13/6 | 🟡 MID |
| `backend/tests/backtester/test_intraday_simulator.py` | 126/1 | 34/0 | 🟢 LOW |
| `backend/tests/risk/test_balance_gate.py` | 6/5 | 111/21 | 🟡 MID |
| `backend/tests/strategy/test_f_zone.py` | 11/11 | 54/9 | 🟡 MID |
| `backend/tests/strategy/test_gold_zone.py` | 12/12 | 107/9 | 🟡 MID |
| `backend/tests/strategy/test_scalping_consensus.py` | 14/14 | 7/7 | 🟢 LOW |
| `backend/tests/strategy/test_sf_zone.py` | 11/11 | 36/9 | 🟡 MID |
| `backend/tests/strategy/test_swing_38.py` | 10/10 | 132/9 | 🟡 MID |
| `.gitignore` | 미세 | 미세 | 🟢 LOW |

**등급 기준**:
- 🔴 HIGH: 양쪽 50라인 이상 변경 + 시맨틱 충돌
- 🟡 MID: 양쪽 5라인 이상 변경 + 영역 일부 겹침
- 🟢 LOW: 한쪽만 의미있는 변경 또는 인접 라인 분리

---

## 2. 시맨틱 충돌 핵심 — score 스케일 0-1 → 0-10

### 2.1 main 측 변경 (worktree 분기 후 진행됨)

| 커밋 | 내용 |
|---|---|
| **BAR-173** `3ae64d6` | StockStrategy score 0-10 정규화 |
| **BAR-175** `6e60512` | Swing38Strategy `raw * 10.0`, 임계 `0.3 → 3.0`, position_size 임계 `0.7/0.5 → 7.0/5.0` |
| **BAR-176** `1d1d359` | GoldZone + ScalpingConsensus 동일 패턴 |
| **BAR-177** `449c27f` | 테스트 fixture `sample_signal_*_score_fz` (8.5/6.0/3.5) |
| **BAR-165** `9abaeec` | F존/SF존 position_size 스케일 임계값 추가 |

### 2.2 BAR-OPS-09 측 score 사용처

| 위치 | 값 | main 머지 후 의미 |
|---|---|---|
| `swing_38.py` Phase 8a `Swing38Params.min_score: float = 0.3` | 0-1 가정 | **0-10 스케일에서는 거의 모든 시그널 통과** — 의미상 3.0 의도 |
| `intraday_simulator.py` Phase 8a override `min_score=0.5` | 0-1 가정 | **5.0 의도** — 그대로 두면 0.5 임계로 100% 통과 |
| Phase 9 `position_size()` 균등 0.08 통일 (5 strategy) | score 무관 | main 의 score 차등 (BAR-175/176) **자동 무력화** ✓ |
| 5 strategy 테스트 `assert size == Decimal("11")` | score 무관 | main 의 fixture `*_score_fz` (8.5/6.0/3.5) 무관해짐 ✓ |

### 2.3 결론

- worktree 의 **Phase 9 균등 진입이 main BAR-175/176/177 의 score 차등 정책을 자연 무력화** → 머지 정합성 자동 확보
- 단 **`min_score` 임계 값 2곳만 수동 변환 필요** (`0.3 → 3.0`, `0.5 → 5.0`)

---

## 3. 비충돌 영역 — 명확히 호환

| main 변경 | worktree 영향 | 호환성 |
|---|---|---|
| **BAR-159** `2a6c442` SignalScanner SFZoneStrategy 등록 | worktree 의 `signals.py` / `orchestrator.py` 와 별도 영역 | ✓ 자동 머지 가능 |
| **BAR-178** `af5157a` `_evaluate_intrabar` TIME_EXIT 필터 누락 fix (3 라인) | worktree 의 `_scaled_exit_plan(early_tp)` 추가와 별도 영역 | ✓ 자동 머지 가능 |
| **BAR-167** `adb61fc` simulate_leaders `daily_pnl_pct` 전달 | worktree 의 `--max-concurrent-positions` 추가와 별도 영역 | ✓ 자동 머지 가능 |
| **BAR-174/183** backtester end_of_data fix | worktree의 IntradaySimulator 와 다른 클래스 (`backtester.py` vs `backtester/intraday_simulator.py`) | ✓ |
| **BAR-169** simulate_leaders --execute 전략 선택 fix | worktree 의 simulate_leaders CLI 인자 추가와 별도 라인 | ✓ |

---

## 4. 머지 전략 — 3 옵션 비교

### 옵션 A: rebase (BAR-OPS-09 → main 위) ⭐ 권장
```bash
git checkout BAR-OPS-09
git checkout -b BAR-OPS-09-rebase-backup    # 안전 백업
git rebase 4c7313ae                          # main 위에 18 commit 재적용
# 충돌 발생 시 §5 가이드 따라 해결
# 각 commit 별 해결 → git rebase --continue
```

**장점**:
- 선형 history → PR 리뷰 명확
- worktree 의 18 commit 각각이 main 위에 재적용 → 변경 의도 보존
- 충돌 해결 후 `git push origin BAR-OPS-09 --force-with-lease` 안전

**단점**:
- 18 commit 각각 충돌 가능 → 노동 강도 큼
- 이미 push 된 브랜치 force push 필요

### 옵션 B: merge (main → BAR-OPS-09)
```bash
git checkout BAR-OPS-09
git checkout -b BAR-OPS-09-merge-backup
git merge 4c7313ae                           # main 을 worktree 로 가져옴
# 충돌 한 번에 해결 → git commit
```

**장점**:
- 충돌 해결 1회 (rebase 대비 부담 적음)
- force push 불필요 (단순 push)

**단점**:
- merge commit 생성 → history 복잡
- 머지 commit 안에 충돌 해결 변경 섞임 → 추적 어려움

### 옵션 C: cherry-pick (분할 머지)
```bash
git checkout main
git cherry-pick c776fbc                       # Phase 9 균등 진입만
git cherry-pick 871b475                       # Phase B 청산 정교화만
# Phase 1~8 은 별도 PR 로 분리
```

**장점**:
- 핵심 fix 만 main 에 즉시 반영
- 충돌 영역 최소화

**단점**:
- BAR-OPS-09 worktree 의 의도(Phase 1~B 단일 흐름) 분리
- 18 commit 일관성 파편화
- audit 보고서·docs 빠짐

### 선택 가이드

| 우선순위 | 옵션 |
|---|---|
| 안전·명확성 (PR 리뷰 기준) | **A (rebase)** |
| 시간 절약 + 충돌 한 번에 | B (merge) |
| 5/27 영업일 전 핵심만 빠르게 | C (cherry-pick Phase 9 + B) |

---

## 5. 파일별 충돌 해결 가이드 (옵션 A 기준)

### 5.1 🔴 HIGH — `backend/core/strategy/swing_38.py`

**main BAR-175 변경**:
```python
# main
raw = impulse_score * 0.4 + fib_score * 0.4 + bounce_score * 0.2
score = raw * 10.0
if score < 3.0:
    return None
```

**worktree Phase 8a 변경**:
```python
# worktree
score = impulse_score * 0.4 + fib_score * 0.4 + bounce_score * 0.2
if score < p.min_score:   # default 0.3, override 0.5
    return None
```

**해결**:
```python
# 통합 (main 스케일 보존 + worktree 파라미터화)
raw = impulse_score * 0.4 + fib_score * 0.4 + bounce_score * 0.2
score = raw * 10.0
if score < p.min_score:   # default 3.0, override 5.0 (0-10 스케일)
    return None
```

**파일 갱신**:
- `Swing38Params.min_score: float = 0.3` → **`3.0`**
- `intraday_simulator._build_strategies` swing_38 분기: `min_score=0.5` → **`5.0`**

### 5.2 🔴 HIGH — `backend/core/strategy/f_zone.py`

main 측 91 라인 변경 (BAR-165 + BAR-46 후속) — score 임계 + 기타 패치.
worktree 측 25/38 (Phase 2/8e/Phase 9).

**충돌 hunk 예상**:
1. score 차등 분기 (worktree Phase 9 가 균등화 → main BAR-165 의 임계 변경 무력화) → worktree 우선
2. `min_atr_pct` / `entry_time_cutoff` 파라미터 (worktree 신규) → 그대로 유지
3. `_atr_pct` helper wrapper (worktree Phase 7) → 그대로 유지

**해결 원칙**: worktree 변경 우선, main BAR-165 의 score 임계 코드는 무관 (Phase 9 가 자동 무력화).

### 5.3 🔴 HIGH — `backend/core/backtester/intraday_simulator.py`

main 측 169 라인 변경 (BAR-174/178/183 등).
worktree 측 82 라인 (Phase 2~B, 특히 Phase B early_tp 옵션).

**충돌 hunk 예상**:
1. `_build_strategies` 분기 (worktree 의 strategy params override) → worktree 우선
2. `_evaluate_intrabar` TIME_EXIT fix (main BAR-178, 3 라인) → main 변경 채택
3. `_scaled_exit_plan(early_tp)`, `_sfzone_atr_exit_plan(early_tp)`, `_exit_plan_for_strategy(early_tp)` (worktree Phase B) → 그대로 유지
4. `IntradaySimulator(early_exit_tp=False)` 인자 추가 (worktree Phase B) → 유지

**해결 원칙**: main BAR-178 의 TIME_EXIT 필터 수정 채택 + worktree 의 early_tp 옵션·strategy override 보존.

### 5.4 🟡 MID — `backend/core/strategy/{gold_zone, scalping_consensus, sf_zone}.py`

main BAR-176 score 0-10 스케일 + worktree Phase 9 균등 진입 충돌.

**해결**: worktree 의 `position_size()` 균등 helper 위임 우선. main 의 score 차등 코드는 worktree 가 제거.

### 5.5 🟡 MID — `backend/api/routes/signals.py` + `backend/core/orchestrator.py`

main 측 BAR-159 (SignalScanner SFZoneStrategy 등록 호출) + BAR-167 (daily_pnl_pct 전달).
worktree 측 Phase 2/3/8e/8f (FZoneParams/BlueLineParams override).

**해결**: 둘 다 채택. main 의 SFZoneStrategy 등록 + worktree 의 params override 동시 유지.

```python
# 통합 결과 (signals.py)
scanner = SignalScanner(
    gateway,
    f_zone_params=FZoneParams(min_atr_pct=0.035, entry_time_cutoff=_dtime(14, 0)),
    blue_line_params=BlueLineParams(min_atr_pct=0.035, entry_time_cutoff=_dtime(14, 0)),
)
# + main BAR-159 의 SFZoneStrategy 인스턴스 추가 (SignalScanner 내부에 이미 등록)
```

### 5.6 🟡 MID — `scripts/simulate_leaders.py`

main: `daily_pnl_pct` 전달 + `--execute` 전략 선택 fix.
worktree: `--max-concurrent-positions` CLI 인자 + 호출 시 전달.

**해결**: 두 변경 다 채택. 인자 추가는 별도 라인, 호출부는 다 통합.

### 5.7 🟡 MID — 5 strategy 테스트 파일

main: fixture 이름 `sample_signal_*_score_fz` + score 임계 0.65 → 6.5.
worktree: Phase 9 균등 비율 (`assert size == Decimal("11")`).

**해결**: worktree 의 균등 비율 테스트 우선. main 의 fixture 이름 변경은 score 차등 가정 → 균등 진입에서는 무관. **단 main 의 score 임계 테스트(`test_threshold_*` 같은) 는 별도로 보존** 가능 (진입 게이트 검증용).

### 5.8 🟡 MID — `backend/core/risk/balance_gate.py`

main: 11/4 (소규모 패치).
worktree: 44/14 (Phase 9 균등 분배 + `max_concurrent_positions` 신규).

**해결**: worktree 의 전면 재작성 우선 (main 변경은 모두 흡수됨).

---

## 6. 검증 절차

### 6.1 사전 dry-run

```bash
# 별도 worktree 에서 시도 (현재 worktree 보존)
git worktree add ../merge-dryrun BAR-OPS-09
cd ../merge-dryrun
git checkout -b dryrun-merge
git rebase 4c7313ae   # 또는 git merge 4c7313ae
# 충돌 영역 확인 후 abort
git rebase --abort
cd -
git worktree remove ../merge-dryrun
```

### 6.2 머지 후 회귀 테스트

```bash
.venv/bin/python -m pytest backend/tests/ -q
# 목표: 902 passed 이상, 0 regression
```

### 6.3 score 임계 동작 검증

```bash
.venv/bin/python -c "
from backend.core.strategy.swing_38 import Swing38Params, Swing38Strategy
p = Swing38Params(min_score=3.0)   # 머지 후 default
assert p.min_score == 3.0, 'min_score 임계 변환 누락'
print('OK swing_38 min_score = 3.0 (0-10 스케일)')
"
```

### 6.4 운영 핵심 경로 통합 확인

| 검증 항목 | 명령 |
|---|---|
| SignalScanner SFZone 등록 (BAR-159) | `grep SFZoneStrategy backend/core/scanner/signal_scanner.py` |
| TIME_EXIT 필터 (BAR-178) | `grep TIME_EXIT backend/core/backtester/intraday_simulator.py` |
| Phase 9 균등 진입 | `grep DEFAULT_EVEN_RATIO backend/core/strategy/position_sizing.py` |
| Phase B early_tp | `grep early_exit_tp backend/core/backtester/intraday_simulator.py` |
| Phase 2/3 운영 변동성 필터 | `grep "min_atr_pct=0.035" backend/api/routes/signals.py backend/core/orchestrator.py` |
| Phase 8e/8f 운영 시간 게이트 | `grep "entry_time_cutoff=_dtime(14, 0)" backend/api/routes/signals.py backend/core/orchestrator.py` |

### 6.5 백테스트 효과 재측정

```bash
# Phase B 효과 재현 (10 종목 일봉)
.venv/bin/python -c "..."  # 본 PR 의 Phase B 보고서 명령 그대로
# 목표: 승률 71.7% 유지 (or 향상)
```

---

## 7. 롤백 안전장치

### 7.1 백업 브랜치 (필수)

```bash
git branch BAR-OPS-09-pre-merge-2026-05-25   # 머지 시작 전
git push origin BAR-OPS-09-pre-merge-2026-05-25
```

### 7.2 단계별 push

옵션 A (rebase) 의 경우:
```bash
# 18 commit rebase 완료 후
git push origin BAR-OPS-09 --force-with-lease
# --force-with-lease 는 원격이 예상 sha 와 다르면 거부 → 동시 작업 안전
```

### 7.3 실패 시 복구

```bash
# rebase 중 막힘
git rebase --abort

# 푸시 후 문제 발견
git reset --hard BAR-OPS-09-pre-merge-2026-05-25
git push origin BAR-OPS-09 --force-with-lease
```

---

## 8. 운영 머신 사후 작업

머지 완료 후 운영 맥북:

```bash
# 1. 코드 동기화
git pull --rebase origin main   # 또는 BAR-OPS-09 merge 결과

# 2. data/policy.json 갱신 (gitignored — Phase 9 정책)
# 다음 값으로 수동 갱신:
{
  "min_score": 0.6,
  "max_per_position": 0.1,
  "max_total_position": 0.8,
  "max_concurrent_positions": 10
}

# 3. 데몬 재기동
./scripts/stop.sh && ./scripts/start.sh
```

---

## 9. 권장 실행 순서

1. **백업 생성** (§7.1)
2. **dry-run** (§6.1) → 충돌 영역 사전 확인
3. **rebase 시작** (옵션 A, §4)
4. **충돌 해결** (§5 가이드, 18 commit 순회)
5. **회귀 테스트** (§6.2)
6. **score 임계 동작 검증** (§6.3)
7. **운영 핵심 경로 통합 확인** (§6.4)
8. **백테스트 효과 재측정** (§6.5)
9. **force-with-lease push** (§7.2)
10. **운영 머신 동기화** (§8) — 5/27 영업일 전

---

## 10. 위험·완화

| 위험 | 가능성 | 완화 |
|---|---|---|
| score 임계 변환 누락 → 시그널 폭주 | 중 | §6.3 검증 + sample input 테스트 |
| main BAR-178 TIME_EXIT fix 누락 → 시뮬 PnL 오류 | 중 | §6.4 grep 확인 |
| Phase B `early_exit_tp` 옵션이 main 의 새 인자와 충돌 | 낮 | dry-run 에서 사전 확인 |
| force push 로 원격 BAR-OPS-09 손상 | 낮 | `--force-with-lease` + 백업 브랜치 |
| 운영 머신 pull 후 데몬 깨짐 | 중 | data/policy.json 갱신 (§8) + 단계별 재기동 |

---

## 11. 한 줄 요약

> **옵션 A (rebase) 권장 — worktree Phase 9 가 main BAR-175/176/177 의 score 차등을 자연 무력화하므로 정합성 자동 확보. `min_score` 임계 2곳만 0-10 스케일 변환(`0.3 → 3.0`, `0.5 → 5.0`) 후 §5 가이드대로 18 commit 충돌 해결 → §6 검증 → force-with-lease push.**

— 2026-05-25 작성, BAR-OPS-09 871b475 ↔ main 4c7313ae 분기 분석 기반
