#!/usr/bin/env python3
"""
BAR-OPS-09 Phase D2.1 단타 일일 모니터링 (B1 v2 — 2026-05-29 보강).

운영 머신에서 매일 장 마감 후 실행:
    venv/bin/python scripts/daytrading_daily_monitor.py
    venv/bin/python scripts/daytrading_daily_monitor.py --date 2026-05-29

소스:
  1. data/barro_trade.db  trades 테이블 (strategy_id 포함, 신뢰성 최상)
  2. data/order_audit.csv (백업 감사 + 미청산 종목 검출 — B1 v2)
  3. data/active_positions.json (현재 보유 — 시스템 측 인식)
  4. logs/barro.log (JSON line — "신호 발생 [strategy]" 패턴)

출력:
  - 콘솔 컬러 요약
  - reports/daytrading_daily_<date>.json
  - reports/daytrading_daily_<date>.md

알람 조건 (docs/04-report/features/2026-05-28-daytrading-strategies-analysis.md §6.3):
  - gold_zone 일 trade > 20 + 승률 < 30% → 비활성 권고
  - 단일 전략 자본가중 누적 -3% → 사후 분석 권고
  - sf_zone 일주일 누적 trade 0건 → 임계 완화 시뮬 권고

B1 v2 (2026-05-29) — 미청산 종목 자동 감지 알람:
  - order_audit.csv 의 전체 buy/sell 누적 매칭으로 미청산 종목 검출.
  - active_positions.json 과 비교해 불일치 시 HIGH 알람.
  - 2026-05-29 swing_38 잔여 4종목(001820/006660/012330/034220) 인시던트 재발 방지.
  - active_positions {} 빈 객체 + order_audit buy > sell 누적 양수인 종목 발견 시 → 시스템 동기화 누락.
"""
from __future__ import annotations

import argparse
import csv as csv_mod
import json
import re
import sqlite3
import statistics
import sys
from collections import defaultdict
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any, Optional

# ── 활성·전체 전략 키 ──
ACTIVE_STRATEGIES = ["sf_zone", "f_zone", "gold_zone"]
ALL_STRATEGIES = ACTIVE_STRATEGIES + ["blue_line", "crypto_breakout", "swing_38"]

# ── 경로 (운영 머신 기준 상대 경로) ──
PROJECT_ROOT = Path(__file__).resolve().parents[1]
DB_PATH = PROJECT_ROOT / "data" / "barro_trade.db"
ORDER_AUDIT_CSV = PROJECT_ROOT / "data" / "order_audit.csv"
ACTIVE_POSITIONS_JSON = PROJECT_ROOT / "data" / "active_positions.json"
BARRO_LOG = PROJECT_ROOT / "logs" / "barro.log"
REPORTS_DIR = PROJECT_ROOT / "reports"


def _parse_barro_log_signals(log_path: Path, target_day: date) -> dict[str, int]:
    """barro.log JSON line 에서 target_day 의 '신호 발생 [strategy]' 횟수 집계."""
    counts: dict[str, int] = defaultdict(int)
    if not log_path.exists():
        return counts
    pattern = re.compile(r"신호 발생 \[([a-z_]+)\]")
    target_str = target_day.strftime("%Y-%m-%d")
    with log_path.open("r", encoding="utf-8", errors="ignore") as f:
        for line in f:
            if target_str not in line:
                continue
            # JSON line 또는 plain text 양쪽 대응
            try:
                obj = json.loads(line)
                msg = obj.get("msg", "")
            except (ValueError, json.JSONDecodeError):
                msg = line
            m = pattern.search(msg)
            if m:
                counts[m.group(1)] += 1
    return counts


