#!/usr/bin/env python3
"""ai_swing 관측 전용 데몬 — 단테(ai-trade) 교집합 유니버스·진입 신호 수 계측 (2026-07-31 신규).

★ 이 스크립트는 **관측 전용**이다 ★
브로커에 매매 요청을 보내지 않는다. 매매 체결 경로를 **import 조차 하지 않는다**
(테스트 `backend/tests/test_ai_swing_daemon.py::test_source_has_no_execution_symbols`
가 소스 텍스트 검사로 이 계약을 고정한다). 외부 호출은 **읽기 전용 시세 조회 TR**
(일봉 ka10081) 하나뿐이고, 청산 평가도 하지 않는다(런북 §4 의 두 경로가 담당).

무엇을 하는가
------------
1. `load_ai_trade_universe()` 로 ai-trade 스캔 ∩ 예측 교집합을 읽어
   `data/ai_swing_universe.json` 에 기록한다.
2. 교집합 종목의 일봉을 받아 `AiSwingStrategy` 로 진입 신호를 판정하고,
   개수·목록을 `data/ai_swing_signals.json` 에 기록한다.
운영 런북 §2 "1) shadow (주문 0건)" 단계의 실측 표본을 쌓는 것이 유일한 목적이다.
표본이 없으면 전략 활성 판단을 할 수 없다 (런북 §2-2).

환경변수 (전부 default-OFF / 무해한 기본값)
-----------------------------------------
| 이름 | 기본 | 의미 |
|---|---|---|
| `BARRO_AI_TRADE_DIR`             | `""` | ai-trade 산출물 디렉토리. 미설정이면 로더가 no_data |
| `BARRO_AI_SWING_ENABLED`         | `0`  | 이 데몬의 마스터. truthy 아니면 즉시 종료(파일도 안 쓴다) |
| `BARRO_AI_SWING_MIN_PRED_SCORE`  | `0`  | 예측점수 하한 (0=무필터) |
| `BARRO_AI_SWING_MIN_CONSENSUS`   | `""` | 합의수준 하한 (빈값=무필터) |
| `BARRO_AI_SWING_TOP_N`           | `0`  | 관측 상한 종목수 (0=전체). `--top` 이 우선 |
| `BARRO_AI_SWING_ALLOW_STALE`     | `0`  | 전일자 산출물(status="stale") 로도 신호를 평가할지 |
| `BARRO_DATA_DIR`                 | `<repo>/data` | 산출 JSON 디렉토리 (테스트 격리용) |
truthy 판정 = {"1","true","yes","on"} (소문자 비교).

산출 파일 (원자적 저장 — tmp 파일에 쓰고 os.replace. 부분 파일이 남지 않는다)
--------------------------------------------------------------------------
- `data/ai_swing_universe.json` — 로더 산출 **그대로**(필터 적용 전). 즉 `items` /
  `intersect_count` 는 교집합 전량이며, 아래 필터는 "무엇을 관측했는가"에만 작용한다.
- `data/ai_swing_signals.json` — `evaluated` 는 **전략이 실제로 판정한 종목 수**다
  (조회 실패·캔들 부족은 `evaluated` 에서 빠지고 `skipped` 로 간다).
  `universe_reason` 은 유니버스 사유를 담되, 데몬 자체가 실패하면
  `daemon_error:<예외클래스명>` 을 함께 싣는다 (계약 필드를 늘리지 않기 위함).

exit code 는 **항상 0** 이다 (cron 안전). 사유는 stdout 과 signals 파일에 남긴다.
단 CLI 오사용(argparse) 은 즉시 알려야 하므로 그대로 전파된다.

사용:
  BARRO_AI_SWING_ENABLED=1 BARRO_AI_TRADE_DIR=/path/to/ai-trade/logs \\
      python scripts/ai_swing_daemon.py --sleep 0.25
  python scripts/ai_swing_daemon.py --telegram      # 요약 1건 발송(실패해도 무시)
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
import tempfile
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path
from typing import Any, Optional, Sequence

_REPO = Path(__file__).resolve().parent.parent
if str(_REPO) not in sys.path:
    sys.path.insert(0, str(_REPO))

from backend.core.scanner.ai_trade_universe import (  # noqa: E402
    AiTradeUniverse,
    load_ai_trade_universe,
)
from backend.core.strategy.ai_swing import (  # noqa: E402
    AiSwingStrategy,
    build_exit_plan,
)
from backend.models.market import MarketType  # noqa: E402
from backend.models.strategy import AnalysisContext  # noqa: E402

_KST = timezone(timedelta(hours=9))
_TRUTHY = {"1", "true", "yes", "on"}

ENV_ENABLED = "BARRO_AI_SWING_ENABLED"
ENV_MIN_PRED_SCORE = "BARRO_AI_SWING_MIN_PRED_SCORE"
ENV_MIN_CONSENSUS = "BARRO_AI_SWING_MIN_CONSENSUS"
ENV_TOP_N = "BARRO_AI_SWING_TOP_N"
ENV_ALLOW_STALE = "BARRO_AI_SWING_ALLOW_STALE"
ENV_FALLBACK = "BARRO_AI_SWING_FALLBACK"   # 로더 소유. 데몬은 partial 허용 판정에만 읽는다.
ENV_DATA_DIR = "BARRO_DATA_DIR"

UNIVERSE_FILENAME = "ai_swing_universe.json"
SIGNALS_FILENAME = "ai_swing_signals.json"

# 합의수준 사다리 — backend/legacy_scalping/scanner/agents/coordinator.py::_consensus_label
# 이 만드는 라벨(관찰). ai-trade 측이 다른 라벨을 쓰면 rank 0 으로 떨어지므로,
# 임계 라벨이 이 표에 없으면 **필터를 끄고 경고만** 한다(전량 탈락 사고 방지).
_CONSENSUS_RANK = {
    "단독판단": 1,
    "소수합의": 2,
    "다수합의": 3,
    "강한합의": 4,
    "만장일치": 5,
}

_LOG_PREFIX = "[ai_swing_daemon]"


# ─── 소도구 ──────────────────────────────────────────────────────────────────
def is_truthy(raw: Optional[str]) -> bool:
    """{"1","true","yes","on"} (소문자 비교) 만 참."""
    return (raw or "").strip().lower() in _TRUTHY


def data_dir() -> Path:
    """산출 디렉토리 — `BARRO_DATA_DIR` override (테스트가 tmp_path 로 격리)."""
    return Path(os.environ.get(ENV_DATA_DIR) or str(_REPO / "data"))


def now_iso() -> str:
    """KST(+09:00) tz-aware ISO8601."""
    return datetime.now(_KST).isoformat()


def _log(msg: str) -> None:
    print(f"{_LOG_PREFIX} {msg}")


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


def atomic_write_json(path: Path, payload: dict) -> None:
    """tmp 파일 → fsync → os.replace. 중간에 죽어도 부분 파일이 남지 않는다."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(dir=str(path.parent), prefix=f".{path.name}.", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(payload, fh, ensure_ascii=False, indent=2)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp_name, path)
    except BaseException:
        try:
            os.unlink(tmp_name)
        except OSError:
            pass
        raise


