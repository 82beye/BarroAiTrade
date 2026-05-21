"""Phase 1 — 운영 머신 zip 1-click 처리 → 종목별 정확 net + 전략별 audit.

운영 머신에서 받은 BarroAiTrade_*.zip 을 해제하고, kt00009 체결 데이터로
종목별 정확 net 을 계산한 뒤 전략별로 분류해 누적 ledger 에 기록한다.

사용:
    # 라이브 kt00009 호출 (M4 — KIWOOM_APP_KEY/SECRET/ACCOUNT_NO 필요)
    python scripts/_daily_evening_pipeline.py --zip ~/Downloads/BarroAiTrade_x.zip \
        --date 2026-05-21

    # 사전 덤프 파일로 (라이브 호출 없이 — 원격 검증 가능)
    python scripts/_daily_evening_pipeline.py --date 2026-05-21 \
        --executions-file fixtures/kt00009_2026-05-21.json --import-dir analysis/imports/2026-05-21
"""
from __future__ import annotations

import argparse
import asyncio
import csv
import glob
import json
import os
import sys
import zipfile
from datetime import datetime
from decimal import Decimal
from pathlib import Path
from typing import Callable, Optional

_REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_REPO_ROOT))

from backend.core.journal.active_positions import ActivePositionStore

STRATEGY_NAMES = ["f_zone", "sf_zone", "gold_zone", "swing_38", "scalping_consensus"]
COMMISSION_RATE = Decimal("0.00015")   # 매수·매도 각 0.015% (키움 위탁 표준)
TAX_RATE = Decimal("0.0018")           # 매도 시 0.18% (증권거래세+농특세)

IMPORTS_ROOT = _REPO_ROOT / "analysis" / "imports"
LEDGER_PATH = _REPO_ROOT / "analysis" / "strategy_ledger.csv"
LEDGER_HEADER = [
    "date", "symbol", "name", "strategy",
    "buy_avg", "sell_avg", "qty", "net", "result",
]


# ─── 순수 로직 (테스트 대상) ──────────────────────────────────────────────


def normalize_execution(raw: dict) -> dict:
    """kt00009 체결 dict 또는 fixture row → 정규 스키마.

    반환: {code, name, side('buy'|'sell'), qty:Decimal, price:Decimal, time}.
    """
    code = str(raw.get("code") or raw.get("stk_cd") or "").strip().lstrip("A")
    name = str(raw.get("name") or raw.get("stk_nm") or "").strip()
    blob = " ".join(
        str(raw.get(k, ""))
        for k in ("trade_type", "order_type", "side", "trde_tp", "io_tp_nm")
    )
    if "매도" in blob or "sell" in blob.lower():
        side = "sell"
    else:
        side = "buy"
    qty = raw.get("filled_qty") or raw.get("qty") or 0
    price = raw.get("filled_price") or raw.get("price") or 0
    return {
        "code": code,
        "name": name,
        "side": side,
        "qty": Decimal(str(qty or 0)),
        "price": Decimal(str(price or 0)),
        "time": str(raw.get("time") or raw.get("cntr_tm") or "").strip(),
    }


def compute_net(buy_avg: Decimal, sell_avg: Decimal, qty: Decimal) -> Decimal:
    """매수·매도 평단 기준 정확 net — 수수료(매수+매도) + 세금(매도) 차감."""
    gross = (sell_avg - buy_avg) * qty
    commission = (buy_avg + sell_avg) * qty * COMMISSION_RATE
    tax = sell_avg * qty * TAX_RATE
    return gross - commission - tax


