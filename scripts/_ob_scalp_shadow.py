"""ob_scalp 호가 스캘핑 — Shadow 신호 로깅 (주문 없음, 검증 전용).

설계: docs/02-design/features/2026-05-30-ob-scalp.design.md §5
  "1. 실시간 신호 로깅(shadow): 장중 고속 폴링으로 ob_scalp 신호 발생 시각·OFI·
   스프레드·이후 N초 가격을 기록 → 신호 후 실제 단기 방향의 적중률·기대값 측정(주문 없이)."

L2 호가 이력이 없어 ob_scalp 은 **백테스트 불가**. 따라서 실시간 shadow 관측으로만
검증 가능하다. 본 스크립트는 장중 universe 의 호가를 고속 폴링하여:
  1) OBScalpStrategy 진입 신호 발생 시각·OFI·스프레드·마이크로프라이스·비용커버틱·순TP 기록
  2) 신호 후 first-touch 페이퍼 체결 시뮬레이션(TP/SL/시간청산) — 진입=best_ask, 청산=best_bid
  3) 수수료+제세금 차감 **순(net) 수익률**과 적중률·기대값 집계

★★★ 안전 — 이 스크립트는 절대 주문하지 않는다 ★★★
  - 주문 실행기(KiwoomNativeOrderExecutor)를 import 조차 하지 않는다(구조적 차단).
  - 읽기 전용 API(호가 ka10004 + 순위 ka10027/ka10030 등)만 호출.
  - 산출물은 JSONL 로그 + 종료 시 요약뿐. 자금 시스템에 어떤 부수효과도 없다.

사용:
    set -a; . ./.env.local; set +a
    python scripts/_ob_scalp_shadow.py                 # 장중 무한 관측
    python scripts/_ob_scalp_shadow.py --interval 3 --top 8 --horizon 60
    python scripts/_ob_scalp_shadow.py --once          # 1회 폴링(연결 점검)
    python scripts/_ob_scalp_shadow.py --ignore-hours  # 장외 강제 실행(점검용)
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import signal
import sys
from dataclasses import dataclass, field
from datetime import datetime, time as dtime, timezone, timedelta
from pathlib import Path

_REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_REPO))

KST = timezone(timedelta(hours=9))
# shadow 관측 창: 매수 시작(09:05)~스캘핑 강제청산(15:10) + horizon 여유.
_OBS_OPEN = dtime(9, 5)
_OBS_CLOSE = dtime(15, 15)

_LOG_DIR = _REPO / "data" / "ob_scalp_shadow"


def _now_kst() -> datetime:
    return datetime.now(KST)


def _load_env() -> None:
    """.env.local → os.environ (setdefault: 이미 export 된 값 우선)."""
    envf = _REPO / ".env.local"
    if not envf.exists():
        return
    for line in envf.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, v = line.split("=", 1)
            os.environ.setdefault(k, v.strip().strip('"').strip("'"))


def _in_obs_hours() -> bool:
    return _OBS_OPEN <= _now_kst().time() <= _OBS_CLOSE


# ─── 관측 레코드: 신호 발생 후 first-touch 페이퍼 체결 추적 ──────────────────
@dataclass
class Observation:
    obs_id: str
    symbol: str
    name: str
    signal_ts: datetime
    entry_ask: float          # 진입 가정가 (최우선 매도호가 매수)
    tick: int
    tp_target: float          # 비용 내재화 TP 목표가
    sl_price: float           # SL 가격 (entry*(1+sl_pct))
    breakeven_ticks: float
    ofi: float
    spread_ticks: float
    net_tp_pct: float         # TP 도달 시 순수익률(%) (수수료+제세금 차감)
    resolve_at: datetime      # 시간청산 기준 시각
    # 추적 상태
    max_bid: float = 0.0      # 관측 중 최고 매수호가(상방 best-case)
    min_bid: float = 0.0      # 관측 중 최저 매수호가
    last_bid: float = 0.0     # 마지막 관측 매수호가(종료 flush 시 미실현 평가용)
    outcome: str = ""         # tp_hit / sl_hit / time_exit
    exit_bid: float = 0.0     # first-touch 청산가(매도호가 기준=best_bid)
    resolved: bool = False

    def update(self, bb: float, now: datetime) -> bool:
        """현재 최우선 매수호가(bb)로 first-touch 페이퍼 체결 갱신.

        롱 포지션이므로 청산은 best_bid 에 매도(보수적). TP/SL 중 먼저 닿는 쪽으로
        확정하고, 둘 다 미접촉 상태로 resolve_at 경과 시 시간청산. 해결되면 True.
        """
        if self.resolved:
            return True
        self.last_bid = bb
        self.max_bid = max(self.max_bid, bb)
        self.min_bid = bb if self.min_bid == 0.0 else min(self.min_bid, bb)
        if bb >= self.tp_target:
            self.outcome, self.exit_bid, self.resolved = "tp_hit", float(self.tp_target), True
        elif bb <= self.sl_price:
            self.outcome, self.exit_bid, self.resolved = "sl_hit", float(self.sl_price), True
        elif now >= self.resolve_at:
            self.outcome, self.exit_bid, self.resolved = "time_exit", float(bb), True
        return self.resolved


@dataclass
class ShadowStats:
    polls: int = 0
    signals: int = 0
    resolved: int = 0
    tp_hits: int = 0
    sl_hits: int = 0
    time_exits: int = 0
    net_pcts: list[float] = field(default_factory=list)  # 실현 순수익률(%) 모음

    def expectancy(self) -> float:
        return sum(self.net_pcts) / len(self.net_pcts) if self.net_pcts else 0.0

    def win_rate(self) -> float:
        if not self.net_pcts:
            return 0.0
        wins = sum(1 for r in self.net_pcts if r > 0)
        return wins / len(self.net_pcts) * 100.0


class ShadowLogger:
    def __init__(self, log_path: Path) -> None:
        self._path = log_path
        self._path.parent.mkdir(parents=True, exist_ok=True)

    def write(self, event: str, payload: dict) -> None:
        rec = {"ts": _now_kst().isoformat(), "event": event, **payload}
        with self._path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(rec, ensure_ascii=False, default=str) + "\n")


async def run_shadow(args: argparse.Namespace) -> int:
    _load_env()

    # ── 읽기 전용 게이트웨이만 구성 (주문 실행기 import 없음) ──
    from pydantic import SecretStr
    from backend.core.gateway.kiwoom_native_oauth import KiwoomNativeOAuth
    from backend.core.gateway.kiwoom_native_orderbook import KiwoomNativeOrderbookFetcher
    from backend.core.gateway.kiwoom_native_rank import KiwoomNativeLeaderPicker
    from backend.core.strategy.ob_scalp import OBScalpStrategy, OBScalpParams, krx_tick_size, net_return_pct, ROUND_TRIP_COST_PCT
    from backend.models.market import OHLCV, OrderBook, MarketType
    from backend.models.strategy import AnalysisContext

    app_key = os.environ.get("KIWOOM_APP_KEY", "")
    app_secret = os.environ.get("KIWOOM_APP_SECRET", "")
    base_url = os.environ.get("KIWOOM_BASE_URL", "https://mockapi.kiwoom.com")
    if not app_key or not app_secret:
        raise SystemExit("KIWOOM_APP_KEY / KIWOOM_APP_SECRET 필요 (set -a; . ./.env.local; set +a)")

    import httpx
    http = httpx.AsyncClient(timeout=10)
    oauth = KiwoomNativeOAuth(app_key=SecretStr(app_key), app_secret=SecretStr(app_secret),
                              base_url=base_url, http_client=http)
    ob_fetcher = KiwoomNativeOrderbookFetcher(oauth=oauth, http_client=http)
    picker = KiwoomNativeLeaderPicker(oauth=oauth, min_flu_rate=args.min_flu)

    params = OBScalpParams(
        imb_threshold=args.imb, max_spread_ticks=args.max_spread,
        min_depth=args.min_depth, profit_ticks=args.profit_ticks,
        sl_ticks=args.sl_ticks, max_breakeven_ticks=args.max_breakeven,
        slippage_ticks=args.slippage,
    )
    strat = OBScalpStrategy(params)

    log_path = _LOG_DIR / f"{_now_kst().strftime('%Y-%m-%d')}.jsonl"
    logger = ShadowLogger(log_path)
    stats = ShadowStats()
    pending: dict[str, Observation] = {}      # obs_id → Observation (미해결)
    cooldown_until: dict[str, datetime] = {}  # symbol → 재신호 허용 시각
    leaders_cache: list = []
    last_leader_refresh: datetime | None = None

    stop = {"flag": False}

    def _on_signal(*_):
        stop["flag"] = True
        print("\n[shadow] 종료 신호 수신 — 요약 출력 후 종료합니다.", flush=True)

    signal.signal(signal.SIGINT, _on_signal)
    signal.signal(signal.SIGTERM, _on_signal)

    logger.write("session_start", {"base_url": base_url, "params": vars(args),
                                    "note": "SHADOW ONLY — no orders ever placed"})
    print(f"[shadow] ob_scalp shadow 관측 시작 → {log_path}")
    print(f"[shadow] universe top={args.top} · interval={args.interval}s · horizon={args.horizon}s "
          f"· imb≥{args.imb} · spread≤{args.max_spread}틱 (주문 없음)")

    def _book_quotes(book: OrderBook):
        bb = max((float(p) for p, q in book.bids if float(q) > 0), default=None)
        ba = min((float(p) for p, q in book.asks if float(q) > 0), default=None)
        return bb, ba

    async def _fetch_book(symbol: str):
        """호가 조회 + 경량 백오프 재시도. 실패 시 None (관측 계속).

        재시도 대상: 429(rate-limit) + 네트워크 전송오류(httpx.TransportError:
        ConnectError/ReadTimeout/ConnectTimeout/RemoteProtocolError 등).
        그 외 4xx/5xx·예외는 즉시 None(과도 재시도 방지)."""
        for attempt in range(args.max_retries + 1):
            try:
                return await ob_fetcher.fetch_orderbook(symbol)
            except httpx.HTTPStatusError as e:
                if e.response.status_code == 429 and attempt < args.max_retries:
                    await asyncio.sleep(args.req_delay * (attempt + 2))  # 점증 백오프
                    continue
                logger.write("error", {"where": "fetch_orderbook", "symbol": symbol,
                                        "status": e.response.status_code, "err": repr(e)})
                return None
            except httpx.TransportError as e:
                if attempt < args.max_retries:
                    await asyncio.sleep(args.req_delay * (attempt + 2))  # 네트워크 장애 백오프
                    continue
                logger.write("error", {"where": "fetch_orderbook", "symbol": symbol,
                                        "kind": "transport", "err": repr(e)})
                return None
            except Exception as e:  # noqa: BLE001
                logger.write("error", {"where": "fetch_orderbook", "symbol": symbol, "err": repr(e)})
                return None

    try:
        cycle = 0
        while not stop["flag"]:
            if not args.ignore_hours and not _in_obs_hours():
                now_t = _now_kst().time()
                if now_t > _OBS_CLOSE:
                    print("[shadow] 관측 시간 종료(15:15 KST).")
                    break
                await asyncio.sleep(min(30, args.interval))
                continue

            cycle += 1
            now = _now_kst()

            # universe 갱신(leader refresh interval) — pending 종목은 항상 유지
            if (last_leader_refresh is None
                    or (now - last_leader_refresh).total_seconds() >= args.leader_refresh):
                try:
                    leaders_cache = await picker.pick(top_n=args.top)
                    last_leader_refresh = now
                except Exception as e:  # noqa: BLE001
                    logger.write("error", {"where": "picker.pick", "err": repr(e)})
            # ob_scalp 틱 모델은 주식(equity) 기준 — ETF/ETN(5원 단일틱)은 기본 제외(측정 순도).
            if args.equity_only:
                kept = {c.symbol: c.name for c in leaders_cache if c.symbol.isdigit()}
                dropped = [c.symbol for c in leaders_cache if not c.symbol.isdigit()]
                if dropped and last_leader_refresh == now:
                    logger.write("universe_filter", {"equity_only": True, "dropped": dropped,
                                                     "kept": list(kept.keys())})
                watch = kept
            else:
                watch = {c.symbol: c.name for c in leaders_cache}
            for obs in pending.values():
                watch.setdefault(obs.symbol, obs.name)

            for idx, (symbol, name) in enumerate(list(watch.items())):
                if idx > 0 and args.req_delay > 0:
                    await asyncio.sleep(args.req_delay)  # rate-limit(429) 회피 throttle
                book = await _fetch_book(symbol)
                if book is None:
                    continue
                bb, ba = _book_quotes(book)
                if bb is None or ba is None:
                    continue

                # ── 1) 진행 중 관측 갱신 (first-touch 페이퍼 체결) ──
                for obs in pending.values():
                    if obs.symbol == symbol:
                        obs.update(bb, now)

                # ── 2) 신규 신호 판정 (cooldown 외) ──
                if now < cooldown_until.get(symbol, now):
                    continue
                mid = (bb + ba) / 2
                candle = OHLCV(symbol=symbol, timestamp=datetime.now(timezone.utc),
                               open=mid, high=ba, low=bb, close=mid, volume=0,
                               market_type=MarketType.STOCK)
                ctx = AnalysisContext(symbol=symbol, name=name or symbol, candles=[candle],
                                      market_type=MarketType.STOCK, orderbook=book)
                sig = strat._analyze_v2(ctx)
                if sig is None:
                    continue

                m = sig.metadata
                tick = int(m.get("tick") or krx_tick_size(ba))
                sl_price = ba * (1.0 - (params.sl_ticks * tick) / ba)
                obs_id = f"{symbol}-{now.strftime('%H%M%S')}-{cycle}"
                obs = Observation(
                    obs_id=obs_id, symbol=symbol, name=name or symbol, signal_ts=now,
                    entry_ask=float(ba), tick=tick, tp_target=float(m["tp_target"]),
                    sl_price=float(sl_price), breakeven_ticks=float(m["breakeven_ticks"]),
                    ofi=float(m["ofi"]), spread_ticks=float(m["spread_ticks"]),
                    net_tp_pct=float(m["net_tp_pct"]),
                    resolve_at=now + timedelta(seconds=args.horizon),
                    min_bid=bb, max_bid=bb, last_bid=bb,
                )
                pending[obs_id] = obs
                cooldown_until[symbol] = now + timedelta(seconds=args.cooldown)
                stats.signals += 1
                logger.write("signal", {
                    "obs_id": obs_id, "symbol": symbol, "name": name,
                    "entry_ask": ba, "best_bid": bb, "tick": tick,
                    "ofi": obs.ofi, "spread_ticks": obs.spread_ticks,
                    "breakeven_ticks": obs.breakeven_ticks, "tp_target": obs.tp_target,
                    "sl_price": round(sl_price, 2), "net_tp_pct": obs.net_tp_pct,
                    "score": sig.score, "reason": sig.reason,
                })
                print(f"[signal] {symbol} {name} · OFI {obs.ofi:+.2f} · 진입 {ba:.0f} → TP {obs.tp_target:.0f} "
                      f"(순+{obs.net_tp_pct:.2f}%) · SL {sl_price:.0f}")

            # ── 3) 해결된 관측 → outcome 로깅 + 집계 ──
            done = [o for o in pending.values() if o.resolved]
            for obs in done:
                net = net_return_pct(obs.entry_ask, obs.exit_bid)          # 실현 순수익률(first-touch)
                net_best = net_return_pct(obs.entry_ask, obs.max_bid)      # 상방 best-case(순)
                stats.resolved += 1
                stats.net_pcts.append(net)
                if obs.outcome == "tp_hit":
                    stats.tp_hits += 1
                elif obs.outcome == "sl_hit":
                    stats.sl_hits += 1
                else:
                    stats.time_exits += 1
                held_s = (min(_now_kst(), obs.resolve_at) - obs.signal_ts).total_seconds()
                logger.write("outcome", {
                    "obs_id": obs.obs_id, "symbol": obs.symbol, "outcome": obs.outcome,
                    "entry_ask": obs.entry_ask, "exit_bid": obs.exit_bid,
                    "net_pct": round(net, 4), "net_best_pct": round(net_best, 4),
                    "max_bid": obs.max_bid, "min_bid": obs.min_bid,
                    "held_seconds": round(held_s, 1), "tp_target": obs.tp_target, "sl_price": obs.sl_price,
                })
                mark = "✅" if net > 0 else "❌"
                print(f"[outcome] {mark} {obs.symbol} {obs.outcome} · 순 {net:+.3f}% "
                      f"(best {net_best:+.3f}%) · {held_s:.0f}s")
                del pending[obs.obs_id]

            stats.polls += 1
            if args.once:
                break
            # 폴링 간 대기 (인터럽트 반응성 위해 잘게)
            slept = 0.0
            while slept < args.interval and not stop["flag"]:
                await asyncio.sleep(min(0.5, args.interval - slept))
                slept += 0.5
            if args.max_cycles and cycle >= args.max_cycles:
                print(f"[shadow] max-cycles {args.max_cycles} 도달 — 종료.")
                break
    except Exception as e:  # noqa: BLE001
        logger.write("error", {"where": "main_loop", "err": repr(e)})
        print(f"[shadow] 예외 종료: {e!r}")
    finally:
        # 종료 시 미해결 pending → 'unresolved' 로깅(투명성·silent drop 방지).
        # horizon 미경과분이므로 기대값/승률 집계엔 제외(절단편향 방지) — net_pcts 에 넣지 않음.
        for obs in pending.values():
            last = obs.last_bid if obs.last_bid > 0 else obs.entry_ask
            logger.write("unresolved", {
                "obs_id": obs.obs_id, "symbol": obs.symbol,
                "entry_ask": obs.entry_ask, "last_bid": last,
                "unrealized_net_pct": round(net_return_pct(obs.entry_ask, last), 4),
                "max_bid": obs.max_bid, "min_bid": obs.min_bid,
                "note": "horizon 미경과 — 기대값 집계 제외",
            })
        summary = {
            "polls": stats.polls, "signals": stats.signals, "resolved": stats.resolved,
            "pending_unresolved": len(pending),
            "tp_hits": stats.tp_hits, "sl_hits": stats.sl_hits, "time_exits": stats.time_exits,
            "win_rate_pct": round(stats.win_rate(), 2),
            "expectancy_net_pct": round(stats.expectancy(), 4),
            "round_trip_cost_pct": round(ROUND_TRIP_COST_PCT * 100, 4),  # [BAR-OPS-39] 실측 연동
        }
        logger.write("session_summary", summary)
        print("\n" + "═" * 60)
        print(f"[shadow] 요약 — 신호 {stats.signals} · 해결 {stats.resolved} "
              f"(TP {stats.tp_hits}/SL {stats.sl_hits}/시간 {stats.time_exits}) · 미해결 {len(pending)}")
        if stats.resolved > 0:
            print(f"[shadow] 적중률(순+) {stats.win_rate():.1f}% · 거래당 기대값(순) {stats.expectancy():+.4f}%")
            print(f"[shadow] 판정 기준: 기대값(순) > 0 이어야 페이퍼/소액 실거래 검토 가능 "
                  f"(왕복비용 ~0.21% 이미 차감됨)")
        else:
            print(f"[shadow] 해결 0건 — 기대값 미산출(관측 horizon 미경과). "
                  f"무한 관측으로 신호 후 {args.horizon:.0f}s 경과분이 쌓여야 판정 가능.")
        print(f"[shadow] 로그: {log_path}")
        print("═" * 60)
        await http.aclose()
    return 0


def _build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(description="ob_scalp 호가 스캘핑 shadow 신호 로깅 (주문 없음)")
    ap.add_argument("--interval", type=float, default=3.0, help="폴링 간격(초, 기본 3)")
    ap.add_argument("--req-delay", type=float, default=0.25, help="종목간 호가요청 간격(초, 429 회피)")
    ap.add_argument("--max-retries", type=int, default=2, help="호가 429·네트워크 오류 재시도 횟수")
    ap.add_argument("--top", type=int, default=8, help="관측 universe 상위 N (기본 8)")
    ap.add_argument("--horizon", type=float, default=60.0, help="신호 후 시간청산 기준(초, 기본 60)")
    ap.add_argument("--cooldown", type=float, default=60.0, help="동일종목 재신호 쿨다운(초)")
    ap.add_argument("--leader-refresh", type=float, default=60.0, help="universe 갱신 간격(초)")
    ap.add_argument("--min-flu", type=float, default=1.0, help="leader 최소 등락률(%)")
    ap.add_argument("--imb", type=float, default=0.55, help="OFI 임계")
    ap.add_argument("--max-spread", type=float, default=2.0, help="스프레드 상한(틱)")
    ap.add_argument("--min-depth", type=float, default=100.0, help="최소 깊이(주)")
    ap.add_argument("--profit-ticks", type=int, default=2, help="순이익 목표 틱(비용커버 위에)")
    ap.add_argument("--sl-ticks", type=int, default=3, help="손절 틱")
    ap.add_argument("--max-breakeven", type=float, default=4.0, help="비용커버 최대 허용 틱")
    ap.add_argument("--slippage", type=float, default=0.0, help="슬리피지 가정(틱)")
    ap.add_argument("--once", action="store_true", help="1회 폴링 후 종료(연결 점검)")
    ap.add_argument("--max-cycles", type=int, default=0, help="최대 사이클(0=무한)")
    ap.add_argument("--ignore-hours", action="store_true", help="장외에도 강제 실행(점검용)")
    ap.add_argument("--equity-only", dest="equity_only", action="store_true", default=True,
                    help="순수 6자리 주식만 관측(ETF/ETN 제외, 기본 on — 틱모델 정합)")
    ap.add_argument("--all-symbols", dest="equity_only", action="store_false",
                    help="ETF/ETN 포함 전체 universe 관측")
    return ap


if __name__ == "__main__":
    args = _build_parser().parse_args()
    raise SystemExit(asyncio.run(run_shadow(args)))