def _load_trades(db_path: Path, target_day: date) -> list[dict[str, Any]]:
    """trades 테이블에서 target_day 의 모든 trade 추출."""
    if not db_path.exists():
        return []
    con = sqlite3.connect(str(db_path))
    cur = con.cursor()
    day_start = target_day.isoformat() + "T00:00:00"
    day_end = (target_day + timedelta(days=1)).isoformat() + "T00:00:00"
    try:
        rows = cur.execute(
            "SELECT id, symbol, side, order_type, quantity, price, strategy_id, "
            "order_id, status, created_at "
            "FROM trades WHERE created_at >= ? AND created_at < ? "
            "ORDER BY created_at ASC",
            (day_start, day_end),
        ).fetchall()
        cols = [d[0] for d in cur.description]
        return [dict(zip(cols, r)) for r in rows]
    except sqlite3.OperationalError:
        return []
    finally:
        con.close()


def _load_active_positions(json_path: Path) -> dict[str, Any]:
    if not json_path.exists():
        return {}
    try:
        return json.loads(json_path.read_text())
    except (ValueError, json.JSONDecodeError):
        return {}


def _match_trades_to_pnl(trades: list[dict]) -> dict[str, list[float]]:
    """전략별로 buy→sell FIFO 매칭하여 PnL% 계산.

    반환: {strategy_id: [pnl_pct1, pnl_pct2, ...]}
    """
    by_symbol_strategy: dict[tuple[str, str], list[dict]] = defaultdict(list)
    for t in trades:
        key = (t["symbol"], t["strategy_id"] or "unknown")
        by_symbol_strategy[key].append(t)

    pnl_by_strategy: dict[str, list[float]] = defaultdict(list)
    for (_sym, strat), tlist in by_symbol_strategy.items():
        # 시각 순 정렬
        tlist.sort(key=lambda x: x["created_at"])
        buy_stack: list[dict] = []
        for t in tlist:
            side = (t["side"] or "").lower()
            if side in ("buy", "bid"):
                buy_stack.append(t)
            elif side in ("sell", "ask") and buy_stack:
                # FIFO 매칭
                buy = buy_stack.pop(0)
                buy_p = float(buy["price"]) if buy["price"] not in (None, "MKT") else 0
                sell_p = float(t["price"]) if t["price"] not in (None, "MKT") else 0
                if buy_p > 0 and sell_p > 0:
                    pnl_by_strategy[strat].append((sell_p - buy_p) / buy_p)
    return dict(pnl_by_strategy)


# ── B1 v2 (2026-05-29) — order_audit.csv 기반 미청산 종목 검출 ──

def _aggregate_unfilled_from_csv(
    csv_path: Path, max_lookback_days: int = 60,
) -> dict[str, dict[str, Any]]:
    """order_audit.csv 의 전체 buy/sell 누적으로 종목별 미청산 수량 추정.

    Args:
        csv_path: order_audit.csv 경로
        max_lookback_days: 과거 N일 데이터만 (None=전체)
                           default 60일 — swing_38 max_hold=20일 + 보수 여유.

    Returns:
        {symbol: {"buy_qty": int, "sell_qty": int, "net_qty": int,
                  "buy_count": int, "sell_count": int,
                  "first_buy_ts": str, "last_buy_ts": str, "last_sell_ts": str}}
        net_qty > 0 인 종목만 포함 (= 미청산 추정).
    """
    if not csv_path.exists():
        return {}

    cutoff_ts: Optional[str] = None
    if max_lookback_days is not None:
        cutoff = datetime.now() - timedelta(days=max_lookback_days)
        cutoff_ts = cutoff.isoformat()

    by_symbol: dict[str, dict[str, Any]] = defaultdict(lambda: {
        "buy_qty": 0, "sell_qty": 0,
        "buy_count": 0, "sell_count": 0,
        "first_buy_ts": None, "last_buy_ts": None, "last_sell_ts": None,
    })

    with csv_path.open("r", encoding="utf-8") as f:
        reader = csv_mod.DictReader(f)
        for row in reader:
            ts = row.get("ts", "")
            action = (row.get("action") or "").upper()
            side = (row.get("side") or "").lower()
            symbol = row.get("symbol", "").strip()
            try:
                qty = int(row.get("qty", "0") or 0)
            except ValueError:
                qty = 0
            rc = row.get("return_code", "")

            # DRY_RUN 은 미체결 — 무시. ORDERED + return_code=0 만 매칭.
            if action == "DRY_RUN":
                continue
            if rc != "0":
                continue
            if not symbol or qty <= 0:
                continue
            if cutoff_ts is not None and ts and ts < cutoff_ts:
                continue

            bucket = by_symbol[symbol]
            if side in ("buy", "bid"):
                bucket["buy_qty"] += qty
                bucket["buy_count"] += 1
                if bucket["first_buy_ts"] is None or ts < bucket["first_buy_ts"]:
                    bucket["first_buy_ts"] = ts
                if bucket["last_buy_ts"] is None or ts > bucket["last_buy_ts"]:
                    bucket["last_buy_ts"] = ts
            elif side in ("sell", "ask"):
                bucket["sell_qty"] += qty
                bucket["sell_count"] += 1
                if bucket["last_sell_ts"] is None or ts > bucket["last_sell_ts"]:
                    bucket["last_sell_ts"] = ts

    # net_qty 계산 + 미청산만 반환
    unfilled: dict[str, dict[str, Any]] = {}
    for symbol, bucket in by_symbol.items():
        net = bucket["buy_qty"] - bucket["sell_qty"]
        if net > 0:
            bucket["net_qty"] = net
            bucket["symbol"] = symbol
            unfilled[symbol] = bucket
    return unfilled


