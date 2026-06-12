"""ob_scalp shadow 로깅 — first-touch 페이퍼 체결/집계 로직 결정적 검증.

L2 호가 이력이 없어 ob_scalp 은 백테스트 불가 → shadow 실시간 관측이 유일한 검증 경로.
그 측정 엔진(Observation.update first-touch + 순수익 집계)이 정확해야 신뢰 가능하므로
순수 로직을 결정적으로 고정한다. (라이브 API 불필요)
"""
from __future__ import annotations

import pytest

from datetime import datetime, timedelta, timezone

from scripts._ob_scalp_shadow import Observation, ShadowStats
from backend.core.strategy.ob_scalp import net_return_pct

KST = timezone(timedelta(hours=9))
T0 = datetime(2026, 5, 31, 10, 0, 0, tzinfo=KST)


def _obs(entry_ask=10010.0, tp_target=10060.0, sl_price=9980.0, horizon_s=60):
    return Observation(
        obs_id="X-100000-1", symbol="005930", name="삼성전자", signal_ts=T0,
        entry_ask=entry_ask, tick=10, tp_target=tp_target, sl_price=sl_price,
        breakeven_ticks=2.1, ofi=0.7, spread_ticks=1.0, net_tp_pct=0.3,
        resolve_at=T0 + timedelta(seconds=horizon_s), min_bid=entry_ask, max_bid=entry_ask,
    )



@pytest.fixture(autouse=True)
def _legacy_costs(monkeypatch):
    """[BAR-OPS-39] 비용 상수가 브로커 실측(왕복 0.55%)으로 교체됨 — 본 파일의 메커니즘
    테스트들은 설계 당시 요율(0.015%/0.18%) 기준 시나리오(2.1틱 본전 등)라, 요율을 고정해
    '비용 게이트/TP 내재화 메커니즘'만 검증한다. 실측 요율 검증은 test_bar_ops_39.py.
    (함수들은 모듈 전역을 호출 시점에 읽으므로 monkeypatch 가 적용된다.)"""
    import backend.core.strategy.ob_scalp as ob
    monkeypatch.setattr(ob, "COMMISSION_RATE", 0.00015)
    monkeypatch.setattr(ob, "TAX_RATE", 0.0018)
    monkeypatch.setattr(ob, "ROUND_TRIP_COST_PCT", 2 * 0.00015 + 0.0018)


class TestFirstTouch:
    def test_tp_first(self):
        o = _obs()
        assert o.update(10050.0, T0 + timedelta(seconds=5)) is False  # 미접촉
        assert o.update(10065.0, T0 + timedelta(seconds=10)) is True
        assert o.outcome == "tp_hit"
        assert o.exit_bid == 10060.0          # TP 목표가로 청산(과대평가 방지)
        # ★ 비용 내재화 TP → 순수익 양(+)
        assert net_return_pct(o.entry_ask, o.exit_bid) > 0

    def test_sl_first(self):
        o = _obs()
        assert o.update(9975.0, T0 + timedelta(seconds=5)) is True
        assert o.outcome == "sl_hit"
        assert o.exit_bid == 9980.0
        assert net_return_pct(o.entry_ask, o.exit_bid) < 0  # 손절 순손실

    def test_time_exit(self):
        o = _obs()
        assert o.update(10020.0, T0 + timedelta(seconds=5)) is False
        assert o.update(10020.0, T0 + timedelta(seconds=61)) is True  # horizon 경과
        assert o.outcome == "time_exit"
        assert o.exit_bid == 10020.0

    def test_tp_precedence_over_time(self):
        # horizon 경과 + TP 접촉 동시 → TP 우선(먼저 체결 가정)
        o = _obs()
        assert o.update(10070.0, T0 + timedelta(seconds=120)) is True
        assert o.outcome == "tp_hit"

    def test_idempotent_after_resolved(self):
        o = _obs()
        o.update(10065.0, T0 + timedelta(seconds=10))   # tp_hit
        assert o.update(9900.0, T0 + timedelta(seconds=20)) is True
        assert o.outcome == "tp_hit"                     # 변경 안 됨
        assert o.exit_bid == 10060.0

    def test_max_min_bid_tracking(self):
        o = _obs()
        o.update(10030.0, T0 + timedelta(seconds=3))
        o.update(10005.0, T0 + timedelta(seconds=6))
        o.update(10040.0, T0 + timedelta(seconds=9))
        assert o.max_bid == 10040.0
        assert o.min_bid == 10005.0

    def test_last_bid_tracks_latest(self):
        # 종료 flush 시 미실현 평가는 '마지막 관측가' 기준 — 최신 bb 를 따라가야 함
        o = _obs()
        o.update(10030.0, T0 + timedelta(seconds=3))
        o.update(10018.0, T0 + timedelta(seconds=6))
        assert o.last_bid == 10018.0

    def test_last_bid_default_zero_before_observation(self):
        # 관측 전(같은 사이클 생성 직후 등) last_bid 기본 0 → flush 시 entry_ask 폴백 대상
        o = Observation(
            obs_id="x", symbol="005930", name="n", signal_ts=T0,
            entry_ask=10010.0, tick=10, tp_target=10060.0, sl_price=9980.0,
            breakeven_ticks=2.1, ofi=0.7, spread_ticks=1.0, net_tp_pct=0.3,
            resolve_at=T0 + timedelta(seconds=60),
        )
        assert o.last_bid == 0.0


class TestStats:
    def test_expectancy_and_winrate(self):
        s = ShadowStats()
        s.net_pcts = [0.3, -0.4, 0.5, -0.1]   # 2승 2패
        assert s.win_rate() == 50.0
        assert abs(s.expectancy() - (0.3 - 0.4 + 0.5 - 0.1) / 4) < 1e-9

    def test_empty_stats_safe(self):
        s = ShadowStats()
        assert s.win_rate() == 0.0
        assert s.expectancy() == 0.0

    def test_net_negative_expectancy_detectable(self):
        # 적중률 높아도(75%) 기대값 음수일 수 있음 — shadow 의 핵심 판정
        s = ShadowStats()
        s.net_pcts = [0.05, 0.05, 0.05, -0.5]   # 3승 1패, 합 -0.35
        assert s.win_rate() == 75.0
        assert s.expectancy() < 0   # 승률 함정 — 실거래 금지 신호
