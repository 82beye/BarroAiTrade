"""ai-trade 산출물(스캔 + 예측) 교집합 유니버스 로더 — 2026-07-30 신규.

운영 머신의 ai-trade 봇이 매일 생성하는 두 JSON 파일을 읽어
**교집합 유니버스**(스캔 통과 ∩ 예측 상위)를 만드는 **순수 로더**다.
네트워크·DB·주문 경로에 전혀 닿지 않는다(파일 읽기 전용).

읽는 파일 (`BARRO_AI_TRADE_DIR` 하위):
  - `watchlist_{YYYY-MM-DD}.json`    — daily_screener 스캔 결과(11필드/종목)
  - `predictions_{YYYY-MM-DD}.json`  — 멀티에이전트 예측 결과(점수/랭크/합의)

**default-OFF**: `BARRO_AI_TRADE_DIR` 가 없으면 즉시 `status="no_data"` 로 강등한다.
따라서 미설정 환경(개발 머신·라이브 백엔드)에서는 아무 동작도 하지 않는다.
또한 이 함수는 **어떤 입력에도 예외를 던지지 않는다** — 파싱 실패·스키마 불일치는
전부 `status="no_data"` + `reason="parse_error:<예외클래스명>"` 으로 흡수한다.
라이브 경로가 이 로더 때문에 죽는 일이 없어야 한다는 요구(§2 S3) 때문이다.

파서는 **lenient** 하다: 계약에 없는 미지 필드가 와도 무시하고 계속 진행한다.
ai-trade 측 스키마가 진화해도 로더가 깨지지 않게 하기 위한 의도적 선택이다.
(strict 스키마 검증을 넣지 말 것.)

역할 분담 — **신선도 임계 판단은 소비자(데몬)가 한다**:
  - 로더: 파일의 `date` 가 오늘(KST)인지 여부만 보고 `ok`/`stale` 을 매긴다.
    `BARRO_AI_SWING_MAX_AGE_H`(기본 12)·`BARRO_AI_SWING_ALLOW_STALE`(기본 0)은
    **읽어만 두고 status 판정에 쓰지 않는다**(로그로만 남긴다).
  - 소비자: `as_of`·`source_scan_date`·`source_pred_date`·`status` 를 보고
    "이 데이터로 진입해도 되는가"를 결정한다. 로더는 진입 허용을 판단하지 않는다.

시각 표기: `as_of` 는 KST(+09:00) tz-aware ISO8601 이다.
반면 ai-trade 파일의 `generated_at` 은 **tz 없는 naive KST 문자열**이라
신뢰할 수 없어 사용하지 않는다(날짜 판정은 `date` 필드 / 파일명으로 한다).

종목코드는 `_normalize_symbol` 로 정규화한 뒤 교집합을 낸다('005930_AL' → '005930').

검증: 단위 테스트(tmp_path JSON 픽스처)만. 실 운영 파일은 운영 머신에만 존재한다.
"""
from __future__ import annotations

import glob
import json
import logging
import os
import re
from dataclasses import asdict, dataclass
from datetime import date, datetime, timedelta, timezone
from typing import Any, Optional

from backend.core.gateway.kiwoom_native_rank import _normalize_symbol

logger = logging.getLogger(__name__)

_KST = timezone(timedelta(hours=9))

# ─── 환경변수 (전부 default-OFF / 무해한 기본값) ──────────────────────────────
ENV_DIR = "BARRO_AI_TRADE_DIR"              # 미설정 → no_data (기능 자체 OFF)
ENV_FALLBACK = "BARRO_AI_SWING_FALLBACK"    # "scan_only" 일 때만 스캔 단독 허용
ENV_MAX_AGE_H = "BARRO_AI_SWING_MAX_AGE_H"  # 소비자용 힌트 (로더 미사용)
ENV_ALLOW_STALE = "BARRO_AI_SWING_ALLOW_STALE"  # 소비자용 힌트 (로더 미사용)

_SCAN_PREFIX = "watchlist"
_PRED_PREFIX = "predictions"
_DATE_RE = re.compile(r"(\d{4}-\d{2}-\d{2})")