def _detect_position_discrepancy(
    unfilled: dict[str, dict[str, Any]],
    active_positions: dict[str, Any],
    stale_buy_days: int = 7,
) -> dict[str, list[dict]]:
    """미청산(order_audit 기준) vs active_positions 불일치 검출.

    v2.1 (2026-05-29, 4번 작업): CSV ground truth 가정 약화.
      - 439960 케이스 — broker 청산됐는데 CSV sell 행 누락 가능.
      - 마지막 buy 가 stale_buy_days 이전이면 "stale" 라벨 → MEDIUM 알람으로 다운그레이드 가능.
      - 즉 갓 매수한 종목 잔여는 동기화 누락이 강함(HIGH), 오래된 잔여는 CSV 누락 의심(MEDIUM).

    Returns:
        {
          "unfilled_not_in_active": [
            {symbol, net_qty, ..., stale: bool, days_since_last_buy: int},
            ...
          ],
          "active_not_in_unfilled": [...],
          "matched": [...],
          "matched_count": int,
        }
    """
    unfilled_symbols = set(unfilled.keys())
    active_symbols = set(active_positions.keys()) if active_positions else set()

    only_in_csv = unfilled_symbols - active_symbols       # ★ 어제 인시던트 패턴
    only_in_active = active_symbols - unfilled_symbols    # 이상 케이스
    both = unfilled_symbols & active_symbols              # 일치 OK

    # v2.1: stale 판정 — 마지막 buy 가 stale_buy_days 이전인지
    now_iso = datetime.now().isoformat()
    cutoff_iso = (datetime.now() - timedelta(days=stale_buy_days)).isoformat()

    discrepancy_items = []
    for s in sorted(only_in_csv):
        item = {**unfilled[s], "symbol": s}
        last_buy = item.get("last_buy_ts", "") or ""
        is_stale = bool(last_buy and last_buy < cutoff_iso)
        item["stale"] = is_stale
        if last_buy:
            try:
                last_buy_dt = datetime.fromisoformat(last_buy.replace("+00:00", "+00:00"))
                # naive comparison
                days_diff = (datetime.now() - last_buy_dt.replace(tzinfo=None)).days
                item["days_since_last_buy"] = days_diff
            except (ValueError, AttributeError):
                item["days_since_last_buy"] = None
        else:
            item["days_since_last_buy"] = None
        discrepancy_items.append(item)

    return {
        "unfilled_not_in_active": discrepancy_items,
        "active_not_in_unfilled": [
            {"symbol": s, "active_data": active_positions[s]}
            for s in sorted(only_in_active)
        ],
        "matched": sorted(both),
        "matched_count": len(both),
        "stale_buy_days_threshold": stale_buy_days,
    }


