"""ai_swing 전략 활성화 현황 API (대시보드 우측 패널용) — 2026-08-12 신규.

★ 읽기 전용 ★
주문·체결·계좌 경로를 **import 조차 하지 않는다**(§2 S1/S2). 외부 네트워크·DB·브로커
호출이 없고, 파일 읽기만 한다. 쓰기는 어떤 경로에도 없다 —
`ActivePositionStore` 는 손상 감지 시 **격리·복원 쓰기**를 하므로 의도적으로 쓰지 않고
`active_positions.json` 을 평문 read-only 로 읽는다.

**default-OFF** (§2 S3): `BARRO_AI_SWING_DASHBOARD_ENABLED` 가 truthy 가 아니면
아무 파일도 읽지 않고 `status="disabled"` 로 즉시 강등한다. 라우터 자체는 항상 등록돼
404 대신 "왜 안 보이는지"를 UI 가 설명할 수 있게 한다.

왜 `.env.local` 을 읽는가 (설정 진실원천)
------------------------------------------
라이브 백엔드는 launchd 가 기동할 때 `.env.local` 을 **한 번** 소싱한다. 이후 운영자가
`.env.local` 을 바꿔도 **실행 중 프로세스의 `os.environ` 은 갱신되지 않는다**
(2026-08-12 실측: 백엔드 프로세스 env 는 `BARRO_DAEMON_STRATEGIES=f_zone,sf_zone`
이었으나 `.env.local` 은 `ai_swing`). 반면 매매 데몬은 cron 이 매 실행마다
`set -a && . .env.local && set +a` 로 소싱하므로 **다음 데몬 실행에 실제로 적용되는 값은
`.env.local` 파일**이다. 따라서 이 API 는 파일을 진실원천으로 삼고, 프로세스 env 와
다른 항목은 `config_mismatch` 로 함께 보고한다(§8 — 라벨 없는 강등 금지).

노출 정책 (백엔드 API 무인증 + 공개 터널 — docs/04-report/2026-07-01-security-review.md)
----------------------------------------------------------------------------------------
`.env.local` 에는 키·토큰이 들어 있으므로 **허용목록(`_ALLOWED_ENV_KEYS`) 밖의 키는
파싱 단계에서 즉시 버린다**. 허용목록에 자격증명 계열 키는 없다.
`KIWOOM_BASE_URL` 은 원문 대신 `broker_mode`("mock"/"real"/"unknown") 라벨로만 내보내고,
`BARRO_AI_TRADE_DIR` 은 경로 대신 설정 여부(bool) 만 내보낸다.

엔드포인트:
  GET /api/ai-swing/status  - 게이트·캡·유니버스·보유·shadow 관측 현황

status 값: "ok" | "disabled" | "no_data"
데이터가 없으면 만들어 넣지 않고 하위 블록별로 강등한다(§8).
"""
from __future__ import annotations

import json
import logging
import os
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from fastapi import APIRouter

logger = logging.getLogger(__name__)
router = APIRouter()

_REPO_ROOT = Path(__file__).resolve().parents[3]
_DATA_DIR = _REPO_ROOT / "data"
_ENV_FILE = _REPO_ROOT / ".env.local"

_KST = timezone(timedelta(hours=9))
_TRUTHY = {"1", "true", "yes", "on"}

# 이 라우트 자체의 default-OFF 마스터 (§2 S3)
ENV_DASHBOARD = "BARRO_AI_SWING_DASHBOARD_ENABLED"

_STRATEGY_ID = "ai_swing"

# `.env.local` 에서 **읽어도 되는 키**. 이 목록 밖은 파싱 즉시 폐기한다.
# 자격증명(KIWOOM_APP_KEY/SECRET, TELEGRAM_BOT_TOKEN 등)은 의도적으로 없다.
_ALLOWED_ENV_KEYS = frozenset({
    "BARRO_DAEMON_STRATEGIES",
    "BARRO_AI_SWING_ENABLED",
    "BARRO_AI_SWING_ENTRY_ENABLED",
    "BARRO_AI_SWING_BUDGET_RATIO",
    "BARRO_AI_SWING_MAX_POSITIONS",
    "BARRO_AI_SWING_MAX_AGE_H",
    "BARRO_AI_SWING_ALLOW_STALE",
    "BARRO_AI_SWING_FALLBACK",
    "BARRO_AI_SWING_MIN_PRED_SCORE",
    "BARRO_AI_SWING_MIN_CONSENSUS",
    "BARRO_AI_SWING_TOP_N",
    "BARRO_AI_TRADE_DIR",       # 값은 내보내지 않고 설정 여부만 쓴다
    "LIVE_TRADING_ENABLED",
    "KIWOOM_BASE_URL",          # 값은 내보내지 않고 mock/real 라벨만 쓴다
})