# ─── 계약 스키마 빌더 ────────────────────────────────────────────────────────
def item_payload(item: Any) -> dict:
    """AiTradeItem → universe.json items[] 1행 (계약 11필드 고정)."""
    return {
        "symbol": str(getattr(item, "symbol", "") or ""),
        "name": str(getattr(item, "name", "") or ""),
        "scan_score": _as_float(getattr(item, "scan_score", 0.0)),
        "blue_line_status": str(getattr(item, "blue_line_status", "") or ""),
        "watermelon_signal": bool(getattr(item, "watermelon_signal", False)),
        "volume_ratio": _as_float(getattr(item, "volume_ratio", 0.0)),
        "pred_rank": _as_int(getattr(item, "pred_rank", 0)),
        "pred_score": _as_float(getattr(item, "pred_score", 0.0)),
        "confidence": _as_float(getattr(item, "confidence", 0.0)),
        "consensus_level": str(getattr(item, "consensus_level", "") or ""),
        "rank_combined": _as_int(getattr(item, "rank_combined", 0)),
    }


def universe_payload(uni: AiTradeUniverse) -> dict:
    """AiTradeUniverse → ai_swing_universe.json (계약 스키마)."""
    return {
        "as_of": str(getattr(uni, "as_of", "") or now_iso()),
        "status": str(getattr(uni, "status", "") or "no_data"),
        "reason": str(getattr(uni, "reason", "") or ""),
        "source_scan_date": str(getattr(uni, "source_scan_date", "") or ""),
        "source_pred_date": str(getattr(uni, "source_pred_date", "") or ""),
        "scan_count": _as_int(getattr(uni, "scan_count", 0)),
        "pred_count": _as_int(getattr(uni, "pred_count", 0)),
        "intersect_count": _as_int(getattr(uni, "intersect_count", 0)),
        "items": [item_payload(it) for it in (getattr(uni, "items", ()) or ())],
    }


