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
from backend.core.supertrend_auto_trader import (
    SupertrendAutoTrader, SupertrendAutoConfig,
)

KST = timezone(timedelta(hours=9))
MARKET_OPEN = time(9, 5)       # 시초가 안정 후 매수 시작 (08:58→09:05)
MARKET_CLOSE = time(15, 20)
BUY_START = time(9, 5)         # 매수는 09:05 이후만 (일반 전략: f_zone/sf_zone/gold_zone)
SELL_START = time(9, 1)        # 매도 평가는 09:01부터
# 슈퍼트렌드(시그널 전략)는 09:00 정규장 개장부터 진입 — 09:05/09:30 매수시간 규칙의 예외.
#   --supertrend 활성 시 데몬 루프를 09:00 부터 가동(일반 전략은 BUY_START 09:05 자체 게이트로 보호).
SUPERTREND_OPEN = time(9, 0)
MIN_HOLD_MINUTES = 15          # 매수 후 최소 보유 (P7 5/20: 10→15, 노이즈 SL 회피)
MAX_BUY_PER_CYCLE = 2          # 사이클당 최대 매수 2종목
BUY_REENTRY_COOLDOWN_MIN = 30  # P6 (2026-05-20): 매수 후 동일 종목 재진입 금지 (30분)
HARD_SL_PCT = -5.0             # P7 (2026-05-20): cooldown 안 극한 SL 우회 임계
_MAX_FLU_RATE = 30.0           # 급등 추격매수 차단 등락률 상한(%). 2026-06-01 25→30
                               #   완화 — 강세장 +29%대 주도주 진입 허용, 상한가만 차단.


def _env_truthy(name: str, default: str = "") -> bool:
    """BAR-OPS-37 — env 토글(1/true/yes/on 이면 True)."""
    return os.environ.get(name, default).strip().lower() in {"1", "true", "yes", "on"}


# BAR-OPS-33 (2026-06-08): 안정성 1위 swing_38 라이브 활성화. 4~6월 백테스트 최우수
#   (승률55%·손익비7.36·기대값+5.34). 다일보유 청산은 STRATEGY_EXIT_PROFILES["swing_38"]
#   (min_hold 3/max_hold 20)이 holding_evaluator 에서 게이트 → 당일 강제청산 안 됨.
DEFAULT_ZONE_STRATEGIES = ["swing_38", "f_zone", "sf_zone", "gold_zone"]


def _parse_strategies(raw: str) -> list[str]:
    """--strategies 문자열 → 일반 매수 전략 리스트.

    빈 값/none/off → [] (일반 전략 비활성 = 슈퍼트렌드 단독 운영). 그 외는 쉼표분리.
    (BAR-OPS-10 2026-06-03: 6/2 'supertrend만' 의도인데 f_zone/gold_zone 가 매매된 문제 →
     데몬에서 일반 전략을 끌 수 있게 옵션화.)
    """
    r = (raw or "").strip().lower()
    if r in {"", "none", "off"}:
        return []
    return [s.strip() for s in raw.split(",") if s.strip()]


def _supertrend_yield_to_bot(want_supertrend: bool, env: dict | None = None) -> bool:
    """run_telegram_bot(SupertrendAutoTrader)이 슈퍼트렌드 담당 중이면 데몬은 양보(False).

    SUPERTREND_AUTO_ENABLED 가 truthy 면 슈퍼트렌드 주문은 봇이 담당 → 데몬 --supertrend 를
    꺼서 **이중 주문(중복 진입)** 을 방지한다. 그 외엔 요청값 그대로.
    """
    env = env if env is not None else os.environ
    truthy = (env.get("SUPERTREND_AUTO_ENABLED", "") or "").strip().lower() in {"1", "true", "yes", "on"}
    return False if (want_supertrend and truthy) else want_supertrend


def _now_kst() -> datetime:
    return datetime.now(KST)


