"""시장-맥락 add-on — advisory.json 확장 섹션 소비 (config-gated default-OFF).

Phase 1~2 의 종목 verdict(agent_advisory.py)에 더해, advisory.json 의 top-level 섹션
`market_context`(시장국면)·`sector_themes`(거래대금 집중)·`portfolio_signals`(테마 쏠림/리스크)
를 읽어 매수 의사결정에 반영한다.

거버넌스(불변, agent_advisory 와 동일):
  · 각 add-on `enabled=False`(default) → apply 무변경(byte-identical).
  · 섹션 부재 / TTL stale / 미매핑 → fail-open(베이스라인 매매).
  · ★LLM은 주문 동기경로에 없음 — 데몬은 미리 계산된 advisory.json 만 읽는다.
  · 단계: off → soft(사이징/우선순위) → hard(차단/캡). 하드 승격은 (d) HITL.

설계: docs/02-design/features/2026-06-23-market-context-addons.design.md.
결정적 집계는 backend/core/risk/theme_map.py.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

import json

from backend.core.risk.theme_map import themes_of

SOFT = "soft"
HARD = "hard"


def parse_ts(raw) -> Optional[datetime]:
    if not raw:
        return None
    try:
        dt = datetime.fromisoformat(str(raw).strip().replace("Z", "+00:00"))
    except (ValueError, TypeError):
        return None
    return dt.replace(tzinfo=timezone.utc) if dt.tzinfo is None else dt


def _fresh(ts: Optional[datetime], now: datetime, ttl_sec: int) -> bool:
    if ts is None:
        return False
    age = (now - ts).total_seconds()
    return 0 <= age <= ttl_sec


# ── 데이터(advisory.json top-level 섹션) ─────────────────────────────────────

@dataclass(frozen=True)
class MarketContext:
    regime: str = "unknown"                 # bull | sideways | bearish | unknown
    risk_on: Optional[bool] = None
    confidence: float = 0.0
    strategy_gates: dict = field(default_factory=dict)   # strategy -> bool(허용)
    reason: str = ""
    ts: Optional[datetime] = None
    source: str = ""

    def is_fresh(self, now, ttl_sec):
        return _fresh(self.ts, now, ttl_sec)


@dataclass(frozen=True)
class SectorThemes:
    hot: tuple = ()      # ({theme,turnover,turnover_pct,rank,symbols}, ...)
    ts: Optional[datetime] = None
    source: str = ""

    def is_fresh(self, now, ttl_sec):
        return _fresh(self.ts, now, ttl_sec)

    def hot_names(self) -> set:
        return {h.get("theme") for h in self.hot if isinstance(h, dict) and h.get("theme")}


@dataclass(frozen=True)
class PortfolioSignals:
    theme_exposure: dict = field(default_factory=dict)   # theme -> pct(0~1)
    concentration_pct: float = 0.0                        # 최대 단일 노출(0~1)
    leverage_warn: bool = False
    ts: Optional[datetime] = None
    source: str = ""

    def is_fresh(self, now, ttl_sec):
        return _fresh(self.ts, now, ttl_sec)


@dataclass(frozen=True)
class MarketAdvisory:
    market_context: MarketContext = field(default_factory=MarketContext)
    sector_themes: SectorThemes = field(default_factory=SectorThemes)
    portfolio_signals: PortfolioSignals = field(default_factory=PortfolioSignals)


def load_market_advisory(path) -> MarketAdvisory:
    """advisory.json 의 top-level 섹션 파싱. 부재/오류 → 빈 기본값(fail-open)."""
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, ValueError):
        return MarketAdvisory()
    if not isinstance(data, dict):
        return MarketAdvisory()

    mc = data.get("market_context") or {}
    market = MarketContext(
        regime=str(mc.get("regime", "unknown")).lower(),
        risk_on=mc.get("risk_on"),
        confidence=float(mc.get("confidence", 0.0) or 0.0),
        strategy_gates=dict(mc.get("strategy_gates") or {}),
        reason=str(mc.get("reason", "")),
        ts=parse_ts(mc.get("ts")), source=str(mc.get("source", "")),
    ) if isinstance(mc, dict) else MarketContext()

    st = data.get("sector_themes") or {}
    hot = tuple(h for h in (st.get("hot") or []) if isinstance(h, dict)) if isinstance(st, dict) else ()
    sector = SectorThemes(hot=hot, ts=parse_ts(st.get("ts") if isinstance(st, dict) else None),
                          source=str(st.get("source", "") if isinstance(st, dict) else ""))

    ps = data.get("portfolio_signals") or {}
    portfolio = PortfolioSignals(
        theme_exposure={str(k): float(v) for k, v in (ps.get("theme_exposure") or {}).items()},
        concentration_pct=float(ps.get("concentration_pct", 0.0) or 0.0),
        leverage_warn=bool(ps.get("leverage_warn", False)),
        ts=parse_ts(ps.get("ts")), source=str(ps.get("source", "")),
    ) if isinstance(ps, dict) else PortfolioSignals()

    return MarketAdvisory(market, sector, portfolio)


# ── config (PolicyConfig 에서 조립) ──────────────────────────────────────────

@dataclass(frozen=True)
class MarketContextConfig:
    enabled: bool = False
    mode: str = SOFT
    ttl_sec: int = 600

    @classmethod
    def from_policy_config(cls, cfg):
        return cls(enabled=bool(getattr(cfg, "market_context_enabled", False)),
                   mode=str(getattr(cfg, "market_context_mode", SOFT)),
                   ttl_sec=int(getattr(cfg, "market_context_ttl_sec", 600)))


@dataclass(frozen=True)
class PortfolioThemeConfig:
    enabled: bool = False
    mode: str = SOFT
    ttl_sec: int = 600
    max_theme_pct: float = 0.30          # 단일 테마 최대 노출
    soft_size_factor: float = 0.5        # soft 모드 사이징 축소 배수

    @classmethod
    def from_policy_config(cls, cfg):
        return cls(enabled=bool(getattr(cfg, "portfolio_theme_enabled", False)),
                   mode=str(getattr(cfg, "portfolio_theme_mode", SOFT)),
                   ttl_sec=int(getattr(cfg, "portfolio_theme_ttl_sec", 600)),
                   max_theme_pct=float(getattr(cfg, "portfolio_max_theme_pct", 0.30)),
                   soft_size_factor=float(getattr(cfg, "portfolio_theme_soft_factor", 0.5)))


@dataclass(frozen=True)
class PortfolioRiskConfig:
    enabled: bool = False
    mode: str = SOFT
    ttl_sec: int = 600
    max_concentration_pct: float = 0.40
    throttle_factor: float = 0.5

    @classmethod
    def from_policy_config(cls, cfg):
        return cls(enabled=bool(getattr(cfg, "portfolio_risk_enabled", False)),
                   mode=str(getattr(cfg, "portfolio_risk_mode", SOFT)),
                   ttl_sec=int(getattr(cfg, "portfolio_risk_ttl_sec", 600)),
                   max_concentration_pct=float(getattr(cfg, "portfolio_max_concentration_pct", 0.40)),
                   throttle_factor=float(getattr(cfg, "portfolio_risk_throttle", 0.5)))


# ── apply (데몬 hook — off → 무변경) ─────────────────────────────────────────

def apply_market_context(max_buy, signals, cfg: MarketContextConfig,
                         ctx: MarketContext, now):
    """시장국면 반영. 반환: (new_max_buy, kept_signals, notes).

    off/stale → 입력 그대로. soft: risk-off/bearish 면 max_buy 축소. hard: 추가로
    strategy_gates 에서 False 인 전략 신호 제거.
    """
    if not cfg.enabled or not ctx.is_fresh(now, cfg.ttl_sec):
        return max_buy, signals, []
    notes = []
    new_max = max_buy
    risk_off = (ctx.risk_on is False) or (ctx.regime == "bearish")
    if risk_off:
        new_max = max(1, max_buy // 2)
        notes.append(f"risk-off(regime={ctx.regime}) → max_buy {max_buy}→{new_max}")
    kept = signals
    if cfg.mode == HARD and ctx.strategy_gates:
        kept = [s for s in signals if ctx.strategy_gates.get(s[1], True)]
        if len(kept) != len(signals):
            notes.append(f"strategy_gate 차단 {len(signals)-len(kept)}건")
    return new_max, kept, notes


def apply_theme_guard(signals, cfg: PortfolioThemeConfig, psig: PortfolioSignals,
                      theme_map: dict, now):
    """포트폴리오 테마 쏠림 가드. 반환: (kept, skipped, size_factors).

    off/stale → (signals, [], {}). 보유 노출이 max_theme_pct 이상인 테마에 속한
    매수 신호를: hard=차단 / soft=size_factors[symbol]=soft_size_factor.
    skipped=[(symbol, [over_themes], reason)].
    """
    if not cfg.enabled or not psig.is_fresh(now, cfg.ttl_sec):
        return signals, [], {}
    over = {t for t, pct in psig.theme_exposure.items() if pct >= cfg.max_theme_pct}
    if not over:
        return signals, [], {}
    kept, skipped, size_factors = [], [], {}
    for s in signals:
        sym = getattr(s[0], "symbol", None)
        hit = sorted(set(themes_of(sym, theme_map)) & over)
        if hit:
            if cfg.mode == HARD:
                skipped.append((sym, hit, f"테마 과다노출 {','.join(hit)}(≥{cfg.max_theme_pct:.0%})"))
                continue
            size_factors[sym] = cfg.soft_size_factor   # soft: 사이징 축소
        kept.append(s)
    return kept, skipped, size_factors


def apply_portfolio_risk(cfg: PortfolioRiskConfig, psig: PortfolioSignals, now):
    """전역 사이징 throttle. 반환: (size_factor(1.0=무변경), note|None).

    off/stale → (1.0, None). 집중도≥cap 또는 leverage 경고 → throttle_factor.
    """
    if not cfg.enabled or not psig.is_fresh(now, cfg.ttl_sec):
        return 1.0, None
    if psig.concentration_pct >= cfg.max_concentration_pct or psig.leverage_warn:
        why = ("leverage" if psig.leverage_warn
               else f"집중도 {psig.concentration_pct:.0%}≥{cfg.max_concentration_pct:.0%}")
        return cfg.throttle_factor, f"사이징 throttle ×{cfg.throttle_factor} ({why})"
    return 1.0, None


__all__ = [
    "SOFT", "HARD", "MarketContext", "SectorThemes", "PortfolioSignals", "MarketAdvisory",
    "load_market_advisory", "MarketContextConfig", "PortfolioThemeConfig", "PortfolioRiskConfig",
    "apply_market_context", "apply_theme_guard", "apply_portfolio_risk", "parse_ts",
]
