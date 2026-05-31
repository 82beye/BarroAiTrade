"""슈퍼트렌드 자동매매 루프 — signal → 자동 모의/실 주문 (2026-06-01, BAR-OPS-ST).

운영 봇(scripts/run_telegram_bot.py)과 동일한 실거래 인프라를 사용한다:
  - 시세:   KiwoomNativeCandleFetcher.fetch_minute(tic_scope="5")  (5분봉 실시세)
  - 자금:   KiwoomNativeAccountFetcher.fetch_deposit/fetch_balance/fetch_daily_pnl
  - 사이징: evaluate_risk_gate (예수금 80% ÷ 10종목 = 종목당 8% 균등배분)
  - 주문:   LiveOrderGate.place_buy/place_sell (안전 게이트 + audit + mockapi 모의체결)
  - 포지션: ActivePositionStore (strategy="supertrend" 로 식별, JSON 영속)

기존 텔레그램 수동 흐름(/sim_execute→/confirm)과 달리 **수동 확인 없이** 신호 발생 시
자동으로 주문을 송출한다. 진입/청산 양쪽. dry_run=True 면 LiveOrderGate 가 실제 송출
대신 DRY_RUN 결과를 반환하므로 안전 관찰 가능.

신호 정의 (backend.core.strategy.supertrend):
  - 진입: 5분봉 최근 entry_lookback 봉 내 BUY 전환(trend −1→+1) + 현재 상승추세
  - 청산: 보유 supertrend 포지션의 5분봉 최근 exit_lookback 봉 내 SELL 전환(trend +1→−1)

안전장치:
  - enabled=False 면 사이클 자체를 돌지 않음(즉시 OFF 스위치).
  - 슈퍼트렌드 동시 보유 max_positions(기본 10) 상한.
  - 미보유 종목만 진입(중복 진입 방지) + 진입 직후 ActivePositionStore 등록으로 재진입 차단.
  - 일일손실률을 LiveOrderGate 에 전달 → 한도 도달 시 신규 매수 자동 차단(매도는 허용).
"""
from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any, Awaitable, Callable, Optional, Sequence

from backend.core.risk.balance_gate import evaluate_risk_gate
from backend.core.market_session.service import MarketSessionService
from backend.core.strategy.supertrend import (
    SupertrendParams,
    compute_supertrend,
)
from backend.models.market import TradingSession

logger = logging.getLogger(__name__)

_STRATEGY_ID = "supertrend"   # ActivePosition.strategy / audit strategy_id


@dataclass
class SupertrendAutoConfig:
    enabled: bool = True               # False → 사이클 미실행 (즉시 OFF)
    interval_sec: int = 300            # 5분봉 → 5분 주기
    max_positions: int = 10            # 슈퍼트렌드 동시 보유 상한
    min_candles: int = 30              # 지표 안정화 최소 봉수
    tic_scope: str = "5"               # 5분봉
    universe_max: int = 80             # 진입 스캔 유니버스 상한
    # 자금배분 (evaluate_risk_gate 기본과 동일: 80%÷10 = 8%/종목)
    max_total_position_ratio: Decimal = Decimal("0.80")
    max_per_position_ratio: Decimal = Decimal("0.10")
    params: SupertrendParams = field(default_factory=SupertrendParams)
    # 장시간 가드 — True 면 정규장(REGULAR, 09:00~15:20 KST, 주말·휴장일 제외)
    #   에서만 매매 사이클 실행. 그 외 시간엔 사이클 skip(주문/시세호출 안 함).
    #   시장가 자동주문 전략이므로 시간외(지정가만 가능) 세션은 진입 자체를 막는다.
    market_hours_only: bool = True