# 프로세스 env 와 대조해 stale 여부를 보고할 키 (경로·URL 은 값 노출 없이 제외)
_MISMATCH_KEYS = (
    "BARRO_DAEMON_STRATEGIES",
    "BARRO_AI_SWING_ENABLED",
    "BARRO_AI_SWING_ENTRY_ENABLED",
    "BARRO_AI_SWING_BUDGET_RATIO",
    "BARRO_AI_SWING_MAX_POSITIONS",
)

_ENV_FILE_MAX_BYTES = 256 * 1024   # 비정상적으로 큰 파일은 읽지 않는다
_KEY_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")

_UNIVERSE_FILENAME = "ai_swing_universe.json"
_SIGNALS_FILENAME = "ai_swing_signals.json"
_HISTORY_DIRNAME = "ai_swing_history"
_POSITIONS_FILENAME = "active_positions.json"

# 유니버스 목록 응답 상한 — 교집합은 통상 한 자릿수지만 방어적으로 자른다.
_MAX_UNIVERSE_ITEMS = 50


# ─── 소도구 ──────────────────────────────────────────────────────────────────
def _now_iso() -> str:
    return datetime.now(_KST).isoformat()


def _is_truthy(raw: str | None) -> bool:
    """데몬(`_env_truthy`)과 동일 판정 — {"1","true","yes","on"}."""
    return (raw or "").strip().lower() in _TRUTHY


def _dashboard_enabled() -> bool:
    return _is_truthy(os.environ.get(ENV_DASHBOARD, "0"))


def _as_float(v: Any, default: float = 0.0) -> float:
    try:
        return float(v)
    except (TypeError, ValueError):
        return default


def _as_int(v: Any, default: int = 0) -> int:
    try:
        return int(float(v))
    except (TypeError, ValueError):
        return default


def _strip_value(raw: str) -> str:
    """dotenv 값 1개 정규화 — 따옴표 해제, 비따옴표 값의 인라인 주석 제거.

    운영 `.env.local` 은 `KEY=value   # [2026-08-12] 설명` 형태로 주석을 단다.
    **빈 값 + 주석**(`BARRO_AI_SWING_FALLBACK=   # 빈값=완전 교집합만`)도 흔하므로
    주석 판정은 `strip()` 전에 한다 — 먼저 strip 하면 `#` 앞 공백이 사라져 주석이
    값으로 잡힌다(2026-08-12 D4 실측 버그).
    따옴표가 있으면 주석 제거를 하지 않아 값 안의 `#` 을 보존한다.
    """
    v = raw.lstrip()
    if v[:1] in ("'", '"'):
        quote = v[0]
        end = v.find(quote, 1)
        return v[1:end] if end > 0 else v[1:]
    # 비따옴표: 값 맨 앞이거나 공백 뒤에 오는 '#' 부터 주석 (`abc#def` 는 값 보존)
    m = re.search(r"(^|\s)#", v)
    if m:
        v = v[: m.start()]
    return v.strip()


def _read_env_file(path: Path) -> tuple[dict[str, str], str, str]:
    """`.env.local` 에서 **허용목록 키만** 읽는다 → (values, as_of, reason).

    허용목록 밖의 키는 값에 손대지 않고 즉시 버려, 자격증명이 메모리에 남지 않는다.
    실패는 예외 대신 reason 으로 강등한다(라이브 무영향).
    """
    values: dict[str, str] = {}
    try:
        if not path.is_file():
            return values, "", "env_file_missing"
        stat = path.stat()
        if stat.st_size > _ENV_FILE_MAX_BYTES:
            return values, "", f"env_file_too_large:{stat.st_size}"
        as_of = datetime.fromtimestamp(stat.st_mtime, _KST).isoformat()
        with path.open("r", encoding="utf-8", errors="replace") as fh:
            for line in fh:
                line = line.strip()
                if not line or line.startswith("#"):
                    continue
                if line.startswith("export "):
                    line = line[len("export "):].lstrip()
                key, sep, raw = line.partition("=")
                if not sep:
                    continue
                key = key.strip()
                if key not in _ALLOWED_ENV_KEYS or not _KEY_RE.match(key):
                    continue        # ← 허용목록 밖은 값을 만지지 않고 폐기
                values[key] = _strip_value(raw)
        return values, as_of, ""
    except OSError as exc:
        return {}, "", f"env_file_error:{type(exc).__name__}"


def _strategy_list(raw: str) -> list[str]:
    return [s.strip() for s in (raw or "").split(",") if s.strip()]