def _compute_daily_pnl_pct(current_total: float) -> Decimal:
    """[DEPRECATED 2026-06-02] balance_history.json 스냅샷 기반 일일손익률 — 더 이상
    일일손실 게이트 입력으로 쓰지 않는다(호출처 제거). 스냅샷 오염으로 가짜 손실
    KillSwitch 오발동이 사흘 연속(5/29·6/1·6/2) 발생해, 게이트 입력을 브로커 실시간
    balance.total_pnl_rate 로 일원화함. 본 함수는 참고용으로만 보존(미사용).

    전일 마감 총자산 대비 당일 손익률(%) 계산.

    balance_history.json 에서 직전 거래일 마감 잔고를 읽어 current_total 과 비교.

    기준은 직전 항목의 **total**(현금+평가 총자산). 호출부 current_total 이
    `deposit.cash + balance.total_eval`(오늘 현금+평가=총자산)이므로, 비교 기준도
    반드시 같은 '총자산'이어야 대칭이 맞는다(2026-06-02 fix).

    이력:
    - (2026-06-01) total→cash 변경: 당시 전일 장중 평가가 청산돼 현금화된 것을
      손실로 오인하는 문제 회피 목적이었으나, current_total 은 평가 포함 총자산이라
      prev=cash 와 기준 불일치(사과 vs 오렌지) → 전일 평가액만큼 가짜 손실
      (-26% 등)이 발생했다.
    - (2026-06-02) total 로 환원해 대칭 복원. 전일 장중 평가 오염 문제는 supertrend
      가 강제청산 제외(540724e)되어 마감 스냅샷 평가액이 정상 보유분이므로 해소.
      total 누락된 옛 항목은 cash 로 폴백.

    데이터 없거나 파싱 실패 시 Decimal("0.0") 반환 (fail-open).
    """
    import json as _json
    path = _DATA_DIR / "balance_history.json"
    if not path.exists():
        return Decimal("0.0")
    try:
        history = _json.loads(path.read_text(encoding="utf-8"))
        today_str = _now_kst().strftime("%Y-%m-%d")
        # 직전 거래일 항목 (오늘 제외, 양수 기준값). 주말 갭이 있어도 가장 최근 거래일.
        prev_entry = next(
            (e for e in reversed(history)
             if e.get("date") != today_str
             and (e.get("total", 0) > 0 or e.get("cash", 0) > 0)),
            None,
        )
        if prev_entry is None:
            return Decimal("0.0")
        # total(총자산=현금+평가) 우선 — current_total 과 동일 기준. 누락 시 cash 폴백.
        prev_base = float(prev_entry.get("total") or prev_entry.get("cash") or 0)
        if prev_base <= 0:
            return Decimal("0.0")
        pnl_pct = (current_total - prev_base) / prev_base * 100.0
        return Decimal(str(round(pnl_pct, 4)))
    except Exception:
        return Decimal("0.0")


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


async def _sync_positions(pos_store: ActivePositionStore, held_symbols: set[str],
                          pending_symbols: "set[str] | None" = None) -> int:
    """브로커 잔고와 active_positions 동기화. 잔고에 없는 종목은 제거.

    2026-06-08: 발주 직후 '접수' 상태로 아직 미체결인 주문(ka10075 oso)이 있는
    종목은 잔고0이어도 제거하지 않는다 — 접수정체/체결지연을 SYNC 가 조용히
    지워버리던 문제(접수정체 인시던트) 차단.
    """
    pending_symbols = pending_symbols or set()
    active = pos_store.load_all()
    removed = 0
    for sym in list(active.keys()):
        if sym in held_symbols:
            continue
        if sym in pending_symbols:
            ts = _now_kst().strftime("%H:%M:%S")
            print(f"  [{ts}][SYNC] {sym} {active[sym].name} — 잔고엔 없으나 미체결(접수) 존재 → 보존")
            continue
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

    # 브로커 잔고 ↔ active_positions 동기화 (미체결 접수 주문은 보존)
    pos_store = ActivePositionStore(args.pos_log)
    held_symbols = {h.symbol for h in (balance.holdings or [])}
    try:
        _open = await account.fetch_open_orders()   # ka10075 oso
        pending_symbols = {o.symbol for o in _open if o.pending_qty > 0}
        await _sync_positions(pos_store, held_symbols, pending_symbols)
    except Exception as _e:
        # 미체결 조회 실패 → 접수정체를 잘못 지울 위험. 이번 사이클 SYNC 제거 보류.
        print(f"  [SYNC-SKIP] 미체결 조회 실패({type(_e).__name__}) — 제거 보류")

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
        # BAR-166: DCA는 방어적 매수 — 일일 손실 한도 적용 불필요.
        policy=GatePolicy(daily_loss_limit_pct=Decimal("-100.0"),
                          daily_max_orders=cfg.daily_max_orders),
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
        # BAR-OPS-33: swing_38 등 자체 분할진입 전략은 데몬 DCA 무조건 스킵(이중분할 방지).
        if pos.strategy in _NO_DCA_STRATEGIES:
            ts_d = _now_kst().strftime("%H:%M:%S")
            print(f"  [{ts_d}][DCA-SKIP] {h.symbol} {pos.strategy} 자체분할전략 — 데몬 DCA 비활성")
            continue
        # ⑧ (2026-05-30): 되돌림(바닥) 전략 gold 는 하락 중 DCA(물타기) 비활성 — 약전략의
        #   추세하락 평단 물타기가 손실을 키움(5/29 한온시스템 gold 고점매수→DCA→ -6%).
        #   --dca-strategy-gate 시 활성. 기본 off(동작 불변).
        if getattr(args, "dca_strategy_gate", False) and pos.strategy in _MEANREV_STRATEGIES:
            ts_d = _now_kst().strftime("%H:%M:%S")
            print(f"  [{ts_d}][DCA-SKIP] {h.symbol} {pos.strategy} 되돌림전략 — DCA(물타기) 비활성")
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
                # 고도화 #3 (2026-05-30): DCA buy 에 보유 포지션 전략 전파.
                # 미전달 시 strategy_id 가 빈칸으로 기록돼 전략별 실현손익 귀속이
                # 'unknown' 버킷에 격리됨(5/29 018880 DCA 행 빈칸). pos 는 232줄 로드.
                r = await gate.place_buy(
                    symbol=h.symbol, qty=tranche.qty, strategy_id=pos.strategy
                )
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
            # Phase D2.6: strategy_id 전파 — active_positions 메타에서 조회 (없으면 None)
            _ap = active_positions.get(d.symbol) if isinstance(active_positions, dict) else None
            _strategy = getattr(_ap, "strategy", None) if _ap else None
            r = await gate.place_sell(symbol=d.symbol, qty=sell_qty,
                                      strategy_id=_strategy)
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


