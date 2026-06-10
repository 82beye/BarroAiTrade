"""BAR-OPS-38 P2 — 일일감사 이월 청산 분리 + UNFILLED 상쇄 테스트.

근거: reports/2026-06-10/2026-06-10_매매복기.md P2 (이월/당일 분해) + 인시던트 1.
"""
from __future__ import annotations

import csv
from datetime import datetime, timezone

import scripts._daily_strategy_audit as audit
from scripts._daily_strategy_audit import fifo_roundtrip_pnl, load_orders

COMM = 0.00015
TAX = 0.0018


class TestCarrySplit:
    def test_unmatched_sell_goes_to_carry_not_realized(self):
        """[6/10 결함 수정] 원가 미매칭 매도 — 종전엔 매도대금 전액이 이익 계상."""
        r = fifo_roundtrip_pnl([
            {"side": "sell", "qty": 12, "price": 312000},   # 이월 청산 (당일 매수 없음)
        ])
        assert r["realized"] == 0.0          # 과대계상 제거
        assert r["sells"] == 0               # 당일 KPI 매도건수에서 제외
        assert r["carry_sells"] == 1
        assert r["carry_sell_value"] == 12 * 312000

    def test_partial_carry_split(self):
        """당일 10주 매수 후 15주 매도 — 10주는 당일 실현, 5주는 이월 버킷."""
        r = fifo_roundtrip_pnl([
            {"side": "buy", "qty": 10, "price": 100},
            {"side": "sell", "qty": 15, "price": 110},
        ])
        sval_m, cost = 10 * 110, 10 * 100
        expected = (sval_m - cost) - ((sval_m + cost) * COMM + sval_m * TAX)
        assert abs(r["realized"] - expected) < 1e-6
        assert r["sells"] == 1 and r["wins"] == 1
        assert r["carry_sells"] == 1
        assert r["carry_sell_value"] == 5 * 110

    def test_fully_matched_has_no_carry(self):
        r = fifo_roundtrip_pnl([
            {"side": "buy", "qty": 10, "price": 100},
            {"side": "sell", "qty": 10, "price": 110},
        ])
        assert r["carry_sells"] == 0 and r["carry_sell_value"] == 0.0

    def test_sell_details_carry_flag(self):
        ts = datetime(2026, 6, 10, 0, 2, tzinfo=timezone.utc)
        r = fifo_roundtrip_pnl([
            {"side": "buy", "qty": 5, "price": 100, "ts": ts},
            {"side": "sell", "qty": 5, "price": 110, "ts": ts},
            {"side": "sell", "qty": 3, "price": 110, "ts": ts},   # 미매칭
        ])
        flags = sorted(d["carry"] for d in r["sell_details"])
        assert flags == [False, True]


class TestUnfilledExclusion:
    HEADERS = ["ts", "action", "side", "symbol", "qty", "price", "order_no",
               "return_code", "blocked", "reason", "strategy_id",
               "filled_qty", "avg_fill_price"]

    def _write_audit(self, tmp_path, rows):
        d = tmp_path / "data"
        d.mkdir(parents=True, exist_ok=True)
        with open(d / "order_audit.csv", "w", newline="", encoding="utf-8") as f:
            w = csv.writer(f)
            w.writerow(self.HEADERS)
            for r in rows:
                w.writerow(r)

    def test_unfilled_order_no_cancels_ordered(self, tmp_path, monkeypatch):
        """6/10 475150 재현 — 상한가 잠김 매수 49주: ORDERED 후 UNFILLED 로 상쇄."""
        self._write_audit(tmp_path, [
            ["2026-06-10T00:10:20+00:00", "ORDERED", "buy", "475150", "49", "MKT",
             "0018753", "0", "0", "", "gold_zone", "", ""],
            ["2026-06-10T01:00:54+00:00", "UNFILLED", "buy", "475150", "49", "MKT",
             "0018753", "", "0", "SYNC 퍼지 — 잔고 부재", "gold_zone", "0", ""],
            ["2026-06-10T02:44:46+00:00", "ORDERED", "buy", "475150", "51", "MKT",
             "0084475", "0", "0", "", "gold_zone", "", ""],
        ])
        monkeypatch.setattr(audit, "_REPO", tmp_path)
        orders = load_orders("2026-06-10")
        assert len(orders) == 1
        assert orders[0]["qty"] == 51.0

    def test_unfilled_without_order_no_falls_back_to_symbol_qty(self, tmp_path, monkeypatch):
        self._write_audit(tmp_path, [
            ["2026-06-10T00:10:20+00:00", "ORDERED", "buy", "475150", "49", "MKT",
             "0018753", "0", "0", "", "gold_zone", "", ""],
            ["2026-06-10T01:00:54+00:00", "UNFILLED", "buy", "475150", "49", "MKT",
             "", "", "0", "SYNC 퍼지", "gold_zone", "0", ""],
        ])
        monkeypatch.setattr(audit, "_REPO", tmp_path)
        assert load_orders("2026-06-10") == []

    def test_no_unfilled_keeps_all(self, tmp_path, monkeypatch):
        self._write_audit(tmp_path, [
            ["2026-06-10T00:10:20+00:00", "ORDERED", "buy", "005930", "10", "MKT",
             "0000001", "0", "0", "", "supertrend", "", ""],
            ["2026-06-10T00:20:20+00:00", "ORDERED", "sell", "005930", "10", "MKT",
             "0000002", "0", "0", "", "supertrend", "", ""],
        ])
        monkeypatch.setattr(audit, "_REPO", tmp_path)
        assert len(load_orders("2026-06-10")) == 2
