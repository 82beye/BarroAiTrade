"""BAR-OPS-31 — 정책 config JSON 영속.

simulate_leaders / evaluate_holdings 의 기본값을 파일에서 로드.
/tune apply 명령으로 추천값 자동 반영.
"""
from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional


@dataclass
class PolicyConfig:
    min_score: float = 0.5
    stop_loss_pct: float = -4.0
    take_profit_pct: float = 5.0
    # BAR-OPS-09 Phase 9 (2026-05-23): 균등 진입 default — max_total / max_concurrent = 0.80 / 10 = 0.08.
    # max_per_position 은 균등 슬롯의 안전 상한 캡. 30% → 10% (5/22 비중 편차 6배 차단).
    max_per_position: float = 0.10
    max_total_position: float = 0.80
    max_concurrent_positions: int = 10
    daily_loss_limit: float = -3.0
    daily_max_orders: int = 50
    # 적응형 매도 정책
    trailing_start_pct: float = 3.0       # 트레일링 시작 수익률 (%)
    trailing_offset_pct: float = 1.5      # 고점 대비 하락 허용폭 (%)
    breakeven_trigger_pct: float = 2.5    # 브레이크이븐 전환 수익률 (%)
    partial_tp_pct: float = 3.5           # 1차 분할 익절 기준 (%)
    partial_tp_ratio: float = 0.5         # 1차 익절 매도 비율
    hold_days_tighten: int = 5            # N일 이상 보유 시 SL 강화
    tightened_sl_pct: float = -2.0        # 장기 보유 시 강화 SL (%)
    # 2026-06-21 — 국면 적응 청산 (default-OFF). evaluate_holdings 가 RegimeExitConfig 로 조립해
    # PositionContext 로 전달. enabled=False 또는 배수 1.0 → 청산 무변경(byte-identical).
    # SIDEWAYS(6월 변동성장) SL 타이트·보유 단축, BULL TP 확장. 설계: backend/core/risk/regime_exit.py.
    # 활성화는 측정 후 (d) HITL. load() 가 known-fields-only 라 forward-compatible.
    regime_exit_enabled: bool = False
    regime_sideways_sl_mult: float = 1.0
    regime_sideways_tp_mult: float = 1.0
    regime_sideways_max_hold_days: Optional[int] = None
    regime_bull_tp_mult: float = 1.0
    regime_bull_sl_mult: float = 1.0
    regime_bearish_sl_mult: float = 1.0
    # 2026-06-21 — net-aware TP(default-OFF). True 면 TP/분할익절 임계에 왕복 비용 가산.
    net_aware_tp_enabled: bool = False
    # 2026-06-22 — distribution(세력이탈 장대음봉) 청산 게이트 (default-OFF). JD-R13.
    # evaluate_holding 이 DistributionExitConfig.from_policy_config 로 조립, daemon 이 일봉 주입.
    # enabled=False → 청산 무변경(byte-identical). OOS 검증·임계: 거래량 3.0배·몸통 3%(표준).
    # 설계: dante_filters.DistributionExitConfig. 활성화는 약세장 dry-run 후 (d) HITL.
    distribution_exit_enabled: bool = False
    distribution_exit_vol_mult: float = 3.0
    distribution_exit_body_min: float = 0.03
    # 2026-06-22 — 에이전트 자문(advisory) 매수 게이트 (default-OFF).
    # Hermes/quick-decider verdict(data/advisory.json) 로 매수 신호 필터. _scan_and_buy 가
    # AgentAdvisoryConfig.from_policy_config 로 조립. enabled=False → 신호 무변경(byte-identical).
    # verdict 없음/TTL stale/저신뢰 → fail-open(베이스라인 매매). LLM 은 주문 동기경로에 없음.
    # 설계: backend/core/risk/agent_advisory.py. 활성화는 shadow 측정 후 (d) HITL.
    agent_advisory_enabled: bool = False
    agent_advisory_ttl_sec: int = 180
    agent_advisory_block_wait: bool = False    # True 면 WAIT 도 차단(보수적), 기본 NO-GO 만
    agent_advisory_min_confidence: float = 0.0
    # 2026-06-23 — 시장-맥락 add-on (전부 default-OFF, mode soft). advisory.json top-level
    # 섹션(market_context/sector_themes/portfolio_signals)을 _scan_and_buy 가 소비.
    # 결정적 하드캡 + LLM 소프트. off → 무변경(byte-identical). 설계: market_context.py.
    # 활성/하드 승격은 shadow 측정 후 (d) HITL. mode ∈ {soft, hard}.
    # ① 시장국면: risk-off/bearish 시 max_buy 축소(soft)·전략게이트 차단(hard)
    market_context_enabled: bool = False
    market_context_mode: str = "soft"
    market_context_ttl_sec: int = 600
    # ② 거래대금 집중 테마: 오늘 거래대금 쏠림 핫테마 중 under-exposed 신호를 우선순위 가점(soft 재정렬)
    sector_themes_enabled: bool = False
    sector_themes_mode: str = "soft"
    sector_themes_ttl_sec: int = 600
    sector_underexposed_max_pct: float = 0.30   # 보유 노출 ≥ 이 값인 테마는 가점 제외(이미 충분)
    sector_min_turnover_pct: float = 0.0        # 거래대금 share 이 미만인 핫테마는 무시
    # ③ 포트폴리오 테마 쏠림 가드: 단일 테마 노출 ≥cap → 매수 차단(hard)/사이징 축소(soft)
    portfolio_theme_enabled: bool = False
    portfolio_theme_mode: str = "soft"
    portfolio_theme_ttl_sec: int = 600
    portfolio_max_theme_pct: float = 0.30
    portfolio_theme_soft_factor: float = 0.5
    # ④ 포트폴리오 리스크: 집중도≥cap 또는 leverage 경고 → 전역 사이징 throttle
    portfolio_risk_enabled: bool = False
    portfolio_risk_mode: str = "soft"
    portfolio_risk_ttl_sec: int = 600
    portfolio_max_concentration_pct: float = 0.40
    portfolio_risk_throttle: float = 0.5
    history: list[dict] = field(default_factory=list)         # 변경 이력

    def as_dict(self) -> dict:
        return asdict(self)