# ── 고도화 #6 (2026-05-30): 진입 시 전략 analyze() 재검증 게이트 ──────────
#   문제: 데몬은 일봉 sim 으로 전략을 선정하나 진입 시점에 그 전략의 진입조건을
#   재검증하지 않아, gold(바닥매수)가 장중 고점에 진입(5/29 한온시스템 -6%).
#   해결: 진입 직전 선정 전략을 '분봉' 컨텍스트로 analyze() 재호출 → None 이면 skip.
#   분봉 적정 min_atr=0.01 (일봉 0.035 와 분리 — 0.035 는 분봉서 신호 전멸).
#   안전: 데이터 부족/예외 시 보수적 통과(over-block 방지). enforce 는 --entry-revalidate.

_REVAL_MIN_BARS = 120          # 재검증 최소 분봉 수 (f_zone for_intraday min_candles)
_REVAL_WINDOW = 200            # 사용할 최근 분봉 수
_REVAL_MIN_ATR = 0.01          # 분봉 적정 변동성 임계
# ⑦/⑧ — 되돌림(바닥매수) 전략: 고점근접 무조건 차단 + DCA(물타기) 비활성 대상.
_MEANREV_STRATEGIES = {"gold_zone"}
# BAR-OPS-33: 데몬 DCA(물타기) 무조건 비활성 전략 — swing_38 은 자체 2차분할(add_on_signal)을
#   사용하므로 데몬 tranche DCA 와 겹치면 이중 분할이 된다. 따라서 데몬 DCA 에서 제외.
_NO_DCA_STRATEGIES = {"swing_38"}


def _build_reval_strategy(strategy_id: str):
    """진입 재검증용 분봉 전략 인스턴스 (분봉 min_atr 0.01)."""
    from backend.core.strategy.f_zone import FZoneStrategy, FZoneParams
    from backend.core.strategy.sf_zone import SFZoneStrategy
    from backend.core.strategy.gold_zone import GoldZoneStrategy, GoldZoneParams
    if strategy_id in ("f_zone", "sf_zone"):
        p = FZoneParams.for_intraday()
        p.min_atr_pct = _REVAL_MIN_ATR
        return SFZoneStrategy(p) if strategy_id == "sf_zone" else FZoneStrategy(p)
    if strategy_id == "gold_zone":
        return GoldZoneStrategy(GoldZoneParams(min_atr_pct=_REVAL_MIN_ATR))
    return None


def _revalidate_entry(strategy_id: str, symbol: str, name: str, minute_bars: list):
    """선정 전략을 분봉으로 analyze 재호출. 반환 (ok: bool, reason: str).

    ok=False(=진입조건 미충족)만 명확한 차단 신호. 데이터 부족/예외는 ok=True(보수적 통과).
    """
    strat = _build_reval_strategy(strategy_id)
    if strat is None:
        return True, "미지원전략-통과"
    # forming(미완성) 마지막 봉 제외 → 최근 _REVAL_WINDOW 봉
    bars = minute_bars[:-1] if len(minute_bars) > 1 else minute_bars
    window = bars[-_REVAL_WINDOW:]
    if len(window) < _REVAL_MIN_BARS:
        return True, f"분봉부족({len(window)})-통과"
    from backend.models.strategy import AnalysisContext
    from backend.models.market import MarketType
    try:
        ctx = AnalysisContext(symbol=symbol, name=name, candles=window, market_type=MarketType.STOCK)
        sig = strat.analyze(ctx)
    except Exception as exc:  # noqa: BLE001
        return True, f"analyze예외-통과({type(exc).__name__})"
    if sig is None:
        return False, "진입조건 미충족"
    return True, "통과"


