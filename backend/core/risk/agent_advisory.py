"""에이전트 자문(advisory) 게이트 — config-gated, default-OFF.

Hermes/quick-decider 등 전문가 에이전트가 `data/advisory.json` 에 기록한 종목별
verdict(GO/WAIT/NO-GO)를 읽어 **매수 신호를 필터**한다.

거버넌스(불변):
  · `enabled=False`(default) → `apply_buy_advisory()` 가 입력을 그대로 반환(byte-identical).
  · verdict 없음 / TTL stale / 파싱 실패 / 저신뢰 → **fail-open**(베이스라인대로 매매).
  · ★LLM은 주문 동기 경로에 없다 — 데몬은 미리 계산된 `advisory.json` 만 읽는다.
  · ★활성화(enabled=True)는 shadow 측정 후 별도 HITL((d)).

설계: docs/02-design/features/2026-06-22-agent-advisory-realtime.design.md.
선례(동일 패턴): backend/core/strategy/dante_filters.DistributionExitConfig.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

GO = "GO"
WAIT = "WAIT"
NOGO = "NO-GO"
_VALID_ACTIONS = {GO, WAIT, NOGO}


def _parse_ts(raw) -> Optional[datetime]:
    """ISO8601 → tz-aware datetime. naive 면 UTC 가정(writer 는 UTC 'Z' 권장). 실패 시 None."""
    if not raw:
        return None
    try:
        s = str(raw).strip().replace("Z", "+00:00")
        dt = datetime.fromisoformat(s)
    except (ValueError, TypeError):
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


@dataclass(frozen=True)
class AdvisoryVerdict:
    """단일 종목 자문. action ∈ {GO, WAIT, NO-GO}."""

    symbol: str
    action: str
    confidence: float = 0.0
    reason: str = ""
    ts: Optional[datetime] = None      # verdict 생성 시각(tz-aware)
    strategy: Optional[str] = None

    def is_fresh(self, now: datetime, ttl_sec: int) -> bool:
        """now(tz-aware) 기준 TTL 내에서 신선한가. ts 없음/미래/만료 → False."""
        if self.ts is None:
            return False
        age = (now - self.ts).total_seconds()
        return 0 <= age <= ttl_sec


@dataclass
class AgentAdvisoryStore:
    """symbol → 최신 verdict. load_advisory() 가 생성. 부재/오류 시 빈 store(fail-open)."""

    verdicts: dict = field(default_factory=dict)

    def fresh(self, symbol: Optional[str], now: datetime, ttl_sec: int) -> Optional[AdvisoryVerdict]:
        if not symbol:
            return None
        v = self.verdicts.get(symbol)
        if v is None or not v.is_fresh(now, ttl_sec):
            return None
        return v


def load_advisory(path) -> AgentAdvisoryStore:
    """data/advisory.json 로드. 파일 부재/JSON 오류 → 빈 store(fail-open).

    스키마: {"updated_at": ISO, "verdicts": [{symbol, action, confidence, reason, ts, strategy}, ...]}
    동일 symbol 다중 출현 시 마지막 항목이 우선(writer 가 최신을 뒤에 append).
    """
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, ValueError):
        return AgentAdvisoryStore()
    if not isinstance(data, dict):
        return AgentAdvisoryStore()
    out: dict = {}
    for raw in (data.get("verdicts") or []):
        if not isinstance(raw, dict):
            continue
        try:
            sym = str(raw["symbol"])
            action = str(raw["action"]).strip().upper().replace("NOGO", "NO-GO")
        except (KeyError, TypeError, ValueError):
            continue
        if action not in _VALID_ACTIONS:
            continue
        try:
            conf = float(raw.get("confidence", 0.0))
        except (TypeError, ValueError):
            conf = 0.0
        out[sym] = AdvisoryVerdict(
            symbol=sym,
            action=action,
            confidence=conf,
            reason=str(raw.get("reason", "")),
            ts=_parse_ts(raw.get("ts")),
            strategy=raw.get("strategy"),
        )
    return AgentAdvisoryStore(out)


@dataclass(frozen=True)
class AgentAdvisoryConfig:
    """매수 자문 게이트 설정. PolicyConfig 에서 조립. default 전부 비활성(무영향)."""

    enabled: bool = False
    ttl_sec: int = 180             # verdict 신선도 한도(초). 이 이상 오래되면 fail-open.
    block_wait: bool = False       # True 면 WAIT 도 차단(보수적). 기본은 NO-GO 만 차단.
    min_confidence: float = 0.0    # 이 신뢰도 미만 verdict 는 무시(fail-open).

    @classmethod
    def from_policy_config(cls, cfg) -> "AgentAdvisoryConfig":
        """PolicyConfig(또는 동등 객체)에서 조립. 필드 부재 시 default(비활성)."""
        return cls(
            enabled=bool(getattr(cfg, "agent_advisory_enabled", False)),
            ttl_sec=int(getattr(cfg, "agent_advisory_ttl_sec", 180)),
            block_wait=bool(getattr(cfg, "agent_advisory_block_wait", False)),
            min_confidence=float(getattr(cfg, "agent_advisory_min_confidence", 0.0)),
        )

    def blocks(self, verdict: Optional[AdvisoryVerdict]) -> bool:
        """이 verdict 가 매수를 차단하는가. None/저신뢰 → False(fail-open)."""
        if verdict is None:
            return False
        if verdict.confidence < self.min_confidence:
            return False
        if verdict.action == NOGO:
            return True
        if verdict.action == WAIT and self.block_wait:
            return True
        return False


def apply_buy_advisory(signals, cfg: AgentAdvisoryConfig, store: AgentAdvisoryStore, now: datetime):
    """매수 신호 리스트를 advisory 로 필터.

    Args:
        signals: list of (leader, strategy, pnl) 튜플. leader 는 .symbol/.name 속성 보유.
        cfg: AgentAdvisoryConfig.
        store: AgentAdvisoryStore.
        now: tz-aware 현재 시각.
    Returns:
        (kept_signals, skipped). skipped = [(symbol, name, action, reason), ...].
        cfg.enabled=False → (signals, []) 입력 그대로(byte-identical).
    """
    if not cfg.enabled:
        return signals, []
    kept = []
    skipped = []
    for item in signals:
        leader, strategy = item[0], item[1]
        sym = getattr(leader, "symbol", None)
        verdict = store.fresh(sym, now, cfg.ttl_sec)
        if cfg.blocks(verdict):
            name = getattr(leader, "name", "")
            skipped.append((sym, name, verdict.action, verdict.reason or "advisory block"))
        else:
            kept.append(item)
    return kept, skipped


__all__ = [
    "GO",
    "WAIT",
    "NOGO",
    "AdvisoryVerdict",
    "AgentAdvisoryStore",
    "AgentAdvisoryConfig",
    "load_advisory",
    "apply_buy_advisory",
]
