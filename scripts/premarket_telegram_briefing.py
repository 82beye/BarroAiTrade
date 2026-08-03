#!/usr/bin/env python3
"""08:25 개장 전 스캔·상승예측·전략분석 Telegram 브리핑.

운영 정책:
- 평일 08:25 하루 1회 발송한다.
- 생성/네트워크 실패 때만 5분 간격 최대 2회 추가 시도한다.
- 논리 메시지(scan/prediction/strategy)별 성공 상태를 기록해 재시작·재시도 시
  이미 보낸 긴 메시지가 중복 전송되지 않게 한다.
- watchlist/predictions JSON을 함께 저장해 ai_swing 교집합 로더의 원천으로 쓴다.

이 프로세스는 조회·분석·알림만 수행하며 주문 API를 호출하지 않는다.
"""
from __future__ import annotations

import argparse
import asyncio
import csv
import json
import logging
import os
import sys
from collections import defaultdict
from datetime import date, datetime, time, timedelta
from pathlib import Path
from typing import Any

import pandas as pd
import yaml
from dotenv import load_dotenv
from pydantic import SecretStr

REPO_ROOT = Path(__file__).resolve().parents[1]
LEGACY_ROOT = REPO_ROOT / "backend" / "legacy_scalping"
for path in (REPO_ROOT, LEGACY_ROOT):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

from backend.core.gateway.kiwoom_native_candles import KiwoomNativeCandleFetcher
from backend.core.gateway.kiwoom_native_oauth import KiwoomNativeOAuth
from backend.core.market_data.stock_names import load_names
from backend.core.notify.telegram import TelegramNotifier
from backend.core.premarket_briefing import (
    KST,
    DeliveryState,
    build_predictions_payload,
    build_watchlist_payload,
    deliver_once,
    format_prediction_message,
    format_scan_message,
    format_strategy_message,
    inspect_cache_readiness,
    write_json_atomic,
)
from scanner.agents.coordinator import PredictionCoordinator
from scanner.daily_screener import DailyScreener
from strategy.strategy_team.coordinator import StrategyCoordinator
from strategy.strategy_team.base_agent import TradeRecord

logger = logging.getLogger("premarket_briefing")


class CacheNotReady(RuntimeError):
    """전종목 캐시 커버리지/신선도가 브리핑 기준을 충족하지 못함."""


class LocalUniverseAPI:
    """legacy DailyScreener용 읽기 전용 어댑터.

    종목목록은 전일 생성한 로컬 마스터를 사용하고, 캐시에서 빠진 신규 종목의 일봉만
    공유 OAuth 캐시를 사용하는 현재 KiwoomNativeCandleFetcher로 보충한다.
    """

    def __init__(self, names: dict[str, str], cache_dir: Path):
        self._names = names
        self._cache_dir = cache_dir
        self._fetcher: KiwoomNativeCandleFetcher | None = None

    async def initialize(self) -> None:
        app_key = os.environ.get("KIWOOM_APP_KEY", "")
        app_secret = os.environ.get("KIWOOM_APP_SECRET", "")
        if not app_key or not app_secret:
            return
        oauth = KiwoomNativeOAuth(
            app_key=SecretStr(app_key),
            app_secret=SecretStr(app_secret),
            base_url=os.environ.get("KIWOOM_BASE_URL", "https://mockapi.kiwoom.com"),
        )
        self._fetcher = KiwoomNativeCandleFetcher(oauth=oauth, rate_limit_seconds=0.55)

    async def close(self) -> None:
        return None

    async def get_stock_list_with_meta(self, market_code: str = "0") -> list[dict]:
        # stock_names.json은 코스피·코스닥 통합 마스터다. 첫 호출에만 전체를 돌려 중복 방지.
        if market_code != "0":
            return []
        return [
            {
                "code": code,
                "name": name,
                "state": "",
                "auditInfo": "",
                "orderWarning": "0",
            }
            for code, name in sorted(self._names.items())
        ]

    async def get_daily_ohlcv(self, code: str, count: int = 300) -> pd.DataFrame | None:
        if self._fetcher is None:
            return None
        candles = await self._fetcher.fetch_daily(code)
        if not candles:
            return None
        rows = [
            {
                "date": candle.timestamp,
                "open": candle.open,
                "high": candle.high,
                "low": candle.low,
                "close": candle.close,
                "volume": candle.volume,
            }
            for candle in candles[-count:]
        ]
        return pd.DataFrame(rows)


def _load_config(cache_dir: Path) -> dict[str, Any]:
    with open(LEGACY_ROOT / "config" / "settings.yaml", "r", encoding="utf-8") as handle:
        config = yaml.safe_load(handle)
    config["mode"] = os.environ.get("TRADING_MODE", "simulation")
    config.setdefault("scanner", {})["cache_dir"] = str(cache_dir)
    # 브리핑 본문에 없는 시장상태 조회는 제외한다. 주문 경로와도 완전히 분리한다.
    config.setdefault("risk", {}).setdefault("market_condition", {})["enabled"] = False
    # 금요일 EOD 캐시를 월요일 08:25에도 재사용할 수 있게 한다.
    config["scanner"]["cache_max_age_days"] = 7
    return config