# ─── 게이트 판정 (scripts/intraday_buy_daemon.py 의 5중 default-OFF 미러) ─────
def _build_gates(env: dict[str, str]) -> list[dict]:
    """진입 5중 게이트 — intraday_buy_daemon `_ai_swing_entry_enabled`/`_ai_swing_caps` 기준.

    ①전략목록 ②마스터 ③실진입 ④예산캡>0 ⑤원본 디렉토리. 전부 열려야 주문 시도가 생긴다.
    """
    strategies = _strategy_list(env.get("BARRO_DAEMON_STRATEGIES", ""))
    budget = _as_float(env.get("BARRO_AI_SWING_BUDGET_RATIO", "0"), 0.0)
    return [
        {
            "id": "strategy_included",
            "label": "데몬 전략 목록",
            "env": "BARRO_DAEMON_STRATEGIES",
            "value": ", ".join(strategies) or "(미설정)",
            "ok": _STRATEGY_ID in strategies,
        },
        {
            "id": "master",
            "label": "마스터 스위치",
            "env": "BARRO_AI_SWING_ENABLED",
            "value": env.get("BARRO_AI_SWING_ENABLED", "") or "(미설정)",
            "ok": _is_truthy(env.get("BARRO_AI_SWING_ENABLED")),
        },
        {
            "id": "entry",
            "label": "실진입 허용",
            "env": "BARRO_AI_SWING_ENTRY_ENABLED",
            "value": env.get("BARRO_AI_SWING_ENTRY_ENABLED", "") or "(미설정)",
            "ok": _is_truthy(env.get("BARRO_AI_SWING_ENTRY_ENABLED")),
        },
        {
            "id": "budget",
            "label": "예산 캡",
            "env": "BARRO_AI_SWING_BUDGET_RATIO",
            "value": f"{budget * 100:.0f}%" if budget > 0 else "0% (차단)",
            "ok": 0.0 < budget <= 1.0,
        },
        {
            "id": "source",
            "label": "단테 원본 경로",
            "env": "BARRO_AI_TRADE_DIR",
            "value": "설정됨" if (env.get("BARRO_AI_TRADE_DIR") or "").strip() else "(미설정)",
            "ok": bool((env.get("BARRO_AI_TRADE_DIR") or "").strip()),
        },
    ]


def _broker_mode(raw: str) -> str:
    """KIWOOM_BASE_URL → mock/real 라벨. 원문 URL 은 응답에 넣지 않는다."""
    url = (raw or "").strip().lower()
    if "mockapi" in url:
        return "mock"
    if "api.kiwoom.com" in url:
        return "real"
    return "unknown"


def _build_config(env: dict[str, str]) -> dict:
    return {
        "budget_ratio": _as_float(env.get("BARRO_AI_SWING_BUDGET_RATIO", "0"), 0.0),
        "max_positions": _as_int(env.get("BARRO_AI_SWING_MAX_POSITIONS", "0"), 0),
        "max_age_h": _as_float(env.get("BARRO_AI_SWING_MAX_AGE_H", "12"), 12.0),
        "allow_stale": _is_truthy(env.get("BARRO_AI_SWING_ALLOW_STALE")),
        "fallback": (env.get("BARRO_AI_SWING_FALLBACK") or "").strip(),
        "live_trading": _is_truthy(env.get("LIVE_TRADING_ENABLED")),
        "broker_mode": _broker_mode(env.get("KIWOOM_BASE_URL", "")),
    }


def _config_mismatch(env: dict[str, str]) -> list[dict]:
    """`.env.local`(다음 데몬 실행값) vs 이 백엔드 프로세스 env(기동 시점 값).

    다르면 대시보드가 보는 값과 백엔드 프로세스 내부 값이 갈린다는 뜻이므로 그대로 보고한다.
    """
    out: list[dict] = []
    for key in _MISMATCH_KEYS:
        file_val = (env.get(key) or "").strip()
        proc_val = (os.environ.get(key) or "").strip()
        if file_val != proc_val:
            out.append({"env": key, "env_local": file_val or "(없음)", "process": proc_val or "(없음)"})
    return out