@dataclass(frozen=True)
class AiTradeItem:
    """교집합 1종목 — 스캔 지표 + 예측 점수 결합."""

    symbol: str
    name: str
    # 스캔(daily_screener) 측
    scan_score: float
    blue_line_status: str
    watermelon_signal: bool
    volume_ratio: float
    # 예측(멀티에이전트) 측 — 스캔 단독(partial) 시 0/"" 로 남는다
    pred_rank: int
    pred_score: float
    confidence: float
    consensus_level: str
    # 결합 순위 (1부터)
    rank_combined: int


@dataclass(frozen=True)
class AiTradeUniverse:
    """로더 산출물. 데이터가 없으면 items 를 비우고 status/reason 으로 강등한다.

    status:
      - "ok"      두 파일 모두 오늘(KST) 자 → items = 교집합
      - "stale"   파일이 존재하나 date 중 하나라도 과거 → items = 교집합 (판단은 소비자 몫)
      - "partial" watchlist 만 존재 → items 는 fallback 정책에 따름
      - "no_data" 디렉토리 미설정·부재, 파일 부재, 파싱 실패 → items = ()
    """

    status: str
    as_of: str
    reason: str = ""
    source_scan_date: str = ""
    source_pred_date: str = ""
    scan_count: int = 0
    pred_count: int = 0
    items: tuple[AiTradeItem, ...] = ()

    @property
    def intersect_count(self) -> int:
        """items 개수.

        주의: status="partial" + `BARRO_AI_SWING_FALLBACK=scan_only` 인 경우
        items 는 교집합이 아니라 **스캔 단독** 종목이다. 반드시 status 와 함께 해석한다.
        """
        return len(self.items)

    def to_dict(self) -> dict:
        """`data/ai_swing_universe.json` 직렬화용 dict."""
        return {
            "as_of": self.as_of,
            "source_scan_date": self.source_scan_date,
            "source_pred_date": self.source_pred_date,
            "status": self.status,
            "reason": self.reason,
            "scan_count": self.scan_count,
            "pred_count": self.pred_count,
            "intersect_count": self.intersect_count,
            "items": [asdict(it) for it in self.items],
        }


# ─── 안전 변환 헬퍼 (외부 입력 — 예외를 내지 않는다) ─────────────────────────
def _as_str(v: Any) -> str:
    if v is None:
        return ""
    if isinstance(v, str):
        return v
    return str(v)


def _as_float(v: Any) -> float:
    try:
        if isinstance(v, bool):
            return float(int(v))
        if v is None or v == "":
            return 0.0
        return float(v)
    except (TypeError, ValueError):
        return 0.0


def _as_int(v: Any) -> int:
    try:
        if isinstance(v, bool):
            return int(v)
        if v is None or v == "":
            return 0
        return int(float(v))
    except (TypeError, ValueError):
        return 0


def _as_bool(v: Any) -> bool:
    if isinstance(v, str):
        return v.strip().lower() in ("1", "true", "y", "yes")
    return bool(v)


def _read_freshness_env() -> tuple[float, bool]:
    """소비자용 신선도 힌트만 읽어 로그로 남긴다 — **status 판정에 쓰지 않는다**."""
    try:
        max_age_h = float(os.environ.get(ENV_MAX_AGE_H, "12") or 12)
    except ValueError:
        max_age_h = 12.0
    allow_stale = (os.environ.get(ENV_ALLOW_STALE, "0") or "0").strip() in ("1", "true", "Y", "y", "yes")
    logger.debug("ai-trade universe 신선도 힌트(소비자 판단용): max_age_h=%s allow_stale=%s",
                 max_age_h, allow_stale)
    return max_age_h, allow_stale


def _find_source(base_dir: str, prefix: str, today_iso: str) -> Optional[str]:
    """`{prefix}_{today}.json` 우선, 없으면 같은 prefix 중 가장 최근 날짜 파일.

    후자로 잡히면 date 가 과거이므로 status 는 stale 로 귀결된다.
    """
    exact = os.path.join(base_dir, f"{prefix}_{today_iso}.json")
    if os.path.isfile(exact):
        return exact
    dated: list[tuple[str, str]] = []
    for path in glob.glob(os.path.join(base_dir, f"{prefix}_*.json")):
        if not os.path.isfile(path):
            continue
        m = _DATE_RE.search(os.path.basename(path))
        if m:
            dated.append((m.group(1), path))
    if not dated:
        return None
    dated.sort()
    return dated[-1][1]


