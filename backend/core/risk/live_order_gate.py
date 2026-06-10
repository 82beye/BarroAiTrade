"""BAR-OPS-17 — LiveOrderGate: 실전 진입 안전 게이트.

KiwoomNativeOrderExecutor 위 wrapper. 실전(api.kiwoom.com) 호출 전:
1. LIVE_TRADING_ENABLED 환경변수 검증 (없으면 강제 DRY_RUN)
2. 일일 손실 한도 (-N% 도달 시 신규 매수 차단)
3. 일일 거래수 한도 (N 건 초과 시 차단)
4. audit log append (감사 무결성)

BAR-64 Kill Switch / BAR-68 audit log 의 경량 통합 버전.
정식 BAR-64/68 머지 시 이 게이트는 제거 또는 보강.
"""
from __future__ import annotations

import asyncio
import csv
import logging
import os
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from typing import Optional

from backend.core.gateway.kiwoom_native_orders import (
    KiwoomNativeOrderExecutor,
    OrderResult,
    OrderSide,
)
from backend.core.notify.telegram import (
    TelegramNotifier,
    format_blocked_alert,
)

logger = logging.getLogger(__name__)


_AUDIT_HEADERS = [
    "ts", "action", "side", "symbol", "qty", "price",
    "order_no", "return_code", "blocked", "reason",
    "strategy_id",  # BAR-OPS-09 Phase D2.6 (2026-05-29) — 전략별 KPI 측정 인프라
    # BAR-OPS-35 (2026-06-08) — 요청수량(qty) ≠ 체결수량 분리. DCA/부분체결 환경에서
    #   audit 기반 손익 재구성을 정확히 하기 위함(001740 audit 296 vs filled 178 sync-loss).
    "filled_qty", "avg_fill_price",
]


def _migrate_audit_csv_header(audit_path: Path) -> bool:
    """기존 audit csv 에 _AUDIT_HEADERS 의 누락 컬럼을 끝에 추가하는 in-place migration.

    10→11(strategy_id)·11→13(filled_qty/avg_fill_price) 모두 처리. 누락분만 append.

    Returns:
        True 시 migration 수행, False 시 이미 최신 또는 파일 없음.
    """
    if not audit_path.exists():
        return False
    try:
        with open(audit_path, "r", encoding="utf-8", newline="") as f:
            reader = csv.reader(f)
            rows = list(reader)
    except OSError:
        return False
    if not rows:
        return False
    header = rows[0]
    missing = [h for h in _AUDIT_HEADERS if h not in header]
    if not missing:
        return False  # already migrated (모든 컬럼 존재)

    pad = [""] * len(missing)
    new_header = header + missing
    new_rows = [new_header] + [r + pad for r in rows[1:]]

    # atomic write — tempfile + replace
    tmp = audit_path.with_suffix(audit_path.suffix + ".tmp")
    with open(tmp, "w", encoding="utf-8", newline="") as f:
        w = csv.writer(f)
        w.writerows(new_rows)
    tmp.replace(audit_path)
    logger.info("order_audit.csv migration 완료 (+%s)", ",".join(missing))
    return True


class TradingDisabled(RuntimeError):
    """LIVE_TRADING_ENABLED 미설정 + dry_run=False 시도."""


class DailyLossLimitExceeded(RuntimeError):
    """일일 손실 한도 도달 — 신규 매수 차단."""


class DailyOrderLimitExceeded(RuntimeError):
    """일일 거래수 한도 초과."""


class InvalidOrderQty(ValueError):
    """qty ≤ 0 으로 주문 시도. executor 단의 ValueError 가 audit 에 'FAILED' 로
    남는 것을 방지하기 위해 게이트에서 'BLOCKED' 로 사전 차단한다.

    원인: DCA 분할 비율 계산 시 보유 1주 × 25% = 0주 같은 floor=0 케이스
    (2026-05-13 운영 로그에서 ValueError 2건 발견)."""


