"""verify_eod_data.py 회귀 테스트 — 합성 fixture 로 NG/OK/스킵 판정 검증.

BARRO_DATA_DIR 로 데이터 경로를 주입하고 CLI(종료코드=NG건수) 계약을 그대로 검증한다.
6/15 처럼 이브닝 파이프라인이 침묵해 fill_audit·EOD balance·buy_audit 가 누락된 날을
hard-NG 로 잡는지가 핵심.
"""
import csv, json, os, subprocess, sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
SCRIPT = REPO / "scripts" / "verify_eod_data.py"


def _write_order_audit(data: Path, day_iso: str, *, buys: int, sells: int):
    rows = [["ts", "action", "side", "symbol", "qty", "price", "order_no",
             "return_code", "blocked", "reason", "strategy_id", "filled_qty", "avg_fill_price"]]
    for i in range(buys):
        rows.append([f"{day_iso}T00:0{i}:00+00:00", "ORDERED", "buy", f"00{i:04d}",
                     "10", "MKT", f"010{i:04d}", "0", "", "", "supertrend", "", ""])
    for i in range(sells):
        rows.append([f"{day_iso}T05:3{i}:00+00:00", "ORDERED", "sell", f"00{i:04d}",
                     "10", "MKT", f"020{i:04d}", "0", "", "", "supertrend", "", ""])
    with (data / "order_audit.csv").open("w", newline="", encoding="utf-8") as f:
        csv.writer(f).writerows(rows)


def _write_fill_audit(data: Path, day: str, n: int):
    rows = [["date", "symbol", "name", "qty", "buy_price", "sell_price",
             "pnl", "pnl_rate", "commission", "tax"]]
    for i in range(n):
        rows.append([day, f"00{i:04d}", "종목", "10", "1000", "1010", "100", "1.0", "17", "20"])
    with (data / "fill_audit.csv").open("w", newline="", encoding="utf-8") as f:
        csv.writer(f).writerows(rows)


def _write_balance(data: Path, day_iso: str, hour: int):
    rec = [{"date": day_iso, "cash": 1000.0, "eval_total": 0, "total": 1000.0,
            "estimated_asset": 1000.0, "position_count": 0,
            "updated_at": f"{day_iso}T{hour:02d}:05:00+09:00"}]
    (data / "balance_history.json").write_text(json.dumps(rec), encoding="utf-8")


def _run(data: Path, day: str):
    env = dict(os.environ, BARRO_DATA_DIR=str(data))
    return subprocess.run([sys.executable, str(SCRIPT), day], env=env,
                          capture_output=True, text=True)


def test_complete_day_passes(tmp_path):
    """fill_audit·EOD balance 갖춘 정상일 → exit 0."""
    d = tmp_path
    _write_order_audit(d, "2026-06-12", buys=2, sells=2)
    _write_fill_audit(d, "20260612", 2)
    _write_balance(d, "2026-06-12", hour=15)            # 장 마감 후
    (d / "active_positions.json").write_text("{}", encoding="utf-8")  # EOD 보유 0
    r = _run(d, "2026-06-12")
    assert r.returncode == 0, r.stdout


def test_evening_pipeline_silent_fails(tmp_path):
    """매도는 있는데 fill_audit 0 + balance 오전만 → 2 NG (이브닝 파이프라인 침묵)."""
    d = tmp_path
    _write_order_audit(d, "2026-06-15", buys=3, sells=3)
    # fill_audit 미작성(파일 없음) = ka10073 미수집
    _write_balance(d, "2026-06-15", hour=9)             # 아침 스냅샷만
    (d / "active_positions.json").write_text("{}", encoding="utf-8")
    r = _run(d, "2026-06-15")
    assert r.returncode == 2, r.stdout
    assert "fill_audit" in r.stdout and "balance_history" in r.stdout


def test_weekend_skipped(tmp_path):
    """주말(주문 0건) → 스킵, exit 0."""
    d = tmp_path
    (d / "order_audit.csv").write_text(
        "ts,action,side,symbol,qty,price,order_no,return_code,blocked,reason,strategy_id,filled_qty,avg_fill_price\n",
        encoding="utf-8")
    r = _run(d, "2026-06-14")                            # 일요일
    assert r.returncode == 0, r.stdout
    assert "비거래일" in r.stdout


def test_today_holdings_without_buy_audit_is_ng(tmp_path, monkeypatch):
    """당일 EOD 보유가 있는데 buy_audit 없음 → hard-NG (BAR-OPS-39 미배포 신호).

    DAY==오늘일 때만 hard-NG 이므로, 인자 없이(오늘) 실행하고 order/fill/balance 는
    오늘 날짜로 채워 fill·balance 는 통과시키고 buy_audit 단독 NG 를 검증한다.
    """
    from datetime import datetime, timedelta, timezone
    today_iso = datetime.now(timezone(timedelta(hours=9))).strftime("%Y-%m-%d")
    today = today_iso.replace("-", "")
    d = tmp_path
    _write_order_audit(d, today_iso, buys=1, sells=1)
    _write_fill_audit(d, today, 1)
    _write_balance(d, today_iso, hour=15)
    (d / "active_positions.json").write_text(
        json.dumps({"011070": {"name": "LG이노텍", "strategy": "supertrend"}}), encoding="utf-8")
    env = dict(os.environ, BARRO_DATA_DIR=str(d))
    r = subprocess.run([sys.executable, str(SCRIPT)], env=env, capture_output=True, text=True)
    assert r.returncode == 1, r.stdout
    assert "buy_audit" in r.stdout
