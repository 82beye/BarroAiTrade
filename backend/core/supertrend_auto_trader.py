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
    compute_adx,
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
    # 최소 진입가격 — 저가주/동전주 제외. 2026-06-02: 252670(종가 79원)이 슬롯금액÷주가로
    #   qty=38,219주(현금전액급) 폭주 진입 시도 → 동전주는 수량이 폭주하고 변동성·체결
    #   리스크가 커 진입 후보에서 제외. price < min_price 면 candidates 에 안 담는다.
    min_price: Decimal = Decimal("1000")
    # 자금배분 (evaluate_risk_gate 기본과 동일: 80%÷10 = 8%/종목)
    max_total_position_ratio: Decimal = Decimal("0.80")
    max_per_position_ratio: Decimal = Decimal("0.10")
    params: SupertrendParams = field(default_factory=SupertrendParams)
    # 장시간 가드 — True 면 정규장(REGULAR, 09:00~15:20 KST, 주말·휴장일 제외)
    #   에서만 매매 사이클 실행. 그 외 시간엔 사이클 skip(주문/시세호출 안 함).
    #   시장가 자동주문 전략이므로 시간외(지정가만 가능) 세션은 진입 자체를 막는다.
    market_hours_only: bool = True

    # ── 6/2 복기 개선 3건 (2026-06-03, recon_2026-06-02) ──────────────────────
    # [개선1] 장초반 진입 차단 — 개장 직후 변동성 구간(09:00~entry_start_time)은
    #   ATR 밴드 불안정·거짓 전환 빈발(6/2 466100 -7.18% 등). 이 시각 이전엔 진입 보류.
    #   KST HH:MM. None 이면 비활성. 청산은 시각 무관(보유 리스크 관리 우선).
    entry_start_time: str = "09:30"

    # [개선2] whipsaw 필터 — 진입 시 ADX(추세강도) + FLIP(전환 이탈폭) 게이트 적용.
    #   백테스트상 ADX≥25/FLIP≥1.0 이 PF 1.76→2.00, 승률 35→44% 로 개선.
    #   6/2 자동매매는 무필터(기본 0)라 거짓 전환을 다 매매 → 적자. 운영 기본 ON.
    min_adx: float = 25.0          # ADX(14) < 이 값이면 진입 거부 (0=비활성)
    adx_period: int = 14
    min_flip_atr_mult: float = 1.0  # 전환봉이 직전 dn밴드(저항)를 ATR×이배 이상 돌파해야 진입 (0=비활성)

    # [개선3] 주문 수량 하드캡 — 저가 종목 사이징 폭주 방지(6/2 252670 38,219주 RuntimeError).
    #   단일주문 절대 상한: 수량·금액 둘 중 작은 쪽으로 클램프. 0 이면 해당 캡 비활성.
    max_order_qty: int = 5000          # 단일주문 최대 수량
    max_order_value: float = 5_000_000.0  # 단일주문 최대 금액(원) — qty×price 상한

    # ── 멀티 타임프레임 RSI 확인 필터 (BAR-OPS-10, 2026-06-03) ────────────────
    # 상위 타임프레임(예 10분) RSI 골든크로스로 5분봉 진입을 확인, 데드크로스로 조기청산.
    # HTF RSI 는 이미 가져온 5분봉 bars 에서 **리샘플**로 도출(추가 fetch_minute 없음 —
    # 80종목×5분주기 API 부하 2배 회피). 값은 self.config 에서 직접 읽음(ADX/FLIP 선례 동일).
    #
    # 설계(사용자 의도): 슈퍼트렌드 신호가 '기준', RSI 골든/데드크로스(signal_cross=교합)가
    #   '확인(AND)'. 진입=ST BUY + 최근 RSI 골든크로스, 청산=ST SELL + 최근 RSI 데드크로스.
    #   RSI 단독 진입/청산 없음. config-gated OFF — 운영 머신에서 opt-in 시 작동.
    rsi_enabled: bool = False           # 마스터 스위치 (False → 전부 no-op)
    rsi_timeframe_mult: int = 2         # 5분봉 기준 배수 (1=5m, 2=10m, 3=15m, 6=30m)
    rsi_period: int = 14
    rsi_signal_period: int = 9
    rsi_mode: str = "signal_cross"      # signal_cross(교합·기본) | centerline | level
    rsi_cross_lookback: int = 3         # ST 신호 기준 최근 N HTF봉 내 RSI 크로스 확인창
    rsi_min_level: float = 50.0
    rsi_max_level: float = 100.0
    rsi_exit_enabled: bool = False      # 청산 시 RSI 데드크로스 '확인'(AND) 요구 (단독 청산 아님)

    # ── 손익 귀속 분석(2026-06-08, recon_2026-06_손실원인) 개선 3건 ──────────────
    # 패배거래 평균 MFE +2.5~3.9%(수익 거쳤다 손실 청산)·청산후 +2.8% 반등(저점매도) →
    # 손실 1차 원인이 '청산 지연'으로 규명. 진입 불변·청산만 개선 시 -3.55%→-1.16%.
    # 5~6월(22거래일) 백테스트 검증: 현행 -3.27% → 트레일3.0+익절5%+고점위치≤90% 진입 +2.73%.
    #
    # [청산1] ATR 트레일링 청산(샹들리에) — 진입 후 고점종가 − trail_atr_mult×ATR 를 종가가
    #   이탈하면 신호 무관 청산(가격기반 리스크 스톱, 신호와 OR). 0=비활성.
    #   2026-06-08: 0.0→3.0 (트레일 단독으로 22일 -3.27%→+0.93% 흑자전환, 수익 보호).
    trail_atr_mult: float = 3.0

    # [청산2] 익절 — 진입가 대비 +take_profit_pct% 도달 시 전량청산. 0=비활성.
    #   수익 반납 방지(SELL전환 지각 청산 보완). 2026-06-08 백테스트로 5.0 채택.
    take_profit_pct: float = 5.0

    # [진입] 고점/상단권 진입 억제 — 일중 고점대비 위치·당일 상승률 게이트.
    #   090360(진입위치 92%, -10%) 등 모멘텀 소진 고점 매수 차단.
    #   max_intraday_range_pos: 진입봉 종가가 당일 high-low 중 이 비율 초과 위치면 거부.
    #     0=비활성. 2026-06-08: 0.90 (top 10% 위치 차단 — 22일 +0.60%→+2.73% 최적).
    #   max_day_change_pct: 당일 전일종가 대비 상승률이 이 값 초과면 거부.
    #     0=비활성. 백테스트상 과필터로 악화 → 기본 OFF, 운영 머신 opt-in.
    max_intraday_range_pos: float = 0.90
    max_day_change_pct: float = 0.0


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
                # ── 가격기반 리스크 청산 — 신호와 OR(최우선). 진입 불변·청산개선 효과 ──
                #   [청산1] 트레일(샹들리에): 진입후 고점종가 − k×ATR 이탈 시 청산.
                #   [청산2] 익절: 진입가 대비 +take_profit_pct% 도달 시 청산(수익 반납 방지).
                trailed = self._trail_hit(pos, bars, res)
                tp_hit = self._take_profit_hit(pos, bars) if not trailed else False
                if not (trailed or tp_hit):
                    # 청산 = 슈퍼트렌드 SELL(기준·필수). RSI 단독 청산 없음.
                    st_exit = bool(res.sell_signals) and any(res.sell_signals[-lb:])
                    if not st_exit:
                        continue
                    # rsi_exit_enabled 면 ST SELL 을 최근 RSI 데드크로스가 '확인'(AND)해야 청산.
                    if self.config.rsi_exit_enabled:
                        from backend.core.strategy.indicators import htf_rsi_confirms_exit
                        if not htf_rsi_confirms_exit(
                            bars, i=len(bars) - 1, tf_mult=self.config.rsi_timeframe_mult,
                            period=self.config.rsi_period,
                            signal_period=self.config.rsi_signal_period,
                            mode=self.config.rsi_mode, lookback=self.config.rsi_cross_lookback,
                            min_level=self.config.rsi_min_level,
                            max_level=self.config.rsi_max_level,
                        ):
                            logger.debug("%s: ST SELL 발생했으나 RSI 데드크로스 미확인 — 청산 보류", symbol)
                            continue
                qty = int(getattr(pos, "total_recommended_qty", 0)) or self._filled_qty(pos)
                if qty <= 0:
                    continue
                r = await self._gate.place_sell(
                    symbol=symbol, qty=qty,
                    daily_pnl_pct=daily_pnl_pct, strategy_id=_STRATEGY_ID,
                )
                self._pos.remove(symbol)
                _xreason = "트레일청산" if trailed else ("익절" if tp_hit else "SELL 전환")
                result["exited"].append({"symbol": symbol, "qty": qty, "reason": _xreason,
                                         "order_no": getattr(r, "order_no", ""),
                                         "dry_run": getattr(r, "dry_run", False)})
                logger.warning("슈퍼트렌드 자동청산: %s qty=%d (%s)", symbol, qty, _xreason)
                await self._notify("슈퍼트렌드 자동매도", f"{symbol} {qty}주 청산 ({_xreason})")
            except Exception as e:
                logger.error("슈퍼트렌드 청산 실패: %s — %s", symbol, type(e).__name__)

        # ── [개선1] 장초반 진입 차단 — 개장 직후 변동성 구간 진입 보류 ──────
        # 청산은 위에서 이미 수행(보유 리스크 관리 우선), 진입만 차단한다.
        if not self._entry_time_open():
            logger.debug("슈퍼트렌드 진입 보류 — 장초반(< %s) 변동성 구간",
                         self.config.entry_start_time)
            return result

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
                # ── [개선2] whipsaw 필터 — ADX 추세강도 + FLIP 전환 이탈폭 ──
                if not self._whipsaw_pass(bars, res, symbol):
                    continue
                price = Decimal(str(float(bars[-1].close)))
                if price <= 0:
                    continue
                # 저가주/동전주 제외 — min_price 미만은 수량 폭주·체결 리스크로 진입 차단.
                if price < self.config.min_price:
                    logger.debug("슈퍼트렌드 진입 제외(저가주): %s @%s < min_price %s",
                                 symbol, price, self.config.min_price)
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
            # ── [개선3] 주문 수량 하드캡 — 저가종목 사이징 폭주 방지 ──────────
            qty = self._cap_qty(rec.recommended_qty, float(rec.cur_price), rec.symbol)
            if qty <= 0:
                continue
            try:
                r = await self._gate.place_buy(
                    symbol=rec.symbol, qty=qty,
                    daily_pnl_pct=daily_pnl_pct, strategy_id=_STRATEGY_ID,
                )
                self._pos.create_from_order(
                    symbol=rec.symbol, name=rec.name, strategy=_STRATEGY_ID,
                    entry_price=float(rec.cur_price),
                    total_recommended_qty=qty,
                    order_no=getattr(r, "order_no", ""),
                )
                placed += 1
                result["entered"].append({"symbol": rec.symbol, "qty": rec.recommended_qty,
                                          "price": float(rec.cur_price),
                                          "order_no": getattr(r, "order_no", ""),
                                          "dry_run": getattr(r, "dry_run", False)})
                logger.info("슈퍼트렌드 자동진입: %s qty=%d @%.0f",
                            rec.symbol, qty, float(rec.cur_price))
                await self._notify("슈퍼트렌드 자동매수",
                                   f"{rec.symbol} {rec.name} {qty}주 @{int(rec.cur_price):,}")
            except Exception as e:
                logger.error("슈퍼트렌드 진입 주문 실패: %s — %s", rec.symbol, type(e).__name__)

        return result

    # ── 6/2 복기 개선 헬퍼 ────────────────────────────────────────────────────
    def _entry_time_open(self) -> bool:
        """[개선1] 현재 KST 시각이 entry_start_time 이후면 True(진입 허용).

        entry_start_time None/빈값이면 항상 허용. 세션 판단기(KST) 기준 시각 사용.
        """
        cutoff = (self.config.entry_start_time or "").strip()
        if not cutoff:
            return True
        try:
            from datetime import datetime, timezone, timedelta
            now = datetime.now(timezone(timedelta(hours=9))).time()
            hh, mm = cutoff.split(":")
            from datetime import time as dtime
            return now >= dtime(int(hh), int(mm))
        except Exception:
            return True  # 파싱 실패 시 보수적으로 허용(기존 동작)

    def _whipsaw_pass(self, bars, res, symbol: str) -> bool:
        """[개선2/BAR-OPS-10] ADX + FLIP + 멀티 TF RSI 게이트. 모두 통과 시 True.

        - ADX(adx_period) < min_adx → 횡보로 보고 거부.
        - 최근 BUY 전환 봉이 '방금 돌파한 저항(직전 dn밴드)'을 ATR×min_flip_atr_mult
          이상 넘지 못하면 약한 전환(휩쏘)으로 거부. (supertrend.py 정정 정의와 동일)
        - rsi_enabled 면 상위 TF RSI 골든크로스/레짐 미확정 시 거부(bars 리샘플, 추가 fetch X).
        각 게이트는 0/False 면 비활성(기존 무필터 동작).
        """
        c = self.config
        # (1) ADX
        if c.min_adx > 0:
            adx = compute_adx(bars, period=c.adx_period)
            adx_now = adx[-1] if adx else 0.0
            if adx_now < c.min_adx:
                logger.debug("%s: ADX %.1f < %.1f — 진입거부(횡보)", symbol, adx_now, c.min_adx)
                return False
        # (2) FLIP — 최근 BUY 전환봉의 저항(직전 dn밴드) 대비 이탈폭
        if c.min_flip_atr_mult > 0:
            if True in res.buy_signals:
                flip_bar = len(res.buy_signals) - 1 - res.buy_signals[::-1].index(True)
                atr_ref = res.atr[flip_bar]
                resist = res.dn[flip_bar - 1] if flip_bar >= 1 else res.supertrend[flip_bar]
                breakout = float(bars[flip_bar].close) - resist
            else:
                atr_ref = res.atr[-1]
                breakout = float(bars[-1].close) - res.supertrend[-1]
            if atr_ref <= 0 or breakout < c.min_flip_atr_mult * atr_ref:
                logger.debug("%s: 전환이탈폭 %.1f < %.2f·ATR — 진입거부(약한전환)",
                             symbol, breakout, c.min_flip_atr_mult)
                return False
        # (3) 멀티 타임프레임 RSI 확인 — bars(5분봉) 리샘플로 HTF 도출(추가 fetch 없음).
        if c.rsi_enabled:
            from backend.core.strategy.indicators import htf_rsi_confirms_long
            if not htf_rsi_confirms_long(
                bars, i=len(bars) - 1, tf_mult=c.rsi_timeframe_mult,
                period=c.rsi_period, signal_period=c.rsi_signal_period,
                mode=c.rsi_mode, lookback=c.rsi_cross_lookback,
                min_level=c.rsi_min_level, max_level=c.rsi_max_level,
            ):
                logger.debug("%s: HTF(%d×5m) RSI 미확정 — 진입거부", symbol, c.rsi_timeframe_mult)
                return False
        # (4) 고점/상단권 진입 억제 — 모멘텀 소진 고점 매수 차단(2026-06-08 손익귀속).
        if c.max_intraday_range_pos > 0 or c.max_day_change_pct > 0:
            cur_date = bars[-1].timestamp.date()
            today = [b for b in bars if b.timestamp.date() == cur_date]
            if today:
                dh = max(float(b.high) for b in today)
                dl = min(float(b.low) for b in today)
                cur_px = float(bars[-1].close)
                if c.max_intraday_range_pos > 0 and dh > dl:
                    pos_in = (cur_px - dl) / (dh - dl)
                    if pos_in > c.max_intraday_range_pos:
                        logger.debug("%s: 일중위치 %.0f%% > %.0f%% — 진입거부(고점권)",
                                     symbol, pos_in * 100, c.max_intraday_range_pos * 100)
                        return False
                if c.max_day_change_pct > 0:
                    prior = [b for b in bars if b.timestamp.date() < cur_date]
                    if prior:
                        pc = float(prior[-1].close)
                        if pc > 0 and (cur_px - pc) / pc * 100.0 > c.max_day_change_pct:
                            logger.debug("%s: 당일상승 %.1f%% > %.1f%% — 진입거부(급등)",
                                         symbol, (cur_px - pc) / pc * 100, c.max_day_change_pct)
                            return False
        return True

    def _trail_hit(self, pos: Any, bars, res) -> bool:
        """ATR 트레일링 청산(샹들리에) 판정. 진입 후 고점종가 − trail_atr_mult×ATR 를
        현재 종가가 이탈하면 True. 비활성(0)/데이터부족 시 False.

        peak = 진입(entry_time) 이후 종가 최고. entry_time 비교 불가 시 가용 bars 전체로 폴백.
        가격기반 리스크 스톱이라 신호(SELL/RSI)와 무관하게 OR 로 작동.
        """
        k = self.config.trail_atr_mult
        if k <= 0 or not res.atr:
            return False
        entry_px = float(getattr(pos, "entry_price", 0) or 0)
        if entry_px <= 0:
            return False
        et = getattr(pos, "entry_time", None)
        closes = []
        for b in bars:
            try:
                if et is None or b.timestamp >= et:
                    closes.append(float(b.close))
            except TypeError:
                closes.append(float(b.close))   # tz mismatch 등 → 전체 폴백
        if not closes:
            closes = [float(b.close) for b in bars]
        peak = max(closes)
        atr_now = res.atr[-1]
        if atr_now <= 0:
            return False
        trail_stop = peak - k * atr_now
        return float(bars[-1].close) <= trail_stop

    def _take_profit_hit(self, pos: Any, bars) -> bool:
        """익절 판정 — 진입가 대비 현재 종가 수익률 ≥ take_profit_pct. 0=비활성."""
        tp = self.config.take_profit_pct
        if tp <= 0:
            return False
        entry_px = float(getattr(pos, "entry_price", 0) or 0)
        if entry_px <= 0 or not bars:
            return False
        cur = float(bars[-1].close)
        return (cur - entry_px) / entry_px * 100.0 >= tp

    def _cap_qty(self, qty: int, price: float, symbol: str) -> int:
        """[개선3] 단일주문 수량·금액 하드캡. 캡 초과 시 클램프 후 로그.

        저가 종목(예: 인버스 ETF)에서 '예수금÷저가=거대수량' 폭주를 방지
        (6/2 252670 38,219주 RuntimeError 인시던트). 0 캡은 비활성.
        """
        c = self.config
        capped = qty
        if c.max_order_qty > 0 and capped > c.max_order_qty:
            capped = c.max_order_qty
        if c.max_order_value > 0 and price > 0:
            qty_by_value = int(c.max_order_value // price)
            if capped > qty_by_value:
                capped = qty_by_value
        if capped < qty:
            logger.warning("슈퍼트렌드 수량 하드캡: %s %d→%d주 (@%.0f, 캡 qty=%d/value=%.0f)",
                           symbol, qty, capped, price, c.max_order_qty, c.max_order_value)
        return max(capped, 0)

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
