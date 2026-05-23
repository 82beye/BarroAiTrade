"""장중 실시간 포지션 관리 데몬 — 09:30~15:20 3분 간격.

매 사이클마다:
  1) 보유 종목 매도 평가 (TP/SL/트레일링/DCA)
  2) 신규 시그널 매수 스캔

사용:
    python scripts/intraday_buy_daemon.py
    python scripts/intraday_buy_daemon.py --interval 180 --top 5 --no-dry-run --telegram
"""
from __future__ import annotations

import argparse
import asyncio
import csv as _csv
import os
import signal
import sys
from datetime import datetime, time, timezone, timedelta
from decimal import Decimal
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

_DATA_DIR = Path(__file__).resolve().parents[1] / "data"

from pydantic import SecretStr

from backend.core.backtester import IntradaySimulator
from backend.core.gateway.kiwoom_native_account import KiwoomNativeAccountFetcher
from backend.core.gateway.kiwoom_native_candles import KiwoomNativeCandleFetcher
from backend.core.gateway.kiwoom_native_oauth import KiwoomNativeOAuth
from backend.core.gateway.kiwoom_native_orders import KiwoomNativeOrderExecutor
from backend.core.gateway.kiwoom_native_rank import KiwoomNativeLeaderPicker
from backend.core.journal.active_positions import ActivePositionStore
from backend.core.journal.policy_config import PolicyConfigStore
from backend.core.notify.telegram import TelegramNotifier, format_buy_alert, format_sell_alert
from backend.core.risk.balance_gate import evaluate_risk_gate
from backend.core.risk.holding_evaluator import (
    ExitPolicy, PositionContext, SellSignal, STRATEGY_EXIT_PROFILES,
    evaluate_all, resolve_policy,
)
from backend.core.risk.live_order_gate import (
    GatePolicy, LiveOrderGate, DailyOrderLimitExceeded,
)
from backend.core.backtester.market_regime import (
    MarketRegime, classify_regime, regime_weights,
)

KST = timezone(timedelta(hours=9))
MARKET_OPEN = time(9, 5)       # 시초가 안정 후 매수 시작 (08:58→09:05)
MARKET_CLOSE = time(15, 20)
BUY_START = time(9, 5)         # 매수는 09:05 이후만
SELL_START = time(9, 1)        # 매도 평가는 09:01부터
MIN_HOLD_MINUTES = 15          # 매수 후 최소 보유 (P7 5/20: 10→15, 노이즈 SL 회피)
MAX_BUY_PER_CYCLE = 2          # 사이클당 최대 매수 2종목
BUY_REENTRY_COOLDOWN_MIN = 30  # P6 (2026-05-20): 매수 후 동일 종목 재진입 금지 (30분)
HARD_SL_PCT = -5.0             # P7 (2026-05-20): cooldown 안 극한 SL 우회 임계


def _now_kst() -> datetime:
    return datetime.now(KST)


def _in_market_hours() -> bool:
    now = _now_kst().time()
    return MARKET_OPEN <= now <= MARKET_CLOSE


def _build_oauth() -> KiwoomNativeOAuth:
    app_key = os.environ.get("KIWOOM_APP_KEY", "")
    app_secret = os.environ.get("KIWOOM_APP_SECRET", "")
    base_url = os.environ.get("KIWOOM_BASE_URL", "https://openapi.kiwoom.com")
    if not app_key or not app_secret:
        raise SystemExit("KIWOOM_APP_KEY / KIWOOM_APP_SECRET 환경변수 필요")
    return KiwoomNativeOAuth(
        app_key=SecretStr(app_key), app_secret=SecretStr(app_secret),
        base_url=base_url,
    )


async def _sync_positions(pos_store: ActivePositionStore, held_symbols: set[str]) -> int:
    """브로커 잔고와 active_positions 동기화. 잔고에 없는 종목은 제거."""
    active = pos_store.load_all()
    removed = 0
    for sym in list(active.keys()):
        if sym not in held_symbols:
            pos_store.remove(sym)
            ts = _now_kst().strftime("%H:%M:%S")
            print(f"  [{ts}][SYNC] {sym} {active[sym].name} — 잔고에 없음, active_positions 제거")
            removed += 1
    return removed