class SupertrendAutoTrader:
    """슈퍼트렌드 5분봉 자동매매 (진입+청산).

    의존성 주입 — 모든 외부 협력자를 생성자에서 받아 네트워크 없이 테스트 가능.

    Args:
        candle_fetcher: .fetch_minute(symbol, tic_scope) -> list[OHLCV] (시간 오름차순 권장)
        account_fetcher: .fetch_deposit() / .fetch_balance() / .fetch_daily_pnl()
        order_gate:     LiveOrderGate (.place_buy/.place_sell(symbol, qty, daily_pnl_pct, strategy_id))
        pos_store:      ActivePositionStore (.load_all/.create_from_order/.remove/.get)
        universe_provider: async () -> list[(symbol, name)]  진입 스캔 후보
        notifier:       optional, .send(level, title, body) — 체결 알림
        config:         SupertrendAutoConfig
    """

    def __init__(
        self,
        candle_fetcher: Any,
        account_fetcher: Any,
        order_gate: Any,
        pos_store: Any,
        universe_provider: Callable[[], Awaitable[Sequence[tuple[str, str]]]],
        notifier: Optional[Any] = None,
        config: Optional[SupertrendAutoConfig] = None,
        session_service: Optional[Any] = None,
    ) -> None:
        self._candles = candle_fetcher
        self._account = account_fetcher
        self._gate = order_gate
        self._pos = pos_store
        self._universe_provider = universe_provider
        self._notifier = notifier
        self.config = config or SupertrendAutoConfig()
        # 장시간 판단기 (주입 가능 — 테스트는 가짜 주입). None 이면 기본 서비스.
        self._session = session_service or MarketSessionService()
        self._running = False

    # ── 라이프사이클 ──────────────────────────────────────────────────────────
    async def run_forever(self) -> None:
        """주기적으로 run_cycle 실행 (백그라운드 태스크). 예외는 사이클 단위로 격리."""
        self._running = True
        logger.info("SupertrendAutoTrader 시작 (interval=%ds, max_pos=%d, dry_run=%s)",
                    self.config.interval_sec, self.config.max_positions,
                    getattr(self._gate, "_executor", None) and
                    getattr(self._gate._executor, "_dry_run", "?"))
        while self._running:
            try:
                if self.config.enabled:
                    await self.run_cycle()
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error("supertrend auto cycle 오류: %s", e)
            await asyncio.sleep(self.config.interval_sec)

    def stop(self) -> None:
        self._running = False

    # ── 1 사이클 (청산 먼저, 그 다음 진입) ────────────────────────────────────
    async def run_cycle(self) -> dict:
        """청산 평가 → 진입 평가 1회. 반환: {entered:[...], exited:[...]} (관측/테스트용)."""
        result = {"entered": [], "exited": []}

        # ── 장시간 가드 — 정규장(REGULAR)에서만 매매 ────────────────────────
        # 시장가 자동주문이므로 장외/시간외(지정가만)·주말·휴장일엔 사이클 skip.
        # (시세·주문 호출 자체를 막아 장외 rc=20 거부·불필요 API 호출 방지.)
        if self.config.market_hours_only:
            session = self._session.get_session()
            if session != TradingSession.REGULAR:
                logger.debug("슈퍼트렌드 자동매매 skip — 비정규장 세션(%s)",
                             getattr(session, "value", session))
                return result

        # 손익률 (LiveOrderGate 매수 차단 게이트 입력) — 계좌 평가손익률을 사용.
        #   native fetch_daily_pnl 은 손익'액'만 제공(률 없음)이라, 잔고 조회의
        #   total_pnl_rate(계좌 전체 평가수익률)를 보수적 게이트 입력으로 사용한다.
        #   (정확한 '당일' 률은 아니나 손실 시 매수 차단이라는 안전 방향엔 부합.)
        daily_pnl_pct = await self._account_pnl_pct()

        # 보유 포지션 로드 (strategy=supertrend 만 대상)
        held = self._pos.load_all()  # dict[symbol, ActivePosition]
        st_held = {s: p for s, p in held.items()
                   if (getattr(p, "strategy", "") or "").startswith(_STRATEGY_ID)}

        # ── 청산: 보유 supertrend 종목의 SELL 전환 ───────────────────────────
        for symbol, pos in st_held.items():
            try:
                bars = await self._fetch_bars(symbol)
                if bars is None:
                    continue
                res = compute_supertrend(
                    bars, period=self.config.params.atr_period,
                    multiplier=self.config.params.multiplier,
                    source=self.config.params.source,
                )
                lb = max(1, self.config.params.exit_lookback)
                if not res.sell_signals or not any(res.sell_signals[-lb:]):
                    continue
                qty = int(getattr(pos, "total_recommended_qty", 0)) or self._filled_qty(pos)
                if qty <= 0:
                    continue
                r = await self._gate.place_sell(
                    symbol=symbol, qty=qty,
                    daily_pnl_pct=daily_pnl_pct, strategy_id=_STRATEGY_ID,
                )
                self._pos.remove(symbol)
                result["exited"].append({"symbol": symbol, "qty": qty,
                                         "order_no": getattr(r, "order_no", ""),
                                         "dry_run": getattr(r, "dry_run", False)})
                logger.warning("슈퍼트렌드 자동청산: %s qty=%d (SELL 전환)", symbol, qty)
                await self._notify("슈퍼트렌드 자동매도", f"{symbol} {qty}주 청산 (SELL 시그널)")
            except Exception as e:
                logger.error("슈퍼트렌드 청산 실패: %s — %s", symbol, type(e).__name__)

        # ── 진입: 유니버스 BUY 전환 (미보유만, 상한 준수) ────────────────────
        # 청산 후 갱신된 보유 수 기준
        held_after = self._pos.load_all()
        st_count = sum(1 for p in held_after.values()
                       if (getattr(p, "strategy", "") or "").startswith(_STRATEGY_ID))
        slots = self.config.max_positions - st_count
        if slots <= 0:
            return result

        universe = await self._universe_provider()
        candidates: list[tuple[str, str, Decimal]] = []  # (symbol, name, price)
        for symbol, name in universe[: self.config.universe_max]:
            if symbol in held_after:
                continue  # 이미 보유(전략 무관) — 중복 진입 방지
            try:
                bars = await self._fetch_bars(symbol)
                if bars is None:
                    continue
                res = compute_supertrend(
                    bars, period=self.config.params.atr_period,
                    multiplier=self.config.params.multiplier,
                    source=self.config.params.source,
                )
                if not res.trend or res.trend[-1] != 1:
                    continue
                lbe = self.config.params.entry_lookback
                if lbe is not None:
                    n = max(1, lbe)
                    if not res.buy_signals or not any(res.buy_signals[-n:]):
                        continue
                price = Decimal(str(float(bars[-1].close)))
                if price <= 0:
                    continue
                candidates.append((symbol, name, price))
            except Exception as e:
                logger.warning("슈퍼트렌드 진입 분석 실패: %s — %s", symbol, type(e).__name__)
            if len(candidates) >= slots:
                break  # 슬롯만큼만 평가 (불필요한 시세 호출 절약)

        if not candidates:
            return result

        # 자금배분 사이징 (예수금 80% ÷ 10종목)
        deposit = await self._account.fetch_deposit()
        balance = await self._account.fetch_balance()
        gate = evaluate_risk_gate(
            deposit=deposit, balance=balance, candidates=candidates,
            max_per_position_ratio=self.config.max_per_position_ratio,
            max_total_position_ratio=self.config.max_total_position_ratio,
            max_concurrent_positions=self.config.max_positions,
        )

        placed = 0
        for rec in gate.recommendations:
            if placed >= slots:
                break
            if rec.blocked or rec.recommended_qty <= 0:
                continue
            try:
                r = await self._gate.place_buy(
                    symbol=rec.symbol, qty=rec.recommended_qty,
                    daily_pnl_pct=daily_pnl_pct, strategy_id=_STRATEGY_ID,
                )
                self._pos.create_from_order(
                    symbol=rec.symbol, name=rec.name, strategy=_STRATEGY_ID,
                    entry_price=float(rec.cur_price),
                    total_recommended_qty=rec.recommended_qty,
                    order_no=getattr(r, "order_no", ""),
                )
                placed += 1
                result["entered"].append({"symbol": rec.symbol, "qty": rec.recommended_qty,
                                          "price": float(rec.cur_price),
                                          "order_no": getattr(r, "order_no", ""),
                                          "dry_run": getattr(r, "dry_run", False)})
                logger.info("슈퍼트렌드 자동진입: %s qty=%d @%.0f",
                            rec.symbol, rec.recommended_qty, float(rec.cur_price))
                await self._notify("슈퍼트렌드 자동매수",
                                   f"{rec.symbol} {rec.name} {rec.recommended_qty}주 @{int(rec.cur_price):,}")
            except Exception as e:
                logger.error("슈퍼트렌드 진입 주문 실패: %s — %s", rec.symbol, type(e).__name__)

        return result

    # ── 내부 헬퍼 ─────────────────────────────────────────────────────────────
    async def _fetch_bars(self, symbol: str):
        """5분봉 조회 → 시간 오름차순 정렬. min_candles 미만이면 None."""
        bars = await self._candles.fetch_minute(symbol, tic_scope=self.config.tic_scope)
        if not bars:
            return None
        bars = sorted(bars, key=lambda b: b.timestamp)
        if len(bars) < self.config.min_candles:
            return None
        return bars

    async def _account_pnl_pct(self) -> Decimal:
        """계좌 평가수익률(%) — LiveOrderGate 매수 차단 입력. 조회 실패 시 0(매수 허용)."""
        try:
            balance = await self._account.fetch_balance()
            return Decimal(str(getattr(balance, "total_pnl_rate", 0) or 0))
        except Exception:
            return Decimal("0.0")

    @staticmethod
    def _filled_qty(pos: Any) -> int:
        fn = getattr(pos, "filled_qty", None)
        if callable(fn):
            try:
                return int(fn())
            except Exception:
                return 0
        return 0

    async def _notify(self, title: str, body: str) -> None:
        if not self._notifier:
            return
        try:
            send = getattr(self._notifier, "send", None)
            if send:
                from backend.core.monitoring.alert_service import AlertLevel  # local
                await send(AlertLevel.INFO, title, body)
        except Exception:
            pass


__all__ = ["SupertrendAutoTrader", "SupertrendAutoConfig"]