def _is_leverage_or_inverse(symbol: str, name: str) -> bool:
    """레버리지/인버스 ETF 또는 ETN 판정 — zone 진입 제외용.
    backend SupertrendAutoTrader._is_leverage_or_inverse 와 동일 로직(데몬 복제)."""
    nm = name or ""
    if any(k in nm for k in ("레버리지", "인버스", "곱버스", "2X", "2x")):
        return True
    if any(ch.isalpha() for ch in (symbol or "")):  # ETN: 코드에 영문자
        return True
    return False


def _is_etf_or_etn(symbol: str, name: str) -> bool:
    """KRX ETF/ETN/리츠 등 펀드형 판정 — 개별주만 허용(ETF류 전면 차단)용.
    backend SupertrendAutoTrader._is_etf_or_etn 와 동일 로직(데몬 복제).
    True=펀드형 → 차단 / False=개별주(스팩·우선주 포함) → 허용."""
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


async def _scan_and_buy(
    args, oauth, session_bought: set[str],
    recent_buys: dict[str, datetime] | None = None,
) -> int:
    """한 사이클: 스캔 → 시그널 검증 → 매수. 매수 건수 반환."""
    # 매수는 BUY_START 이후만
    if _now_kst().time() < BUY_START:
        return 0
    # 일반 매수 전략 비활성(--strategies 빈 값) → 스캔 자체를 건너뜀(슈퍼트렌드 단독 운영).
    zone_strategies = getattr(args, "zone_strategies", DEFAULT_ZONE_STRATEGIES)
    if not zone_strategies:
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
    # 급등 추격매수 방지 필터 — 등락률이 _MAX_FLU_RATE 이상인 종목 제외.
    # 2026-06-01: 25.0→30.0 완화. 강세장(코스피 급등)에서 +29%대 주도주(LG전자·
    # 두산로보틱스 등)가 25% 게이트에 막혀 진입 기회를 전부 놓치던 문제. 상한가(+30%)
    # 직전까지는 진입 허용하되, 상한가 도달분만 차단(추격매수 손실 위험 한계선).
    _excl_lev = _env_truthy("SUPERTREND_AUTO_EXCLUDE_LEVERAGE")
    _excl_etf = _env_truthy("SUPERTREND_AUTO_EXCLUDE_ETF")
    filtered = [c for c in leaders if c.symbol not in excluded
                and c.flu_rate < _MAX_FLU_RATE and c.cur_price >= 5_000
                and not (_excl_lev and _is_leverage_or_inverse(c.symbol, c.name))
                and not (_excl_etf and _is_etf_or_etn(c.symbol, c.name))]

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
        regime_max_buy = 2  # P8→D2.1 — 박스권 보수 운영 (1→2건 완화, 단타 시그널 확보)
        print(f"  [{ts_r}][REGIME] SIDEWAYS — 보수 운영, 최대 2건")

    # 전략 시뮬레이션 시그널 검증 (--strategies 로 선택, 기본 f_zone/sf_zone/gold_zone)
    sim = IntradaySimulator()
    strategies = zone_strategies
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
                    # ⑦ (2026-05-30): momentum 예외는 모멘텀형(f/sf)에만 적용. gold(되돌림/바닥
                    #   전략)는 고점근접 시 momentum 이어도 무조건 차단 — 바닥전략의 고점진입 방지.
                    #   #6 의 보조 방어선(분봉 fetch/analyze 실패 시에도 작동). --entry-revalidate 활성.
                    if getattr(args, "entry_revalidate", False) and best_strategy in _MEANREV_STRATEGIES:
                        momentum_active = False
                    if proximity_pct < MIN_HIGH_PROXIMITY_PCT and not momentum_active:
                        ts_p = _now_kst().strftime("%H:%M:%S")
                        print(
                            f"  [{ts_p}][SKIP] {c.symbol} {c.name:<14} 일중 H "
                            f"{day_high:,.0f} vs cur {cur:,.0f} (거리 {proximity_pct:.2f}% "
                            f"< {MIN_HIGH_PROXIMITY_PCT}%) — 고점 인접 + 모멘텀 종료"
                        )
                        continue
                # ⑥ 진입 재검증 게이트 — 일봉 sim 선정 전략을 분봉으로 analyze 재호출.
                #   진입 시점에 진입조건이 깨졌으면(gold 고점 등) None → skip. 항상 shadow
                #   로그, --entry-revalidate 시에만 enforce(continue). 보수적 통과(데이터부족/예외).
                reval_ok, reval_reason = _revalidate_entry(
                    best_strategy, c.symbol, c.name, minute_bars
                )
                if not reval_ok:
                    ts_v = _now_kst().strftime("%H:%M:%S")
                    enforce = getattr(args, "entry_revalidate", False)
                    print(
                        f"  [{ts_v}][{'SKIP-REVAL' if enforce else 'SHADOW-REVAL'}] "
                        f"{c.symbol} {c.name:<14} 전략={best_strategy} "
                        f"진입조건 재검증 실패 ({reval_reason})"
                    )
                    if enforce:
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

    # 일일손실 게이트 입력 — 브로커 실시간 계좌 평가수익률(total_pnl_rate)을 사용.
    # 2026-06-02 근본수정: 종전 balance_history.json(파일 스냅샷) 기반
    #   _compute_daily_pnl_pct 는 마감 스냅샷이 미청산 평가/주말갭/강제청산 타이밍으로
    #   오염되면 가짜 손실(-8.45%·-26.24% 등)로 KillSwitch 를 오발동시켜 사흘 연속
    #   (5/29·6/1·6/2) 매수 전면차단 사고를 냈다. 파일 의존을 끊고, 잔고 조회의
    #   total_pnl_rate(계좌 전체 평가수익률 %)로 일원화한다. SupertrendAutoTrader
    #   (_account_pnl_pct)가 이미 쓰는 방식과 동일 — 두 매매 경로 게이트 입력 통일.
    #   '누적 평가수익률'이라 엄밀한 당일손익과는 다르나, 손실 시 매수 차단이라는
    #   안전 방향엔 부합하며 파일 오염 리스크가 없다(보수적·결정적).
    daily_pnl_pct = Decimal(str(getattr(balance, "total_pnl_rate", 0) or 0))

    # 주문 실행
    notifier = TelegramNotifier.from_env() if args.telegram else None
    executor = KiwoomNativeOrderExecutor(oauth=oauth, dry_run=args.dry_run)
    gate = LiveOrderGate(
        executor=executor, audit_path=args.audit_log,
        policy=GatePolicy(
            daily_loss_limit_pct=Decimal(str(cfg.daily_loss_limit)),
            daily_max_orders=cfg.daily_max_orders,
            # ── BAR-OPS-35 env 토글 (전부 기본 OFF — env 미설정 시 동작 불변) ──
            daily_loss_latch=_env_truthy("SUPERTREND_AUTO_LOSS_LATCH"),
            order_retry_count=int(os.environ.get("SUPERTREND_AUTO_ORDER_RETRY", "0")),
            order_retry_backoff_sec=float(os.environ.get("SUPERTREND_AUTO_ORDER_RETRY_BACKOFF", "0")),
            retry_sell_only=_env_truthy("SUPERTREND_AUTO_RETRY_SELL_ONLY", "1"),
        ),
        notifier=notifier,
    )

    executed = 0
    for r, strategy in buyable[:regime_max_buy]:
        tranche1_qty = max(1, round(r.recommended_qty * 0.6))
        try:
            # Phase D2.6: strategy_id 전파 (order_audit.csv 신규 컬럼)
            result = await gate.place_buy(symbol=r.symbol, qty=tranche1_qty,
                                          daily_pnl_pct=daily_pnl_pct,
                                          strategy_id=strategy)
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


