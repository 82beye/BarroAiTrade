#!/usr/bin/env python3
"""ai_swing 고아 포지션 진단·복구 — 2026-05-29 사고 대응 도구 (2026-07-30 신규).

고아 포지션이란
--------------
브로커에는 보유가 있는데 `data/active_positions.json` 에 장부가 없는 상태다.
그러면 `HoldingEvaluator.evaluate_holding()` 이 `ctx=None` 경로로 빠져
**전략 프로파일을 통째로 무시**하고 `data/policy.json`(SL -2.0 / TP +5.0)만 평가한다
→ ai_swing 의 SL -15% 와 min_hold 3일이 전부 무시되고 -2% 대에서 전량 손절된다.
(현상 고정 테스트: `backend/tests/test_ai_swing_orphan.py`)

2026-05-29 swing_38 비활성 시 정확히 이 경로로 잔여 4종목이 방치돼 사용자가 수동
청산해야 했다(평균 -0.985%).

★ 안전 규칙 ★
- **주문을 내지 않는다.** 장부(JSON)만 읽고 쓴다.
- **원천이 없으면 복원하지 않는다** (§0-2). `order_audit.csv` 에 해당 종목의 ai_swing
  매수 기록이 없으면 `no_data` 로 보고하고 사용자 판단에 맡긴다.
  `entry_time` 을 "지금"으로 채우면 `min_hold_days=3` 이 리셋돼 3일 더 묶이므로 금지.
- `--apply` 는 장부에 **없는** 종목만 추가한다. 기존 엔트리는 절대 덮어쓰지 않는다
  (심볼 단일 키 구조라 덮어쓰면 다른 전략 포지션이 소실된다).

사용:
  # 진단만 (기본) — 운영 머신
  python scripts/ai_swing_recover.py --dry-run
  # 복원
  python scripts/ai_swing_recover.py --apply
  # 잔고를 파일로 주입 (API 키 없는 환경 검증용)
  python scripts/ai_swing_recover.py --dry-run --balance-file /tmp/holdings.json
"""
from __future__ import annotations

import argparse
import csv
import json
import os
import sys
from dataclasses import dataclass
from pathlib import Path

_REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_REPO))

_DATA_DIR = Path(os.environ.get("BARRO_DATA_DIR", str(_REPO / "data")))
AUDIT_PATH = _DATA_DIR / "order_audit.csv"
POSITIONS_PATH = _DATA_DIR / "active_positions.json"

# 장부에 저장되는 전략 태그는 버전 없는 sid("ai_swing")지만, audit 의 strategy_id 는
# 호출자에 따라 "ai_swing" / "ai_swing_v1" 둘 다 올 수 있어 접두 매칭한다.
_STRATEGY_PREFIX = "ai_swing"


@dataclass(frozen=True)
class OrphanCandidate:
    """브로커 보유 ∖ 장부 차집합 중 ai_swing 흔적이 있는 종목."""

    symbol: str
    name: str
    broker_qty: int
    entry_time: str      # audit ts (ISO8601) — 원천. 빈 문자열이면 복원 불가
    entry_price: float   # audit avg_fill_price 우선, 없으면 price
    order_no: str
    source: str          # 근거 요약 (audit 행)


# ─── 순수 로직 (테스트 대상) ──────────────────────────────────────────────
def parse_audit_buys(rows: list[dict], strategy_prefix: str = _STRATEGY_PREFIX) -> dict[str, dict]:
    """order_audit 행들 → {symbol: 가장 최근 ai_swing 매수 행}.

    - `action` 이 ORDERED/DRY_RUN 인 buy 만 본다(BLOCKED/FAILED 는 체결 아님).
    - `strategy_id` 가 strategy_prefix 로 시작해야 한다.
    - 같은 종목이 여러 번이면 **가장 최근 ts** 를 남긴다(재진입 후 장부 유실 케이스).
    """
    out: dict[str, dict] = {}
    for row in rows:
        if (row.get("side") or "").strip().lower() != "buy":
            continue
        if (row.get("action") or "").strip().upper() not in ("ORDERED", "DRY_RUN"):
            continue
        sid = (row.get("strategy_id") or "").strip()
        if not sid.startswith(strategy_prefix):
            continue
        sym = (row.get("symbol") or "").strip()
        if not sym:
            continue
        prev = out.get(sym)
        if prev is None or (row.get("ts") or "") > (prev.get("ts") or ""):
            out[sym] = row
    return out