def _aggregate(
    signals: dict[str, int],
    trades: list[dict],
    pnl_by_strategy: dict[str, list[float]],
    active_positions: dict[str, Any],
) -> dict[str, Any]:
    """전략별 통합 집계."""
    # 전략별 trade 수 (buy + sell)
    trade_count_by_strategy: dict[str, dict[str, int]] = defaultdict(lambda: {"buy": 0, "sell": 0})
    for t in trades:
        strat = t["strategy_id"] or "unknown"
        side = (t["side"] or "").lower()
        if side in ("buy", "bid"):
            trade_count_by_strategy[strat]["buy"] += 1
        elif side in ("sell", "ask"):
            trade_count_by_strategy[strat]["sell"] += 1

    per_strategy: dict[str, dict[str, Any]] = {}
    for strat in ALL_STRATEGIES + sorted(set(trade_count_by_strategy.keys()) - set(ALL_STRATEGIES)):
        pnls = pnl_by_strategy.get(strat, [])
        n_close = len(pnls)
        n_win = sum(1 for p in pnls if p > 0)
        avg_pnl = (statistics.mean(pnls) * 100) if pnls else None
        sum_pnl = (sum(pnls) * 100) if pnls else None
        per_strategy[strat] = {
            "active": strat in ACTIVE_STRATEGIES,
            "signals": signals.get(strat, 0),
            "trades_buy": trade_count_by_strategy[strat]["buy"],
            "trades_sell": trade_count_by_strategy[strat]["sell"],
            "closed_pairs": n_close,
            "win_count": n_win,
            "win_rate_pct": round(n_win / n_close * 100, 2) if n_close > 0 else None,
            "avg_pnl_pct": round(avg_pnl, 3) if avg_pnl is not None else None,
            "sum_pnl_pct": round(sum_pnl, 3) if sum_pnl is not None else None,
        }
    return {
        "per_strategy": per_strategy,
        "active_positions_count": len(active_positions),
        "active_positions": active_positions,
        "trades_total": len(trades),
        "signals_total": sum(signals.values()),
    }