class PolicyConfigStore:
    """JSON 파일 기반 정책 영속."""

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def load(self) -> PolicyConfig:
        if not self.path.exists():
            return PolicyConfig()
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return PolicyConfig()
        # known fields only — forward compat
        cfg = PolicyConfig()
        for k in cfg.__dataclass_fields__:
            if k in data:
                setattr(cfg, k, data[k])
        return cfg

    def save(self, cfg: PolicyConfig) -> None:
        self.path.write_text(
            json.dumps(cfg.as_dict(), indent=2, ensure_ascii=False),
            encoding="utf-8",
        )

    def apply(
        self, recommendations: list, *, source: str = "tune",
    ) -> tuple[PolicyConfig, list[dict]]:
        """추천 list[PolicyRecommendation] 반영 + history append.

        반환: (new_cfg, applied_changes).
        """
        cfg = self.load()
        applied: list[dict] = []
        for r in recommendations:
            if not hasattr(cfg, r.field):
                continue
            old = getattr(cfg, r.field)
            new = r.recommended
            if old == new:
                continue
            setattr(cfg, r.field, new)
            applied.append({
                "field": r.field, "old": old, "new": new,
                "reason": r.reason, "severity": r.severity,
            })
        if applied:
            cfg.history.append({
                "timestamp": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                "source": source,
                "changes": applied,
            })
            # history 최근 50건만 유지
            cfg.history = cfg.history[-50:]
            self.save(cfg)
        return cfg, applied


__all__ = ["PolicyConfig", "PolicyConfigStore"]