def _read_payload(path: str) -> dict:
    with open(path, "r", encoding="utf-8") as f:
        payload = json.load(f)
    if not isinstance(payload, dict):
        raise TypeError(f"ai-trade payload 최상위가 dict 아님: {type(payload).__name__}")
    return payload


def _payload_date(payload: dict, path: str) -> str:
    """`date` 필드 우선, 없으면 파일명에서 날짜 추출. 둘 다 없으면 ""(→ stale)."""
    raw = _as_str(payload.get("date")).strip()
    if _DATE_RE.fullmatch(raw):
        return raw
    m = _DATE_RE.search(os.path.basename(path))
    return m.group(1) if m else ""


def _parse_scan(payload: dict) -> dict[str, dict]:
    """watchlist stocks → {정규화코드: 스캔지표}. 중복 코드는 첫 등장만 유지."""
    out: dict[str, dict] = {}
    for row in payload.get("stocks") or ():
        if not isinstance(row, dict):
            continue
        sym = _normalize_symbol(_as_str(row.get("code"))).strip()
        if not sym or sym in out:
            continue
        out[sym] = {
            "name": _as_str(row.get("name")),
            "scan_score": _as_float(row.get("score")),
            "blue_line_status": _as_str(row.get("blue_line_status")),
            "watermelon_signal": _as_bool(row.get("watermelon_signal")),
            "volume_ratio": _as_float(row.get("volume_ratio")),
        }
    return out


def _parse_pred(payload: dict) -> dict[str, dict]:
    """predictions stocks → {정규화코드: 예측점수}. 중복 코드는 첫 등장만 유지."""
    out: dict[str, dict] = {}
    for row in payload.get("stocks") or ():
        if not isinstance(row, dict):
            continue
        sym = _normalize_symbol(_as_str(row.get("code"))).strip()
        if not sym or sym in out:
            continue
        out[sym] = {
            "name": _as_str(row.get("name")),
            "pred_rank": _as_int(row.get("rank")),
            "pred_score": _as_float(row.get("total_score")),
            "confidence": _as_float(row.get("confidence")),
            "consensus_level": _as_str(row.get("consensus_level")),
        }
    return out


def _build_items(
    scan_rows: dict[str, dict],
    pred_rows: dict[str, dict],
    symbols: list[str],
) -> tuple[AiTradeItem, ...]:
    """정렬(pred_score DESC → scan_score DESC → symbol ASC) + rank_combined 부여.

    symbol ASC 는 동점 시 결과를 결정적으로 만들기 위한 마지막 tie-break 다.
    """
    def _sort_key(sym: str) -> tuple[float, float, str]:
        s = scan_rows.get(sym) or {}
        p = pred_rows.get(sym) or {}
        return (-float(p.get("pred_score", 0.0)), -float(s.get("scan_score", 0.0)), sym)

    items: list[AiTradeItem] = []
    for idx, sym in enumerate(sorted(symbols, key=_sort_key), start=1):
        s = scan_rows.get(sym) or {}
        p = pred_rows.get(sym) or {}
        items.append(AiTradeItem(
            symbol=sym,
            name=s.get("name") or p.get("name") or "",
            scan_score=float(s.get("scan_score", 0.0)),
            blue_line_status=_as_str(s.get("blue_line_status")),
            watermelon_signal=bool(s.get("watermelon_signal", False)),
            volume_ratio=float(s.get("volume_ratio", 0.0)),
            pred_rank=int(p.get("pred_rank", 0)),
            pred_score=float(p.get("pred_score", 0.0)),
            confidence=float(p.get("confidence", 0.0)),
            consensus_level=_as_str(p.get("consensus_level")),
            rank_combined=idx,
        ))
    return tuple(items)