def aggregate_by_symbol(executions: list[dict]) -> dict[str, dict]:
    """정규화된 체결 리스트 → 종목별 매수/매도 평단·수량·net."""
    acc: dict[str, dict] = {}
    for e in executions:
        code = e["code"]
        if not code or e["qty"] <= 0 or e["price"] <= 0:
            continue
        s = acc.setdefault(code, {
            "name": e["name"],
            "buy_qty": Decimal(0), "buy_value": Decimal(0),
            "sell_qty": Decimal(0), "sell_value": Decimal(0),
            "buy_time": "", "sell_time": "",
        })
        if e["name"] and not s["name"]:
            s["name"] = e["name"]
        if e["side"] == "buy":
            s["buy_qty"] += e["qty"]
            s["buy_value"] += e["qty"] * e["price"]
            if e["time"] and (not s["buy_time"] or e["time"] < s["buy_time"]):
                s["buy_time"] = e["time"]
        else:
            s["sell_qty"] += e["qty"]
            s["sell_value"] += e["qty"] * e["price"]
            if e["time"] and e["time"] > s["sell_time"]:
                s["sell_time"] = e["time"]

    out: dict[str, dict] = {}
    for code, s in acc.items():
        buy_avg = s["buy_value"] / s["buy_qty"] if s["buy_qty"] > 0 else Decimal(0)
        sell_avg = s["sell_value"] / s["sell_qty"] if s["sell_qty"] > 0 else Decimal(0)
        matched = min(s["buy_qty"], s["sell_qty"])
        net = compute_net(buy_avg, sell_avg, matched) if matched > 0 else Decimal(0)
        if s["sell_qty"] <= 0:
            result = "open"
        elif net > 0:
            result = "익절"
        else:
            result = "손실"
        out[code] = {
            "code": code, "name": s["name"],
            "buy_avg": buy_avg, "sell_avg": sell_avg,
            "qty": matched, "net": net, "result": result,
            "buy_time": s["buy_time"], "sell_time": s["sell_time"],
        }
    return out


def attribute_from_logs(code: str, logs_text: str) -> Optional[str]:
    """로그에서 종목코드 + 전략명이 같은 줄에 있으면 전략 추정 (best-effort)."""
    for line in logs_text.splitlines():
        if code not in line:
            continue
        for sid in STRATEGY_NAMES:
            if sid in line:
                return sid
    return None


def attribute_strategy(
    code: str,
    active_positions: dict,
    logs_text: str,
    sim_fn: Optional[Callable[[str], Optional[str]]] = None,
) -> str:
    """다단계 fallback — active_positions → logs → IntradaySimulator → 'unknown'."""
    pos = active_positions.get(code)
    if pos is not None and getattr(pos, "strategy", ""):
        return pos.strategy
    from_logs = attribute_from_logs(code, logs_text)
    if from_logs:
        return from_logs
    if sim_fn is not None:
        sim_strat = sim_fn(code)
        if sim_strat:
            return sim_strat
    return "unknown"


def update_ledger(ledger_path: Path, date: str, rows: list[dict]) -> None:
    """동일 date 행을 교체 후 ledger 재작성 (재실행 idempotent)."""
    kept: list[dict] = []
    if ledger_path.exists():
        with open(ledger_path, newline="", encoding="utf-8") as f:
            kept = [r for r in csv.DictReader(f) if r.get("date") != date]
    ledger_path.parent.mkdir(parents=True, exist_ok=True)
    with open(ledger_path, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=LEDGER_HEADER)
        w.writeheader()
        for r in kept + rows:
            w.writerow({k: r.get(k, "") for k in LEDGER_HEADER})


# ─── I/O ────────────────────────────────────────────────────────────────


def latest_zip() -> Optional[Path]:
    hits = glob.glob(os.path.expanduser("~/Downloads/BarroAiTrade_*.zip"))
    return Path(max(hits, key=os.path.getmtime)) if hits else None


def extract_zip(zip_path: Path, date: str) -> Path:
    """zip → analysis/imports/<date>/ 전체 해제."""
    dest = IMPORTS_ROOT / date
    dest.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(zip_path) as zf:
        zf.extractall(dest)
    return dest


def find_subdir(import_dir: Path, name: str) -> Optional[Path]:
    """import_dir 안에서 data/ logs/ ohlcv_cache/ 디렉터리 위치 탐색."""
    direct = import_dir / name
    if direct.is_dir():
        return direct
    for p in import_dir.rglob(name):
        if p.is_dir():
            return p
    return None


