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
    # 주의(BAR-OPS-33): max_positions 하향은 balance_gate 사이징(80%/N)상 종목당 비중을
    #   오히려 키워(8%→10% 캡) 약전략 역효과 → 10 유지. supertrend 드래그 축소는 게이트
    #   강화(min_adx 30/min_flip 1.5, 거래125→30·MDD반감) + priority 최하위로 달성.
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
    # BAR-OPS-33 (2026-06-08): 25→30. 4~6월 제약 sweep — adx≥30·flip≥1.5 시 거래125→30,
    #   MDD-41→-22, 전체 기대값 -0.08→+0.20 (드래그·휩쏘 최소화). out-of-sample은 여전히
    #   음수라 약전략 — priority 최하위(SignalScanner STRATEGY_PRIORITY)와 병행해 비중 억제.
    min_adx: float = 30.0          # ADX(14) < 이 값이면 진입 거부 (0=비활성)
    adx_period: int = 14
    min_flip_atr_mult: float = 1.5  # 전환봉이 직전 dn밴드(저항)를 ATR×이배 이상 돌파해야 진입 (0=비활성)

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

    # ── BAR-OPS-35 (2026-06-08 매매복기 권고) — 전부 default OFF(no-op). shadow→enforce ──
    # 근거: reports/2026-06-08/2026-06-08_매매복기.md. 459550 2차 재진입 -509K(당일손실 91.7%),
    #   두산 supertrend 재진입 -319K. 진입알파는 흑자, 청산·재진입 규율이 손실을 만듦.
    #
    # [P0#3] catastrophic 하드 손절 — 진입가 대비 손실률 ≤ hard_stop_pct(음수) 시 신호 무관 즉시 청산.
    #   ATR 트레일(trail_atr_mult=3.0)은 고변동주에서 손절선이 진입가 -6~13%로 너무 멀어
    #   459550 2차 -12.63% 방치. 변동성 무관 절대 손절 상한(꼬리리스크 캡). 0=비활성. 권고 예: -6.0.
    hard_stop_pct: float = 0.0
    # [P0#1] 동일종목 당일 재진입 횟수 상한 — 459550 1차 익절 후 14:12 같은종목 고점 재진입(-509K) 차단.
    #   >0 이면 당일 그 종목 진입 횟수를 상한(1=재진입 전면 금지). 0=비활성.
    max_entries_per_symbol_day: int = 0
    # [P0#1] 동일종목 청산 후 재진입 cooldown(분) — 직전 '청산' 시각 기준 N분 이내 재진입 차단. 0=비활성.
    reentry_cooldown_min: int = 0
    # [P0#1] 당일 손절(실현손실) 종목 재진입 금지 — 손절난 종목 추격 차단. False=비활성.
    block_reentry_after_loss: bool = False
    # [P1] 고변동(테마) 종목 진입 억제 — ATR/price 비율이 임계 초과면 supertrend 진입 스킵.
    #   고변동일수록 ATR 밴드가 넓어 신호 지연(459550·두산 밴드폭 9~11%)→음의 기대값. 0=비활성. 예 0.05.
    max_atr_pct_for_entry: float = 0.0
    # [P1] 승자 보유 강화 — True 면 고정 익절(take_profit_pct) 비활성, ATR 트레일만으로 수익 동행.
    take_profit_trail_only: bool = False
    # [P1] 변동성 조정 사이징 — ATR/price > 이 값이면 진입 수량 절반(고변동주 비중 축소). 0=비활성. 예 0.05.
    vol_halve_atr_pct: float = 0.0
    # [P1] DCA tranche 미사용 — supertrend 는 전량 단일주문 진입인데 create_from_order 가 178/118 분할로
    #   모델링해 sync-loss(보유≠tracker) 유발. True 면 전량을 단일 filled tranche 로 기록(일치). False=기존.
    single_tranche: bool = False
    # [P2] 추격 매수 가드 — 진입봉 종가가 직전봉 종가 대비 +이값% 초과 급등이면 진입 스킵(고점추격 방지).
    #   0=비활성. 예 3.0.
    max_entry_gap_pct: float = 0.0

    # [BAR-OPS 2026-06-09] 시초가 갭 가드 — 현재가가 '전일 종가' 대비 +이값% 초과면 진입 스킵.
    #   max_entry_gap_pct(봉간)와 달리 전일종가 기준 오픈갭 → 시초가 급등주(089030류) 추격 차단. 0=비활성. 예 15.0.
    max_open_gap_pct: float = 0.0

    # [BAR-OPS 2026-06-09] 레버리지/인버스/ETN 진입 제외 — 변동성 증폭상품 추격 방지. False=비활성.
    exclude_leverage: bool = False

    # [BAR-OPS 2026-06-10] ETF/ETN/리츠 전면 진입 제외(개별주만 허용). False=비활성.
    exclude_etf: bool = False

    # ── BAR-OPS-36 (2026-06-09) Runner — 승자 보유 강화 → 최고점 청산 ─────────────
    # 근거: reports/2026-06-08. 459550 1차를 고점(2,385)까지 들었으면 +101K 추가. 고정 익절(+5%)이
    #   상한가·강한 추세의 초과 수익을 잘라먹음. 러너 모드는 익절가를 '즉시 매도'가 아니라 '최고점 추적
    #   전환' 트리거로 바꿔, 추세가 무너질 때만 청산한다. 전부 default OFF(no-op). shadow→enforce.
    #
    # [러너 마스터] True 면 아래 트리거(TP도달/상한가/시초갭) 충족 시 고정 익절 대신 최고점 추적 청산.
    runner_enabled: bool = False
    # [트리거2] 상한가 — 현재가 ≥ 전일종가×(1+이값/100) 이면 상한가로 보고 러너 + '상한가 잠김 홀딩'.
    #   KRX 일일 상한 ~+30% → 29%로 근접 포착. 0=상한가 트리거 비활성.
    runner_limit_up_pct: float = 29.0
    # [트리거3] 보유종목 시초 갭상승 — 당일 시가 ≥ 전일종가×(1+이값/100) 이면 러너 진입. 0=비활성. 예 5.0.
    runner_gap_up_pct: float = 0.0
    # [청산] 최고점 되돌림 — 진입 후 최고종가 대비 이 %만큼 되돌리면 추세이탈로 청산. 0이면 ATR 사용.
    runner_giveback_pct: float = 3.0
    runner_giveback_atr_mult: float = 0.0   # >0 이면 peak − mult×ATR 사용(giveback_pct 대신)
    # [안전] 수익잠금 floor — 러너 진입 후 현재가가 진입가×(1+이값/100) 밑이면 청산(승자→손실 방지).
    runner_profit_lock_pct: float = 2.0
    # [익일 시가 갭 부분익절] 보유종목이 익일 개장초 갭상승하면 일부 확정 후 잔량은 러너로 런.
    #   분석(2026-06-09, 상한가 익일 531건): 79% 갭상승·평균 +8.3%이나 47%가 장중 페이드 →
    #   시가 갭에서 일부 확정(+8.3% 신뢰구간) + 잔량 peak-trail(고가 평균 +18.6%)이 최적 +EV.
    runner_gap_partial_ratio: float = 0.0       # 익일 시가갭에서 매도할 비율(0.5=절반). 0=비활성
    runner_gap_partial_min_pct: float = 3.0     # 익일 시가갭(전일종가比)이 이 % 이상이어야 부분익절
    runner_gap_partial_window_bars: int = 6     # 개장 후 이 봉수(×5분) 이내만(갭은 개장 현상). 6=30분


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
        # ── BAR-OPS-35 재진입/손절 추적 (당일 단위, KST 일자 바뀌면 _roll_day 로 리셋) ──
        self._entry_day: str = ""
        self._entries_today: dict[str, int] = {}   # symbol -> 당일 진입 횟수
        self._last_exit: dict[str, datetime] = {}   # symbol -> 마지막 청산 시각(UTC)
        self._loss_locked: set[str] = set()         # 당일 손절(실현손실) 종목

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
        self._roll_day()  # BAR-OPS-35 — KST 일자 변경 시 당일 재진입/손절 추적 리셋

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
                # [BAR-OPS-36] 익일 시가 갭 부분익절 — 보유종목 개장초 갭상승 시 일부 확정,
                #   잔량은 러너로 런(고점 추종). 부분익절한 사이클은 잔량 청산평가 스킵(중복매도 방지).
                if await self._maybe_gap_partial(symbol, pos, bars, daily_pnl_pct, result):
                    continue
                lb = max(1, self.config.params.exit_lookback)
                # ── 가격기반 리스크 청산 — 신호와 OR(최우선). 진입 불변·청산개선 효과 ──
                #   [청산1] 트레일(샹들리에): 진입후 고점종가 − k×ATR 이탈 시 청산.
                #   [청산2] 익절: 진입가 대비 +take_profit_pct% 도달 시 청산(수익 반납 방지).
                trailed = self._trail_hit(pos, bars, res)
                # [P0#3] catastrophic 하드 손절 — 트레일보다 먼저 평가(OR, 신호 무관 즉시 청산).
                hard_hit = self._hard_stop_hit(pos, bars) if not trailed else False
                # [BAR-OPS-36] 러너 — 상한가/시초갭/TP도달 시 고정 익절 대신 최고점 추적.
                #   하드/트레일(외곽 안전망)이 먼저 잡으면 러너 평가 생략.
                runner_on = (self.config.runner_enabled and not (trailed or hard_hit)
                             and self._runner_triggered(pos, bars))
                runner_exit, runner_reason = (False, "")
                if runner_on:
                    runner_exit, runner_reason = self._runner_should_exit(pos, bars, res)
                # [P1] take_profit_trail_only 또는 러너 진행 중이면 고정 익절 비활성(러너가 대체).
                tp_hit = (self._take_profit_hit(pos, bars)
                          if not (trailed or hard_hit or runner_on
                                  or self.config.take_profit_trail_only)
                          else False)
                if not (trailed or hard_hit or tp_hit or runner_exit):
                    # 청산 = 슈퍼트렌드 SELL(기준·필수). RSI 단독 청산 없음.
                    #   러너 홀딩 중에도 ST SELL(추세 반전)은 추세이탈 청산으로 인정.
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
                # [P0#1] 재진입 가드용 청산 추적 — 청산 시각 + 손절(실현손실) 여부 기록.
                self._last_exit[symbol] = datetime.now(timezone.utc)
                _entry_px = float(getattr(pos, "entry_price", 0) or 0)
                if _entry_px > 0 and float(bars[-1].close) < _entry_px:
                    self._loss_locked.add(symbol)
                _xreason = ("트레일청산" if trailed else
                            ("하드손절" if hard_hit else
                             (runner_reason if runner_exit else
                              ("익절" if tp_hit else "SELL 전환"))))
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
        atr_pct_by_symbol: dict[str, float] = {}  # BAR-OPS-35 변동성 사이징용
        for symbol, name in universe[: self.config.universe_max]:
            if symbol in held_after:
                continue  # 이미 보유(전략 무관) — 중복 진입 방지
            # [P0#1] BAR-OPS-35 재진입 가드 — 당일 횟수상한/cooldown/손절후 차단 (전부 default OFF)
            _rb = self._reentry_blocked(symbol)
            if _rb:
                logger.debug("슈퍼트렌드 진입 제외(재진입가드): %s — %s", symbol, _rb)
                continue
            # [BAR-OPS] 레버리지/인버스/ETN 제외 (config-gated)
            if self.config.exclude_leverage and self._is_leverage_or_inverse(symbol, name):
                logger.debug("슈퍼트렌드 진입 제외(레버리지/ETN): %s %s", symbol, name)
                continue
            # [BAR-OPS 2026-06-10] ETF/ETN/리츠 전면 제외(개별주만 허용)
            if self.config.exclude_etf and self._is_etf_or_etn(symbol, name):
                logger.debug("슈퍼트렌드 진입 제외(ETF/ETN 전면): %s %s", symbol, name)
                continue
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
                # [P1] 고변동(테마) 필터 — ATR/price 과대 종목 진입 스킵(신호 지연으로 음의 기대값).
                atr_pct = self._atr_pct(res, float(price))
                if self.config.max_atr_pct_for_entry > 0 and atr_pct > self.config.max_atr_pct_for_entry:
                    logger.debug("슈퍼트렌드 진입 제외(고변동): %s ATR%%=%.3f > %.3f",
                                 symbol, atr_pct, self.config.max_atr_pct_for_entry)
                    continue
                # [P2] 추격 매수 가드 — 진입봉 종가가 직전봉 종가 대비 급등이면 스킵(고점추격 방지).
                if self.config.max_entry_gap_pct > 0 and len(bars) >= 2:
                    prev_c = float(bars[-2].close)
                    if prev_c > 0:
                        gap = (float(bars[-1].close) - prev_c) / prev_c * 100.0
                        if gap > self.config.max_entry_gap_pct:
                            logger.debug("슈퍼트렌드 진입 제외(추격): %s 갭=%.2f%% > %.2f%%",
                                         symbol, gap, self.config.max_entry_gap_pct)
                            continue
                # [BAR-OPS] 시초가 갭 가드 — 전일종가 대비 오픈갭 급등이면 스킵(시초가 급등주 추격 방지).
                if self.config.max_open_gap_pct > 0:
                    _pc = self._prev_close(bars)
                    if _pc and _pc > 0:
                        _og = (float(bars[-1].close) - _pc) / _pc * 100.0
                        if _og > self.config.max_open_gap_pct:
                            logger.debug("슈퍼트렌드 진입 제외(시초가갭): %s 전일종가比 %.1f%% > %.1f%%",
                                         symbol, _og, self.config.max_open_gap_pct)
                            continue
                atr_pct_by_symbol[symbol] = atr_pct
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
            # [P1] 변동성 조정 사이징 — 고변동 종목은 비중 절반(꼬리리스크 축소). 0=비활성.
            if self.config.vol_halve_atr_pct > 0:
                _ap = atr_pct_by_symbol.get(rec.symbol, 0.0)
                if _ap > self.config.vol_halve_atr_pct and qty > 1:
                    _halved = qty // 2
                    logger.info("슈퍼트렌드 변동성 사이징: %s %d→%d주 (ATR%%=%.3f > %.3f)",
                                rec.symbol, qty, _halved, _ap, self.config.vol_halve_atr_pct)
                    qty = _halved
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
                    single_tranche=self.config.single_tranche,  # [P1] sync-loss 방지
                )
                placed += 1
                # [P0#1] 당일 진입 횟수 기록 — 동일종목 재진입 상한 평가용.
                self._entries_today[rec.symbol] = self._entries_today.get(rec.symbol, 0) + 1
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

    # ── BAR-OPS-35 재진입/손절/변동성 가드 헬퍼 ───────────────────────────────
    @staticmethod
    def _kst_today() -> str:
        from datetime import datetime, timezone, timedelta
        return datetime.now(timezone(timedelta(hours=9))).strftime("%Y-%m-%d")

    def _roll_day(self) -> None:
        """KST 일자 변경 시 당일 재진입/손절 추적 리셋 (사이클 시작 시 호출)."""
        today = self._kst_today()
        if today != self._entry_day:
            self._entry_day = today
            self._entries_today = {}
            self._loss_locked = set()
            self._last_exit = {}

    def _reentry_blocked(self, symbol: str) -> Optional[str]:
        """[P0#1] 동일종목 재진입 차단 사유(없으면 None). 전부 default OFF."""
        c = self.config
        if c.max_entries_per_symbol_day > 0:
            n = self._entries_today.get(symbol, 0)
            if n >= c.max_entries_per_symbol_day:
                return f"당일 진입 {n}회 ≥ 상한 {c.max_entries_per_symbol_day}"
        if c.block_reentry_after_loss and symbol in self._loss_locked:
            return "당일 손절 종목 재진입 금지"
        if c.reentry_cooldown_min > 0 and symbol in self._last_exit:
            elapsed = (datetime.now(timezone.utc) - self._last_exit[symbol]).total_seconds() / 60.0
            if elapsed < c.reentry_cooldown_min:
                return f"청산 후 {elapsed:.0f}분 < cooldown {c.reentry_cooldown_min}분"
        return None

    def _hard_stop_hit(self, pos: Any, bars) -> bool:
        """[P0#3] 진입가 대비 손실률 ≤ hard_stop_pct(음수) 시 True (변동성 무관 절대 손절). 0=비활성."""
        hs = self.config.hard_stop_pct
        if hs >= 0 or not bars:
            return False
        entry_px = float(getattr(pos, "entry_price", 0) or 0)
        if entry_px <= 0:
            return False
        cur = float(bars[-1].close)
        return (cur - entry_px) / entry_px * 100.0 <= hs

    @staticmethod
    def _atr_pct(res, price: float) -> float:
        """ATR(최근)/price 비율. 변동성 게이트·사이징용. 산출 불가 시 0."""
        try:
            atr = res.atr[-1] if res.atr else 0.0
            return float(atr) / price if price > 0 else 0.0
        except Exception:
            return 0.0

    # ── BAR-OPS-36 Runner (승자 보유 → 최고점 청산) 헬퍼 ──────────────────────────
    @staticmethod
    def _closes_since_entry(pos: Any, bars) -> list[float]:
        """진입(entry_time) 이후 종가 리스트. entry_time 비교 불가 시 전체 폴백 (_trail_hit 와 동일 규약)."""
        et = getattr(pos, "entry_time", None)
        out = []
        for b in bars:
            try:
                if et is None or b.timestamp >= et:
                    out.append(float(b.close))
            except TypeError:
                out.append(float(b.close))
        return out or [float(b.close) for b in bars]

    @staticmethod
    def _prev_close(bars) -> Optional[float]:
        """bars(다일 5분봉) 중 '현재일 직전 거래일'의 마지막 종가 — 상한가/갭 판정 기준."""
        if not bars:
            return None
        cur_date = bars[-1].timestamp.date()
        prior = [b for b in bars if b.timestamp.date() < cur_date]
        return float(prior[-1].close) if prior else None

    @staticmethod
    def _today_open(bars) -> Optional[float]:
        """현재일 첫 봉 시가 — 시초 갭 판정 기준."""
        if not bars:
            return None
        cur_date = bars[-1].timestamp.date()
        today = [b for b in bars if b.timestamp.date() == cur_date]
        return float(today[0].open) if today else None

    @staticmethod
    def _is_leverage_or_inverse(symbol: str, name: str) -> bool:
        """레버리지/인버스 ETF 또는 ETN 판정 — 진입 제외용(이름 키워드 + ETN 코드 영문자).
        예: KODEX 레버리지(122630), KODEX 코스닥150레버리지(233740), 인버스2X, ETN(0193T0)."""
        nm = name or ""
        if any(k in nm for k in ("레버리지", "인버스", "곱버스", "2X", "2x")):
            return True
        if any(c.isalpha() for c in (symbol or "")):  # ETN: 코드에 영문자
            return True
        return False

    @staticmethod
    def _is_etf_or_etn(symbol: str, name: str) -> bool:
        """KRX ETF/ETN/리츠 등 펀드형 판정 — 개별주만 허용(ETF류 전면 차단)용.
        True=펀드형(ETF/ETN/레버리지/인버스/지수/섹터/채권/리츠/인프라) → 차단.
        False=개별 회사주(스팩/우선주 포함) → 허용. 이름/코드 기반·대소문자/공백 무시.
        BAR-OPS 2026-06-10: 적대적검증 반영(메리츠/HK이노엔/합성수지 오차단 제거, KoAct/WOORI/흥국/다올 보강)."""
        raw = name or ""
        up = raw.upper()
        up_ns = "".join(up.split())
        sym = (symbol or "").strip().upper()
        if "스팩" in raw or "기업인수목적" in raw:
            return False
        if (raw.endswith("우") or up.endswith("우B") or up.endswith("우C")
                or raw.endswith("우(전환)") or raw.endswith("(전환우)")):
            return False
        pref_code = (len(sym) == 6 and sym[:5].isdigit() and sym[5] in ("K", "L", "M"))
        _ETF_BRANDS = (
            "KODEX", "TIGER", "KBSTAR", "ARIRANG", "KOSEF", "HANARO", "KINDEX",
            "TIMEFOLIO", "KIWOOM", "TREX", "TRUSTON", "KCGI", "KOACT", "UNICORN",
            "WOORI", "FREEDOM", "VITA", "에셋플러스", "마이다스", "히어로즈",
            "ACE", "PLUS", "SOL", "RISE", "SMART", "FOCUS", "BNK", "WON",
            "1Q", "ITF", "마이티", "파워",
        )
        for b in _ETF_BRANDS:
            if up.startswith(b):
                rest = up[len(b):]
                if rest == "" or rest[0] == " " or rest[0].isdigit():
                    return True
        _FUND_TOKENS = (
            "ETN", "ETF", "레버리지", "인버스", "곱버스", "선물", "국고채",
            "통안채", "회사채", "물가채", "단기채", "종합채", "혼합채",
            "커버드콜", "양매도", "MSCI", "S&P", "나스닥", "코스피200", "코스닥150",
        )
        for t in _FUND_TOKENS:
            if t.replace(" ", "") in up_ns:
                return True
        if (raw.endswith("리츠") or "맥쿼리" in raw or "리얼티" in raw
                or "부동산투자회사" in raw or "REIT" in up):
            return True
        if (not pref_code) and any(c.isalpha() for c in sym):
            return True
        return False

    def _is_limit_up(self, bars) -> bool:
        """현재가가 전일종가 대비 runner_limit_up_pct% 이상 = 상한가권."""
        c = self.config
        if c.runner_limit_up_pct <= 0 or not bars:
            return False
        pc = self._prev_close(bars)
        if not pc or pc <= 0:
            return False
        return float(bars[-1].close) >= pc * (1 + c.runner_limit_up_pct / 100.0)

    def _runner_triggered(self, pos: Any, bars) -> bool:
        """러너 진입 트리거 — TP 도달 | 상한가 | 보유종목 시초 갭상승 중 하나."""
        c = self.config
        entry = float(getattr(pos, "entry_price", 0) or 0)
        if entry <= 0 or not bars:
            return False
        cur = float(bars[-1].close)
        # (1) TP 도달 — 익절가를 '즉시 매도'가 아닌 러너 전환점으로 사용
        if c.take_profit_pct > 0 and (cur - entry) / entry * 100.0 >= c.take_profit_pct:
            return True
        # (2) 상한가
        if self._is_limit_up(bars):
            return True
        # (3) 보유종목 시초 갭상승
        if c.runner_gap_up_pct > 0:
            pc = self._prev_close(bars)
            op = self._today_open(bars)
            if pc and op and pc > 0 and op >= pc * (1 + c.runner_gap_up_pct / 100.0):
                return True
        return False

    def _runner_should_exit(self, pos: Any, bars, res) -> tuple[bool, str]:
        """러너 모드 청산 판정 — (청산여부, 사유). '추세 무너지면만' 청산.

        우선순위: 상한가 잠김 홀딩 > 수익잠금 floor > 최고점 되돌림(추세이탈).
        """
        c = self.config
        entry = float(getattr(pos, "entry_price", 0) or 0)
        if entry <= 0 or not bars:
            return (False, "")
        cur = float(bars[-1].close)
        # 1) 상한가 잠김 — 상한가권이면 되돌림 무시하고 홀딩 (최고점에서 안 판다)
        if self._is_limit_up(bars):
            return (False, "상한가 홀딩")
        # 2) 수익잠금 floor — 러너 진입 후 진입가×(1+lock%) 밑으론 청산(승자→손실/본전 방지)
        floor = entry * (1 + c.runner_profit_lock_pct / 100.0)
        if c.runner_profit_lock_pct > 0 and cur <= floor:
            return (True, "러너 수익잠금")
        # 3) 최고점 되돌림 = 추세이탈
        peak = max(self._closes_since_entry(pos, bars))
        if c.runner_giveback_atr_mult > 0 and getattr(res, "atr", None):
            level = peak - c.runner_giveback_atr_mult * res.atr[-1]
        else:
            level = peak * (1 - c.runner_giveback_pct / 100.0)
        if cur <= level:
            return (True, "추세이탈(고점되돌림)")
        return (False, "러너 홀딩")

    async def _maybe_gap_partial(self, symbol: str, pos: Any, bars, daily_pnl_pct,
                                 result: dict) -> bool:
        """[BAR-OPS-36] 익일 시가 갭 부분익절 — 보유(오버나잇)종목이 개장초 갭상승하면 일부 확정.

        조건(전부 충족): runner_enabled + gap_partial_ratio>0, 부분익절 미완료(partial_tp_done),
        개장 후 window_bars 이내(오늘 봉수 ≤ window), 익일 시가갭 ≥ min_pct, 오버나잇 보유(진입일<오늘),
        현재가 > 진입가(이익). 성사 시 part 매도 + 잔량(total_recommended_qty) 갱신 + 마킹 후 True.
        """
        c = self.config
        if not (c.runner_enabled and c.runner_gap_partial_ratio > 0):
            return False
        if getattr(pos, "partial_tp_done", False):
            return False
        if not bars:
            return False
        cur_date = bars[-1].timestamp.date()
        today_bars = [b for b in bars if b.timestamp.date() == cur_date]
        if not today_bars or len(today_bars) > c.runner_gap_partial_window_bars:
            return False  # 개장 초반 윈도우 밖
        # 오버나잇 보유만 (당일 진입은 갭 대상 아님)
        et = getattr(pos, "entry_time", "") or ""
        if et:
            try:
                if datetime.fromisoformat(et).date() >= cur_date:
                    return False
            except ValueError:
                pass
        pc = self._prev_close(bars)
        op = self._today_open(bars)
        if not pc or not op or pc <= 0:
            return False
        gap = (op - pc) / pc
        if gap < c.runner_gap_partial_min_pct / 100.0:
            return False
        entry = float(getattr(pos, "entry_price", 0) or 0)
        cur = float(bars[-1].close)
        if entry <= 0 or cur <= entry:   # 이익일 때만 부분익절
            return False
        held = int(getattr(pos, "total_recommended_qty", 0)) or self._filled_qty(pos)
        part = int(held * c.runner_gap_partial_ratio)
        if part <= 0 or part >= held:
            return False
        r = await self._gate.place_sell(
            symbol=symbol, qty=part, daily_pnl_pct=daily_pnl_pct, strategy_id=_STRATEGY_ID,
        )
        # 잔량 갱신 + 부분익절 마킹 후 영속(remove 아님 — 잔량은 러너로 런)
        pos.total_recommended_qty = held - part
        pos.partial_tp_done = True
        try:
            self._pos.upsert(pos)
        except Exception as e:
            logger.error("부분익절 후 포지션 갱신 실패: %s — %s", symbol, type(e).__name__)
        result["exited"].append({"symbol": symbol, "qty": part, "reason": "익일갭 부분익절",
                                 "order_no": getattr(r, "order_no", ""),
                                 "dry_run": getattr(r, "dry_run", False), "partial": True})
        logger.info("익일 시가갭 부분익절: %s %d/%d주 (갭 %+.1f%%, 잔량 %d 러너)",
                    symbol, part, held, gap * 100, held - part)
        await self._notify("익일갭 부분익절", f"{symbol} {part}주 확정 (갭 {gap*100:+.1f}%, 잔량 런)")
        return True

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
        """계좌 대비 평가손익률(%) — LiveOrderGate 매수 차단 입력. 조회 실패 시 0(매수 허용).

        주의(BAR-OPS): balance.total_pnl_rate(키움 tot_prft_rt)는 '매입금액' 대비라
        소액·고변동 보유에서 과대(예: -299,968/7,749,640 = -3.87%)로 잡혀 일일손실
        게이트(-3.0)를 오발동시킨다. 게이트 의미(계좌 대비 일손실)에 맞게
        총평가손익 / 추정예탁자산(prsm_dpst_aset_amt) 으로 계산한다.
        """
        try:
            balance = await self._account.fetch_balance()
            total_pnl = Decimal(str(getattr(balance, "total_pnl", 0) or 0))
            base = Decimal(str(getattr(balance, "estimated_deposit", 0) or 0))
            if base <= 0:
                return Decimal("0.0")
            return (total_pnl / base) * Decimal("100")
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