def signals_payload(
    *,
    universe_status: str,
    universe_reason: str,
    evaluated: int,
    signals: Sequence[dict],
    skipped: Sequence[dict],
) -> dict:
    """ai_swing_signals.json (계약 스키마)."""
    return {
        "as_of": now_iso(),
        "universe_status": str(universe_status or ""),
        "universe_reason": str(universe_reason or ""),
        "evaluated": int(evaluated),
        "signal_count": len(signals),
        "signals": list(signals),
        "skipped": list(skipped),
    }


# ─── 관측 대상 선별 (순수 함수 — 테스트 대상) ────────────────────────────────
def consensus_rank(label: str) -> int:
    """합의수준 라벨 → 서열. 미지·빈값은 0 (어떤 임계도 통과 못 함)."""
    return _CONSENSUS_RANK.get((label or "").strip(), 0)


def select_items(
    items: Sequence[dict],
    *,
    min_pred_score: float = 0.0,
    min_consensus: str = "",
    top_n: int = 0,
) -> tuple[list[dict], dict]:
    """관측 대상 필터링 — (선별 목록, 통계 dict).

    입력 순서(로더의 rank_combined 순)를 보존한다. top_n<=0 이면 상한 없음.
    min_consensus 라벨이 `_CONSENSUS_RANK` 에 없으면 **필터를 끈다**
    (라벨 규약이 달라 전량 탈락하는 사고를 막기 위한 의도적 fail-open).
    """
    stats: dict[str, Any] = {
        "input": len(items),
        "dropped_pred_score": 0,
        "dropped_consensus": 0,
        "dropped_top_n": 0,
        "consensus_filter": "",
    }
    out = list(items)

    if min_pred_score > 0:
        kept = [it for it in out if _as_float(it.get("pred_score")) >= min_pred_score]
        stats["dropped_pred_score"] = len(out) - len(kept)
        out = kept

    label = (min_consensus or "").strip()
    if label:
        threshold = consensus_rank(label)
        if threshold <= 0:
            stats["consensus_filter"] = f"unknown_label:{label}(필터 미적용)"
        else:
            stats["consensus_filter"] = f"{label}(rank>={threshold})"
            kept = [it for it in out if consensus_rank(str(it.get("consensus_level", ""))) >= threshold]
            stats["dropped_consensus"] = len(out) - len(kept)
            out = kept

    if top_n > 0 and len(out) > top_n:
        stats["dropped_top_n"] = len(out) - top_n
        out = out[:top_n]

    stats["selected"] = len(out)
    return out, stats


# ─── 시세 조회기 (읽기 전용 TR 만) ───────────────────────────────────────────
def build_candle_fetcher() -> Optional[Any]:
    """일봉(ka10081) 조회기. 키 미설정·생성 실패 시 None (예외 전파 금지).

    ★읽기 전용 시세 TR 전용★ — 이 데몬이 만드는 유일한 외부 클라이언트다.
    테스트는 이 함수를 monkeypatch 해 실 API 호출 0건을 보장한다.
    """
    app_key = (os.environ.get("KIWOOM_APP_KEY") or "").strip()
    app_secret = (os.environ.get("KIWOOM_APP_SECRET") or "").strip()
    if not app_key or not app_secret:
        return None
    try:
        from pydantic import SecretStr

        from backend.core.gateway.kiwoom_native_candles import KiwoomNativeCandleFetcher
        from backend.core.gateway.kiwoom_native_oauth import KiwoomNativeOAuth

        oauth = KiwoomNativeOAuth(
            app_key=SecretStr(app_key),
            app_secret=SecretStr(app_secret),
            base_url=os.environ.get("KIWOOM_BASE_URL", "https://mockapi.kiwoom.com"),
        )
        return KiwoomNativeCandleFetcher(oauth=oauth)
    except Exception as exc:  # noqa: BLE001 — 데몬은 죽지 않는다
        _log(f"시세 조회기 생성 실패 {type(exc).__name__}: {exc}")
        return None


