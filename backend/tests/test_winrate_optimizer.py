"""승률 최적화 — simulate_exit 파라미터화 청산 로직 검증."""
from __future__ import annotations

from types import SimpleNamespace

from scripts._winrate_optimizer import simulate_exit


def _c(o, h, l, cl):
    return SimpleNamespace(open=o, high=h, low=l, close=cl)


def _cfg(**kw):
    base = dict(tp=[2.0, 4.0, 6.0], q=[1.0, 0.0, 0.0], sl=-2.0, be=False, trail=None, max_hold=5)
    base.update(kw)
    return base


class TestSimulateExit:
    def test_tp1_full_win(self):
        # 진입 100, 다음봉 high 103(>+2%) → TP1 100% 체결
        candles = [_c(0, 0, 0, 0), _c(100, 100, 100, 100), _c(101, 103, 99.5, 102)]
        r = simulate_exit(candles, 0, _cfg())
        assert r is not None and r > 0  # +2% - 비용 > 0

    def test_sl_loss(self):
        candles = [_c(0, 0, 0, 0), _c(100, 100, 100, 100), _c(100, 100.5, 97, 98)]
        r = simulate_exit(candles, 0, _cfg())
        assert r is not None and r < 0  # SL -2%

    def test_max_hold_close_exit(self):
        # TP/SL 미도달 → max_hold 후 종가 청산
        flat = [_c(100, 100.5, 99.5, 100)] * 3
        candles = [_c(0, 0, 0, 0), _c(100, 100, 100, 100)] + flat
        r = simulate_exit(candles, 0, _cfg(max_hold=3))
        assert r is not None and r < 0  # 보합인데 비용만큼 손실

    def test_breakeven_saves_runner(self):
        # TP1 체결 후 be=True → 잔량 SL 이 본전으로 이동, 하락해도 손실 축소
        candles = [
            _c(0, 0, 0, 0), _c(100, 100, 100, 100),
            _c(100, 102.5, 99.5, 102),  # TP1(+2%) 체결 (q[0]=0.5)
            _c(102, 102, 99.0, 100),    # 하락 — be 면 잔량 본전(100) 청산
        ]
        cfg = _cfg(q=[0.5, 0.25, 0.25], be=True)
        r = simulate_exit(candles, 0, cfg)
        # TP1 +2%(0.5) 이익 + 잔량 본전 부근 → 전체 양수 또는 소폭
        assert r is not None and r > -0.5

    def test_no_entry_at_last_bar(self):
        candles = [_c(0, 0, 0, 0), _c(100, 100, 100, 100)]
        assert simulate_exit(candles, 1, _cfg()) is None  # entry_idx+1 없음
