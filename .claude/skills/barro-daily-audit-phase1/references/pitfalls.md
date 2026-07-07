# 함정 — 반드시 회피 (Phase 1 구현 시 흔한 4가지)

`references/files/` 의 코드 본문은 이 함정들을 이미 회피하도록 작성돼 있다. 그래도 적용 후 테스트가 실패하면 이 문서를 순서대로 점검하라.

## 1. `markdown_report` import 금지

**문제**: `backend/core/journal/markdown_report.py` 는 `kiwoom_native_rank` → `httpx` 를 전이 import 한다. 가벼운 헬퍼 하나 가져오려고 import 하면 `httpx` 가 import 안 된 환경에서 ImportError.

**해결**: `_strategy_perf_track.py` 의 `_fmt_signed` 같은 sub-line 헬퍼는 모듈 안에 직접 인라인 정의한다. `02-strategy-perf-track.py` 는 이미 그렇게 작성됨:

```python
def _fmt_signed(v) -> str:
    return f"{int(v):+,}"
```

**증상**: `pytest backend/tests/test_daily_pipeline.py` 가 collection 단계에서 `ModuleNotFoundError: No module named 'httpx'` 또는 `ImportError` 로 실패.

## 2. float/Decimal 혼용 금지

**문제**: `OHLCV.high / low / close / open` 은 **float**, `entry_price` / `net` 은 **Decimal**. 혼용 시 `TypeError: unsupported operand type(s) for *: 'Decimal' and 'float'`.

**해결**: 캔들 값을 Decimal 연산에 쓸 때 반드시 `Decimal(str(c.high))` 로 변환:

```python
peak = max(Decimal(str(c.high)) for c in hold)
peak_gain = (peak - entry_price) / entry_price  # entry_price 도 Decimal
```

`03-loss-drill-down.py` 의 `diagnose`, `section_holding`, `section_pre_entry` 가 이미 모두 그렇게 작성됨. 테스트 `test_diagnose_handles_float_candles_and_detects_immediate_drop` 가 이 회귀 방지용 — float OHLCV + Decimal entry_price 조합으로 동작 확인.

**증상**: drill-down 실행 시 또는 `test_diagnose_*` 에서 `TypeError`.

## 3. legacy KiwoomRestAPI import 경로

**문제**: `backend/legacy_scalping/` 은 정식 패키지가 아니다 (또는 `__init__.py` 가 적절히 구성돼 있지 않다). `from backend.legacy_scalping.execution.kiwoom_api import KiwoomRestAPI` 하면 ImportError.

**해결**: `sys.path` 에 `backend/legacy_scalping` 을 추가하고 짧은 경로로 import:

```python
legacy_root = _REPO_ROOT / "backend" / "legacy_scalping"
if str(legacy_root) not in sys.path:
    sys.path.insert(0, str(legacy_root))
from execution.kiwoom_api import KiwoomRestAPI
```

`01-daily-evening-pipeline.py` 의 `fetch_executions_live` 가 이미 그렇게 작성됨.

**증상**: 라이브 모드 (`--mode real` / `--executions-file` 없이 실행) 에서 `ModuleNotFoundError: No module named 'backend.legacy_scalping...'`. 단위 테스트는 영향 없음 (테스트에서는 이 함수 호출 안 함).

## 4. `scripts/__init__.py` 부재

**문제**: 테스트가 `from scripts._daily_evening_pipeline import ...` 함. `scripts/` 가 패키지가 아니면 ImportError.

**해결**: worktree 에 `scripts/__init__.py` 가 이미 존재 (확인 완료). 만약 없다면 빈 파일로 생성:

```bash
touch "$WORKTREE/scripts/__init__.py"
```

**증상**: `pytest backend/tests/test_daily_pipeline.py` 의 collection 단계에서 `ModuleNotFoundError: No module named 'scripts'`.

## 5. 커밋 범위 — 잔재 파일 포함 금지

PRD 와 무관한 변경(`docs/.bkit-memory.json`, `docs/.pdca-status.json`, `docs/.pdca-snapshots/`, `analysis/imports/2026-05-13/REPORT_*.md` 등) 이 worktree 에 남아 있을 수 있다. **6개 대상 파일만 명시적으로 stage**:

```bash
git add scripts/_daily_evening_pipeline.py \
        scripts/_strategy_perf_track.py \
        scripts/_loss_drill_down.py \
        backend/tests/test_daily_pipeline.py \
        docs/04-report/features/2026-05-22-daily-pipeline.md \
        .gitignore
```

`git add .` 또는 `git add -A` 절대 사용 금지.

## 6. `_REPO_ROOT` 계산

3개 스크립트 모두 다음 패턴 사용:

```python
_REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_REPO_ROOT))
```

이는 `scripts/` 안에서 실행될 때 정확. **`references/files/` 의 파일을 직접 실행하면 안 된다** — 반드시 worktree 의 `scripts/` 경로로 옮긴 다음 실행.

## 디버깅 흐름

테스트 실패 시:

1. **collection 에러** → §1, §3, §4 점검
2. **runtime TypeError** (Decimal/float) → §2 점검
3. **runtime ImportError (httpx)** → §1 점검
4. **runtime ImportError (legacy)** → §3 점검, 또는 `--executions-file` 로 우회

`references/files/` 의 6개 파일은 원격 세션에서 14건 + 회귀 154건 통과까지 검증된 본문이다. 그대로 옮기면 통과해야 한다. 통과 안 되면 옮기는 과정의 누락·오타 우선 의심.