def _parse_timestamp(raw: str) -> datetime | None:
    try:
        parsed = datetime.fromisoformat(raw.strip().replace("Z", "+00:00"))
    except (ValueError, TypeError, AttributeError):
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=KST)
    return parsed


def _number(raw: Any, default: float = 0.0) -> float:
    try:
        return float(str(raw or "").replace(",", ""))
    except (TypeError, ValueError):
        return default


def load_audit_trade_records(
    *, order_audit: Path, fill_audit: Path, names: dict[str, str],
    now: datetime, lookback_days: int = 30,
) -> list[TradeRecord]:
    """현재 BarroAiTrade audit CSV를 legacy 전략팀의 TradeRecord로 정규화."""
    cutoff = now.astimezone(KST).date() - timedelta(days=lookback_days)
    order_rows: list[dict[str, str]] = []
    if order_audit.exists():
        with open(order_audit, newline="", encoding="utf-8") as handle:
            order_rows = list(csv.DictReader(handle))

    sell_orders: dict[tuple[str, str], list[dict[str, str]]] = defaultdict(list)
    records: list[TradeRecord] = []
    for row in order_rows:
        ts = _parse_timestamp(row.get("ts", ""))
        if ts is None or ts.astimezone(KST).date() < cutoff:
            continue
        if row.get("action") != "ORDERED" or row.get("blocked") in {"1", "true", "True"}:
            continue
        symbol = str(row.get("symbol", "")).split("_", 1)[0]
        if not symbol:
            continue
        side = str(row.get("side", "")).lower()
        day_key = ts.astimezone(KST).date().strftime("%Y%m%d")
        if side == "sell":
            sell_orders[(day_key, symbol)].append(row)
            continue
        if side != "buy":
            continue
        price = _number(row.get("avg_fill_price") or row.get("price"))
        qty = int(_number(row.get("filled_qty") or row.get("qty")))
        records.append(TradeRecord(
            action="BUY", code=symbol, name=names.get(symbol, symbol), qty=qty,
            price=price, timestamp=ts.isoformat(), amount=int(price * qty),
            reason=str(row.get("reason", "")),
        ))

    for rows in sell_orders.values():
        rows.sort(key=lambda row: row.get("ts", ""))

    if fill_audit.exists():
        with open(fill_audit, newline="", encoding="utf-8") as handle:
            fills = list(csv.DictReader(handle))
        for row in fills:
            raw_day = str(row.get("date", ""))
            try:
                fill_day = datetime.strptime(raw_day, "%Y%m%d").date()
            except ValueError:
                continue
            if fill_day < cutoff:
                continue
            symbol = str(row.get("symbol", "")).split("_", 1)[0]
            matched = sell_orders.get((raw_day, symbol), [])
            order = matched.pop(0) if matched else {}
            ts = _parse_timestamp(order.get("ts", ""))
            if ts is None:
                ts = datetime.combine(fill_day, time(15, 20), tzinfo=KST)
            pnl_pct = _number(row.get("pnl_rate"))
            reason = str(order.get("reason", ""))
            lowered = reason.lower()
            if pnl_pct < 0 or "stop" in lowered or "손절" in reason:
                exit_type = "손절"
            elif pnl_pct > 0:
                exit_type = "익절"
            else:
                exit_type = "기타청산"
            qty = int(_number(row.get("qty")))
            sell_price = _number(row.get("sell_price"))
            records.append(TradeRecord(
                action="SELL", code=symbol,
                name=str(row.get("name") or names.get(symbol, symbol)), qty=qty,
                price=sell_price, timestamp=ts.isoformat(), amount=int(sell_price * qty),
                entry_price=_number(row.get("buy_price")), pnl_pct=pnl_pct,
                exit_type=exit_type, reason=reason,
            ))
    records.sort(key=lambda record: record.timestamp)
    return records


class AuditStrategyCoordinator(StrategyCoordinator):
    def __init__(self, config: dict, trades: list[TradeRecord]):
        super().__init__(config)
        self._trades = trades

    def _load_trades(self) -> list[TradeRecord]:
        return list(self._trades)


def _paths() -> dict[str, Path]:
    output_dir = Path(os.environ.get("BARRO_PREMARKET_OUTPUT_DIR", REPO_ROOT / "logs"))
    return {
        "cache": Path(os.environ.get("BARRO_PREMARKET_CACHE_DIR", REPO_ROOT / "data" / "ohlcv_cache")),
        "output": output_dir,
        "state": Path(os.environ.get(
            "BARRO_PREMARKET_STATE_PATH", output_dir / "premarket_briefing_state.json",
        )),
        "orders": Path(os.environ.get("BARRO_PREMARKET_ORDER_AUDIT", REPO_ROOT / "data" / "order_audit.csv")),
        "fills": Path(os.environ.get("BARRO_PREMARKET_FILL_AUDIT", REPO_ROOT / "data" / "fill_audit.csv")),
    }