def _check_alarms(
    per_strategy: dict[str, dict],
    week_signals: dict[str, int],
    discrepancy: Optional[dict[str, list[dict]]] = None,
) -> list[dict]:
    """알람 조건 검사 — 분석 리포트 §6.3 + B1 v2 미청산 검출."""
    alarms: list[dict] = []
    # gold_zone 일 trade > 20 + 승률 < 30%
    gz = per_strategy.get("gold_zone", {})
    if gz.get("closed_pairs", 0) > 20 and (gz.get("win_rate_pct") or 100) < 30:
        alarms.append({
            "level": "HIGH",
            "key": "gold_zone_high_volume_low_win",
            "msg": (
                f"gold_zone closed {gz['closed_pairs']} > 20 + 승률 {gz['win_rate_pct']}% < 30% "
                "→ enabled_strategies={'gold_zone': False} 즉시 비활성 권고"
            ),
        })
    # 단일 전략 자본가중 누적 -3% 이상
    for strat, st in per_strategy.items():
        if st.get("sum_pnl_pct") is not None and st["sum_pnl_pct"] <= -3.0:
            alarms.append({
                "level": "HIGH",
                "key": f"{strat}_cumulative_loss",
                "msg": (
                    f"{strat} 누적 PnL {st['sum_pnl_pct']:+.2f}% ≤ -3% "
                    "→ 해당 전략 비활성 + 사후 분석"
                ),
            })
    # sf_zone 주간 시그널 0
    if week_signals.get("sf_zone", 0) == 0:
        alarms.append({
            "level": "MEDIUM",
            "key": "sf_zone_no_signal_week",
            "msg": "sf_zone 일주일 누적 신호 0건 → score≥7.0 임계 완화 시뮬 권고",
        })

    # ── B1 v2 (2026-05-29) — 미청산 종목 불일치 ──
    # v2.1 (4번 작업): stale 종목은 MEDIUM 으로 다운그레이드 (CSV sell 누락 가능 — 439960 패턴).
    if discrepancy is not None:
        for item in discrepancy.get("unfilled_not_in_active", []):
            symbol = item["symbol"]
            net_qty = item["net_qty"]
            buy_qty = item["buy_qty"]
            sell_qty = item["sell_qty"]
            last_buy = (item.get("last_buy_ts") or "")[:10]
            is_stale = item.get("stale", False)
            days_diff = item.get("days_since_last_buy")
            if is_stale:
                # MEDIUM: 오래된 잔여 — broker 청산 + CSV sell 누락 가능성 ↑ (439960 패턴)
                alarms.append({
                    "level": "MEDIUM",
                    "key": f"unfilled_stale_{symbol}",
                    "msg": (
                        f"⚠ {symbol} order_audit 미청산 {net_qty}주 (last buy {days_diff}일 전, {last_buy}) "
                        "— stale → broker 잔고 직접 조회로 청산 여부 확인 "
                        "(CSV sell 행 누락 가능성)"
                    ),
                })
            else:
                # HIGH: 최근 매수 종목인데 active 누락 (어제 인시던트 패턴, 5/28 4종목)
                alarms.append({
                    "level": "HIGH",
                    "key": f"unfilled_not_in_active_{symbol}",
                    "msg": (
                        f"⚠ {symbol} order_audit 미청산 {net_qty}주 (buy {buy_qty} − sell {sell_qty}, "
                        f"마지막 buy {last_buy}) — active_positions.json 누락 "
                        "→ 시스템 동기화 점검 + broker 잔고 직접 조회"
                    ),
                })
        for item in discrepancy.get("active_not_in_unfilled", []):
            symbol = item["symbol"]
            alarms.append({
                "level": "HIGH",
                "key": f"active_not_in_unfilled_{symbol}",
                "msg": (
                    f"⚠ {symbol} active_positions 보유인데 order_audit buy 이력 없음 "
                    "→ 데이터 일관성 점검 (수동 입력 또는 데이터 손실 가능성)"
                ),
            })
    return alarms


