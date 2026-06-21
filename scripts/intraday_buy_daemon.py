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
import json
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
from backend.core.strategy.dante_filters import DistributionExitConfig
from backend.core.risk.daily_gate_input import compute_daily_gate_input
from backend.core.risk.live_order_gate import (
    GatePolicy, LiveOrderGate, DailyOrderLimitExceeded,
)
from backend.core.backtester.market_regime import (
    MarketRegime, classify_regime, regime_weights,
)
from backend.core.strategy.trap_guard import TrapGuardConfig, evaluate_trap_guard
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
# 급등 추격매수 차단 등락률 상한(%). 2026-06-01 25→30 완화(강세장 +29%대 주도주 허용,
#   상한가만 차단 의도) → [BAR-OPS-38 P0#3] 29.5 로 조정: 상한가 잠김 종목은 등락률이
#   +29.8~29.9% 로 표시돼 30.0 게이트를 통과했다(6/10 475150 +29.9% 시장가 매수 → 매도잔량
#   없는 상한가 잠김으로 미체결). 29.5 가 '상한가(근접 잠김 포함)만 차단'이라는 6/1 의
#   원래 의도를 실제로 구현한다. env BARRO_MAX_FLU_RATE 로 조정.
_MAX_FLU_RATE = float(os.environ.get("BARRO_MAX_FLU_RATE", "29.5"))


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


def _closing_bet_held() -> set[str]:
    """종베(closing_bet) 보유 종목 — 자동 청산/재진입 제외(사용자 수동관리 전용).
    [사용자 요청 2026-06-18] 종베 포지션은 다른 전략/EOD강제청산이 건드리지 않는다."""
    try:
        _cb = json.loads((_DATA_DIR / "closing_bet_positions.json").read_text(encoding="utf-8"))
        return {str(p["symbol"]) for p in _cb}
    except Exception:
        return set()


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
                          pending_symbols: "set[str] | None" = None,
                          audit_path: "str | None" = None) -> int:
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
        pos = active[sym]
        pos_store.remove(sym)
        ts = _now_kst().strftime("%H:%M:%S")
        print(f"  [{ts}][SYNC] {sym} {pos.name} — 잔고에 없음, active_positions 제거")
        # [BAR-OPS-38 P0#5] 당일 진입 포지션의 퍼지 = 미체결 추정 — audit 에 자가설명 행 기록.
        #   6/10 475150: 상한가 잠김 시장가 매수 49주가 접수(rc=0)만 되고 미체결 → SYNC 퍼지.
        #   audit 엔 ORDERED 만 남아 원장 분석이 '체결'로 오인(매매복기 인시던트 1). UNFILLED
        #   행이 있으면 손익 재구성이 해당 주문을 자동 제외할 수 있다.
        if audit_path is not None:
            _append_unfilled_audit(audit_path, pos)
        removed += 1
    return removed


def _append_unfilled_audit(audit_path, pos) -> None:
    """[BAR-OPS-38 P0#5] SYNC 퍼지 시 미체결 추정 audit 행 — 당일 진입 포지션만 기록.

    트랜치별로 1행씩(order_no 포함) 기록해, 일일감사(_daily_strategy_audit.load_orders)가
    원 ORDERED 행을 order_no 로 정확히 상쇄할 수 있게 한다(6/10 475150 49주 사례).
    """
    try:
        entry_dt = datetime.fromisoformat(str(pos.entry_time))
        if entry_dt.tzinfo is None:
            entry_dt = entry_dt.replace(tzinfo=timezone.utc)
        if entry_dt.astimezone(KST).date() != _now_kst().date():
            return  # 당일 진입이 아니면(레거시 sync-loss 등) 미체결 단정 불가 — 기록 생략
        path = Path(audit_path)
        if not path.exists():
            return  # audit 파일이 없으면(테스트 등) 생성하지 않음 — 게이트가 헤더 소유
        filled = [t for t in pos.tranches if getattr(t, "status", "") == "filled"]
        if not filled:
            return
        with path.open("a", encoding="utf-8", newline="") as f:
            w = _csv.writer(f)
            for t in filled:
                w.writerow([
                    datetime.now(timezone.utc).isoformat(timespec="seconds"),
                    "UNFILLED", "buy", pos.symbol, int(t.qty), "MKT",
                    getattr(t, "order_no", "") or "", "", "0",
                    "SYNC 퍼지 — 잔고 부재(미체결 추정, 접수 ORDERED 무효)",
                    pos.strategy or "", "0", "",
                ])
    except Exception as exc:  # noqa: BLE001 — 감사 기록 실패가 SYNC 를 막으면 안 됨
        print(f"  [SYNC] UNFILLED audit 기록 실패: {type(exc).__name__}")