def _to_float(v, default: float = 0.0) -> float:
    try:
        if v is None or str(v).strip() in ("", "MKT"):
            return default
        return float(v)
    except (TypeError, ValueError):
        return default


def _to_int(v, default: int = 0) -> int:
    try:
        if v is None or str(v).strip() == "":
            return default
        return int(float(v))
    except (TypeError, ValueError):
        return default


def find_orphans(
    broker_holdings: dict[str, dict],
    ledger_symbols: set[str],
    audit_buys: dict[str, dict],
) -> tuple[list[OrphanCandidate], list[str]]:
    """차집합 → (복원 가능한 고아, 원천 없는 고아 심볼).

    Args:
        broker_holdings: {symbol: {"name": str, "qty": int}}
        ledger_symbols: active_positions.json 의 심볼 집합
        audit_buys: parse_audit_buys 결과

    Returns:
        (복원 가능 후보, 원천 부재 심볼 목록). 후자는 복원하지 않고 보고한다 (§0-2).
    """
    recoverable: list[OrphanCandidate] = []
    unknown: list[str] = []
    for sym, info in sorted(broker_holdings.items()):
        if sym in ledger_symbols:
            continue                      # 장부 있음 — 고아 아님
        row = audit_buys.get(sym)
        if row is None:
            unknown.append(sym)           # ai_swing 흔적 없음 → 다른 전략/수동 매매
            continue
        ts = (row.get("ts") or "").strip()
        if not ts:
            unknown.append(sym)           # entry_time 원천 없음 → 복원 금지
            continue
        price = _to_float(row.get("avg_fill_price")) or _to_float(row.get("price"))
        if price <= 0:
            unknown.append(sym)           # 진입가 원천 없음 → 복원 금지
            continue
        recoverable.append(OrphanCandidate(
            symbol=sym,
            name=str(info.get("name") or ""),
            broker_qty=_to_int(info.get("qty")),
            entry_time=ts,
            entry_price=price,
            order_no=str(row.get("order_no") or ""),
            source=f"audit ts={ts} action={row.get('action')} sid={row.get('strategy_id')}",
        ))
    return recoverable, unknown


# ─── I/O ──────────────────────────────────────────────────────────────────
def read_audit_rows(path: Path = AUDIT_PATH) -> list[dict]:
    """order_audit.csv 전체 행. 부재·파손 시 빈 목록(예외 흡수)."""
    try:
        with open(path, "r", encoding="utf-8", newline="") as f:
            return list(csv.DictReader(f))
    except Exception as exc:
        print(f"  ⚠️ audit 읽기 실패({type(exc).__name__}) — 원천 없음으로 처리: {path}")
        return []


def read_ledger_symbols() -> set[str]:
    from backend.core.journal.active_positions import ActivePositionStore

    store = ActivePositionStore(POSITIONS_PATH)
    return set(store.load_all().keys())


def read_balance_file(path: str) -> dict[str, dict]:
    """검증용 잔고 주입 — [{"symbol","name","qty"}, ...] 또는 {symbol: {...}}."""
    raw = json.loads(Path(path).read_text(encoding="utf-8"))
    if isinstance(raw, dict):
        return {str(k): dict(v) for k, v in raw.items()}
    out: dict[str, dict] = {}
    for row in raw:
        sym = str(row.get("symbol") or "").strip()
        if sym:
            out[sym] = {"name": row.get("name") or "", "qty": row.get("qty") or 0}
    return out


async def fetch_broker_holdings() -> dict[str, dict]:
    """운영 머신 브로커 잔고 (읽기 전용 조회 TR — 주문 없음)."""
    from backend.core.gateway.kiwoom_native_account import KiwoomNativeAccountFetcher
    from backend.core.gateway.kiwoom_native_oauth import KiwoomNativeOAuth
    from pydantic import SecretStr

    oauth = KiwoomNativeOAuth(
        app_key=SecretStr(os.environ["KIWOOM_APP_KEY"]),
        app_secret=SecretStr(os.environ["KIWOOM_APP_SECRET"]),
        base_url=os.environ.get("KIWOOM_BASE_URL", "https://mockapi.kiwoom.com"),
    )
    balance = await KiwoomNativeAccountFetcher(oauth=oauth).fetch_balance()
    return {
        h.symbol: {"name": h.name, "qty": int(h.qty)}
        for h in balance.holdings
    }