def _render_console(report: dict, target_day: date) -> str:
    """터미널 출력 (컬러 ANSI)."""
    G, Y, R, B, RESET = "\033[32m", "\033[33m", "\033[31m", "\033[36m", "\033[0m"
    lines = []
    lines.append(f"\n{B}=== BAR-OPS-09 Phase D2.1 단타 일일 모니터링 — {target_day} ==={RESET}")
    lines.append(f"\n총 시그널 {report['signals_total']}건 | 총 trade {report['trades_total']}건 | 활성 보유 {report['active_positions_count']}종목")

    lines.append(f"\n{B}── 전략별 ──{RESET}")
    lines.append(
        f"{'전략':<18} {'활성':>4} {'시그널':>6} {'매수':>4} {'매도':>4} {'closed':>6} "
        f"{'승률':>6} {'평균PnL%':>10} {'누적PnL%':>10}"
    )
    lines.append("-" * 80)
    for strat, st in report["per_strategy"].items():
        active = G + "ON " + RESET if st["active"] else "OFF"
        win = f"{st['win_rate_pct']:.1f}%" if st["win_rate_pct"] is not None else "-"
        avg = f"{st['avg_pnl_pct']:+.3f}%" if st["avg_pnl_pct"] is not None else "-"
        sumv = f"{st['sum_pnl_pct']:+.3f}%" if st["sum_pnl_pct"] is not None else "-"
        sum_color = R if (st["sum_pnl_pct"] or 0) < 0 else G if (st["sum_pnl_pct"] or 0) > 0 else ""
        sumv_str = f"{sum_color}{sumv}{RESET}" if sum_color else sumv
        lines.append(
            f"{strat:<18} {active:>4} {st['signals']:>6} {st['trades_buy']:>4} {st['trades_sell']:>4} "
            f"{st['closed_pairs']:>6} {win:>6} {avg:>10} {sumv_str:>16}"
        )

    if report["active_positions_count"] > 0:
        lines.append(f"\n{B}── 활성 보유 ──{RESET}")
        for sym, pos in report["active_positions"].items():
            lines.append(f"  {sym}: {pos}")

    # B1 v2: 미청산(order_audit 기준) + 불일치 출력
    discrepancy = report.get("discrepancy", {})
    unfilled_not_in_active = discrepancy.get("unfilled_not_in_active", [])
    active_not_in_unfilled = discrepancy.get("active_not_in_unfilled", [])
    matched = discrepancy.get("matched", [])

    lines.append(f"\n{B}── order_audit 미청산 검출 (B1 v2) ──{RESET}")
    lines.append(
        f"  active_positions 일치: {len(matched)}건 / "
        f"⚠ order_audit 미청산-active 누락: {len(unfilled_not_in_active)}건 / "
        f"⚠ active-order_audit 누락: {len(active_not_in_unfilled)}건"
    )
    if unfilled_not_in_active:
        lines.append(f"  {R}● order_audit 미청산이지만 active_positions 누락:{RESET}")
        for item in unfilled_not_in_active:
            last_buy = (item.get("last_buy_ts") or "")[:10]
            last_sell = (item.get("last_sell_ts") or "")[:10] or "없음"
            lines.append(
                f"    {R}{item['symbol']}{RESET}: net {item['net_qty']}주 "
                f"(buy {item['buy_qty']}/sell {item['sell_qty']}, "
                f"마지막 buy {last_buy} · sell {last_sell})"
            )
    if active_not_in_unfilled:
        lines.append(f"  {R}● active_positions 인데 order_audit buy 이력 없음:{RESET}")
        for item in active_not_in_unfilled:
            lines.append(f"    {R}{item['symbol']}{RESET}: {item['active_data']}")

    alarms = report.get("alarms", [])
    if alarms:
        lines.append(f"\n{Y}── 알람 ──{RESET}")
        for a in alarms:
            color = R if a["level"] == "HIGH" else Y
            lines.append(f"  {color}[{a['level']}]{RESET} {a['msg']}")
    else:
        lines.append(f"\n{G}── 알람 없음 ──{RESET}")
    return "\n".join(lines) + "\n"