# ─── 유니버스 (읽기 전용 순수 로더 재사용) ────────────────────────────────────
def _load_universe(source_dir: str) -> dict:
    """`load_ai_trade_universe()` 는 어떤 입력에도 예외를 던지지 않는 순수 파일 로더다.

    `.env.local` 의 경로를 명시 주입해, 프로세스 env 가 stale 이어도 데몬과 같은 원본을 본다.
    """
    if not source_dir:
        return {"status": "no_data", "reason": "ai_trade_dir_unset", "items": []}
    try:
        from backend.core.scanner.ai_trade_universe import load_ai_trade_universe

        uni = load_ai_trade_universe(base_dir=source_dir)
        items = [
            {
                "symbol": it.symbol,
                "name": it.name or it.symbol,
                "rank_combined": it.rank_combined,
                "scan_score": round(float(it.scan_score), 2),
                "pred_score": round(float(it.pred_score), 2),
                "pred_rank": int(it.pred_rank),
                "confidence": round(float(it.confidence), 2),
                "consensus_level": it.consensus_level,
                "blue_line_status": it.blue_line_status,
                "volume_ratio": round(float(it.volume_ratio), 2),
                "watermelon_signal": bool(it.watermelon_signal),
            }
            for it in (uni.items or ())[:_MAX_UNIVERSE_ITEMS]
        ]
        return {
            "status": uni.status,
            "reason": uni.reason,
            "as_of": uni.as_of,
            "scan_date": uni.source_scan_date,
            "pred_date": uni.source_pred_date,
            "scan_count": uni.scan_count,
            "pred_count": uni.pred_count,
            "intersect_count": uni.intersect_count,
            "items": items,
            "truncated": uni.intersect_count > len(items),
        }
    except Exception as exc:  # noqa: BLE001 — 라이브 무영향
        logger.warning("ai_swing 유니버스 로드 실패: %s", type(exc).__name__)
        return {"status": "no_data", "reason": f"loader_error:{type(exc).__name__}", "items": []}


def _entry_ready(source_dir: str) -> dict:
    """실진입 원본 신선도 게이트 — 데몬과 동일한 `validate_current_sources()`.

    max_age_h 는 로더가 프로세스 env(기본 12)에서 해석한다. `.env.local` 값과 다르면
    `config_mismatch` 로 함께 드러난다.
    """
    if not source_dir:
        return {"ok": False, "reason": "ai_trade_dir_unset"}
    try:
        from backend.core.scanner.ai_trade_universe import validate_current_sources

        ok, reason = validate_current_sources(base_dir=source_dir)
        return {"ok": bool(ok), "reason": reason}
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "reason": f"validate_error:{type(exc).__name__}"}


# ─── 보유 포지션 (평문 read-only — 저널 쓰기 경로를 쓰지 않는다) ──────────────
def _ai_swing_positions(data_dir: Path) -> dict:
    path = data_dir / _POSITIONS_FILENAME
    try:
        if not path.is_file():
            return {"status": "no_data", "reason": "positions_file_missing", "items": []}
        with path.open("r", encoding="utf-8") as fh:
            raw = json.load(fh)
        if not isinstance(raw, dict):
            return {"status": "no_data", "reason": "positions_schema", "items": []}
        items = []
        for symbol, pos in raw.items():
            if not isinstance(pos, dict):
                continue
            if str(pos.get("strategy") or "").strip() != _STRATEGY_ID:
                continue
            tranches = pos.get("tranches") or []
            filled_qty = sum(
                _as_int(t.get("qty"))
                for t in tranches
                if isinstance(t, dict) and str(t.get("status") or "") == "filled"
            )
            items.append({
                "symbol": str(pos.get("symbol") or symbol),
                "name": str(pos.get("name") or symbol),
                "entry_price": _as_float(pos.get("entry_price")),
                "entry_time": str(pos.get("entry_time") or ""),
                "filled_qty": filled_qty,
                "total_recommended_qty": _as_int(pos.get("total_recommended_qty")),
                "sl_pct": _as_float(pos.get("sl_pct")),
                "peak_pnl_rate": _as_float(pos.get("peak_pnl_rate")),
            })
        items.sort(key=lambda r: r["entry_time"], reverse=True)
        return {"status": "ok", "reason": "", "items": items, "total_positions": len(raw)}
    except (OSError, ValueError) as exc:
        return {"status": "no_data", "reason": f"positions_error:{type(exc).__name__}", "items": []}


