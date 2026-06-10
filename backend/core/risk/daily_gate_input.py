"""BAR-OPS-38 — 일일손실 게이트 입력(당일 실현+평가) + latch 영속 상태.

근거: reports/2026-06-10/2026-06-10_매매복기.md 심층분석 2 (P0 권고 #1).

기존 게이트 입력의 구조 결함(6/10 실증):
- intraday_buy_daemon._scan_and_buy: balance.total_pnl_rate(kt00018 tot_prft_rt)
  = '현 보유 매입금액 대비 누적 평가수익률'. 당일 손익이 아니며, **보유가 비는 순간
  0% 로 리셋**된다. 6/10 09:05 차단(-5.76% 표기) → 09:07 이월 매도 체결로 보유 0
  → 0% → 이후 31건 무차단(차단된 100090 을 3분 뒤 그대로 매수).
- SupertrendAutoTrader._account_pnl_pct: 분모는 추정예탁자산으로 개선됐으나
  분자가 여전히 '보유 평가손익'뿐 — 당일 실현손실 미반영 + 보유 0 리셋 동일.

새 입력 (브로커 권위 데이터만 사용, 파일 스냅샷 의존 없음):
    당일손익률(%) = (당일 실현손익 net 합(ka10074) + 현 보유 평가손익(kt00018 tot_evlt_pl))
                    / 추정예탁자산(prsm_dpst_aset_amt) × 100

- ka10074(일자별실현손익합산)는 매도 체결 기준 실현손익(수수료·세금 차감 net) —
  보유가 비어도 당일 실현손실이 그대로 남아 게이트가 무력화되지 않는다.
- 보유 평가손익은 매입가 대비라 이월 포지션의 전일 발생분을 포함한다(보수적 —
  손실 시 매수 차단이라는 게이트 방향에 부합. 2026-06-02 사고의 원인이었던
  balance_history 파일 오염 의존은 없음).
- 조회 실패 시 0%(fail-open) — 5/29·6/1·6/2 '가짜 손실 과차단' 사고 교훈.
  단 latch 는 파일 영속이라 실패와 무관하게 유지된다.

latch 영속(BAR-OPS-35 P0#2 보완): LiveOrderGate 의 latch 는 인스턴스 메모리에만
있는데 데몬은 사이클마다 게이트를 재생성 → latch 가 사이클 간 증발한다(6/10 발견).
DailyGateStateStore 가 data/daily_gate_state.json 에 당일 latch 를 영속한다.
"""
from __future__ import annotations

import json
import logging
import os
import tempfile
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from typing import Any, Optional

logger = logging.getLogger(__name__)

_STATE_FILENAME = "daily_gate_state.json"


def default_state_path() -> Path:
    """data/daily_gate_state.json — 저장소 루트 기준 (daemon/_DATA_DIR 와 동일 규약)."""
    root = Path(__file__).resolve().parents[3]
    return root / "data" / _STATE_FILENAME


def _utc_today() -> str:
    # order_audit/게이트 일일 한도와 동일하게 UTC 일자 사용 (KRX 정규장 09:00~15:30 KST
    # = 00:00~06:30 UTC 로 달력일이 일치).
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


class DailyGateStateStore:
    """일일손실 latch 상태 영속 — 당일(UTC 일자) 단위. atomic write."""

    def __init__(self, path: str | Path | None = None) -> None:
        self._path = Path(path) if path else default_state_path()

    def load(self) -> dict:
        try:
            data = json.loads(self._path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {}
        if not isinstance(data, dict):
            return {}
        # 당일 상태만 유효 — 일자가 다르면 빈 상태(자동 롤오버, 이월 오염 없음).
        if data.get("date") != _utc_today():
            return {}
        return data

    def is_latched(self) -> bool:
        return bool(self.load().get("latched"))

    def latch_reason(self) -> str:
        return str(self.load().get("latch_reason", ""))

    def set_latched(self, reason: str) -> None:
        state = self.load()
        state.update({
            "date": _utc_today(),
            "latched": True,
            "latch_reason": reason,
            "latched_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        })
        self._write(state)

    def _write(self, state: dict) -> None:
        try:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            fd, tmp = tempfile.mkstemp(dir=str(self._path.parent), suffix=".tmp")
            try:
                with os.fdopen(fd, "w", encoding="utf-8") as f:
                    json.dump(state, f, ensure_ascii=False, indent=1)
                os.replace(tmp, self._path)
            finally:
                if os.path.exists(tmp):
                    try:
                        os.unlink(tmp)
                    except OSError:
                        pass
        except OSError as exc:
            logger.error("daily_gate_state write 실패: %s", type(exc).__name__)


async def compute_daily_gate_input(
    account: Any,
    balance: Any = None,
) -> Decimal:
    """일일손실 게이트 입력(%) — 당일 실현(ka10074 net) + 보유 평가손익 / 추정예탁자산.

    Args:
        account: KiwoomNativeAccountFetcher (.fetch_daily_pnl/.fetch_balance)
        balance: 호출부가 이미 조회한 AccountBalance 재사용(없으면 직접 조회 — API 1회 절약)

    Returns:
        signed % (Decimal). 조회 실패·분모 0 → 0%(fail-open, 로그).
    """
    today_kw = datetime.now(timezone.utc).strftime("%Y%m%d")
    realized = Decimal("0")
    try:
        entries = await account.fetch_daily_pnl(today_kw, today_kw)
        for e in entries or []:
            if getattr(e, "date", "") == today_kw:
                realized += Decimal(str(getattr(e, "net_pnl", 0) or 0))
    except Exception as exc:  # noqa: BLE001 — fail-open(과차단 방지) + latch 는 파일로 별도 유지
        logger.warning("게이트 입력: ka10074 당일실현 조회 실패(%s) — 실현분 0 처리",
                       type(exc).__name__)

    eval_pnl = Decimal("0")
    base = Decimal("0")
    try:
        if balance is None:
            balance = await account.fetch_balance()
        eval_pnl = Decimal(str(getattr(balance, "total_pnl", 0) or 0))
        base = Decimal(str(getattr(balance, "estimated_deposit", 0) or 0))
        if base <= 0:
            # 추정예탁자산 미제공 시 폴백 — 평가총액 기반(근사). 둘 다 0 이면 fail-open.
            base = Decimal(str(getattr(balance, "total_eval", 0) or 0))
    except Exception as exc:  # noqa: BLE001
        logger.warning("게이트 입력: 잔고 조회 실패(%s) — 0%% fail-open", type(exc).__name__)
        return Decimal("0.0")

    if base <= 0:
        logger.warning("게이트 입력: 기준자산 0 — 0%% fail-open (realized=%s eval=%s)",
                       realized, eval_pnl)
        return Decimal("0.0")

    pct = (realized + eval_pnl) / base * Decimal("100")
    return pct.quantize(Decimal("0.01"))


__all__ = ["DailyGateStateStore", "compute_daily_gate_input", "default_state_path"]