async def _evaluate_and_sell(args, oauth, notifier) -> int:
    """보유 종목 매도 평가 + DCA. 매도 건수 반환."""
    # 매도 평가는 SELL_START 이후만
    if _now_kst().time() < SELL_START:
        return 0

    cfg = PolicyConfigStore(str(_DATA_DIR / "policy.json")).load()
    account = KiwoomNativeAccountFetcher(oauth=oauth)
    balance = await account.fetch_balance()

    # 브로커 잔고 ↔ active_positions 동기화
    pos_store = ActivePositionStore(args.pos_log)
    held_symbols = {h.symbol for h in (balance.holdings or [])}
    await _sync_positions(pos_store, held_symbols)

    if not balance.holdings:
        return 0

    policy = ExitPolicy(
        take_profit_pct=Decimal(str(cfg.take_profit_pct)),
        stop_loss_pct=Decimal(str(cfg.stop_loss_pct)),
        trailing_start_pct=Decimal(str(cfg.trailing_start_pct)),
        trailing_offset_pct=Decimal(str(cfg.trailing_offset_pct)),
        breakeven_trigger_pct=Decimal(str(cfg.breakeven_trigger_pct)),
        partial_tp_pct=Decimal(str(cfg.partial_tp_pct)),
        partial_tp_ratio=Decimal(str(cfg.partial_tp_ratio)),
        hold_days_tighten=cfg.hold_days_tighten,
        tightened_sl_pct=Decimal(str(cfg.tightened_sl_pct)),
    )

    active_positions = pos_store.load_all()
    contexts: dict[str, PositionContext] = {}

    # 2026-05-21 — 단기 고점 매도 평가용 1분봉 fetch (각 보유 종목).
    # 익절 구간(rate ≥ partial_tp_pct) 도달 종목만 fetch (API 부담 절감).
    fetcher_for_exit = KiwoomNativeCandleFetcher(oauth=oauth)
    minute_cache: dict[str, list] = {}

    for h in balance.holdings:
        pos = active_positions.get(h.symbol)
        if pos:
            cur_rate = float(h.pnl_rate)
            now_iso = datetime.now(timezone.utc).isoformat(timespec="seconds")
            dirty = False
            if cur_rate > pos.peak_pnl_rate:
                pos.peak_pnl_rate = cur_rate
                pos.peak_updated_at = now_iso
                dirty = True
            # P2 fix — trough(lowest) 추적 → DCA trigger 평가에 사용
            if cur_rate < pos.trough_pnl_rate:
                pos.trough_pnl_rate = cur_rate
                pos.trough_updated_at = now_iso
                dirty = True
            if dirty:
                pos_store.upsert(pos)

            # SHORT_TERM_HIGH 평가용 1분봉 — 전략별 partial_tp_pct 도달 시만 fetch
            _strat_key = (pos.strategy or "").replace("_v1", "").replace("_v2", "")
            _strat_partial_tp = float(
                STRATEGY_EXIT_PROFILES.get(_strat_key, {}).get("partial_tp_pct", policy.partial_tp_pct)
            )
            minute_candles = None
            if cur_rate >= _strat_partial_tp:
                try:
                    bars = await fetcher_for_exit.fetch_minute(symbol=h.symbol, tic_scope="1")
                    today_str = _now_kst().strftime("%Y-%m-%d")
                    minute_candles = [
                        b for b in bars if b.timestamp.strftime("%Y-%m-%d") == today_str
                    ]
                    minute_cache[h.symbol] = minute_candles
                except Exception:
                    pass

            contexts[h.symbol] = PositionContext(
                peak_pnl_rate=pos.peak_pnl_rate,
                partial_tp_done=pos.partial_tp_done,
                entry_time=pos.entry_time,
                strategy=pos.strategy,
                minute_candles=minute_candles,
            )

    decisions = evaluate_all(balance.holdings, policy, contexts)

    # DCA 분할매수
    _SELL_SIGNALS = {
        SellSignal.STOP_LOSS, SellSignal.TRAILING_STOP,
        SellSignal.BREAKEVEN_STOP, SellSignal.TIME_TIGHTENED_SL,
        SellSignal.SHORT_TERM_HIGH,
    }
    sl_symbols = {d.symbol for d in decisions if d.signal in _SELL_SIGNALS}

    executor = KiwoomNativeOrderExecutor(oauth=oauth, dry_run=args.dry_run)
    gate = LiveOrderGate(
        executor=executor, audit_path=args.audit_log,
        policy=GatePolicy(daily_max_orders=cfg.daily_max_orders),
        notifier=notifier,
    )

    # DCA — 2026-05-19 P2 fix:
    # 기존: cur_price > trigger_price 면 skip (폴링 사이 일중 low 도달 놓침)
    # 변경: trough_pnl_rate(=일중 lowest) 가 trigger_drop_pct 이하 도달했으면 발동
    #       매수가는 현재가(cur_price). 발동 후 status=filled 로 1회만 실행.
    # 5/18 069500 L -6.4% 도달했어도 T2(-2%) pending 영구 → fix 후 발동 가능.
    active_positions = pos_store.load_all()
    for h in balance.holdings:
        if h.symbol in sl_symbols:
            continue
        pos = active_positions.get(h.symbol)
        if not pos:
            continue
        pending = pos.pending_tranches()
        if not pending:
            continue
        cur_price = float(h.cur_price)
        cur_rate = float(h.pnl_rate)
        # trough 기반 평가: 일중 lowest 가 trigger 도달했으면 발동
        # (현재 cur_rate 회복했어도 OK — 한 번 trigger 도달 = 매수 가치 있음)
        eff_drop_pct = min(cur_rate, float(pos.trough_pnl_rate))
        for tranche in pending:
            if tranche.qty <= 0:
                continue
            if eff_drop_pct > tranche.trigger_drop_pct:
                continue
            try:
                r = await gate.place_buy(symbol=h.symbol, qty=tranche.qty)
                tag = "DRY_RUN" if r.dry_run else "DCA"
                ts = _now_kst().strftime("%H:%M:%S")
                print(f"  [{ts}][{tag}] {h.symbol} {h.name} T{tranche.tranche} qty={tranche.qty}")
                tranche.status = "filled"
                tranche.order_no = r.order_no
                tranche.filled_price = cur_price
                tranche.filled_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
                pos_store.upsert(pos)
            except DailyOrderLimitExceeded:
                ts = _now_kst().strftime("%H:%M:%S")
                print(f"  [{ts}][DCA] 일일 거래수 한도 도달 — DCA 중단")
                break
            except Exception as e:
                print(f"  [DCA-ERR] {h.symbol}: {e}")

    # 매도 실행
    # 쿨다운: 매수 후 MIN_HOLD_MINUTES 미경과 종목 매도 제외
    now_utc = datetime.now(timezone.utc)
    cooldown_symbols: set[str] = set()
    for sym, pos in active_positions.items():
        if pos.entry_time:
            try:
                entry_dt = datetime.fromisoformat(pos.entry_time)
                elapsed = (now_utc - entry_dt).total_seconds() / 60
                if elapsed < MIN_HOLD_MINUTES:
                    cooldown_symbols.add(sym)
            except Exception:
                pass

    # P7+P9 (2026-05-20) — cooldown 안 매도 정책:
    #   - defensive (STOP_LOSS·BREAKEVEN_STOP·TIME_TIGHTENED_SL) 차단
    #   - 단 STOP_LOSS rate ≤ -5% (hard SL) 는 우회 (큰 손실 방지)
    #   - 익절 (TRAILING_STOP·TAKE_PROFIT·PARTIAL_TP) 는 통과 (P9 — 강세 종목 익절 기회 보장)
    # P7 단독 시 274090 peak +8.1% trail 차단으로 -358k 손실 발생 → P9 보완.
    _DEFENSIVE_SIGNALS = {
        SellSignal.STOP_LOSS,
        SellSignal.BREAKEVEN_STOP,
        SellSignal.TIME_TIGHTENED_SL,
    }

    def _allow_sell(d) -> bool:
        if d.symbol not in cooldown_symbols:
            return True
        # cooldown 안 — 익절 신호 (trail/TP/partial_tp) 는 통과
        if d.signal not in _DEFENSIVE_SIGNALS:
            return True
        # defensive 차단 — 단 hard SL 만 우회
        return (
            d.signal == SellSignal.STOP_LOSS
            and float(d.pnl_rate) <= HARD_SL_PCT
        )

    sell_targets = [
        d for d in decisions
        if d.signal != SellSignal.HOLD and _allow_sell(d)
    ]
    if not sell_targets:
        return 0

    sold = 0
    for d in sell_targets:
        # partial_tp만 분할, 나머지(손절/트레일링 등)는 전량 매도
        if d.signal == SellSignal.PARTIAL_TP and d.sell_qty > 0:
            sell_qty = d.sell_qty
        else:
            sell_qty = d.qty
        try:
            r = await gate.place_sell(symbol=d.symbol, qty=sell_qty)
            tag = "DRY_RUN" if r.dry_run else "SOLD"
            ts = _now_kst().strftime("%H:%M:%S")
            print(
                f"  [{ts}][{tag}] {d.symbol} {d.name:<14} qty={sell_qty}/{d.qty} "
                f"signal={d.signal.value} pnl={d.pnl_rate:+.1f}%"
            )
            if d.signal == SellSignal.PARTIAL_TP:
                pos = active_positions.get(d.symbol)
                if pos:
                    pos.partial_tp_done = True
                    pos_store.upsert(pos)
            elif sell_qty >= d.qty:
                pos_store.remove(d.symbol)

            if notifier:
                try:
                    await notifier.send(format_sell_alert(
                        d.symbol, d.name, sell_qty, d.signal.value,
                        float(d.pnl_rate), r.order_no, r.dry_run,
                    ))
                except Exception:
                    pass
            sold += 1
        except DailyOrderLimitExceeded:
            ts = _now_kst().strftime("%H:%M:%S")
            print(f"  [{ts}][SELL] 일일 거래수 한도 도달 — 매도 중단")
            break
        except Exception as e:
            print(f"  [SELL-ERR] {d.symbol}: {e}")

    return sold