@dataclass(frozen=True)
class GatePolicy:
    daily_loss_limit_pct: Decimal = Decimal("-3.0")     # -3% 도달 시 차단
    daily_max_orders: int = 50                           # 일 50건
    require_env_flag: bool = True                        # LIVE_TRADING_ENABLED 강제
    env_flag_name: str = "LIVE_TRADING_ENABLED"
    # ── BAR-OPS-35 (2026-06-08 매매복기 권고) — 전부 default OFF(no-op). shadow→enforce ──
    # [P0#2] 일일 손실 한도 latch — True 면 한도 최초 도달 시 당일 sticky lock(평가손익이 -3%
    #   위로 회복돼도 신규매수 재개 금지). 현행 stateless 게이트는 12:30/12:35 차단 후 12:55
    #   회복으로 통과시켜 459550 2차(-509K) 재진입을 허용했음. latch 가 이를 원천 차단.
    daily_loss_latch: bool = False
    # [BAR-OPS-38 P0#1] latch 영속 경로 — 데몬은 사이클마다 게이트를 재생성하므로 인스턴스
    #   메모리 latch 가 사이클 간 증발한다(6/10 발견). 경로 지정 시 daily_gate_state.json 에
    #   당일 latch 를 영속해 모든 게이트 인스턴스(데몬 스캔/슈퍼트렌드)가 공유한다. None=기존.
    latch_state_path: Optional[str] = None
    # [BAR-OPS-38 P0#1] 차단 사유에 입력 지표 산식 명시 — 6/10 "-5.76%" 가 일일손실이 아닌
    #   누적 평가수익률이라 사후 분석을 오도(인시던트 5). 빈 문자열이면 기존 문구 유지.
    loss_metric_label: str = ""
    # [P0#5] 주문 실행 재시도 — transient(HTTP 5xx/timeout) 오류 시 재시도 횟수. 0=비활성(기존).
    #   2026-06-08 매도 HTTPStatusError 가 청산을 5분 지연→저점매도(-509K 악화). 매도 우선 재시도.
    order_retry_count: int = 0
    order_retry_backoff_sec: float = 0.0     # 재시도 간 백오프(초) — i회차에 ×(i+1) 선형 증가
    retry_sell_only: bool = True             # True 면 매도(청산)만 재시도(시간민감 우선)