# ── 슈퍼트렌드(시그널 전략) 자동매매 — 09:00 개장부터 예외 진입 ────────────────
# 일반 전략(f_zone/sf_zone/gold_zone)은 BUY_START(09:05) 게이트로 보호되지만,
# 슈퍼트렌드는 "특정 매매 시그널(BUY 전환) 발생 시 즉시" 진입하는 시그널 전략이라
# 09:00 정규장 개장부터 진입을 허용한다(사용자 요청, 2026-06-01).
#
# 검증된 SupertrendAutoTrader(backend/core/supertrend_auto_trader.py)를 데몬과 동일한
# 실거래 인프라(LeaderPicker universe / LiveOrderGate / ActivePositionStore /
# AccountFetcher)로 인스턴스화해 매 사이클 run_cycle() 1회 실행. 진입+청산 모두 포함.
#   - universe: 데몬과 동일한 KiwoomNativeLeaderPicker(당일 주도주 top-N) 결과를 사용.
#   - 청산: strategy="supertrend" 포지션의 5분봉 SELL 전환 시 자동 매도(자체 처리).
#   - 안전: market_hours_only=True(정규장 가드), dry_run 은 args.dry_run 그대로 전파.
_supertrend_trader: "SupertrendAutoTrader | None" = None


def _get_supertrend_trader(args, oauth, notifier) -> "SupertrendAutoTrader":
    """SupertrendAutoTrader 싱글턴 — 데몬 인프라로 1회 구성 후 재사용."""
    global _supertrend_trader
    if _supertrend_trader is not None:
        return _supertrend_trader

    candle_fetcher = KiwoomNativeCandleFetcher(oauth=oauth)
    account_fetcher = KiwoomNativeAccountFetcher(oauth=oauth)
    executor = KiwoomNativeOrderExecutor(oauth=oauth, dry_run=args.dry_run)
    gate = LiveOrderGate(
        executor=executor, audit_path=args.audit_log,
        policy=GatePolicy(daily_max_orders=int(os.environ.get("SUPERTREND_AUTO_MAX_ORDERS", "50"))),  # [0609 임시해제]
        notifier=notifier,
    )
    pos_store = ActivePositionStore(args.pos_log)
    # _scan_and_buy 와 동일한 검증된 LeaderPicker 구성 (min_score 포함).
    _st_cfg = PolicyConfigStore(str(_DATA_DIR / "policy.json")).load()
    picker = KiwoomNativeLeaderPicker(
        oauth=oauth, min_flu_rate=args.min_flu, min_score=_st_cfg.min_score,
    )

    async def _universe_provider() -> list[tuple[str, str]]:
        """데몬과 동일한 당일 주도주 top-N 을 (symbol, name) 으로 반환."""
        try:
            leaders = await picker.pick(top_n=args.supertrend_top)
            return [(c.symbol, c.name) for c in leaders]
        except Exception as e:  # noqa: BLE001
            print(f"  [ST-UNIVERSE-ERR] {type(e).__name__}: {e}")
            return []

    _supertrend_trader = SupertrendAutoTrader(
        candle_fetcher=candle_fetcher,
        account_fetcher=account_fetcher,
        order_gate=gate,
        pos_store=pos_store,
        universe_provider=_universe_provider,
        # notifier 미주입 — SupertrendAutoTrader._notify 는 send(level,title,body) 3-arg
        # 시그니처를 기대하나 데몬 TelegramNotifier.send 는 1-arg(message)라 불일치.
        # 체결 가시성은 _run_supertrend_cycle 의 console 출력 + order_audit.csv 로 확보.
        # (LiveOrderGate 는 notifier=notifier 유지 → blocked 알림은 정상 발송.)
        notifier=None,
        config=SupertrendAutoConfig(
            max_positions=args.supertrend_max_pos,
            universe_max=args.supertrend_top,
            market_hours_only=True,  # 정규장(09:00~15:20) 에서만 — 09:00 개장 즉시 진입
            # ── BAR-OPS-35 가드 env 토글 (전부 기본 OFF/dataclass 기본 → env 미설정 시 동작 불변) ──
            hard_stop_pct=float(os.environ.get("SUPERTREND_AUTO_HARD_STOP", "0")),
            max_entries_per_symbol_day=int(os.environ.get("SUPERTREND_AUTO_MAX_ENTRIES", "0")),
            reentry_cooldown_min=int(os.environ.get("SUPERTREND_AUTO_REENTRY_COOLDOWN", "0")),
            block_reentry_after_loss=_env_truthy("SUPERTREND_AUTO_BLOCK_REENTRY_LOSS"),
            max_atr_pct_for_entry=float(os.environ.get("SUPERTREND_AUTO_MAX_ATR_PCT", "0")),
            take_profit_trail_only=_env_truthy("SUPERTREND_AUTO_TP_TRAIL_ONLY"),
            vol_halve_atr_pct=float(os.environ.get("SUPERTREND_AUTO_VOL_HALVE_ATR", "0")),
            single_tranche=_env_truthy("SUPERTREND_AUTO_SINGLE_TRANCHE"),
            max_entry_gap_pct=float(os.environ.get("SUPERTREND_AUTO_MAX_ENTRY_GAP", "0")),
            # ── BAR-OPS-36 Runner env 토글 ──
            runner_enabled=_env_truthy("SUPERTREND_AUTO_RUNNER"),
            runner_limit_up_pct=float(os.environ.get("SUPERTREND_AUTO_RUNNER_LIMIT_UP", "29")),
            runner_gap_up_pct=float(os.environ.get("SUPERTREND_AUTO_RUNNER_GAP_UP", "0")),
            runner_giveback_pct=float(os.environ.get("SUPERTREND_AUTO_RUNNER_GIVEBACK", "3")),
            runner_giveback_atr_mult=float(os.environ.get("SUPERTREND_AUTO_RUNNER_GIVEBACK_ATR", "0")),
            runner_profit_lock_pct=float(os.environ.get("SUPERTREND_AUTO_RUNNER_LOCK", "2")),
            runner_gap_partial_ratio=float(os.environ.get("SUPERTREND_AUTO_GAP_PARTIAL", "0")),
            runner_gap_partial_min_pct=float(os.environ.get("SUPERTREND_AUTO_GAP_PARTIAL_MIN", "3")),
            runner_gap_partial_window_bars=int(os.environ.get("SUPERTREND_AUTO_GAP_PARTIAL_WINDOW", "6")),
        ),
    )
    return _supertrend_trader