def load_executions_file(path: Path) -> list[dict]:
    """사전 덤프 (kt00009 JSON/CSV) 로드."""
    if path.suffix.lower() == ".json":
        data = json.loads(path.read_text(encoding="utf-8"))
        if isinstance(data, dict):
            for key in ("list", "executions", "acnt_ord_cntr_prst_array"):
                if key in data:
                    return list(data[key])
            return []
        return list(data)
    with open(path, newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


async def fetch_executions_live(date: str, mode: str) -> list[dict]:
    """legacy KiwoomRestAPI 로 kt00009 체결 라이브 조회 (M4 전용)."""
    legacy_root = _REPO_ROOT / "backend" / "legacy_scalping"
    if str(legacy_root) not in sys.path:
        sys.path.insert(0, str(legacy_root))
    from execution.kiwoom_api import KiwoomRestAPI  # type: ignore

    api = KiwoomRestAPI({"mode": mode, "kiwoom": {}})
    return await api.get_order_executions(ord_dt=date.replace("-", ""), qry_tp="1")


def load_symbol_candles(ohlcv_dir: Path, code: str):
    """ohlcv_cache 에서 종목 CSV 1개 로드 (best-effort)."""
    from backend.core.backtester import load_csv_candles

    for path in sorted(ohlcv_dir.rglob(f"*{code}*")):
        if path.suffix.lower() != ".csv":
            continue
        try:
            return load_csv_candles(path, symbol=code)
        except Exception:
            continue
    return []


def build_sim_attributor(
    ohlcv_dir: Optional[Path], symbols: dict[str, dict]
) -> Optional[Callable[[str], Optional[str]]]:
    """IntradaySimulator 기반 전략 추정 함수 (Tier 3)."""
    if ohlcv_dir is None:
        return None

    def _attr(code: str) -> Optional[str]:
        candles = load_symbol_candles(ohlcv_dir, code)
        if not candles or len(candles) < 31:
            return None
        try:
            from backend.core.backtester import IntradaySimulator

            result = IntradaySimulator().run(
                candles, symbol=code,
                strategies=["f_zone", "sf_zone", "gold_zone", "swing_38"],
            )
        except Exception:
            return None
        buys = [t for t in result.trades if t.side == "buy"]
        if not buys:
            return None
        target = symbols.get(code, {}).get("buy_time", "")
        if target:
            buys.sort(key=lambda t: abs(
                int(t.timestamp.strftime("%H%M%S")) - int(target[:6] or 0)
            ))
        return buys[0].strategy_id

    return _attr


# ─── 출력 ────────────────────────────────────────────────────────────────


def render_table(rows: list[dict]) -> str:
    lines = [
        f"{'종목':>8} {'이름':<14} {'전략':<18} "
        f"{'매수평단':>10} {'매도평단':>10} {'수량':>6} {'net':>12} {'결과':<6}",
        "─" * 96,
    ]
    for r in sorted(rows, key=lambda x: x["net"]):
        lines.append(
            f"{r['symbol']:>8} {r['name'][:13]:<14} {r['strategy']:<18} "
            f"{int(r['buy_avg']):>10,} {int(r['sell_avg']):>10,} "
            f"{int(r['qty']):>6,} {int(r['net']):>+12,} {r['result']:<6}"
        )
    return "\n".join(lines)


def render_strategy_totals(rows: list[dict]) -> str:
    agg: dict[str, dict] = {}
    for r in rows:
        a = agg.setdefault(r["strategy"], {"net": 0, "wins": 0, "closed": 0, "n": 0})
        a["net"] += int(r["net"])
        a["n"] += 1
        if r["result"] in ("익절", "손실"):
            a["closed"] += 1
            if r["result"] == "익절":
                a["wins"] += 1
    lines = ["", f"{'전략':<18} {'net':>12} {'승률':>8} {'종목수':>6}", "─" * 48]
    for sid, a in sorted(agg.items(), key=lambda kv: kv[1]["net"]):
        wr = a["wins"] / a["closed"] if a["closed"] else 0.0
        lines.append(f"{sid:<18} {a['net']:>+12,} {wr:>7.1%} {a['n']:>6}")
    return "\n".join(lines)


# ─── 오케스트레이션 ───────────────────────────────────────────────────────


def run_pipeline(
    date: str,
    executions: list[dict],
    import_dir: Optional[Path],
) -> list[dict]:
    """정규화 → 집계 → 전략 귀속 → ledger 행 생성. import_dir 없으면 귀속 degrade."""
    normalized = [normalize_execution(e) for e in executions]
    symbols = aggregate_by_symbol(normalized)

    active_positions: dict = {}
    logs_text = ""
    sim_fn = None
    if import_dir is not None:
        data_dir = find_subdir(import_dir, "data")
        if data_dir is not None and (data_dir / "active_positions.json").exists():
            active_positions = ActivePositionStore(
                data_dir / "active_positions.json"
            ).load_all()
        logs_dir = find_subdir(import_dir, "logs")
        if logs_dir is not None:
            parts = []
            for log in sorted(logs_dir.rglob("*.log")):
                try:
                    parts.append(log.read_text(encoding="utf-8", errors="ignore"))
                except OSError:
                    pass
            logs_text = "\n".join(parts)
        sim_fn = build_sim_attributor(find_subdir(import_dir, "ohlcv_cache"), symbols)

    rows: list[dict] = []
    for code, s in symbols.items():
        rows.append({
            "date": date,
            "symbol": code,
            "name": s["name"],
            "strategy": attribute_strategy(code, active_positions, logs_text, sim_fn),
            "buy_avg": int(s["buy_avg"]),
            "sell_avg": int(s["sell_avg"]),
            "qty": int(s["qty"]),
            "net": int(s["net"]),
            "result": s["result"],
        })
    # drill-down 용 정규화 체결 덤프
    if import_dir is not None:
        import_dir.mkdir(parents=True, exist_ok=True)
        dump = [{**e, "qty": str(e["qty"]), "price": str(e["price"])}
                for e in normalized]
        (import_dir / "executions.json").write_text(
            json.dumps(dump, ensure_ascii=False, indent=2), encoding="utf-8"
        )
    return rows


def main() -> None:
    ap = argparse.ArgumentParser(description="Daily 운영 audit 파이프라인 (Phase 1)")
    ap.add_argument("--zip", help="운영 머신 zip (생략 시 ~/Downloads 최신)")
    ap.add_argument("--date", help="영업일 YYYY-MM-DD (생략 시 오늘)")
    ap.add_argument("--executions-file", help="kt00009 사전 덤프 (JSON/CSV)")
    ap.add_argument("--import-dir", help="이미 해제된 디렉터리 (zip 생략 시)")
    ap.add_argument("--mode", default="real", choices=["real", "simulation"],
                    help="라이브 호출 모드 (기본 real)")
    args = ap.parse_args()

    date = args.date or datetime.now().strftime("%Y-%m-%d")

    import_dir: Optional[Path] = None
    if args.import_dir:
        import_dir = Path(args.import_dir)
    elif args.zip or latest_zip():
        zip_path = Path(args.zip) if args.zip else latest_zip()
        print(f"[1] zip 해제: {zip_path}")
        import_dir = extract_zip(zip_path, date)
    elif (IMPORTS_ROOT / date).is_dir():
        import_dir = IMPORTS_ROOT / date
    if import_dir is not None:
        print(f"    import dir: {import_dir}")

    if args.executions_file:
        print(f"[2] 체결 로드 (파일): {args.executions_file}")
        executions = load_executions_file(Path(args.executions_file))
    else:
        print(f"[2] 체결 조회 (라이브 kt00009, mode={args.mode})")
        executions = asyncio.run(fetch_executions_live(date, args.mode))
    print(f"    체결 {len(executions)} 건")

    rows = run_pipeline(date, executions, import_dir)
    if not rows:
        print("종목별 체결 없음.")
        return

    print(f"\n[3] 종목별 정확 net ({date})")
    print(render_table(rows))
    print(render_strategy_totals(rows))

    update_ledger(LEDGER_PATH, date, rows)
    print(f"\n[4] ledger 갱신: {LEDGER_PATH} ({len(rows)} 행, date={date})")


if __name__ == "__main__":
    main()