class LiveOrderGate:
    """주문 실행 전 안전 검증 + audit log."""

    def __init__(
        self,
        executor: KiwoomNativeOrderExecutor,
        audit_path: str | Path,
        policy: Optional[GatePolicy] = None,
        notifier: Optional[TelegramNotifier] = None,
    ) -> None:
        self._executor = executor
        self._audit_path = Path(audit_path)
        self._audit_path.parent.mkdir(parents=True, exist_ok=True)
        self._policy = policy or GatePolicy()
        self._notifier = notifier
        # [P0#2 BAR-OPS-35] 일일 손실 한도 latch 상태 — 한도 도달한 UTC 일자(YYYY-MM-DD). 당일 sticky.
        self._loss_latch_date: Optional[str] = None
        # [BAR-OPS-38] latch 파일 영속 — 게이트 인스턴스 간(데몬 사이클 재생성) latch 공유.
        self._latch_store = None
        if self._policy.daily_loss_latch and self._policy.latch_state_path:
            try:
                from backend.core.risk.daily_gate_input import DailyGateStateStore
                self._latch_store = DailyGateStateStore(self._policy.latch_state_path)
            except Exception as exc:  # noqa: BLE001 — 영속 실패 시 메모리 latch 로 동작
                logger.warning("latch state store 초기화 실패: %s", type(exc).__name__)

    def _preflight(self, side: OrderSide, daily_pnl_pct: Decimal) -> None:
        # 1) ENV flag 강제 (실전 host 의 안전망)
        if self._policy.require_env_flag and not self._executor._dry_run:
            flag = os.environ.get(self._policy.env_flag_name, "").lower()
            if flag not in {"1", "true", "yes", "on"}:
                raise TradingDisabled(
                    f"{self._policy.env_flag_name}=truthy 필요 (현재: {flag!r}). "
                    f"DRY_RUN 모드는 ok. 실전 진입 시 명시적 활성화 필수."
                )

        # 2) 일일 손실 한도 — 매수만 차단 (매도는 손절 가능해야)
        if side == OrderSide.BUY:
            limit = self._policy.daily_loss_limit_pct
            # [BAR-OPS-38] 차단 사유에 입력 지표 산식 명시(사후 분석 오도 방지).
            _label = f"({self._policy.loss_metric_label})" if self._policy.loss_metric_label else ""
            if self._policy.daily_loss_latch:
                # [P0#2 BAR-OPS-35] sticky latch — 한번 도달하면 당일 회복해도 잠금 유지.
                # [BAR-OPS-38] 파일 영속 latch 우선 확인 — 다른 게이트 인스턴스가 건 latch 공유.
                today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
                if self._loss_latch_date == today or (
                        self._latch_store is not None and self._latch_store.is_latched()):
                    raise DailyLossLimitExceeded(
                        f"일일 손실 한도 latch — 당일({today}) 신규 매수 잠금(평가 회복 무관)."
                    )
                if daily_pnl_pct <= limit:
                    self._loss_latch_date = today
                    reason = (f"일일 손실 한도 도달(latch 설정){_label}: "
                              f"{daily_pnl_pct}% ≤ {limit}%. 당일 신규 매수 잠금.")
                    if self._latch_store is not None:
                        try:
                            self._latch_store.set_latched(reason)
                        except Exception as exc:  # noqa: BLE001
                            logger.warning("latch 영속 실패: %s", type(exc).__name__)
                    raise DailyLossLimitExceeded(reason)
            elif daily_pnl_pct <= limit:
                raise DailyLossLimitExceeded(
                    f"일일 손실 한도 도달{_label}: {daily_pnl_pct}% ≤ {limit}%. 신규 매수 차단."
                )

        # 3) 일일 매수 한도 — 매수만 차단 (매도는 손절 가능해야).
        # 2026-05-19 P3 fix: 기존 _count_today_orders 가 매수+매도 합계라
        # 분할매도 폭주 시(5/18 100790 9번 등) 매수가 조기 차단됨.
        # 매수만 카운트로 의미 일치 (5/18 매수 15 + 매도 35 = 50 → 매수만 15 → 통과).
        if side == OrderSide.BUY and self._policy.daily_max_orders > 0:
            today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
            count = self._count_today_buys(today)
            if count >= self._policy.daily_max_orders:
                raise DailyOrderLimitExceeded(
                    f"일일 매수 한도 초과: {count} ≥ {self._policy.daily_max_orders}"
                )

    async def place_buy(
        self, symbol: str, qty: int,
        price: Optional[Decimal] = None,
        daily_pnl_pct: Decimal = Decimal("0.0"),
        strategy_id: Optional[str] = None,
        filled_qty: Optional[int] = None,
        avg_fill_price: Optional[Decimal] = None,
    ) -> OrderResult:
        return await self._gated(OrderSide.BUY, symbol, qty, price, daily_pnl_pct,
                                 strategy_id, filled_qty, avg_fill_price)

    async def place_sell(
        self, symbol: str, qty: int,
        price: Optional[Decimal] = None,
        daily_pnl_pct: Decimal = Decimal("0.0"),
        strategy_id: Optional[str] = None,
        filled_qty: Optional[int] = None,
        avg_fill_price: Optional[Decimal] = None,
    ) -> OrderResult:
        return await self._gated(OrderSide.SELL, symbol, qty, price, daily_pnl_pct,
                                 strategy_id, filled_qty, avg_fill_price)

    async def _place_with_retry(
        self, side: OrderSide, symbol: str, qty: int, price: Optional[Decimal],
    ) -> OrderResult:
        """[P0#5 BAR-OPS-35] transient 오류 시 재시도. order_retry_count=0 이면 1회 시도(기존).

        retry_sell_only=True 면 매도(청산)만 재시도 — 손절 청산은 시간민감이라 우선 보호.
        """
        p = self._policy
        attempts = 1
        if p.order_retry_count > 0 and (side == OrderSide.SELL or not p.retry_sell_only):
            attempts = 1 + p.order_retry_count
        last_exc: Optional[Exception] = None
        for i in range(attempts):
            try:
                if side == OrderSide.BUY:
                    return await self._executor.place_buy(symbol, qty, price)
                return await self._executor.place_sell(symbol, qty, price)
            except Exception as exc:  # noqa: BLE001 — transient 재시도 목적
                last_exc = exc
                if i < attempts - 1:
                    logger.warning("주문 재시도 %d/%d (%s %s): %s",
                                   i + 1, attempts - 1, side.value, symbol, type(exc).__name__)
                    if p.order_retry_backoff_sec > 0:
                        await asyncio.sleep(p.order_retry_backoff_sec * (i + 1))
        assert last_exc is not None
        raise last_exc

    async def _gated(
        self, side: OrderSide, symbol: str, qty: int,
        price: Optional[Decimal], daily_pnl_pct: Decimal,
        strategy_id: Optional[str] = None,
        filled_qty: Optional[int] = None,
        avg_fill_price: Optional[Decimal] = None,
    ) -> OrderResult:
        # 0) qty 검증 — 호출자(DCA 분할 등) 에서 0/음수가 들어와도 executor 진입 전 차단.
        #    이전엔 executor 측 ValueError 가 audit 에 'FAILED' 로 남아 추적 어려웠음.
        if qty <= 0:
            err = InvalidOrderQty(f"qty must be > 0, got {qty}")
            self._audit("BLOCKED", side, symbol, qty, price, None, None,
                        blocked=True, reason=str(err), strategy_id=strategy_id)
            await self._notify_blocked(side, symbol, str(err))
            raise err

        try:
            self._preflight(side, daily_pnl_pct)
        except (TradingDisabled, DailyLossLimitExceeded, DailyOrderLimitExceeded) as e:
            self._audit("BLOCKED", side, symbol, qty, price, None, None,
                        blocked=True, reason=str(e), strategy_id=strategy_id)
            await self._notify_blocked(side, symbol, str(e))
            raise

        try:
            result = await self._place_with_retry(side, symbol, qty, price)
        except Exception as e:
            self._audit("FAILED", side, symbol, qty, price, None, None,
                        blocked=False, reason=type(e).__name__, strategy_id=strategy_id)
            await self._notify_blocked(side, symbol, f"{type(e).__name__}: {e}")
            raise

        self._audit(
            "ORDERED" if not result.dry_run else "DRY_RUN",
            side, symbol, qty, price,
            result.order_no, result.return_code, blocked=False,
            strategy_id=strategy_id,
            filled_qty=filled_qty, avg_fill_price=avg_fill_price,
        )
        return result

    def _audit(
        self, action: str, side: OrderSide, symbol: str, qty: int,
        price: Optional[Decimal], order_no: Optional[str],
        return_code: Optional[int], blocked: bool, reason: str = "",
        strategy_id: Optional[str] = None,
        filled_qty: Optional[int] = None,
        avg_fill_price: Optional[Decimal] = None,
    ) -> None:
        try:
            new_file = not self._audit_path.exists()
            # Phase D2.6: 기존 10 컬럼 파일이 있으면 11 컬럼으로 자동 migration (한 번만 실행).
            if not new_file and not getattr(self, "_migrated", False):
                try:
                    _migrate_audit_csv_header(self._audit_path)
                except Exception as me:
                    logger.warning("audit csv migration 실패: %s", type(me).__name__)
                self._migrated = True

            with open(self._audit_path, "a", encoding="utf-8", newline="") as f:
                w = csv.writer(f)
                if new_file:
                    w.writerow(_AUDIT_HEADERS)
                w.writerow([
                    datetime.now(timezone.utc).isoformat(timespec="seconds"),
                    action, side.value, symbol, qty,
                    str(price) if price is not None else "MKT",
                    order_no or "",
                    str(return_code) if return_code is not None else "",
                    "1" if blocked else "0",
                    reason,
                    strategy_id or "",  # Phase D2.6 신규 컬럼
                    str(filled_qty) if filled_qty is not None else "",       # BAR-OPS-35
                    str(avg_fill_price) if avg_fill_price is not None else "",  # BAR-OPS-35
                ])
        except OSError as exc:
            logger.error("audit log write failed (%s) — %s %s not recorded",
                         type(exc).__name__, action, symbol)

    async def _notify_blocked(self, side: OrderSide, symbol: str, reason: str) -> None:
        if not self._notifier:
            return
        try:
            await self._notifier.send(format_blocked_alert(side.value, symbol, reason))
        except Exception as e:
            logger.warning("blocked alert send failed: %s", type(e).__name__)

    def _count_today_orders(self, today: str) -> int:
        """전체 거래수 (매수+매도) — 통계용. 한도 적용은 _count_today_buys 사용."""
        if not self._audit_path.exists():
            return 0
        n = 0
        with open(self._audit_path, "r", encoding="utf-8", newline="") as f:
            r = csv.DictReader(f)
            for row in r:
                if (row.get("ts", "").startswith(today)
                        and row.get("action") in {"ORDERED", "DRY_RUN"}):
                    n += 1
        return n

    def _count_today_buys(self, today: str) -> int:
        """매수만 카운트 — 일일 매수 한도 평가용 (2026-05-19 P3 fix)."""
        if not self._audit_path.exists():
            return 0
        n = 0
        with open(self._audit_path, "r", encoding="utf-8", newline="") as f:
            r = csv.DictReader(f)
            for row in r:
                if (row.get("ts", "").startswith(today)
                        and row.get("action") in {"ORDERED", "DRY_RUN"}
                        and row.get("side") == "buy"):
                    n += 1
        return n


__all__ = [
    "LiveOrderGate", "GatePolicy",
    "TradingDisabled", "DailyLossLimitExceeded", "DailyOrderLimitExceeded",
]