def load_ai_trade_universe(
    base_dir: str | None = None,
    today: date | None = None,
) -> AiTradeUniverse:
    """ai-trade 스캔·예측 JSON 을 읽어 교집합 유니버스를 만든다.

    Args:
        base_dir: 산출물 디렉토리. None 이면 `BARRO_AI_TRADE_DIR` 환경변수.
                  미설정(빈 문자열 포함)·부재 → status="no_data".
        today: 기준일(KST). None 이면 KST 오늘. 테스트 주입용.

    Returns:
        AiTradeUniverse — 실패해도 예외 대신 status/reason 으로 강등한다.

    강등 사유(reason) 종류:
        ai_trade_dir_unset / ai_trade_dir_missing / files_missing / watchlist_missing
        / predictions_missing:fallback_disabled / predictions_missing:scan_only
        / intersection_empty / parse_error:<예외클래스명>
    """
    now_kst = datetime.now(_KST)
    as_of = now_kst.isoformat()
    today_iso = (today or now_kst.date()).isoformat()

    try:
        raw_dir = base_dir if base_dir is not None else os.environ.get(ENV_DIR, "")
        resolved = (raw_dir or "").strip()
        if not resolved:
            return AiTradeUniverse(status="no_data", as_of=as_of, reason="ai_trade_dir_unset")
        if not os.path.isdir(resolved):
            logger.debug("ai-trade universe 디렉토리 부재: %s", resolved)
            return AiTradeUniverse(status="no_data", as_of=as_of, reason="ai_trade_dir_missing")

        _read_freshness_env()  # 소비자용 힌트 — 로그만 (status 판정에 미사용)

        scan_path = _find_source(resolved, _SCAN_PREFIX, today_iso)
        pred_path = _find_source(resolved, _PRED_PREFIX, today_iso)
        if scan_path is None and pred_path is None:
            return AiTradeUniverse(status="no_data", as_of=as_of, reason="files_missing")
        if scan_path is None:
            # 예측만 있고 스캔이 없으면 교집합의 기반이 없다 → 날조 대신 no_data.
            logger.warning("ai-trade universe: predictions 만 존재(watchlist 부재) → no_data")
            return AiTradeUniverse(status="no_data", as_of=as_of, reason="watchlist_missing")

        scan_payload = _read_payload(scan_path)
        scan_date = _payload_date(scan_payload, scan_path)
        scan_rows = _parse_scan(scan_payload)

        # ─ predictions 부재 → partial. 스캔 단독 사용은 명시 opt-in 일 때만. ─
        if pred_path is None:
            fallback = (os.environ.get(ENV_FALLBACK, "") or "").strip().lower()
            if fallback == "scan_only":
                items = _build_items(scan_rows, {}, list(scan_rows))
                reason = "predictions_missing:scan_only"
            else:
                items = ()
                reason = "predictions_missing:fallback_disabled"
            logger.info("ai-trade universe partial: scan=%d pred=0 items=%d (%s)",
                        len(scan_rows), len(items), reason)
            return AiTradeUniverse(
                status="partial", as_of=as_of, reason=reason,
                source_scan_date=scan_date, source_pred_date="",
                scan_count=len(scan_rows), pred_count=0, items=items,
            )

        pred_payload = _read_payload(pred_path)
        pred_date = _payload_date(pred_payload, pred_path)
        pred_rows = _parse_pred(pred_payload)

        status = "ok" if (scan_date == today_iso and pred_date == today_iso) else "stale"
        symbols = [s for s in scan_rows if s in pred_rows]
        items = _build_items(scan_rows, pred_rows, symbols)
        reason = "" if items else "intersection_empty"
        logger.info("ai-trade universe %s: scan=%d pred=%d 교집합=%d (scan_date=%s pred_date=%s)",
                    status, len(scan_rows), len(pred_rows), len(items), scan_date, pred_date)
        return AiTradeUniverse(
            status=status, as_of=as_of, reason=reason,
            source_scan_date=scan_date, source_pred_date=pred_date,
            scan_count=len(scan_rows), pred_count=len(pred_rows), items=items,
        )
    except Exception as exc:  # 라이브 무영향 — 예외 전량 흡수
        logger.warning("ai-trade universe 로드 실패: %s: %s", type(exc).__name__, exc)
        return AiTradeUniverse(
            status="no_data", as_of=as_of, reason=f"parse_error:{type(exc).__name__}",
        )


__all__ = [
    "AiTradeItem",
    "AiTradeUniverse",
    "load_ai_trade_universe",
    "ENV_DIR",
    "ENV_FALLBACK",
    "ENV_MAX_AGE_H",
    "ENV_ALLOW_STALE",
]