# ─── 신호 판정 ───────────────────────────────────────────────────────────────
async def evaluate_signals(
    items: Sequence[dict],
    fetcher: Any,
    strategy: AiSwingStrategy,
    *,
    sleep_sec: float = 0.25,
) -> tuple[list[dict], list[dict], int]:
    """종목별 일봉 → AiSwingStrategy 진입 판정. (signals, skipped, evaluated).

    어떤 종목의 실패도 전파하지 않는다 — `skipped` 에 사유를 남기고 계속 간다.
    `evaluated` 는 전략이 실제로 판정한 종목 수(=skipped 제외)다.
    """
    signals: list[dict] = []
    skipped: list[dict] = []
    evaluated = 0
    min_candles = int(getattr(strategy.params, "min_candles", 60))

    for idx, it in enumerate(items):
        symbol = str(it.get("symbol") or "")
        name = str(it.get("name") or "") or symbol
        if not symbol:
            skipped.append({"symbol": "", "reason": "empty_symbol"})
            continue
        if idx > 0 and sleep_sec > 0:
            await asyncio.sleep(sleep_sec)   # rate-limit 완화

        try:
            candles = await fetcher.fetch_daily(symbol=symbol)
        except Exception as exc:  # noqa: BLE001
            skipped.append({"symbol": symbol, "reason": f"fetch_error:{type(exc).__name__}"})
            continue

        count = len(candles or ())
        if count < min_candles:
            skipped.append({"symbol": symbol, "reason": f"insufficient_candles:{count}<{min_candles}"})
            continue

        try:
            ctx = AnalysisContext(
                symbol=symbol,
                name=name,
                candles=list(candles),
                market_type=MarketType.STOCK,
            )
            signal = strategy.analyze(ctx)
        except Exception as exc:  # noqa: BLE001
            skipped.append({"symbol": symbol, "reason": f"analyze_error:{type(exc).__name__}"})
            continue

        if signal is None:
            evaluated += 1          # 전략이 판정했고 "신호 없음"이 결론이다
            continue

        entry_price = _as_float(getattr(signal, "price", 0.0))
        if entry_price <= 0:
            # 데이터 결함 — 판정으로 치지 않는다. evaluated + len(skipped) 항등을 지킨다.
            skipped.append({"symbol": symbol, "reason": f"invalid_price:{entry_price}"})
            continue
        evaluated += 1

        sl_price = tp1_price = tp2_price = 0.0
        try:
            plan = build_exit_plan(Decimal(str(entry_price)), strategy.params, symbol=symbol)
            sl_price = float(Decimal(str(entry_price)) * (Decimal("1") + plan.stop_loss.fixed_pct))
            tiers = list(plan.take_profits or ())
            if len(tiers) >= 1:
                tp1_price = float(tiers[0].price)
            if len(tiers) >= 2:
                tp2_price = float(tiers[1].price)
        except Exception as exc:  # noqa: BLE001 — 신호 계측은 살린다(가격만 0.0)
            _log(f"{symbol} 청산가 산출 실패 {type(exc).__name__}: {exc} — 0.0 으로 기록")

        signals.append({
            "symbol": symbol,
            "name": name,
            "entry_price": entry_price,
            "score": _as_float(getattr(signal, "score", 0.0)),
            "sl_price": sl_price,
            "tp1_price": tp1_price,
            "tp2_price": tp2_price,
        })
    return signals, skipped, evaluated