# ─── shadow 관측 산출물 (scripts/ai_swing_daemon.py 가 만든다) ────────────────
def _shadow_snapshot(data_dir: Path) -> dict:
    """`data/ai_swing_signals.json` 최신 판정. 파일이 없으면 만들지 않고 no_data.

    이 파일은 관측 데몬이 실행돼야 생긴다(런북 §4). 미실행 환경에서는 부재가 정상이다.
    """
    path = data_dir / _SIGNALS_FILENAME
    try:
        if not path.is_file():
            return {"status": "no_data", "reason": "shadow_never_run", "signals": []}
        with path.open("r", encoding="utf-8") as fh:
            payload = json.load(fh)
        if not isinstance(payload, dict):
            return {"status": "no_data", "reason": "shadow_schema", "signals": []}
        signals = [
            {
                "symbol": str(s.get("symbol") or ""),
                "name": str(s.get("name") or ""),
                "entry_price": _as_float(s.get("entry_price")),
                "sl_price": _as_float(s.get("sl_price")),
                "tp1_price": _as_float(s.get("tp1_price")),
                "score": _as_float(s.get("score")),
                "reason": str(s.get("reason") or ""),
            }
            for s in (payload.get("signals") or ())
            if isinstance(s, dict)
        ]
        return {
            "status": "ok",
            "reason": "",
            "as_of": str(payload.get("as_of") or ""),
            "universe_status": str(payload.get("universe_status") or ""),
            "universe_reason": str(payload.get("universe_reason") or ""),
            "evaluated": _as_int(payload.get("evaluated")),
            "signal_count": _as_int(payload.get("signal_count"), len(signals)),
            "skipped_count": len(payload.get("skipped") or ()),
            "signals": signals,
        }
    except (OSError, ValueError) as exc:
        return {"status": "no_data", "reason": f"shadow_error:{type(exc).__name__}", "signals": []}


def _shadow_history_days(data_dir: Path) -> int:
    """shadow 이력 일수 — 실주문 전환 게이트 "최소 5거래일"(런북 §6-1) 진척도용."""
    try:
        hist = data_dir / _HISTORY_DIRNAME
        if not hist.is_dir():
            return 0
        return len([p for p in hist.glob("*.jsonl") if p.is_file()])
    except OSError:
        return 0


# ─── 엔드포인트 ──────────────────────────────────────────────────────────────
@router.get("/ai-swing/status")
async def get_ai_swing_status() -> dict:
    """ai_swing 활성화 현황 (읽기 전용).

    응답 예:
    ```json
    {
      "status": "ok",
      "as_of": "2026-08-12T09:10:00+09:00",
      "entry_active": true,
      "gates": [{"id": "master", "label": "마스터 스위치", "ok": true, "value": "1"}],
      "config": {"budget_ratio": 0.1, "max_positions": 1, "broker_mode": "mock"},
      "config_source": {"path": ".env.local", "as_of": "...", "reason": ""},
      "config_mismatch": [{"env": "BARRO_DAEMON_STRATEGIES", "env_local": "ai_swing",
                           "process": "f_zone,sf_zone"}],
      "universe": {"status": "ok", "intersect_count": 1, "items": [...]},
      "entry_ready": {"ok": false, "reason": "source_missing:watchlist_2026-08-12.json"},
      "positions": {"status": "ok", "items": []},
      "shadow": {"status": "no_data", "reason": "shadow_never_run"}
    }
    ```

    `status="disabled"` 는 `BARRO_AI_SWING_DASHBOARD_ENABLED` 미설정(기본)이며
    이때는 어떤 파일도 읽지 않는다.
    """
    if not _dashboard_enabled():
        return {
            "status": "disabled",
            "as_of": _now_iso(),
            "reason": f"{ENV_DASHBOARD} 미설정 (기본 OFF)",
        }

    try:
        env, env_as_of, env_reason = _read_env_file(_ENV_FILE)
        source_dir = (env.get("BARRO_AI_TRADE_DIR") or "").strip()
        gates = _build_gates(env)
        config = _build_config(env)
        data_dir = Path(os.environ.get("BARRO_DATA_DIR") or str(_DATA_DIR))

        return {
            "status": "ok" if not env_reason else "no_data",
            "as_of": _now_iso(),
            # 5중 게이트가 전부 열려야 주문 시도가 생긴다. 위에 LIVE_TRADING_ENABLED 별도.
            "entry_active": all(g["ok"] for g in gates) and config["live_trading"],
            "gates": gates,
            "config": config,
            "config_source": {"path": ".env.local", "as_of": env_as_of, "reason": env_reason},
            "config_mismatch": _config_mismatch(env),
            "universe": _load_universe(source_dir),
            "entry_ready": _entry_ready(source_dir),
            "positions": _ai_swing_positions(data_dir),
            "shadow": _shadow_snapshot(data_dir),
            "shadow_history_days": _shadow_history_days(data_dir),
        }
    except Exception as exc:  # noqa: BLE001 — 대시보드가 라이브 백엔드를 죽이지 않는다
        logger.warning("ai_swing status 조회 실패: %s: %s", type(exc).__name__, exc)
        return {
            "status": "no_data",
            "as_of": _now_iso(),
            "reason": f"error:{type(exc).__name__}",
        }


__all__ = ["router", "ENV_DASHBOARD"]