async def generate_messages(*, now: datetime) -> tuple[dict[str, str], dict[str, Any]]:
    paths = _paths()
    names = load_names(force=True)
    if not names:
        raise CacheNotReady("stock_names_empty")
    readiness = inspect_cache_readiness(paths["cache"], len(names), today=now.date())
    if not readiness.ready:
        raise CacheNotReady(readiness.reason)

    config = _load_config(paths["cache"])
    api = LocalUniverseAPI(names, paths["cache"])
    screener = DailyScreener(api, config)
    await api.initialize()
    try:
        watchlist = await screener.run_scan()
        predictions = PredictionCoordinator(config).predict(
            screener.filtered_codes, top_n=int(os.environ.get("BARRO_PREMARKET_TOP_N", "20")),
        )
        lookback_days = int(os.environ.get("BARRO_PREMARKET_TRADE_LOOKBACK_DAYS", "30"))
        trades = load_audit_trade_records(
            order_audit=paths["orders"], fill_audit=paths["fills"], names=names,
            now=now, lookback_days=lookback_days,
        )
        strategy = AuditStrategyCoordinator(config, trades).optimize(
            watchlist=[{"code": row.code, "name": row.name} for row in watchlist],
        )
    finally:
        await api.close()

    day = now.date()
    output_dir = paths["output"]
    write_json_atomic(
        output_dir / f"watchlist_{day.isoformat()}.json",
        build_watchlist_payload(watchlist, day=day, generated_at=now),
    )
    write_json_atomic(
        output_dir / f"predictions_{day.isoformat()}.json",
        build_predictions_payload(predictions, day=day, generated_at=now),
    )
    messages = {
        "scan": format_scan_message(watchlist, generated_at=now),
        "prediction": format_prediction_message(predictions, generated_at=now),
        "strategy": format_strategy_message(strategy),
    }
    evidence = {
        "watchlist": len(watchlist),
        "predictions": len(predictions),
        "trades": len(trades),
        "cache_files": readiness.file_count,
        "cache_updated": readiness.updated,
    }
    return messages, evidence


async def run_attempt(*, dry_run: bool, force: bool, now: datetime | None = None) -> dict[str, Any]:
    current = (now or datetime.now(KST)).astimezone(KST)
    messages, evidence = await generate_messages(now=current)
    if dry_run:
        for key in ("scan", "prediction", "strategy"):
            print(f"\n--- {key} ---\n{messages[key]}")
        return {"dry_run": True, "evidence": evidence}

    notifier = TelegramNotifier.from_env(parse_mode="HTML")
    result = await deliver_once(
        notifier, messages, day=current.date(), state=DeliveryState(_paths()["state"]),
        force=force, now=current,
    )
    return {**result, "evidence": evidence}


async def _send_terminal_failure(message: str, *, now: datetime) -> None:
    state = DeliveryState(_paths()["state"])
    key = "failure"
    if state.was_sent(now.date(), key):
        return
    try:
        notifier = TelegramNotifier.from_env(parse_mode="HTML")
        await notifier.send(
            "⚠️ <b>개장 전 브리핑 생성 실패</b>\n"
            f"{now.strftime('%Y-%m-%d %H:%M')} KST\n"
            f"사유: {str(message)[:300]}"
        )
        state.mark_sent(now.date(), key, at=now)
    except Exception:
        logger.exception("브리핑 최종 실패 Telegram 알림도 실패")


async def async_main(args: argparse.Namespace) -> int:
    attempts = max(1, args.attempts)
    current = datetime.now(KST)
    if current.weekday() >= 5 and not args.allow_nontrading_day:
        logger.info("주말이므로 개장 전 브리핑을 건너뜁니다")
        return 0
    last_error: Exception | None = None
    for attempt in range(1, attempts + 1):
        try:
            result = await run_attempt(dry_run=args.dry_run, force=args.force)
            logger.info("개장 전 브리핑 완료: %s", result)
            return 0
        except Exception as exc:
            last_error = exc
            logger.exception("브리핑 시도 실패 (%d/%d)", attempt, attempts)
            if attempt < attempts:
                await asyncio.sleep(max(0, args.retry_delay))
    if not args.dry_run and last_error is not None:
        await _send_terminal_failure(str(last_error), now=datetime.now(KST))
    return 1


def main() -> int:
    load_dotenv(REPO_ROOT / ".env.local", override=False)
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )
    parser = argparse.ArgumentParser(description="개장 전 Telegram 종목/예측/전략 브리핑")
    parser.add_argument("--dry-run", action="store_true", help="생성·출력만 하고 Telegram 미발송")
    parser.add_argument("--force", action="store_true", help="당일 중복 방지 상태를 무시하고 재발송")
    parser.add_argument("--attempts", type=int, default=3, help="실패 포함 최대 시도 횟수(기본 3)")
    parser.add_argument("--retry-delay", type=int, default=300, help="실패 재시도 간격 초(기본 300)")
    parser.add_argument("--allow-nontrading-day", action="store_true", help="주말 수동 검증 허용")
    return asyncio.run(async_main(parser.parse_args()))


if __name__ == "__main__":
    raise SystemExit(main())