# ─── 텔레그램 요약 (실패해도 무시) ───────────────────────────────────────────
def summary_text(uni_payload: dict, sig_payload: dict) -> str:
    """관측 요약 1건 — 사람이 읽는 문장."""
    lines = [
        "📊 [ai_swing 관측] 단테 교집합 shadow",
        f"유니버스: status={uni_payload.get('status')} "
        f"reason={uni_payload.get('reason') or '-'} "
        f"(scan {uni_payload.get('scan_count')} / pred {uni_payload.get('pred_count')} "
        f"/ 교집합 {uni_payload.get('intersect_count')})",
        f"판정: {sig_payload.get('evaluated')}종목 → 진입 신호 {sig_payload.get('signal_count')}건 "
        f"(skip {len(sig_payload.get('skipped') or ())})",
    ]
    for s in (sig_payload.get("signals") or ())[:10]:
        lines.append(
            f"  · {s.get('name')}({s.get('symbol')}) score={s.get('score')} "
            f"@~{_as_float(s.get('entry_price')):,.0f} "
            f"SL {_as_float(s.get('sl_price')):,.0f} / TP1 {_as_float(s.get('tp1_price')):,.0f}"
        )
    lines.append("※ 관측 전용 — 이 데몬은 매매하지 않는다.")
    return "\n".join(lines)


async def send_telegram(text: str) -> None:
    """요약 발송. 토큰 부재(SystemExit) 포함 어떤 실패도 흡수한다."""
    try:
        from backend.core.notify.telegram import TelegramNotifier

        await TelegramNotifier.from_env().send(text)
        _log("텔레그램 요약 발송 완료")
    except (Exception, SystemExit) as exc:  # noqa: BLE001 — from_env 는 SystemExit 를 낸다
        _log(f"텔레그램 발송 실패(무시) {type(exc).__name__}: {exc}")


# ─── 메인 흐름 ───────────────────────────────────────────────────────────────
async def _run(args: argparse.Namespace) -> int:
    if not is_truthy(os.environ.get(ENV_ENABLED)):
        _log(f"{ENV_ENABLED} 미설정/off — 관측을 실행하지 않는다 (기본 OFF). 파일도 쓰지 않는다.")
        return 0

    base = data_dir()
    uni_path = base / UNIVERSE_FILENAME
    sig_path = base / SIGNALS_FILENAME

    uni = load_ai_trade_universe()           # 로더는 예외를 내지 않는다(강등만)
    uni_payload_dict = universe_payload(uni)
    atomic_write_json(uni_path, uni_payload_dict)
    _log(
        f"universe status={uni_payload_dict['status']} reason={uni_payload_dict['reason'] or '-'} "
        f"scan={uni_payload_dict['scan_count']} pred={uni_payload_dict['pred_count']} "
        f"교집합={uni_payload_dict['intersect_count']} → {uni_path}"
    )

    status = uni_payload_dict["status"]
    uni_reason = uni_payload_dict["reason"] or ""
    allow_stale = is_truthy(os.environ.get(ENV_ALLOW_STALE, "0"))
    # ★partial(스캔 단독) 도 관측 가능해야 한다.
    #   ai-trade 는 현재 predictions_*.json 을 만들지 않는다(저장 코드 자체가 없음).
    #   그래서 실환경에서 로더가 낼 수 있는 상태는 사실상 partial 뿐이다.
    #   partial 을 거부하면 런북 §2 의 shadow 단계가 **영구히 표본 0건**이 된다.
    #   단 허용은 사용자가 BARRO_AI_SWING_FALLBACK=scan_only 로 **명시 opt-in** 한
    #   경우로 한정한다 — 그때만 로더가 items 를 채우고 이 사유를 붙인다.
    #   (fallback_disabled 면 items 가 비어 있으므로 평가할 것도 없다.)
    partial_ok = status == "partial" and uni_reason == "predictions_missing:scan_only"
    usable = status == "ok" or (status == "stale" and allow_stale) or partial_ok
    if not usable:
        if status == "stale":
            reason = f"stale_not_allowed:{ENV_ALLOW_STALE}=0"
        elif status == "partial":
            # 데몬이 거부한 사실이 드러나야 한다(로더 사유를 그대로 재사용하지 않는다).
            reason = f"partial_not_allowed:{ENV_FALLBACK}=scan_only 필요 ({uni_reason})"
        else:
            reason = uni_reason or f"status_not_usable:{status}"
        payload = signals_payload(
            universe_status=status, universe_reason=reason,
            evaluated=0, signals=[], skipped=[],
        )
        atomic_write_json(sig_path, payload)
        _log(f"신호 평가 건너뜀 — {reason} → {sig_path}")
        return 0

    top_n = args.top if args.top > 0 else _as_int(os.environ.get(ENV_TOP_N, "0"), 0)
    selected, stats = select_items(
        uni_payload_dict["items"],
        min_pred_score=_as_float(os.environ.get(ENV_MIN_PRED_SCORE, "0"), 0.0),
        min_consensus=os.environ.get(ENV_MIN_CONSENSUS, "") or "",
        top_n=top_n,
    )
    _log(
        f"관측 대상 {stats['selected']}/{stats['input']} "
        f"(pred_score 탈락 {stats['dropped_pred_score']}, "
        f"consensus 탈락 {stats['dropped_consensus']}"
        f"{' [' + stats['consensus_filter'] + ']' if stats['consensus_filter'] else ''}, "
        f"top_n 절단 {stats['dropped_top_n']})"
    )

    fetcher = build_candle_fetcher()
    if fetcher is None:
        skipped = [
            {"symbol": str(it.get("symbol") or ""), "reason": "fetcher_unavailable:kiwoom_keys_unset"}
            for it in selected
        ]
        payload = signals_payload(
            universe_status=status, universe_reason=uni_payload_dict["reason"],
            evaluated=0, signals=[], skipped=skipped,
        )
        atomic_write_json(sig_path, payload)
        _log(f"시세 조회기 없음(KIWOOM 키 미설정) — 신호 판정 0건 → {sig_path}")
        return 0

    strategy = AiSwingStrategy()
    signals, skipped, evaluated = await evaluate_signals(
        selected, fetcher, strategy, sleep_sec=max(0.0, float(args.sleep)),
    )
    payload = signals_payload(
        universe_status=status, universe_reason=uni_payload_dict["reason"],
        evaluated=evaluated, signals=signals, skipped=skipped,
    )
    atomic_write_json(sig_path, payload)
    _log(
        f"판정 {evaluated}종목 → 진입 신호 {len(signals)}건 (skip {len(skipped)}) → {sig_path}"
    )
    for s in signals:
        _log(f"  신호: {s['name']}({s['symbol']}) score={s['score']} entry~{s['entry_price']:,.0f}")

    if args.telegram:
        await send_telegram(summary_text(uni_payload_dict, payload))
    return 0