def _save_refined_signals(signals: list, regime) -> None:
    """정제된 시그널을 JSON 파일에 저장 (대시보드 API용)."""
    import json
    now_iso = _now_kst().isoformat()
    data = {
        "regime": regime.value,
        "timestamp": now_iso,
        "signals": [
            {
                "symbol": c.symbol,
                "name": c.name,
                "strategy": strategy,
                "score": round(float(c.score), 3),
                "flu_rate": float(c.flu_rate),
                "cur_price": float(c.cur_price),
                "pnl": round(pnl, 0),
                "ts": now_iso,
            }
            for c, strategy, pnl in signals
        ],
    }
    path = _DATA_DIR / "refined_signals.json"
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


async def _scan_and_buy(
    args, oauth, session_bought: set[str],
    recent_buys: dict[str, datetime] | None = None,
) -> int:
    """한 사이클: 스캔 → 시그널 검증 → 매수. 매수 건수 반환."""
    # 매수는 BUY_START 이후만
    if _now_kst().time() < BUY_START:
        return 0

    cfg = PolicyConfigStore(str(_DATA_DIR / "policy.json")).load()
    picker = KiwoomNativeLeaderPicker(
        oauth=oauth, min_flu_rate=args.min_flu, min_score=cfg.min_score,
    )
    fetcher = KiwoomNativeCandleFetcher(oauth=oauth)

    leaders = await picker.pick(top_n=args.top)
    if not leaders:
        return 0

    # 보유/당일매도/세션매수 제외
    account = KiwoomNativeAccountFetcher(oauth=oauth)
    balance = await account.fetch_balance()
    already_held = {h.symbol for h in (balance.holdings or [])}

    pos_store = ActivePositionStore(args.pos_log)
    active_symbols = set(pos_store.load_all().keys())

    today_sold: set[str] = set()
    audit_path = Path(args.audit_log)
    if audit_path.exists():
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        try:
            with audit_path.open(newline="", encoding="utf-8") as f:
                for row in _csv.DictReader(f):
                    if row.get("ts", "").startswith(today) and row.get("side") == "sell":
                        today_sold.add(row["symbol"])
        except Exception:
            pass

    # P6 (2026-05-20) — 매수 후 BUY_REENTRY_COOLDOWN_MIN 미경과 종목 재진입 금지.
    # 5/19 4건 양수 가격 추매(001430 6분·036930 1분·027360 1분·005500 1분) 차단 목적.
    # session_bought.add 가 pos_store.create_from_order exception 시 누락되거나
    # active_positions disk write timing 지연으로 already_held/active_symbols
    # 필터 우회한 경우의 이중 안전망. process-local recent_buys dict 로 추적.
    cooldown_buys: set[str] = set()
    if recent_buys is not None:
        now_kst = _now_kst()
        for sym, bought_at in list(recent_buys.items()):
            elapsed_min = (now_kst - bought_at).total_seconds() / 60
            if elapsed_min < BUY_REENTRY_COOLDOWN_MIN:
                cooldown_buys.add(sym)
            else:
                recent_buys.pop(sym, None)  # cooldown 종료 → 정리

    # audit log 기반 fallback — recent_buys 가 process restart 등으로 비어있으면
    # audit 의 최근 매수 ts 로 cooldown 평가 (이중 안전망).
    audit_buys: set[str] = set()
    if audit_path.exists():
        now_utc = datetime.now(timezone.utc)
        cutoff_utc = now_utc - timedelta(minutes=BUY_REENTRY_COOLDOWN_MIN)
        try:
            with audit_path.open(newline="", encoding="utf-8") as f:
                for row in _csv.DictReader(f):
                    if row.get("side") != "buy":
                        continue
                    if row.get("action") not in {"ORDERED", "DRY_RUN"}:
                        continue
                    try:
                        row_ts = datetime.fromisoformat(row["ts"])
                        if row_ts.tzinfo is None:
                            row_ts = row_ts.replace(tzinfo=timezone.utc)
                    except (ValueError, KeyError):
                        continue
                    if row_ts >= cutoff_utc:
                        audit_buys.add(row["symbol"])
        except Exception:
            pass

    excluded = (
        already_held | active_symbols | today_sold | session_bought
        | cooldown_buys | audit_buys
    )
    filtered = [c for c in leaders if c.symbol not in excluded
                and c.flu_rate < 25.0 and c.cur_price >= 5_000]

    if not filtered:
        return 0

    # 시장 국면(regime) 분류 — 상위 종목 캔들로 판단
    candles_for_regime: dict[str, list] = {}
    for c in filtered[:5]:
        try:
            clist = await fetcher.fetch_daily(symbol=c.symbol)
            if len(clist) >= 31:
                candles_for_regime[c.symbol] = clist
        except Exception:
            pass

    regime = classify_regime(candles_for_regime, lookback=30)
    weights = regime_weights(regime)
    ts_r = _now_kst().strftime("%H:%M:%S")
    print(f"  [{ts_r}][REGIME] {regime.value.upper()} (종목 {len(candles_for_regime)}개 분석)")

    # 2026-05-20 P8 — 시장 국면별 매수 제한 강화.
    # 5/19 7건 매수 중 5건 손실 (SIDEWAYS·BULL 분류로 max_buy=2 적용된 듯).
    # SIDEWAYS 도 보수 축소 (1건) + BEARISH 는 더 강한 신호 임계 추가.
    regime_max_buy = MAX_BUY_PER_CYCLE
    bearish_min_pnl = 0.0
    if regime == MarketRegime.BEARISH:
        regime_max_buy = 1
        bearish_min_pnl = 50_000.0  # BEARISH: 강한 신호만 (best_pnl > 50k)
        print(f"  [{ts_r}][REGIME] BEARISH — 가중치≥1.0 전략 + best_pnl>50k 만, 최대 1건")
    elif regime == MarketRegime.SIDEWAYS:
        regime_max_buy = 1  # P8 신규 — 박스권도 보수 운영
        print(f"  [{ts_r}][REGIME] SIDEWAYS — 보수 운영, 최대 1건")

    # 전략 시뮬레이션 시그널 검증
    sim = IntradaySimulator()
    strategies = ["f_zone", "sf_zone", "gold_zone", "swing_38"]
    signals = []

    for c in filtered:
        try:
            candles = await fetcher.fetch_daily(symbol=c.symbol)
        except Exception:
            continue
        if len(candles) < 60:
            continue

        result = sim.run(candles, symbol=c.symbol, strategies=strategies)
        # 국면 가중치 적용하여 전략 점수 조정
        weighted_pnl = {
            s: float(result.pnl_by_strategy[s]) * weights.get(s, 1.0)
            for s in result.pnl_by_strategy
        }
        best_strategy = max(weighted_pnl, key=lambda s: weighted_pnl[s])
        best_pnl = weighted_pnl[best_strategy]

        if best_pnl > 0 and len(result.trades) > 0:
            # BEARISH 국면: 가중치 < 1.0 전략은 제외
            if regime == MarketRegime.BEARISH and weights.get(best_strategy, 1.0) < 1.0:
                continue
            # P8 — BEARISH 시 best_pnl 임계 (강한 신호만)
            if bearish_min_pnl > 0 and best_pnl < bearish_min_pnl:
                continue
            # 2026-05-19 P4 fix — 고점 진입 회피.
            # 5/18 122630 entry 161,690 vs H 162,955 (0.78%) → 즉시 하락,
            # 069500 entry 119,220 vs H 119,900 (0.57%) → 미청산 손실 보유.
            # 조건: 일중 H 대비 cur 거리 < 1.5% AND H 가 진입 직전 봉의 high 가 아님.
            #       (직전 봉이 H 봉이면 모멘텀 진행 중 — 080220 같은 정상 진입 보호)
            try:
                minute_bars = await fetcher.fetch_minute(symbol=c.symbol, tic_scope="1")
                today_str = datetime.now(timezone.utc).astimezone(KST).strftime("%Y-%m-%d")
                day_bars = [b for b in minute_bars if b.timestamp.strftime("%Y-%m-%d") == today_str]
                if day_bars:
                    # P10 (2026-05-21) — 시초가 폭등 차단.
                    # 142280 5/20 시초가 +28% 같은 케이스 진입 차단 (눌림목 패턴
                    # 안 맞고 매물대 형성으로 손실 위험).
                    first_open = day_bars[0].open
                    cur = float(c.cur_price)
                    if first_open > 0:
                        intraday_change_pct = (cur - first_open) / first_open * 100
                        MAX_INTRADAY_CHANGE_PCT = 20.0
                        if intraday_change_pct >= MAX_INTRADAY_CHANGE_PCT:
                            ts_p = _now_kst().strftime("%H:%M:%S")
                            print(
                                f"  [{ts_p}][SKIP-P10] {c.symbol} {c.name:<14} 시초가 "
                                f"{first_open:,.0f} → cur {cur:,.0f} "
                                f"(+{intraday_change_pct:.1f}% ≥ {MAX_INTRADAY_CHANGE_PCT}%) — 시초가 폭등 차단"
                            )
                            continue

                    day_high = max(b.high for b in day_bars)
                    last_bar = day_bars[-1]
                    proximity_pct = ((day_high - cur) / day_high * 100) if day_high > 0 else 0.0
                    MIN_HIGH_PROXIMITY_PCT = 1.5
                    # momentum 인정 조건 (둘 중 하나):
                    #   a) last_bar 가 H 봉 (모멘텀 진행 중)
                    #   b) cur 가 last_bar.high 이상 (새 봉이 직전 봉 high 초과 = 강세)
                    momentum_active = (
                        last_bar.high >= day_high - 1e-6
                        or cur >= last_bar.high - 1e-6
                    )
                    if proximity_pct < MIN_HIGH_PROXIMITY_PCT and not momentum_active:
                        ts_p = _now_kst().strftime("%H:%M:%S")
                        print(
                            f"  [{ts_p}][SKIP] {c.symbol} {c.name:<14} 일중 H "
                            f"{day_high:,.0f} vs cur {cur:,.0f} (거리 {proximity_pct:.2f}% "
                            f"< {MIN_HIGH_PROXIMITY_PCT}%) — 고점 인접 + 모멘텀 종료"
                        )
                        continue
            except Exception:
                pass  # 분봉 fetch 실패 시 통과 (보수적 fallback)
            signals.append((c, best_strategy, best_pnl))
            print(f"  [SIGNAL] {c.symbol} {c.name:<14} 전략={best_strategy} PnL={best_pnl:+,.0f} (w={weights.get(best_strategy, 1.0):.1f})")

    # 정제된 시그널을 파일에 저장 (대시보드 노출용)
    _save_refined_signals(signals, regime)

    if not signals:
        return 0

    # 자금 한도 체크
    deposit = await account.fetch_deposit()
    candidates_for_gate = [
        (c.symbol, c.name, Decimal(str(c.cur_price))) for c, _, _ in signals
    ]
    gate_result = evaluate_risk_gate(
        deposit=deposit, balance=balance,
        candidates=candidates_for_gate,
        max_per_position_ratio=Decimal(str(cfg.max_per_position)),
        max_total_position_ratio=Decimal(str(cfg.max_total_position)),
    )

    buyable = [(r, s) for r, (_, s, _) in zip(gate_result.recommendations, signals)
               if not r.blocked and r.recommended_qty > 0]

    if not buyable:
        return 0

    # 주문 실행
    notifier = TelegramNotifier.from_env() if args.telegram else None
    executor = KiwoomNativeOrderExecutor(oauth=oauth, dry_run=args.dry_run)
    gate = LiveOrderGate(
        executor=executor, audit_path=args.audit_log,
        policy=GatePolicy(
            daily_loss_limit_pct=Decimal(str(cfg.daily_loss_limit)),
            daily_max_orders=cfg.daily_max_orders,
        ),
        notifier=notifier,
    )

    executed = 0
    for r, strategy in buyable[:regime_max_buy]:
        tranche1_qty = max(1, round(r.recommended_qty * 0.5))
        try:
            result = await gate.place_buy(symbol=r.symbol, qty=tranche1_qty)
            tag = "DRY_RUN" if result.dry_run else "ORDERED"
            ts = _now_kst().strftime("%H:%M:%S")
            print(
                f"  [{ts}][{tag}] {r.symbol} {r.name:<14} qty={tranche1_qty}"
                f"(전체 {r.recommended_qty}) strategy={strategy} order_no={result.order_no}"
            )

            # active_positions 저장
            profile = STRATEGY_EXIT_PROFILES.get(strategy.replace("_v1", "").replace("_v2", ""), {})
            sl = float(profile.get("stop_loss_pct", -4.0))
            leader = next((c for c, _, _ in signals if c.symbol == r.symbol), None)
            pos_store.create_from_order(
                symbol=r.symbol, name=r.name,
                strategy=strategy,
                entry_price=float(r.cur_price),
                total_recommended_qty=r.recommended_qty,
                order_no=result.order_no,
                sl_pct=sl,
                flu_rate=float(leader.flu_rate) if leader else 0.0,
                score=float(leader.score) if leader else 0.0,
            )

            session_bought.add(r.symbol)
            # P6 — 매수 직후 즉시 cooldown 등록 (recent_buys 인자가 있으면)
            if recent_buys is not None:
                recent_buys[r.symbol] = _now_kst()

            if notifier:
                try:
                    await notifier.send(format_buy_alert(
                        r.symbol, r.name, tranche1_qty,
                        result.order_no, result.dry_run,
                    ))
                except Exception:
                    pass
            executed += 1
        except DailyOrderLimitExceeded:
            ts = _now_kst().strftime("%H:%M:%S")
            print(f"  [{ts}][BUY] 일일 거래수 한도 도달 — 매수 중단")
            break
        except Exception as e:
            print(f"  [BLOCKED] {r.symbol}: {e}")

    return executed


