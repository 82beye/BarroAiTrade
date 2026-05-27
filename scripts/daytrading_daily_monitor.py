#!/usr/bin/env python3
"""
BAR-OPS-09 Phase D2.1 단타 일일 모니터링 (B1).

운영 머신에서 매일 장 마감 후 실행:
    venv/bin/python scripts/daytrading_daily_monitor.py
    venv/bin/python scripts/daytrading_daily_monitor.py --date 2026-05-29

소스:
  1. data/barro_trade.db  trades 테이블 (strategy_id 포함, 신뢰성 최상)
  2. data/order_audit.csv (백업 감사 — strategy_id 없음, 행 수만 활용)
  3. data/active_positions.json (현재 보유)
  4. logs/barro.log (JSON line — "신호 발생 [strategy]" 패턴)

출력:
  - 콘솔 컬러 요약
  - reports/daytrading_daily_<date>.json
  - reports/daytrading_daily_<date>.md

알람 조건 (docs/04-report/features/2026-05-28-daytrading-strategies-analysis.md §6.3):
  - gold_zone 일 trade > 20 + 승률 < 30% → 비활성 권고
  - 단일 전략 자본가중 누적 -3% → 사후 분석 권고
  - sf_zone 일주일 누적 trade 0건 → 임계 완화 시뮬 권고
"""
from __future__ import annotations

import argparse
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


def _check_alarms(per_strategy: dict[str, dict], week_signals: dict[str, int]) -> list[dict]:
    """알람 조건 검사 — 분석 리포트 §6.3."""
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

    # 주간 시그널 (sf_zone 0건 알람용)
    week_signals: dict[str, int] = defaultdict(int)
    for i in range(7):
        d = target_day - timedelta(days=i)
        for k, v in _parse_barro_log_signals(BARRO_LOG, d).items():
            week_signals[k] += v

    alarms = _check_alarms(report["per_strategy"], week_signals)
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