async def _run_supertrend_cycle(args, oauth, notifier) -> dict:
    """슈퍼트렌드 진입+청산 1 사이클. 반환 {entered:[...], exited:[...]}."""
    trader = _get_supertrend_trader(args, oauth, notifier)
    result = await trader.run_cycle()
    ts = _now_kst().strftime("%H:%M:%S")
    entered = result.get("entered", [])
    exited = result.get("exited", [])
    for e in entered:
        tag = "DRY_RUN" if e.get("dry_run") else "ORDERED"
        print(f"  [{ts}][ST-{tag}] {e['symbol']} qty={e['qty']} @{e.get('price', 0):,.0f} "
              f"strategy=supertrend order_no={e.get('order_no', '')}")
    for x in exited:
        tag = "DRY_RUN" if x.get("dry_run") else "SOLD"
        print(f"  [{ts}][ST-{tag}] {x['symbol']} qty={x['qty']} (SELL 전환) "
              f"order_no={x.get('order_no', '')}")
    # Heartbeat — 진입·청산 0건이어도 사이클이 돌았음을 항상 1줄 남긴다(모니터링 가시성).
    #   진입 0건의 정상 사유(전 종목 추세 한가운데, 최근 전환봉 없음)와 사이클 멈춤을 구분.
    held = trader._pos.load_all()
    st_held = sum(1 for p in held.values()
                  if (getattr(p, "strategy", "") or "").startswith("supertrend"))
    print(f"  [{ts}][ST-CYCLE] 평가완료 — 진입 {len(entered)} / 청산 {len(exited)} "
          f"/ 슈퍼트렌드 보유 {st_held}종목 (universe top={args.supertrend_top})")
    return result


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

    # 슈퍼트렌드 활성 시 데몬 가동 개시를 09:00(개장)로 앞당김 — 09:00~09:05 사이에도
    # supertrend 시그널 진입이 가능하게 한다. 일반 전략은 _scan_and_buy 내부 BUY_START
    # (09:05) 게이트가 그대로 작동하므로 09:05 이전엔 일반 매수가 나가지 않는다.
    daemon_open = SUPERTREND_OPEN if args.supertrend else MARKET_OPEN

    def _daemon_hours() -> bool:
        return daemon_open <= _now_kst().time() <= MARKET_CLOSE

    if args.supertrend:
        print(f"  [슈퍼트렌드] 활성 — 09:00 개장부터 BUY 전환 시그널 진입 "
              f"(top={args.supertrend_top}, max_pos={args.supertrend_max_pos}, "
              f"interval={args.supertrend_interval}s)")

    # 슈퍼트렌드는 5분봉 전략 — 매 데몬 사이클(60s)마다 top-N×5분봉을 조회하면 일반
    # 전략 호출과 겹쳐 Kiwoom API rate-limit(429)을 유발. 5분봉은 5분마다 1봉만
    # 갱신되므로 supertrend 평가를 supertrend_interval(기본 300s)마다로 throttle 한다.
    last_st_run: datetime | None = None

    # 장 시작 전이면 대기
    while not _daemon_hours():
        now = _now_kst()
        print(f"  [{now.strftime('%H:%M:%S')}] 장 시작 대기중...")
        await asyncio.sleep(60)

    print(f"  [{_now_kst().strftime('%H:%M:%S')}] 장중 감시 시작")

    # 장 시작 잔고 스냅샷
    await _save_balance_snapshot(oauth)

    while _daemon_hours():
        ts = _now_kst().strftime("%H:%M:%S")

        # 1) 매도 평가 (우선)
        try:
            sell_count = await _evaluate_and_sell(args, oauth, notifier)
            if sell_count > 0:
                total_sold += sell_count
                print(f"  [{ts}] 매도 {sell_count}건 (누적 {total_sold}건)")
        except Exception as e:
            print(f"  [{ts}][SELL-ERROR] {type(e).__name__}: {e}")

        # 2) 매수 스캔 (일반 전략 f_zone/sf_zone/gold_zone — 내부 BUY_START 09:05 게이트)
        try:
            buy_count = await _scan_and_buy(args, oauth, session_bought, recent_buys)
            if buy_count > 0:
                total_bought += buy_count
                print(f"  [{ts}] 매수 {buy_count}건 (누적 {total_bought}건)")
        except Exception as e:
            print(f"  [{ts}][BUY-ERROR] {type(e).__name__}: {e}")

        # 3) 슈퍼트렌드(시그널 전략) — 09:00 개장부터 BUY 전환 시그널 진입/청산.
        #    5분봉 전략이므로 supertrend_interval(기본 300s)마다만 평가 → 429 회피.
        if args.supertrend:
            now_kst = _now_kst()
            due = (last_st_run is None
                   or (now_kst - last_st_run).total_seconds() >= args.supertrend_interval)
            if due:
                last_st_run = now_kst
                try:
                    st_result = await _run_supertrend_cycle(args, oauth, notifier)
                    st_in = len(st_result.get("entered", []))
                    st_out = len(st_result.get("exited", []))
                    if st_in or st_out:
                        total_bought += st_in
                        total_sold += st_out
                        print(f"  [{ts}] 슈퍼트렌드 진입 {st_in}건 / 청산 {st_out}건")
                except Exception as e:
                    print(f"  [{ts}][ST-ERROR] {type(e).__name__}: {e}")

        # 다음 사이클까지 대기
        if _daemon_hours():
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
    ap.add_argument(
        "--entry-revalidate", action="store_true",
        help="고도화 #6/#7: 진입 시 선정 전략을 분봉으로 재검증(조건 미충족 시 skip) + "
             "gold(되돌림) 고점근접 무조건 차단. 미지정 시 #6 은 shadow 로그만(동작 불변).",
    )
    ap.add_argument(
        "--dca-strategy-gate", action="store_true",
        help="고도화 #8: gold(되돌림/바닥) 전략 포지션의 DCA(물타기) 비활성 — 약전략 "
             "추세하락 평단 물타기 손실 확대 방지. 기본 off(동작 불변).",
    )
    # ── 슈퍼트렌드(시그널 전략) — 09:00 개장부터 예외 진입 ────────────────────
    ap.add_argument(
        "--supertrend", action="store_true",
        help="슈퍼트렌드 시그널 전략 자동매매 활성 — 09:00 개장 즉시 BUY 전환 시그널 "
             "발생 시 진입(09:05/09:30 매수시간 규칙 예외). 미지정 시 off(기존 동작 불변).",
    )
    ap.add_argument(
        "--supertrend-top", type=int, default=10,
        help="슈퍼트렌드 진입 스캔 유니버스(당일 주도주 top-N). 기본 10 "
             "(20→10 축소, API rate-limit 완화).",
    )
    ap.add_argument(
        "--supertrend-max-pos", type=int, default=10,
        help="슈퍼트렌드 동시 보유 종목 상한. 기본 10.",
    )
    ap.add_argument(
        "--supertrend-interval", type=int, default=300,
        help="슈퍼트렌드 평가 주기(초). 5분봉 전략이라 기본 300(5분) — 매 데몬 "
             "사이클마다 top-N×5분봉 조회로 인한 Kiwoom 429 rate-limit 회피.",
    )
    ap.add_argument(
        "--strategies", default="f_zone,sf_zone,gold_zone",
        help="일반 매수 스캔 전략(쉼표구분). 빈 값/none → 일반 전략 비활성(슈퍼트렌드 단독). "
             "예: --strategies '' --supertrend (슈퍼트렌드만 운영).",
    )
    args = ap.parse_args()

    # 일반 매수 전략 파싱 + 슈퍼트렌드 이중가동 가드 (BAR-OPS-10).
    args.zone_strategies = _parse_strategies(args.strategies)
    _want_st = args.supertrend
    args.supertrend = _supertrend_yield_to_bot(args.supertrend)
    if _want_st and not args.supertrend:
        print("  [GUARD] SUPERTREND_AUTO_ENABLED 감지 — 슈퍼트렌드는 run_telegram_bot 담당. "
              "데몬 --supertrend 비활성(이중 주문 방지).")
    if not args.zone_strategies:
        print("  [전략] 일반 매수 전략 비활성 — 슈퍼트렌드 단독 운영.")
    else:
        print(f"  [전략] 일반 매수 스캔: {', '.join(args.zone_strategies)}")

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
