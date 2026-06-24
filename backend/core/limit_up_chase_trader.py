"""상따(상한가 따라잡기) + 캡상승 수익극대화 트레이더 — BAR-OPS 2026-06-10.

`SupertrendAutoTrader` 를 상속해 청산(RUNNER)·`_is_limit_up`·익일 갭 부분익절 등
'캡상승 수익극대화' 로직을 100% 재사용하고, **진입만** 상따 전용으로 교체한다.
supertrend_auto_trader.py 는 한 줄도 수정하지 않는다(보수전략·글로벌 가드 무영향).

설계(사용자 확정):
  - 진입: 모멘텀 밴드(등락률 entry_flu_min~max) + **호가 매수벽 확인**(ka10004).
          데몬 글로벌 급등가드(_MAX_FLU_RATE/P10/고점인접)와 무관한 독립 경로.
  - 청산: 당일 RUNNER(상한가 홀딩→수익잠금→고점되돌림) + 오버나잇 모드(점상한가
          익일 시가갭 부분익절). overnight_mode env 로 daily|overnight 전환.
  - 포지션: strategy="limit_up_chase" 태깅 → supertrend/zone 과 상호 배타(더블셀 방지).
  - 안전: 마스터 토글 default OFF, dry_run 우선, 보수한도(동시 1~2종·소액·일일한도).

⚠️ 호가 L2 이력이 없어 백테스트 불가 → 라이브 dry_run 검증 위주(ob_scalp.py 한계와 동일).
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, time as dtime, timedelta, timezone
from decimal import Decimal
from typing import Any, Optional

from backend.core.risk.balance_gate import evaluate_risk_gate
from backend.core.strategy.supertrend import compute_supertrend
from backend.core.supertrend_auto_trader import (
    SupertrendAutoConfig,
    SupertrendAutoTrader,
    _close_rush_active,
)
from backend.models.market import TradingSession

logger = logging.getLogger(__name__)

_STRATEGY_ID = "limit_up_chase"   # ActivePosition.strategy / audit strategy_id


@dataclass
class LimitUpChaseConfig(SupertrendAutoConfig):
    """상따 전용 config — SupertrendAutoConfig(runner_*/gap_partial_*/min_price 등) 상속."""

    # ── 진입: 모멘텀 밴드 ──────────────────────────────────────────────────
    entry_flu_min: float = 20.0        # 등락률 밴드 하한(%) — 상한가 근접 모멘텀
    entry_flu_max: float = 27.0        # 등락률 밴드 상한(%) — 이미 +30% 락 추격 차단
    # ── 진입: 호가 매수벽(ka10004) ────────────────────────────────────────
    wall_near_pct: float = 1.0         # (deprecated·미사용) 옛 '상한가 근접' 게이트 — 밴드 진입과 모순되어 제거
    wall_min_top_qty: int = 50_000     # (deprecated·미사용) 매수1호가 절대 잔량(주)
    wall_min_top_value: float = 100_000_000.0  # 매수1호가 잔량금액 임계(원) — 거래대금 기준(고가주 대응)
    wall_bid_ask_ratio: float = 3.0    # top-N 매수/매도 잔량 비율(매도 소진 임박)
    wall_levels: int = 3               # 비율 산정 단계수
    # ── 진입 시간대 마감(부모엔 entry_start_time 만 존재) ──────────────────
    entry_end_time: str = "14:00"      # 장막판 상한가 추격(익일갭 리스크) 차단
    # ── 오버나잇 ──────────────────────────────────────────────────────────
    overnight_mode: str = "daily"      # daily | overnight
    eod_close_time: str = "15:15"      # daily 모드 자체 강제청산 시각(KST)


class LimitUpChaseTrader(SupertrendAutoTrader):
    """상따 트레이더 — 진입(run_cycle) override, 청산은 부모 RUNNER 헬퍼 재사용."""

    def __init__(self, *args, orderbook_fetcher: Optional[Any] = None, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self._ob = orderbook_fetcher
        # 상따는 청산이 항상 RUNNER 기반 — 캡상승 수익극대화 일관성 위해 강제 ON.
        self.config.runner_enabled = True

    # ── 1 사이클 (청산 먼저, 그 다음 상따 진입) ───────────────────────────────
    async def run_cycle(self) -> dict:
        result: dict = {"entered": [], "exited": []}
        self._roll_day()

        if self.config.market_hours_only:
            session = self._session.get_session()
            if session != TradingSession.REGULAR:
                logger.debug("상따 skip — 비정규장 세션(%s)", getattr(session, "value", session))
                return result

        daily_pnl_pct = await self._account_pnl_pct()

        # ── 청산 (limit_up_chase 포지션만) ───────────────────────────────────
        await self._run_exit_cycle(daily_pnl_pct, result)

        # [6/24] 종가러시 throttle — 종베 API 우선권 위해 상따 진입 스캔 양보(청산 위에서 수행).
        if _close_rush_active():
            return result

        # ── 진입: 진입 시간창(start~end) 안에서만 ────────────────────────────
        if not self._entry_window_open():
            return result

        held_after = self._pos.load_all()
        lu_count = sum(1 for p in held_after.values()
                       if (getattr(p, "strategy", "") or "").startswith(_STRATEGY_ID))
        slots = self.config.max_positions - lu_count
        if slots <= 0:
            return result

        universe = await self._universe_provider()   # list[LeaderCandidate]
        cand_recs: list[tuple[str, str, Decimal]] = []
        for cand in list(universe)[: self.config.universe_max]:
            symbol = getattr(cand, "symbol", "")
            name = getattr(cand, "name", "") or symbol
            if not symbol or symbol in held_after:
                continue
            if self._reentry_blocked(symbol):
                continue
            if self.config.exclude_leverage and self._is_leverage_or_inverse(symbol, name):
                continue
            if self.config.exclude_etf and self._is_etf_or_etn(symbol, name):
                continue
            # (B) 모멘텀 밴드 — 등락률(ranking 메타) 기준. 데몬 글로벌가드와 무관.
            if not self._momentum_band_pass(cand):
                continue
            try:
                bars = await self._fetch_bars(symbol)
                if bars is None:
                    continue
                price = Decimal(str(float(bars[-1].close)))
                if price <= 0 or price < self.config.min_price:
                    continue
                # (C) 호가 매수벽 — 모멘텀 통과 후보만 호가 1회 fetch(API 절감).
                if not await self._passes_orderbook_wall(symbol, bars):
                    continue
            except Exception as e:
                logger.warning("상따 진입 분석 실패: %s — %s", symbol, type(e).__name__)
                continue
            cand_recs.append((symbol, name, price))
            if len(cand_recs) >= slots:
                break

        if not cand_recs:
            return result

        deposit = await self._account.fetch_deposit()
        balance = await self._account.fetch_balance()
        gate = evaluate_risk_gate(
            deposit=deposit, balance=balance, candidates=cand_recs,
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
                    single_tranche=True,   # 상따 단일주문 — sync-loss 방지
                )
                placed += 1
                self._entries_today[rec.symbol] = self._entries_today.get(rec.symbol, 0) + 1
                result["entered"].append({"symbol": rec.symbol, "qty": qty,
                                          "price": float(rec.cur_price),
                                          "order_no": getattr(r, "order_no", ""),
                                          "dry_run": getattr(r, "dry_run", False)})
                logger.info("상따 자동진입: %s qty=%d @%.0f", rec.symbol, qty, float(rec.cur_price))
                await self._notify("상따 자동매수",
                                   f"{rec.symbol} {rec.name} {qty}주 @{int(rec.cur_price):,}")
            except Exception as e:
                logger.error("상따 진입 주문 실패: %s — %s", rec.symbol, type(e).__name__)

        return result

    # ── 청산 사이클 — 부모 RUNNER 헬퍼 재사용, strategy_id=limit_up_chase ────────
    async def _run_exit_cycle(self, daily_pnl_pct, result: dict) -> None:
        held = self._pos.load_all()
        lu_held = {s: p for s, p in held.items()
                   if (getattr(p, "strategy", "") or "").startswith(_STRATEGY_ID)}
        for symbol, pos in lu_held.items():
            try:
                bars = await self._fetch_bars(symbol)
                if bars is None:
                    continue
                res = compute_supertrend(
                    bars, period=self.config.params.atr_period,
                    multiplier=self.config.params.multiplier,
                    source=self.config.params.source,
                )
                # 오버나잇 종목 익일 시가갭 부분익절(성사 시 이번 사이클 청산평가 스킵)
                if await self._maybe_gap_partial(symbol, pos, bars, daily_pnl_pct, result):
                    continue
                # daily 모드: 장 마감 직전 자체 강제청산(상한가 락이라도 당일 정리)
                if self._eod_force_now():
                    exit_now, reason = True, "EOD청산(당일모드)"
                else:
                    trailed = self._trail_hit(pos, bars, res)
                    hard_hit = self._hard_stop_hit(pos, bars) if not trailed else False
                    # 상따는 항상 runner — 트리거(TP/상한가/시초갭) 시 최고점 추적 청산.
                    runner_on = (not (trailed or hard_hit)) and self._runner_triggered(pos, bars)
                    runner_exit, runner_reason = (False, "")
                    if runner_on:
                        runner_exit, runner_reason = self._runner_should_exit(pos, bars, res)
                    # 아직 runner 트리거 전(미상한가·미TP)이면 고정 익절로 보완.
                    tp_hit = (self._take_profit_hit(pos, bars)
                              if not (trailed or hard_hit or runner_on
                                      or self.config.take_profit_trail_only)
                              else False)
                    exit_now = bool(trailed or hard_hit or tp_hit or runner_exit)
                    reason = ("트레일청산" if trailed else
                              ("하드손절" if hard_hit else
                               (runner_reason if runner_exit else
                                ("익절" if tp_hit else ""))))
                if not exit_now:
                    continue   # 상한가 홀딩 / 러너 홀딩 → 보유 지속
                qty = int(getattr(pos, "total_recommended_qty", 0)) or self._filled_qty(pos)
                if qty <= 0:
                    continue
                r = await self._gate.place_sell(
                    symbol=symbol, qty=qty,
                    daily_pnl_pct=daily_pnl_pct, strategy_id=_STRATEGY_ID,
                )
                self._pos.remove(symbol)
                self._last_exit[symbol] = datetime.now(timezone.utc)
                _entry_px = float(getattr(pos, "entry_price", 0) or 0)
                if _entry_px > 0 and float(bars[-1].close) < _entry_px:
                    self._loss_locked.add(symbol)
                result["exited"].append({"symbol": symbol, "qty": qty, "reason": reason,
                                         "order_no": getattr(r, "order_no", ""),
                                         "dry_run": getattr(r, "dry_run", False)})
                logger.warning("상따 자동청산: %s qty=%d (%s)", symbol, qty, reason)
                await self._notify("상따 자동매도", f"{symbol} {qty}주 청산 ({reason})")
            except Exception as e:
                logger.error("상따 청산 실패: %s — %s", symbol, type(e).__name__)

    # ── 진입 게이트 헬퍼 ──────────────────────────────────────────────────────
    @staticmethod
    def _now_kst_time() -> dtime:
        return datetime.now(timezone(timedelta(hours=9))).time()

    def _entry_window_open(self, now: Optional[dtime] = None) -> bool:
        """진입 시간창 — 부모 start 게이트(_entry_time_open) AND entry_end_time 이전.

        now 주입 가능(테스트용). None 이면 실제 KST 현재시각.
        """
        if not self._entry_time_open():
            return False
        end = (self.config.entry_end_time or "").strip()
        if not end:
            return True
        try:
            cur = now or self._now_kst_time()
            hh, mm = end.split(":")
            return cur <= dtime(int(hh), int(mm))
        except Exception:
            return True

    def _eod_force_now(self, now: Optional[dtime] = None) -> bool:
        """daily 모드에서 eod_close_time 이후면 True(강제청산). overnight 모드면 항상 False."""
        if (self.config.overnight_mode or "daily").strip().lower() == "overnight":
            return False
        t = (self.config.eod_close_time or "").strip()
        if not t:
            return False
        try:
            cur = now or self._now_kst_time()
            hh, mm = t.split(":")
            return cur >= dtime(int(hh), int(mm))
        except Exception:
            return False

    def _momentum_band_pass(self, cand: Any) -> bool:
        """등락률(ranking flu_rate)이 [entry_flu_min, entry_flu_max] 밴드 안이면 True.

        거래대금/거래량 급증은 picker 의 3-factor 랭킹·min_score 가 이미 보장 →
        추가 TR 호출 없이 밴드만 검사(429 절감).
        """
        flu = float(getattr(cand, "flu_rate", 0) or 0)
        return self.config.entry_flu_min <= flu <= self.config.entry_flu_max

    async def _passes_orderbook_wall(self, symbol: str, bars) -> bool:
        """ka10004 호가로 '매수벽' 확인. 호가 미가용/실패 시 보수적으로 False.

        판정(AND): ① 매수1호가 잔량금액(가격×잔량) ≥ wall_min_top_value (거래대금 기준, 고가주 대응)
                   ② top-N 매수/매도 잔량 비율 ≥ wall_bid_ask_ratio (매도 전무 시 통과).

        NOTE: 과거 '상한가 근접' 게이트(매수1호가가 상한가가격 ±wall_near_pct% 이내)는
        제거됨. 상따 진입 밴드(등락률 20~27%)는 정의상 상한가(+29~30%)보다 3~10%p
        아래여서 매수1호가가 상한가가격에 닿을 수 없어, 그 게이트가 모든 밴드 종목을
        영구 탈락시켜 상따 진입 0건을 유발했다(원익IPS 2026-06-12 사례). 밴드 자체는
        _momentum_band_pass 가, 매수세 우위는 잔량금액·매수/매도비율이 확인한다.
        """
        if self._ob is None:
            return False
        try:
            ob = await self._ob.fetch_orderbook(symbol)
        except Exception as e:
            logger.warning("상따 호가 조회 실패: %s — %s", symbol, type(e).__name__)
            return False
        bids = list(getattr(ob, "bids", None) or [])
        asks = list(getattr(ob, "asks", None) or [])
        if not bids:
            return False
        top_bid_price, top_bid_qty = float(bids[0][0]), float(bids[0][1])
        # ① 매수1호가 잔량금액(거래대금 기준 — 고가주 대응)
        top_bid_value = top_bid_price * top_bid_qty
        if top_bid_value < self.config.wall_min_top_value:
            logger.debug("상따 호가벽 탈락(잔량금액 부족): %s 매수1잔량금액=%.0f < %.0f",
                         symbol, top_bid_value, self.config.wall_min_top_value)
            return False
        # ② top-N 매수/매도 잔량 비율
        n = max(1, self.config.wall_levels)
        bq = sum(float(q) for _, q in bids[:n])
        aq = sum(float(q) for _, q in asks[:n]) if asks else 0.0
        if aq <= 0:
            return True   # 매도 잔량 전무 = 상한가 락 임박 → 통과
        if bq / aq < self.config.wall_bid_ask_ratio:
            logger.debug("상따 호가벽 탈락(매수/매도비율): %s %.2f < %.2f",
                         symbol, bq / aq, self.config.wall_bid_ask_ratio)
            return False
        return True

    # ── 익일 시가갭 부분익절 override — 부모 로직 동일, strategy_id=limit_up_chase ──
    async def _maybe_gap_partial(self, symbol: str, pos: Any, bars, daily_pnl_pct,
                                 result: dict) -> bool:
        """[부모 _maybe_gap_partial 의 limit_up_chase 귀속판] 오버나잇 종목 개장초 갭 부분익절.

        조건/동작은 부모와 동일. place_sell 의 strategy_id 만 limit_up_chase 로(PnL 귀속).
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
            return False
        et = getattr(pos, "entry_time", "") or ""
        if et:
            try:
                if datetime.fromisoformat(et).date() >= cur_date:
                    return False   # 당일 진입은 갭 대상 아님(오버나잇만)
            except (ValueError, TypeError):
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
        if entry <= 0 or cur <= entry:
            return False
        held = int(getattr(pos, "total_recommended_qty", 0)) or self._filled_qty(pos)
        part = int(held * c.runner_gap_partial_ratio)
        if part <= 0 or part >= held:
            return False
        r = await self._gate.place_sell(
            symbol=symbol, qty=part, daily_pnl_pct=daily_pnl_pct, strategy_id=_STRATEGY_ID,
        )
        pos.total_recommended_qty = held - part
        pos.partial_tp_done = True
        try:
            self._pos.upsert(pos)
        except Exception as e:
            logger.error("상따 부분익절 후 포지션 갱신 실패: %s — %s", symbol, type(e).__name__)
        result["exited"].append({"symbol": symbol, "qty": part, "reason": "익일갭 부분익절",
                                 "order_no": getattr(r, "order_no", ""),
                                 "dry_run": getattr(r, "dry_run", False), "partial": True})
        logger.info("상따 익일 시가갭 부분익절: %s %d/%d주 (갭 %+.1f%%, 잔량 %d 러너)",
                    symbol, part, held, gap * 100, held - part)
        await self._notify("상따 익일갭 부분익절", f"{symbol} {part}주 확정 (갭 {gap*100:+.1f}%, 잔량 런)")
        return True


__all__ = ["LimitUpChaseTrader", "LimitUpChaseConfig"]