async def _save_balance_snapshot(oauth) -> None:
    """잔고 스냅샷을 balance_history.json에 추가 (일 1회)."""
    import json
    try:
        account = KiwoomNativeAccountFetcher(oauth=oauth)
        balance = await account.fetch_balance()
        deposit = await account.fetch_deposit()

        cash = float(deposit.cash)
        eval_total = sum(float(h.cur_price) * h.qty for h in (balance.holdings or []))
        total = cash + eval_total
        pos_count = len(balance.holdings or [])

        today_str = _now_kst().strftime("%Y-%m-%d")
        now_str = _now_kst().isoformat()

        path = _DATA_DIR / "balance_history.json"
        history: list[dict] = []
        if path.exists():
            try:
                history = json.loads(path.read_text(encoding="utf-8"))
            except Exception:
                pass

        # 오늘자 이미 있으면 업데이트, 없으면 추가
        existing = next((p for p in history if p.get("date") == today_str), None)
        entry = {
            "date": today_str,
            "cash": cash,
            "eval_total": eval_total,
            "total": total,
            "position_count": pos_count,
            "updated_at": now_str,
        }
        if existing:
            existing.update(entry)
        else:
            history.append(entry)

        path.write_text(json.dumps(history, ensure_ascii=False, indent=2), encoding="utf-8")
        ts = _now_kst().strftime("%H:%M:%S")
        print(f"  [{ts}][BALANCE] 스냅샷 저장: 총 {total:,.0f}원 (현금 {cash:,.0f} + 평가 {eval_total:,.0f})")
    except Exception as e:
        print(f"  [BALANCE-ERR] {e}")