def _render_markdown(report: dict, target_day: date, alarms: list[dict]) -> str:
    md = [
        f"# BAR-OPS-09 Phase D2.1 단타 일일 모니터링 — {target_day}",
        "",
        f"- 총 시그널: **{report['signals_total']}건**",
        f"- 총 trade: **{report['trades_total']}건**",
        f"- 활성 보유: **{report['active_positions_count']}종목**",
        "",
        "## 전략별 활동",
        "",
        "| 전략 | 활성 | 시그널 | 매수 | 매도 | closed | 승률 | 평균 PnL% | 누적 PnL% |",
        "|---|---:|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for strat, st in report["per_strategy"].items():
        active = "✅" if st["active"] else "⏸"
        win = f"{st['win_rate_pct']:.2f}%" if st["win_rate_pct"] is not None else "—"
        avg = f"{st['avg_pnl_pct']:+.3f}%" if st["avg_pnl_pct"] is not None else "—"
        sumv = f"{st['sum_pnl_pct']:+.3f}%" if st["sum_pnl_pct"] is not None else "—"
        md.append(
            f"| {strat} | {active} | {st['signals']} | {st['trades_buy']} | {st['trades_sell']} | "
            f"{st['closed_pairs']} | {win} | {avg} | {sumv} |"
        )

    if report["active_positions_count"] > 0:
        md.append("")
        md.append("## 활성 보유")
        md.append("```json")
        md.append(json.dumps(report["active_positions"], ensure_ascii=False, indent=2))
        md.append("```")

    # B1 v2: 미청산 + 불일치 출력
    discrepancy = report.get("discrepancy", {})
    unfilled_not_in_active = discrepancy.get("unfilled_not_in_active", [])
    active_not_in_unfilled = discrepancy.get("active_not_in_unfilled", [])
    matched = discrepancy.get("matched", [])
    md.append("")
    md.append("## order_audit 미청산 검출 (B1 v2)")
    md.append("")
    md.append(
        f"- active_positions 일치: **{len(matched)}건**"
    )
    md.append(
        f"- ⚠ order_audit 미청산이지만 active_positions 누락: **{len(unfilled_not_in_active)}건**"
    )
    md.append(
        f"- ⚠ active_positions 인데 order_audit buy 이력 없음: **{len(active_not_in_unfilled)}건**"
    )
    if unfilled_not_in_active:
        md.append("")
        md.append("### 동기화 누락 종목 (order_audit 미청산 + active 누락)")
        md.append("")
        md.append("| 종목 | net 주 | buy 주 | sell 주 | 마지막 buy | 마지막 sell |")
        md.append("|---|---:|---:|---:|---|---|")
        for item in unfilled_not_in_active:
            md.append(
                f"| {item['symbol']} | {item['net_qty']} | {item['buy_qty']} | "
                f"{item['sell_qty']} | {(item.get('last_buy_ts') or '')[:10]} | "
                f"{(item.get('last_sell_ts') or '')[:10] or '—'} |"
            )

    md.append("")
    md.append("## 알람")
    if alarms:
        for a in alarms:
            md.append(f"- **[{a['level']}]** {a['msg']}")
    else:
        md.append("- (조건 미달 — 알람 없음)")
    return "\n".join(md) + "\n"


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--date", type=str, default=None,
        help="대상 날짜 (YYYY-MM-DD). 미지정 시 today.",
    )
    parser.add_argument(
        "--save-md", action="store_true",
        help="reports/ 디렉토리에 MD + JSON 저장",
    )
    args = parser.parse_args()

    target_day = (
        datetime.strptime(args.date, "%Y-%m-%d").date()
        if args.date else date.today()
    )

    # 데이터 수집
    signals = _parse_barro_log_signals(BARRO_LOG, target_day)
    trades = _load_trades(DB_PATH, target_day)
    pnl_by_strategy = _match_trades_to_pnl(trades)
    active_positions = _load_active_positions(ACTIVE_POSITIONS_JSON)

    report = _aggregate(signals, trades, pnl_by_strategy, active_positions)

    # B1 v2: order_audit 미청산 종목 + active_positions 불일치
    unfilled = _aggregate_unfilled_from_csv(ORDER_AUDIT_CSV, max_lookback_days=60)
    discrepancy = _detect_position_discrepancy(unfilled, active_positions)
    report["unfilled_count"] = len(unfilled)
    report["unfilled"] = unfilled
    report["discrepancy"] = discrepancy

    # 주간 시그널 (sf_zone 0건 알람용)
    week_signals: dict[str, int] = defaultdict(int)
    for i in range(7):
        d = target_day - timedelta(days=i)
        for k, v in _parse_barro_log_signals(BARRO_LOG, d).items():
            week_signals[k] += v

    alarms = _check_alarms(report["per_strategy"], week_signals, discrepancy)
    report["alarms"] = alarms
    report["week_signals"] = dict(week_signals)

    # 콘솔 출력
    print(_render_console(report, target_day))

    # 저장
    if args.save_md:
        REPORTS_DIR.mkdir(exist_ok=True)
        json_path = REPORTS_DIR / f"daytrading_daily_{target_day}.json"
        md_path = REPORTS_DIR / f"daytrading_daily_{target_day}.md"
        json_path.write_text(json.dumps(report, ensure_ascii=False, indent=2, default=str))
        md_path.write_text(_render_markdown(report, target_day, alarms))
        print(f"  → JSON: {json_path}")
        print(f"  → MD:   {md_path}")

    # 알람 있을 경우 exit code 1 (cron 알림 활용)
    if any(a["level"] == "HIGH" for a in alarms):
        sys.exit(1)


if __name__ == "__main__":
    main()