def parse_args(argv: Optional[Sequence[str]] = None) -> argparse.Namespace:
    ap = argparse.ArgumentParser(
        description="ai_swing 관측 전용 데몬 (유니버스·진입 신호 수 계측)",
    )
    ap.add_argument("--sleep", type=float, default=0.25,
                    help="종목별 시세 조회 사이 대기(초). rate-limit 완화용")
    ap.add_argument("--top", type=int, default=0,
                    help=f"관측 상한 종목수 (0=env {ENV_TOP_N}, 그것도 0이면 전체)")
    ap.add_argument("--telegram", action="store_true",
                    help="관측 요약 1건 텔레그램 발송 (실패해도 무시)")
    return ap.parse_args(list(argv) if argv is not None else None)


def main(argv: Optional[Sequence[str]] = None) -> int:
    """항상 0 을 돌려준다 (cron 안전). 사유는 stdout·signals 파일에 남는다."""
    args = parse_args(argv)     # CLI 오사용은 argparse 가 즉시 알린다(SystemExit 전파)
    try:
        return asyncio.run(_run(args))
    except (Exception, SystemExit) as exc:  # noqa: BLE001 — 어떤 예외도 exit 0 으로 흡수
        detail = f"daemon_error:{type(exc).__name__}: {exc}"
        _log(detail)
        try:
            atomic_write_json(
                data_dir() / SIGNALS_FILENAME,
                signals_payload(
                    universe_status="error", universe_reason=detail,
                    evaluated=0, signals=[], skipped=[],
                ),
            )
        except Exception as exc2:  # noqa: BLE001 — 기록 실패도 종료를 막지 않는다
            _log(f"사유 기록 실패 {type(exc2).__name__}: {exc2}")
        return 0


if __name__ == "__main__":
    sys.exit(main())