async def _daemon(args):
    print(f"== 실시간 포지션 관리 데몬 (interval={args.interval}s, top={args.top}) ==")
    oauth = _build_oauth()
    notifier = TelegramNotifier.from_env() if args.telegram else None
    session_bought: set[str] = set()
    recent_buys: dict[str, datetime] = {}  # P6 — 매수 후 30분 재진입 금지
    total_bought = 0
    total_sold = 0

    # 장 시작 전이면 대기
    while not _in_market_hours():
        now = _now_kst()
        print(f"  [{now.strftime('%H:%M:%S')}] 장 시작 대기중...")
        await asyncio.sleep(60)

    print(f"  [{_now_kst().strftime('%H:%M:%S')}] 장중 감시 시작")

    # 장 시작 잔고 스냅샷
    await _save_balance_snapshot(oauth)

    while _in_market_hours():
        ts = _now_kst().strftime("%H:%M:%S")

        # 1) 매도 평가 (우선)
        try:
            sell_count = await _evaluate_and_sell(args, oauth, notifier)
            if sell_count > 0:
                total_sold += sell_count
                print(f"  [{ts}] 매도 {sell_count}건 (누적 {total_sold}건)")
        except Exception as e:
            print(f"  [{ts}][SELL-ERROR] {type(e).__name__}: {e}")

        # 2) 매수 스캔
        try:
            buy_count = await _scan_and_buy(args, oauth, session_bought, recent_buys)
            if buy_count > 0:
                total_bought += buy_count
                print(f"  [{ts}] 매수 {buy_count}건 (누적 {total_bought}건)")
        except Exception as e:
            print(f"  [{ts}][BUY-ERROR] {type(e).__name__}: {e}")

        # 다음 사이클까지 대기
        if _in_market_hours():
            await asyncio.sleep(args.interval)

    # 장 마감 잔고 스냅샷
    await _save_balance_snapshot(oauth)
    print(f"\n== 장 마감 — 데몬 종료 (매수 {total_bought}건, 매도 {total_sold}건) ==")