def apply_recovery(candidates: list[OrphanCandidate]) -> int:
    """장부에 없는 종목만 추가. 기존 엔트리는 절대 건드리지 않는다."""
    from backend.core.journal.active_positions import (
        ActivePosition, ActivePositionStore, Tranche,
    )

    store = ActivePositionStore(POSITIONS_PATH)
    existing = store.load_all()
    added = 0
    for c in candidates:
        if c.symbol in existing:
            print(f"  skip {c.symbol} — 장부 이미 존재(덮어쓰지 않는다)")
            continue
        pos = ActivePosition(
            symbol=c.symbol, name=c.name, strategy=_STRATEGY_PREFIX,
            entry_price=c.entry_price,
            entry_time=c.entry_time,          # ★원천 그대로 — "지금"으로 채우지 않는다
            total_recommended_qty=c.broker_qty,
            sl_pct=float(os.environ.get("BARRO_AI_SWING_SL_PCT", "-15.0")),
            # 브로커 보유 전량을 단일 filled 트랜치로 기록 — pending 을 남기면
            # 데몬 DCA 가 가짜 tranche2 를 실주문할 수 있다(_NO_DCA_STRATEGIES 로
            # 이중 방어하지만 장부 자체를 정확히 둔다).
            tranches=[Tranche(
                tranche=1, ratio=1.0, qty=c.broker_qty, trigger_drop_pct=0.0,
                status="filled", order_no=c.order_no,
                filled_price=c.entry_price, filled_at=c.entry_time,
            )],
        )
        store.upsert(pos)
        added += 1
        print(f"  restored {c.symbol} {c.name} qty={c.broker_qty} "
              f"entry={c.entry_price} entry_time={c.entry_time}")
    return added


def main() -> int:
    ap = argparse.ArgumentParser(description="ai_swing 고아 포지션 진단·복구 (주문 없음)")
    mode = ap.add_mutually_exclusive_group()
    mode.add_argument("--dry-run", action="store_true", default=True, help="진단만 (기본)")
    mode.add_argument("--apply", action="store_true", help="장부 복원 실행")
    ap.add_argument("--balance-file", help="잔고 JSON 주입 (API 키 없는 환경 검증용)")
    args = ap.parse_args()

    print("=== ai_swing 고아 포지션 진단 ===")
    print(f"장부: {POSITIONS_PATH}")
    print(f"원천: {AUDIT_PATH}")

    # 브로커 잔고
    if args.balance_file:
        holdings = read_balance_file(args.balance_file)
        print(f"잔고: 파일 주입 {len(holdings)}종목")
    else:
        try:
            import asyncio
            holdings = asyncio.run(fetch_broker_holdings())
            print(f"잔고: 브로커 조회 {len(holdings)}종목")
        except KeyError as exc:
            print(f"⚠️ 브로커 조회 불가 — 환경변수 부재({exc}). "
                  f"운영 머신에서 실행하거나 --balance-file 로 주입할 것.")
            return 2
        except Exception as exc:
            print(f"⚠️ 브로커 조회 실패 — {type(exc).__name__}: {exc}")
            return 2

    ledger = read_ledger_symbols()
    audit_buys = parse_audit_buys(read_audit_rows())
    recoverable, unknown = find_orphans(holdings, ledger, audit_buys)

    print(f"\n장부 등록: {len(ledger)}종목 / ai_swing audit 매수 기록: {len(audit_buys)}종목")
    if not recoverable and not unknown:
        print("✅ 고아 0건 — 브로커 보유가 모두 장부에 있다.")
        return 0

    if recoverable:
        print(f"\n■ 복원 가능한 ai_swing 고아 {len(recoverable)}건")
        for c in recoverable:
            print(f"  {c.symbol} {c.name:<14} qty={c.broker_qty:>5} "
                  f"entry={c.entry_price:>10,.0f} | {c.source}")
    if unknown:
        print(f"\n■ 원천 없는 보유 {len(unknown)}건 — **복원하지 않는다** (status=no_data)")
        print(f"  {', '.join(unknown)}")
        print("  → ai_swing audit 흔적이 없다. 다른 전략/수동 매매일 수 있으므로")
        print("     사용자가 판단한다. entry_time 을 임의로 채우면 min_hold 가 리셋된다.")

    if args.apply:
        if not recoverable:
            print("\n복원 대상 없음 — 종료.")
            return 0
        print(f"\n■ 복원 실행")
        added = apply_recovery(recoverable)
        print(f"  {added}건 장부 추가 완료.")
        print("  → scripts/evaluate_holdings.py 로 프로파일 적용을 확인할 것.")
    else:
        print("\n(진단 모드 — 복원하려면 --apply)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