def _reconcile_position_qty(pos, broker_qty: int) -> bool:
    """[BAR-OPS-38 P0#5] 장부 filled 수량을 브로커 보유수량으로 보정. 변경 시 True.

    - 증가(이중매수 잔재 등): 마지막 filled tranche 에 delta 가산.
    - 감소(부분체결 등): filled tranche 뒤에서부터 차감(0 이 되면 제거).
    - pending tranche(미래 DCA 의도)는 유지.
    - total_recommended_qty 도 브로커 수량으로 갱신 — supertrend 청산이 이 필드를
      전량 매도 수량으로 쓰므로(장부 23 vs 브로커 32 면 9주 고아) 진실원천 일치 필수.
    """
    if broker_qty <= 0:
        return False
    filled = [t for t in pos.tranches if getattr(t, "status", "") == "filled"]
    book_qty = sum(int(t.qty) for t in filled)
    if not filled or book_qty == broker_qty:
        return False
    delta = broker_qty - book_qty
    if delta > 0:
        filled[-1].qty = int(filled[-1].qty) + delta
    else:
        need = -delta
        for t in reversed(filled):
            take = min(int(t.qty), need)
            t.qty = int(t.qty) - take
            need -= take
            if need <= 0:
                break
        pos.tranches = [
            t for t in pos.tranches
            if int(t.qty) > 0 or getattr(t, "status", "") != "filled"
        ]
    pos.total_recommended_qty = broker_qty
    return True


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
        await _sync_positions(pos_store, held_symbols, pending_symbols,
                              audit_path=args.audit_log)
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
    # 2026-06-22 — distribution 청산 게이트(default-OFF). enabled 일 때만 일봉 fetch(API 절감).
    _dist_cfg = DistributionExitConfig.from_policy_config(cfg)

    for h in balance.holdings:
        pos = active_positions.get(h.symbol)
        if pos:
            # [BAR-OPS-38 P0#5] 브로커 보유수량 ↔ 장부 filled 수량 보정 — 브로커가 진실.
            #   부분체결(접수 ORDERED ≠ 체결)·이중매수 잔재(6/10 319660 장부 23 vs 실보유 32)
            #   를 사이클마다 대사해 청산 수량 사고(초과매도/고아 잔량)를 예방.
            if _reconcile_position_qty(pos, int(h.qty)):
                ts_rc = _now_kst().strftime("%H:%M:%S")
                print(f"  [{ts_rc}][FILL-SYNC] {h.symbol} {h.name} 장부수량 보정 → 브로커 {int(h.qty)}주")
                pos_store.upsert(pos)
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

            # distribution 청산용 일봉 — 게이트 enabled 일 때만 fetch (default-OFF → fetch X).
            daily_candles = None
            if _dist_cfg.enabled:
                try:
                    daily_candles = await fetcher_for_exit.fetch_daily(symbol=h.symbol)
                except Exception:
                    daily_candles = None

            contexts[h.symbol] = PositionContext(
                peak_pnl_rate=pos.peak_pnl_rate,
                partial_tp_done=pos.partial_tp_done,
                entry_time=pos.entry_time,
                strategy=pos.strategy,
                minute_candles=minute_candles,
                daily_candles=daily_candles,
                distribution_exit=_dist_cfg if _dist_cfg.enabled else None,
            )

    decisions = evaluate_all(balance.holdings, policy, contexts)
    # [사용자 요청 2026-06-18] 종베 보유분은 다른 전략이 청산 금지 — 수동관리 전용.
    _cb_held = _closing_bet_held()
    if _cb_held:
        decisions = [d for d in decisions if d.symbol not in _cb_held]

    # DCA 분할매수
    _SELL_SIGNALS = {
        SellSignal.STOP_LOSS, SellSignal.TRAILING_STOP,
        SellSignal.BREAKEVEN_STOP, SellSignal.TIME_TIGHTENED_SL,
        SellSignal.SHORT_TERM_HIGH, SellSignal.DISTRIBUTION,
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
            # [BAR-OPS-39 P1] 매도 직전 장부 재확인 — 사이클 시작 스냅샷과 주문 사이에
            #   st 트레이더가 같은 종목을 선청산(remove)하면 중복 매도가 발사돼 브로커
            #   거부 FAILED 가 남는다(6/9 3건·6/11 1건). 스냅샷엔 있었는데 지금 장부에
            #   없으면 = 타 액터가 방금 청산 → skip. (장부 외 보유는 기존대로 매도 진행.)
            if _ap is not None and pos_store.get(d.symbol) is None:
                ts = _now_kst().strftime("%H:%M:%S")
                print(f"  [{ts}][SELL-SKIP] {d.symbol} — 타 액터 선청산 감지(장부 부재), 중복 매도 회피")
                continue
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
# [BAR-OPS-38 P0#2] supertrend 추가 — 트레이더가 전량 단일주문 진입인데 장부가 60/40 트랜치로
#   기록돼, 데몬 DCA 가 가짜 tranche2 를 실주문으로 추가 발사(6/10 319660: 23+9=32주, 권고의
#   139%). single_tranche=True 전환으로 신규 포지션은 pending 이 없지만, 전환 전 생성된
#   레거시 포지션(재시작 시 잔존)을 위한 이중 방어선.
_NO_DCA_STRATEGIES = {"swing_38", "supertrend"}

# [BAR-OPS-38 P0#3] 되돌림(gold)·눌림(f) 전략 시초갭 상한(%) — 갭상승 폭등주에는 바닥/눌림
#   신호가 고점에서 발화한다(6/10 gold 추격 3종 전패 -461K: SK오션플랜트 시가갭 +22.8% 등.
#   5/29·6/8 에 이은 세 번째 'gold 고점매수'). 전일종가 대비 등락률(flu_rate)이 임계 이상이면
#   해당 전략 진입 금지. env BARRO_ZONE_MAX_FLU 로 조정, 0=비활성.
#   ※ 6/11 실증: 임계 15% 바로 아래(13.1~13.5%) 진입 3건 전패 -353K — 임계 조정은
#   일일감사의 갭 분포 누적 측정(BAR-OPS-39) 후 데이터 기반으로.
_ZONE_MAX_FLU = float(os.environ.get("BARRO_ZONE_MAX_FLU", "15.0"))


def _parse_strategy_set(env_key: str, default: set) -> set:
    """csv env → 전략집합. 미설정/빈값이면 default 그대로(기본 동작 보존)."""
    raw = os.environ.get(env_key, "").strip()
    if not raw:
        return set(default)
    return {s.strip() for s in raw.split(",") if s.strip()}


# [thetrading-uplift Increment 1, 2026-06-17] 갭가드 적용 전략집합을 env 로 조정 가능.
#   기본값은 기존과 동일({gold_zone, f_zone}) — 미설정 시 동작 불변(c 분류·안전).
#   설계 §4.3: sf_zone 은 시초 open-gap·장중 flu 갭가드가 모두 없는 '진짜 구멍'.
#   운영이 코드배포 없이 env 로 편입 가능: BARRO_GAP_GUARD_STRATEGIES="gold_zone,f_zone,sf_zone".
#   ※ 실제 차단 활성은 선정 동작을 바꾸므로 (d) HITL — env 설정 자체가 인간 승인 게이트.
_GAP_GUARD_STRATEGIES = _parse_strategy_set(
    "BARRO_GAP_GUARD_STRATEGIES", _MEANREV_STRATEGIES | {"f_zone"})

# [BAR-OPS-39 P0] 일반(데몬) 전략 진입 컷오프 — st 트레이더에만 있던 14:30 컷오프(BAR-OPS-38
#   P0#4①)의 사각지대 봉합: 6/11 f_zone 이 14:33/14:38 현대무벡스 매수 → 이월 발생.
#   늦은 진입 = 짧은 검증시간 + 오버나이트 갭 리스크 후보. 빈 문자열이면 비활성.
#   다일보유가 설계인 swing_38 은 예외(이월이 의도 — 이월 총액 한도 20%가 별도 캡).
_ZONE_ENTRY_CUTOFF = os.environ.get("BARRO_ZONE_ENTRY_CUTOFF", "14:30").strip()
_CUTOFF_EXEMPT_STRATEGIES = {"swing_38"}


def _zone_entry_cutoff_passed() -> bool:
    """[BAR-OPS-39 P0] KST 현재시각이 일반 전략 진입 컷오프 이후면 True."""
    if not _ZONE_ENTRY_CUTOFF:
        return False
    try:
        hh, mm = _ZONE_ENTRY_CUTOFF.split(":")
        return _now_kst().time() >= time(int(hh), int(mm))
    except (ValueError, TypeError):
        return False  # 파싱 실패 시 차단하지 않음(기존 동작 보존)


# [2026-06-21] 6월 트랩(가짜 상승/개미 꼬시기) 진입 가드 env 토글 — 모든 값 0/미설정 →
#   비활성(FZoneParams/GoldZoneParams 의 trap_* default 0 유지 = 기존 동작 byte-identical).
#   진입 재검증(reval) 게이트의 분봉 analyze 에서 트랩(과확장·윗꼬리·고갭ATR) 차단.
#   설계: backend/core/strategy/trap_guard.py. sf_zone 은 FZoneParams 상속이라 자동 적용.
#   ※ 실제 차단 활성은 진입 동작을 바꾸므로 (d) HITL — env 설정 자체가 인간 승인 게이트
#     (_GAP_GUARD_STRATEGIES 와 동일 정책). 활성 전 측정(shadow/백테스트) 권장.
_TRAP_OVER_EXT_K_ATR = float(os.environ.get("BARRO_TRAP_OVER_EXT_K_ATR", "0"))
_TRAP_OVER_EXT_BASELINE = os.environ.get("BARRO_TRAP_OVER_EXT_BASELINE", "ma").strip() or "ma"
_TRAP_OVER_EXT_MA_PERIOD = int(os.environ.get("BARRO_TRAP_OVER_EXT_MA_PERIOD", "20"))
_TRAP_UPPER_WICK_MAX = float(os.environ.get("BARRO_TRAP_UPPER_WICK_MAX", "0"))
_TRAP_GAP_ATR_MULT = float(os.environ.get("BARRO_TRAP_GAP_ATR_MULT", "0"))
_TRAP_GAP_ABS_MAX_PCT = float(os.environ.get("BARRO_TRAP_GAP_ABS_MAX_PCT", "0"))
# [2026-06-21] SHADOW 측정 모드 — 1이면 트랩 임계 설정해도 '차단했을 것'만 로깅하고 미차단
#   (enforce 전 차단율·오차단 측정). 기본 0=enforce. 임계 미설정이면 어차피 무동작.
_TRAP_SHADOW = os.environ.get("BARRO_TRAP_SHADOW", "0").strip().lower() in ("1", "true", "yes")


def _apply_trap_env(params):
    """env BARRO_TRAP_* 를 전략 params 의 trap_* 필드에 주입. 미설정(전부 0)이면 무변경."""
    params.trap_over_ext_k_atr = _TRAP_OVER_EXT_K_ATR
    params.trap_over_ext_baseline = _TRAP_OVER_EXT_BASELINE
    params.trap_over_ext_ma_period = _TRAP_OVER_EXT_MA_PERIOD
    params.trap_upper_wick_max = _TRAP_UPPER_WICK_MAX
    params.trap_gap_atr_mult = _TRAP_GAP_ATR_MULT
    params.trap_gap_abs_max_pct = _TRAP_GAP_ABS_MAX_PCT
    return params


# [2026-06-21] 데몬 후처리 트랩필터 — 일봉 선정 단계 enforcement(전 전략, swing_38 포함).
#   reval(5분봉)의 한계 보완: ① swing_38 미지원(5분봉 부적합) ② bar-gap proxy 로 시초갭 무력.
#   여기선 일봉 후보 캔들(과확장·윗꼬리)과 LeaderPicker 실 flu_rate(전일比 시초갭)를 직접 사용 →
#   _ZONE_MAX_FLU 절대갭가드와 직교 보강. 모든 env 0(미설정) → any_enabled()=False → 무동작.
_DAEMON_TRAP = TrapGuardConfig(
    over_ext_k_atr=_TRAP_OVER_EXT_K_ATR, over_ext_baseline=_TRAP_OVER_EXT_BASELINE,
    over_ext_ma_period=_TRAP_OVER_EXT_MA_PERIOD, upper_wick_max=_TRAP_UPPER_WICK_MAX,
    gap_atr_mult=_TRAP_GAP_ATR_MULT, gap_abs_max_pct=_TRAP_GAP_ABS_MAX_PCT,
)


def _build_reval_strategy(strategy_id: str):
    """진입 재검증용 분봉 전략 인스턴스 (분봉 min_atr 0.01)."""
    from backend.core.strategy.f_zone import FZoneStrategy, FZoneParams
    from backend.core.strategy.sf_zone import SFZoneStrategy
    from backend.core.strategy.gold_zone import GoldZoneStrategy, GoldZoneParams
    if strategy_id in ("f_zone", "sf_zone"):
        p = FZoneParams.for_intraday()
        p.min_atr_pct = _REVAL_MIN_ATR
        _apply_trap_env(p)
        return SFZoneStrategy(p) if strategy_id == "sf_zone" else FZoneStrategy(p)
    if strategy_id == "gold_zone":
        gp = GoldZoneParams(min_atr_pct=_REVAL_MIN_ATR)
        _apply_trap_env(gp)
        return GoldZoneStrategy(gp)
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
    # [BAR-OPS-39 P0] 진입 컷오프 — 컷오프 경과 + 면제 전략(swing_38) 미운영이면 스캔 자체 생략
    #   (API 호출 절약). 면제 전략 운영 중이면 시그널 단계에서 전략별로 차단.
    if _zone_entry_cutoff_passed() and not (set(zone_strategies) & _CUTOFF_EXEMPT_STRATEGIES):
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

    # [사용자 요청 2026-06-18] 종베(알림 데몬) 보유 종목은 다른 전략이 재진입 금지.
    #   브로커 잔고(already_held)가 1차 가드이나, ①balance 조회 실패 fail-open ②종베 수동
    #   진입은 active_positions 에 없음 → closing_bet_positions.json 을 명시 제외(2차 방어).
    closing_bet_held: set[str] = set()
    try:
        _cb = json.loads((_DATA_DIR / "closing_bet_positions.json").read_text())
        closing_bet_held = {str(p["symbol"]) for p in _cb}
    except Exception:
        pass

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
        | cooldown_buys | audit_buys | closing_bet_held
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
            # [BAR-OPS-38 P0#3] 되돌림(gold)·눌림(f) 시초갭 가드 — 갭상승 폭등주 추격 차단.
            if (_ZONE_MAX_FLU > 0 and best_strategy in _GAP_GUARD_STRATEGIES
                    and float(c.flu_rate) >= _ZONE_MAX_FLU):
                ts_g = _now_kst().strftime("%H:%M:%S")
                print(
                    f"  [{ts_g}][SKIP-GAP] {c.symbol} {c.name:<14} 전략={best_strategy} "
                    f"등락률 +{float(c.flu_rate):.1f}% ≥ {_ZONE_MAX_FLU}% — 갭상승 추격 차단"
                )
                continue
            # [2026-06-21] 트랩가드 후처리(default-OFF) — 일봉 후보 + 실 flu_rate 로 가짜돌파/개미꼬시기
            #   차단(전 전략, swing_38 포함). 과확장·윗꼬리·시초갭(flu_rate). env BARRO_TRAP_* 미설정→무동작.
            if _DAEMON_TRAP.any_enabled():
                _tb, _tr = evaluate_trap_guard(candles, _DAEMON_TRAP, flu_rate=float(c.flu_rate))
                if _tb:
                    ts_t = _now_kst().strftime("%H:%M:%S")
                    if _TRAP_SHADOW:
                        # 측정 전용: 차단했을 것만 로깅, 실제 진입은 막지 않음(enforce 전 측정).
                        print(
                            f"  [{ts_t}][SHADOW-TRAP] {c.symbol} {c.name:<14} 전략={best_strategy} "
                            f"— would-block({_tr}) [측정·미차단]"
                        )
                    else:
                        print(
                            f"  [{ts_t}][SKIP-TRAP] {c.symbol} {c.name:<14} 전략={best_strategy} "
                            f"— 트랩가드 차단({_tr})"
                        )
                        continue
            # [BAR-OPS-39 P0] 진입 컷오프 — 늦은 진입(이월 후보) 차단. swing_38(다일보유)은 예외.
            #   6/11 f_zone 14:33 현대무벡스 진입 이월이 실증(st 전용 컷오프의 사각지대).
            if (_zone_entry_cutoff_passed()
                    and best_strategy not in _CUTOFF_EXEMPT_STRATEGIES):
                ts_c = _now_kst().strftime("%H:%M:%S")
                print(
                    f"  [{ts_c}][SKIP-CUTOFF] {c.symbol} {c.name:<14} 전략={best_strategy} "
                    f"— 진입 컷오프(≥ {_ZONE_ENTRY_CUTOFF}) 이후(이월 리스크)"
                )
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
                    # [BAR-OPS-38 P0#3] #7 을 #6 과 분리해 standalone 기본 ON — 6/10 gold 가
                    #   233740 을 일중 고가 1틱 밑(12,060/H 12,110)에서 매수하는 등 3종 전패.
                    #   #6(재검증, 매수 0건화 위험)은 여전히 --entry-revalidate 로만 enforce.
                    #   끄려면 BARRO_GOLD_HIGH_GUARD=0.
                    if best_strategy in _MEANREV_STRATEGIES and (
                            getattr(args, "entry_revalidate", False)
                            or _env_truthy("BARRO_GOLD_HIGH_GUARD", "1")):
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

    # 일일손실 게이트 입력 — [BAR-OPS-38 P0#1, 2026-06-10 매매복기] 입력 교체.
    # 종전 total_pnl_rate(kt00018 tot_prft_rt)는 '현 보유 매입금액 대비 누적 평가수익률'로
    #   ①당일 손익이 아니고 ②보유가 비는 순간 0% 리셋된다 — 6/10 09:05 차단(-5.76% 표기)
    #   → 09:07 이월 매도 체결 후 0% → 31건 무차단, 차단된 100090 을 3분 뒤 그대로 매수.
    # 새 입력 = (당일 실현 net 합(ka10074) + 보유 평가손익(kt00018)) / 추정예탁자산.
    #   브로커 권위 데이터만 사용(2026-06-02 사고 원인이었던 파일 스냅샷 의존 없음),
    #   조회 실패 시 0% fail-open(과차단 방지) + latch 는 파일 영속로 별도 유지.
    daily_pnl_pct = await compute_daily_gate_input(account, balance)

    # 주문 실행
    notifier = TelegramNotifier.from_env() if args.telegram else None
    executor = KiwoomNativeOrderExecutor(oauth=oauth, dry_run=args.dry_run)
    gate = LiveOrderGate(
        executor=executor, audit_path=args.audit_log,
        policy=GatePolicy(
            daily_loss_limit_pct=Decimal(str(cfg.daily_loss_limit)),
            daily_max_orders=cfg.daily_max_orders,
            # ── BAR-OPS-35 env 토글 → [BAR-OPS-38] 기본 ON 전환(2026-06-10 매매복기 P0).
            #    latch: 입력이 당일 기준으로 교체됐으므로 활성(off 는 env=0 명시).
            #    재시도: 매도(청산)만 — 6/8 매도 HTTPStatusError 5분 지연(-509K 악화) 처방.
            daily_loss_latch=_env_truthy("SUPERTREND_AUTO_LOSS_LATCH", "1"),
            latch_state_path=str(_DATA_DIR / "daily_gate_state.json"),
            loss_metric_label="당일실현+보유평가/추정예탁자산",
            order_retry_count=int(os.environ.get("SUPERTREND_AUTO_ORDER_RETRY", "2")),
            order_retry_backoff_sec=float(os.environ.get("SUPERTREND_AUTO_ORDER_RETRY_BACKOFF", "2.0")),
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
        policy=GatePolicy(
            daily_max_orders=int(os.environ.get("SUPERTREND_AUTO_MAX_ORDERS", "50")),  # [0609 임시해제]
            # [BAR-OPS-38 P0#1] 데몬 스캔 경로와 동일 — latch 기본 ON + 파일 영속 + 매도 재시도.
            daily_loss_latch=_env_truthy("SUPERTREND_AUTO_LOSS_LATCH", "1"),
            latch_state_path=str(_DATA_DIR / "daily_gate_state.json"),
            loss_metric_label="당일실현+보유평가/추정예탁자산",
            order_retry_count=int(os.environ.get("SUPERTREND_AUTO_ORDER_RETRY", "2")),
            order_retry_backoff_sec=float(os.environ.get("SUPERTREND_AUTO_ORDER_RETRY_BACKOFF", "2.0")),
            retry_sell_only=_env_truthy("SUPERTREND_AUTO_RETRY_SELL_ONLY", "1"),
        ),
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
            # ── BAR-OPS-35 가드 env 토글 — [BAR-OPS-38, 2026-06-10 매매복기 P0] 일부 기본 ON 전환.
            #    hard_stop -6%: 6/8 459550 -12.63% 방치(P0#3, 메모리 미수정 P0②) 처방.
            #    single_tranche: 6/10 319660 이중매수 실증(장부 23 vs 실보유 32, 권고의 139%) 처방.
            #    max_open_gap 15%: 시초가 갭 급등주 추격 차단(6/9 신설 가드 활성).
            #    끄려면 env 로 명시(예 SUPERTREND_AUTO_SINGLE_TRANCHE=0).
            hard_stop_pct=float(os.environ.get("SUPERTREND_AUTO_HARD_STOP", "-6.0")),
            max_entries_per_symbol_day=int(os.environ.get("SUPERTREND_AUTO_MAX_ENTRIES", "0")),
            reentry_cooldown_min=int(os.environ.get("SUPERTREND_AUTO_REENTRY_COOLDOWN", "0")),
            block_reentry_after_loss=_env_truthy("SUPERTREND_AUTO_BLOCK_REENTRY_LOSS"),
            # [BAR-OPS-39 P1] 재진입 가격조건(직전 진입가 이하만) — 측정 후 활성 판단, 기본 OFF
            reentry_only_below_prev_entry=_env_truthy("SUPERTREND_AUTO_REENTRY_BELOW_ENTRY"),
            reentry_below_tolerance_pct=float(os.environ.get("SUPERTREND_AUTO_REENTRY_BELOW_TOL", "0")),
            max_atr_pct_for_entry=float(os.environ.get("SUPERTREND_AUTO_MAX_ATR_PCT", "0")),
            take_profit_trail_only=_env_truthy("SUPERTREND_AUTO_TP_TRAIL_ONLY"),
            vol_halve_atr_pct=float(os.environ.get("SUPERTREND_AUTO_VOL_HALVE_ATR", "0")),
            single_tranche=_env_truthy("SUPERTREND_AUTO_SINGLE_TRANCHE", "1"),
            max_entry_gap_pct=float(os.environ.get("SUPERTREND_AUTO_MAX_ENTRY_GAP", "0")),
            max_open_gap_pct=float(os.environ.get("SUPERTREND_AUTO_MAX_OPEN_GAP", "15.0")),
            # ── [BAR-OPS-38 P0#4] 이월(오버나이트) 정책 ──
            entry_cutoff_time=os.environ.get("SUPERTREND_AUTO_ENTRY_CUTOFF", "14:30"),
            carry_gap_stop_pct=float(os.environ.get("SUPERTREND_AUTO_CARRY_GAP_STOP", "-3.0")),
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


async def _eod_carry_limit(args, oauth, notifier) -> int:
    """[BAR-OPS-38 P0#4②] EOD 이월(오버나이트) 총액 한도 — 초과분 평가액 큰 순 청산.

    이월 예정(현 보유) 평가합이 추정예탁자산 × BARRO_CARRY_LIMIT_RATIO(기본 0.20)를
    넘으면 평가액 큰 포지션부터 전량 매도해 한도 아래로 낮춘다. 근거: 6/9 이월 21.8M
    (계좌의 43.7%)이 6/10 갭하락 -845K 의 주성분(2026-06-10 매매복기) — 이월 자체가
    아니라 '무제한 이월'이 문제(001740 이월은 +695K). 0 이면 비활성. 매도 건수 반환.
    """
    ratio = float(os.environ.get("BARRO_CARRY_LIMIT_RATIO", "0.20"))
    if ratio <= 0:
        return 0
    account = KiwoomNativeAccountFetcher(oauth=oauth)
    balance = await account.fetch_balance()
    holdings = list(balance.holdings or [])
    if not holdings:
        return 0
    base = float(balance.estimated_deposit or 0)
    if base <= 0:
        deposit = await account.fetch_deposit()
        base = float(deposit.cash) + float(balance.total_eval or 0)
    if base <= 0:
        print("  [CARRY-LIMIT] 기준자산 산출 실패 — 스킵")
        return 0
    limit_value = base * ratio
    eval_sum = sum(float(h.eval_amount) for h in holdings)
    if eval_sum <= limit_value:
        return 0

    ts = _now_kst().strftime("%H:%M:%S")
    print(f"  [{ts}][CARRY-LIMIT] 이월 예정 {eval_sum:,.0f}원 > 한도 {limit_value:,.0f}원"
          f"({ratio:.0%}) — 초과분 청산 개시")
    cfg = PolicyConfigStore(str(_DATA_DIR / "policy.json")).load()
    executor = KiwoomNativeOrderExecutor(oauth=oauth, dry_run=args.dry_run)
    gate = LiveOrderGate(
        executor=executor, audit_path=args.audit_log,
        # 매도 전용 경로 — 일일손실 게이트는 매수만 차단하므로 기본 정책으로 충분.
        policy=GatePolicy(daily_max_orders=cfg.daily_max_orders,
                          order_retry_count=int(os.environ.get("SUPERTREND_AUTO_ORDER_RETRY", "2")),
                          order_retry_backoff_sec=float(os.environ.get("SUPERTREND_AUTO_ORDER_RETRY_BACKOFF", "2.0"))),
        notifier=notifier,
    )
    pos_store = ActivePositionStore(args.pos_log)
    book = pos_store.load_all()
    _cb_skip = _closing_bet_held()
    sold = 0
    for h in sorted(holdings, key=lambda x: float(x.eval_amount), reverse=True):
        if eval_sum <= limit_value:
            break
        if h.symbol in _cb_skip:
            continue  # 종베 보유분 강제청산 제외(수동관리)
        strategy = getattr(book.get(h.symbol), "strategy", None) if book else None
        try:
            r = await gate.place_sell(symbol=h.symbol, qty=int(h.qty),
                                      strategy_id=strategy)
            ts = _now_kst().strftime("%H:%M:%S")
            tag = "DRY_RUN" if r.dry_run else "SOLD"
            print(f"  [{ts}][CARRY-LIMIT-{tag}] {h.symbol} {h.name} {int(h.qty)}주 "
                  f"평가 {float(h.eval_amount):,.0f}원 청산")
            pos_store.remove(h.symbol)
            eval_sum -= float(h.eval_amount)
            sold += 1
            if notifier:
                try:
                    await notifier.send(
                        f"[이월한도] {h.symbol} {h.name} {int(h.qty)}주 EOD 청산 "
                        f"(이월 {eval_sum:,.0f}/{limit_value:,.0f}원)")
                except Exception:
                    pass
        except Exception as e:  # noqa: BLE001 — 종목 단위 격리
            print(f"  [CARRY-LIMIT-ERR] {h.symbol}: {type(e).__name__}: {e}")
    return sold


async def _eod_fill_backfill(oauth) -> None:
    """[BAR-OPS-38 P0#5] 장마감 후 당일 실현(체결) 내역 백필 — ka10073 → data/fill_audit.csv.

    order_audit 는 시장가 접수만 기록(체결가 없음) → 브로커 권위 체결 데이터(종목별 실현손익,
    체결가·수수료·세금 포함)를 일 단위로 적재해 매매복기/일일감사의 추정을 실측으로 대체.
    """
    import csv as _c
    try:
        account = KiwoomNativeAccountFetcher(oauth=oauth)
        today = _now_kst().strftime("%Y%m%d")
        entries = await account.fetch_realized_pnl(today, today)
        if not entries:
            print("  [FILL-BACKFILL] 당일 실현 내역 없음")
            return
        path = _DATA_DIR / "fill_audit.csv"
        headers = ["date", "symbol", "name", "qty", "buy_price", "sell_price",
                   "pnl", "pnl_rate", "commission", "tax"]
        existing: set[tuple] = set()
        if path.exists():
            try:
                with path.open(newline="", encoding="utf-8") as f:
                    for row in _c.DictReader(f):
                        existing.add((row.get("date", ""), row.get("symbol", ""),
                                      row.get("qty", ""), row.get("sell_price", ""),
                                      row.get("pnl", "")))
            except Exception:
                pass
        new_rows = []
        for e in entries:
            key = (e.date, e.symbol, str(e.qty), str(e.sell_price), str(e.pnl))
            if key in existing:
                continue
            new_rows.append([e.date, e.symbol, e.name, e.qty, str(e.buy_price),
                             str(e.sell_price), str(e.pnl), str(e.pnl_rate),
                             str(e.commission), str(e.tax)])
        if not new_rows:
            print("  [FILL-BACKFILL] 신규 체결 행 없음 (이미 적재됨)")
            return
        is_new = not path.exists()
        with path.open("a", encoding="utf-8", newline="") as f:
            w = _c.writer(f)
            if is_new:
                w.writerow(headers)
            w.writerows(new_rows)
        net = sum(float(r[6]) for r in new_rows)
        print(f"  [FILL-BACKFILL] 당일 체결 {len(new_rows)}행 적재 (실현손익 합 {net:+,.0f}원) → {path.name}")
    except Exception as e:  # noqa: BLE001 — EOD 부가 루틴 실패가 데몬 종료를 막으면 안 됨
        print(f"  [FILL-BACKFILL-ERR] {type(e).__name__}: {e}")


async def _eod_buy_snapshot(oauth) -> None:
    """[BAR-OPS-39 P1] EOD 보유 종목 매수 스냅샷 — fill_audit(ka10073)는 '매도 실현'만
    기록하므로 매수 체결의 독립 감사 소스가 없었다(6/11 검증자 지적: 매수가가
    매도 행에 자기참조). 당일 청산분의 매수평단은 ka10073 행에 이미 있고, EOD 보유
    종목의 매수평단은 브로커 잔고(kt00018 avg_buy_price)로 보완 — 커버리지 완성.

    ※ _eod_fill_backfill 과 별도 함수 — 매도 0건(전량 이월 보유)인 날에도 반드시
    실행돼야 한다(리뷰 지적: backfill 의 early return 에 묶이면 가장 필요한 날 스킵).
    """
    import csv as _c
    try:
        account2 = KiwoomNativeAccountFetcher(oauth=oauth)
        balance2 = await account2.fetch_balance()
        holdings = list(balance2.holdings or [])
        if holdings:
            bpath = _DATA_DIR / "buy_audit.csv"
            bheaders = ["date", "symbol", "name", "qty", "avg_buy_price", "source"]
            today2 = _now_kst().strftime("%Y%m%d")
            existing2: set[tuple] = set()
            if bpath.exists():
                try:
                    with bpath.open(newline="", encoding="utf-8") as f:
                        for row in _c.DictReader(f):
                            existing2.add((row.get("date", ""), row.get("symbol", "")))
                except Exception:
                    pass
            rows2 = []
            for h in holdings:
                if (today2, h.symbol) in existing2:
                    continue
                rows2.append([today2, h.symbol, h.name, int(h.qty),
                              str(h.avg_buy_price), "kt00018"])
            if rows2:
                new_file2 = not bpath.exists()
                with bpath.open("a", encoding="utf-8", newline="") as f:
                    w2 = _c.writer(f)
                    if new_file2:
                        w2.writerow(bheaders)
                    w2.writerows(rows2)
                print(f"  [BUY-SNAPSHOT] EOD 보유 {len(rows2)}종목 매수평단 적재 → {bpath.name}")
    except Exception as e:  # noqa: BLE001
        print(f"  [BUY-SNAPSHOT-ERR] {type(e).__name__}: {e}")


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
            # [BAR-OPS-38 P1] 추정예탁자산(kt00018 prsm_dpst_aset_amt, D+2 정산 반영) 병기.
            #   cash(d+0 예수금)는 당일 매수대금을 차감하지 않아 total 이 매수 이월일에
            #   과대표시된다(6/9 71.4M 사례 — 2026-06-10 매매복기 인시던트 6). 일손익은
            #   estimated_asset 차분으로 읽을 것.
            "estimated_asset": float(balance.estimated_deposit or 0),
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
    # [BAR-OPS-38 P0#4②] EOD 이월 한도 — 당일 1회만 실행.
    eod_carry_done = False

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

        # 4) [BAR-OPS-38 P0#4②] EOD 이월 한도 — 15:10~15:19 당일 1회. 이월 예정 평가합이
        #    추정예탁자산 × BARRO_CARRY_LIMIT_RATIO 초과 시 평가액 큰 순 청산(갭 리스크 캡).
        if not eod_carry_done and time(15, 10) <= _now_kst().time() <= time(15, 19):
            eod_carry_done = True
            try:
                n = await _eod_carry_limit(args, oauth, notifier)
                if n > 0:
                    total_sold += n
                    print(f"  [{ts}] 이월 한도 초과분 {n}건 청산 (누적 매도 {total_sold}건)")
            except Exception as e:
                print(f"  [{ts}][CARRY-LIMIT-ERROR] {type(e).__name__}: {e}")

        # 다음 사이클까지 대기
        if _daemon_hours():
            await asyncio.sleep(args.interval)

    # 장 마감 잔고 스냅샷
    await _save_balance_snapshot(oauth)
    # [BAR-OPS-38 P0#5] 당일 체결(실현) 내역 백필 — ka10073 → fill_audit.csv (브로커 권위 체결가)
    await _eod_fill_backfill(oauth)
    # [BAR-OPS-39 P1] EOD 보유 매수평단 스냅샷 — 매도 0건인 날에도 반드시 실행(별도 함수)
    await _eod_buy_snapshot(oauth)
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