def main():
    ap = argparse.ArgumentParser(description="장중 시그널 매수 데몬 (동적 universe: 매 interval picker 재호출)")
    ap.add_argument("--interval", type=int, default=60, help="스캔 간격 초 (기본 60=1분)")
    ap.add_argument(
        "--top", type=int, default=5,
        help="스캔 후보 수 (기본 5, 시뮬 검증 — 후보 10 시 매매 빈도↑·net +0)",
    )
    ap.add_argument(
        "--min-flu", type=float, default=1.0,
        help="최소 등락률%% (기본 1.0 — 2026-05-18 시뮬 검증 후 3.0→1.0 완화. "
             "동적 universe 시뮬: min_flu 1.0 매매 10/승률 50%%, 0.0 매매 12/승률 58.3%%. "
             "운영 안전 마진 위해 1.0 default. 더 공격적이면 --min-flu 0 명시.)",
    )
    ap.add_argument("--dry-run", action="store_true", default=True)
    ap.add_argument("--no-dry-run", action="store_false", dest="dry_run")
    ap.add_argument("--telegram", action="store_true", help="텔레그램 알림")
    ap.add_argument("--audit-log", default=str(_DATA_DIR / "order_audit.csv"))
    ap.add_argument("--pos-log", default=str(_DATA_DIR / "active_positions.json"))
    args = ap.parse_args()

    loop = asyncio.new_event_loop()
    task: asyncio.Task | None = None

    def _shutdown():
        nonlocal task
        if task and not task.done():
            task.cancel()

    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, _shutdown)

    try:
        task = loop.create_task(_daemon(args))
        loop.run_until_complete(task)
    except (KeyboardInterrupt, SystemExit, asyncio.CancelledError):
        print("\n데몬 종료.")
    finally:
        loop.close()


if __name__ == "__main__":
    main()
