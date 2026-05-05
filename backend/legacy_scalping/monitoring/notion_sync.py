"""
Notion 매매 캘린더 동기화

trades.jsonl → Notion 데이터베이스 (캘린더 뷰)
일별 매매 요약을 Notion에 기록하여 캘린더로 시각화
"""

import calendar
import json
import os
import logging
from collections import defaultdict
from datetime import date, datetime
from pathlib import Path
from typing import Optional

import httpx

logger = logging.getLogger(__name__)


class NotionTradeSync:
    """매매 기록을 Notion 데이터베이스에 동기화"""

    BASE_URL = "https://api.notion.com/v1"
    VERSION = "2022-06-28"

    def __init__(self, config: dict):
        self.token = os.getenv("NOTION_TOKEN", "")
        self.database_id = os.getenv("NOTION_TRADE_DB_ID", "")
        self.trade_log_path = config.get(
            "logging", {}).get("trade_log", "./logs/trades.jsonl")
        self._client: Optional[httpx.AsyncClient] = None

    @property
    def enabled(self) -> bool:
        return bool(self.token and self.database_id)

    async def initialize(self):
        if not self.enabled:
            logger.warning("Notion 설정 없음 — 동기화 비활성화")
            return
        self._client = httpx.AsyncClient(
            timeout=15.0,
            headers={
                "Authorization": f"Bearer {self.token}",
                "Notion-Version": self.VERSION,
                "Content-Type": "application/json",
            },
        )
        await self._ensure_db_schema()

    async def close(self):
        if self._client:
            await self._client.aclose()

    # ─── DB 스키마 자동 업데이트 ───

    async def _ensure_db_schema(self):
        """Notion DB에 전략 관련 속성이 없으면 자동 추가"""
        if not self._client:
            return

        required_props = {
            "일반거래": {"number": {"format": "number"}},
            "스캘핑거래": {"number": {"format": "number"}},
            "일반손익": {"number": {"format": "number"}},
            "스캘핑손익": {"number": {"format": "number"}},
        }

        try:
            # 현재 DB 스키마 조회
            resp = await self._client.get(
                f"{self.BASE_URL}/databases/{self.database_id}",
            )
            if resp.status_code != 200:
                logger.error(
                    f"Notion DB 조회 실패: {resp.status_code} {resp.text[:200]}")
                return

            existing = resp.json().get("properties", {})
            missing = {
                k: v for k, v in required_props.items() if k not in existing
            }

            if not missing:
                logger.debug("Notion DB 스키마: 전략 속성 이미 존재")
                return

            # 누락된 속성 추가
            resp = await self._client.patch(
                f"{self.BASE_URL}/databases/{self.database_id}",
                json={"properties": missing},
            )
            if resp.status_code == 200:
                logger.info(
                    f"Notion DB 스키마 업데이트: {', '.join(missing.keys())} 추가")
            else:
                logger.error(
                    f"Notion DB 스키마 업데이트 실패: "
                    f"{resp.status_code} {resp.text[:200]}")
        except Exception as e:
            logger.error(f"Notion DB 스키마 확인 오류: {e}")

    # ─── 핵심: 일별 매매 요약 동기화 ───

    async def sync_date(self, target_date: Optional[str] = None):
        """
        특정 날짜의 매매 요약을 Notion에 동기화

        이미 해당 날짜 레코드가 있으면 업데이트, 없으면 생성.

        Args:
            target_date: YYYY-MM-DD (None이면 오늘)
        """
        if not self.enabled or not self._client:
            return

        target = target_date or date.today().isoformat()
        summary = self._build_daily_summary(target)
        if summary is None:
            logger.info(f"Notion 동기화 스킵: {target} 매매 기록 없음")
            return

        # 기존 페이지 검색
        existing_page_id = await self._find_page_by_date(target)

        if existing_page_id:
            await self._update_page(existing_page_id, summary)
            logger.info(f"Notion 업데이트: {target}")
        else:
            await self._create_page(summary)
            logger.info(f"Notion 생성: {target}")

    async def sync_all(self):
        """trades.jsonl의 모든 날짜를 동기화"""
        if not self.enabled or not self._client:
            return

        dates = self._get_all_trade_dates()
        for d in sorted(dates):
            await self.sync_date(d)
        logger.info(f"Notion 전체 동기화 완료: {len(dates)}일")

    # ─── 월간 요약 동기화 ───

    async def sync_month(self, target_month: Optional[str] = None):
        """
        월간 매매 요약을 Notion에 동기화

        해당 월의 마지막 날짜에 월간 요약 페이지를 생성/업데이트.

        Args:
            target_month: YYYY-MM (None이면 이번 달)
        """
        if not self.enabled or not self._client:
            return

        target = target_month or date.today().strftime("%Y-%m")
        summary = self._build_monthly_summary(target)
        if summary is None:
            logger.info(f"Notion 월간 동기화 스킵: {target} 매매 기록 없음")
            return

        # 월간 요약은 해당 월 마지막 날에 기록
        year, month = map(int, target.split("-"))
        last_day = calendar.monthrange(year, month)[1]
        summary_date = f"{target}-{last_day:02d}"

        # "📊 3월 월간 요약" 형태 검색
        existing_page_id = await self._find_monthly_page(target)

        properties = self._build_monthly_properties(summary, summary_date)
        children = self._build_monthly_content(summary)

        if existing_page_id:
            await self._update_page_with_children(
                existing_page_id, properties, children)
            logger.info(f"Notion 월간 업데이트: {target}")
        else:
            try:
                resp = await self._client.post(
                    f"{self.BASE_URL}/pages",
                    json={
                        "parent": {"database_id": self.database_id},
                        "properties": properties,
                        "children": children,
                    },
                )
                if resp.status_code != 200:
                    logger.error(
                        f"Notion 월간 생성 실패: "
                        f"{resp.status_code} {resp.text[:200]}")
            except Exception as e:
                logger.error(f"Notion 월간 생성 오류: {e}")
            logger.info(f"Notion 월간 생성: {target}")

    async def _find_monthly_page(self, target_month: str) -> Optional[str]:
        """월간 요약 페이지 검색 (제목에 '월간 요약' 포함)"""
        year, month = map(int, target_month.split("-"))
        last_day = calendar.monthrange(year, month)[1]
        summary_date = f"{target_month}-{last_day:02d}"
        try:
            resp = await self._client.post(
                f"{self.BASE_URL}/databases/{self.database_id}/query",
                json={
                    "filter": {
                        "and": [
                            {
                                "property": "거래일",
                                "date": {"equals": summary_date},
                            },
                            {
                                "property": "거래명",
                                "title": {"contains": "월간 요약"},
                            },
                        ],
                    },
                    "page_size": 1,
                },
            )
            data = resp.json()
            results = data.get("results", [])
            if results:
                return results[0]["id"]
        except Exception as e:
            logger.error(f"Notion 월간 검색 실패: {e}")
        return None

    async def _update_page_with_children(
        self, page_id: str, properties: dict, children: list
    ):
        """페이지 속성 + 본문 모두 교체"""
        try:
            resp = await self._client.patch(
                f"{self.BASE_URL}/pages/{page_id}",
                json={"properties": properties},
            )
            if resp.status_code != 200:
                logger.error(
                    f"Notion 월간 업데이트 실패: "
                    f"{resp.status_code} {resp.text[:200]}")
                return
            await self._clear_page_content(page_id)
            await self._client.patch(
                f"{self.BASE_URL}/blocks/{page_id}/children",
                json={"children": children},
            )
        except Exception as e:
            logger.error(f"Notion 월간 업데이트 오류: {e}")

    def _build_monthly_summary(self, target_month: str) -> Optional[dict]:
        """trades.jsonl에서 월간 매매를 집계"""
        path = Path(self.trade_log_path)
        if not path.exists():
            return None

        daily_data = defaultdict(lambda: {
            "buys": [], "sells": [],
            "pnl": 0, "wins": 0, "losses": 0,
        })

        with open(path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    trade = json.loads(line)
                except json.JSONDecodeError:
                    continue
                ts = trade.get("timestamp", "")
                if not ts.startswith(target_month):
                    continue
                d = ts[:10]
                if trade["action"] == "BUY":
                    daily_data[d]["buys"].append(trade)
                elif trade["action"] == "SELL":
                    daily_data[d]["sells"].append(trade)

        if not daily_data:
            return None

        # 일별 집계
        total_pnl = 0
        total_wins = 0
        total_losses = 0
        total_buys = 0
        total_sells_count = 0
        total_tp = 0
        total_sl = 0
        regular_pnl = 0
        scalp_pnl = 0
        regular_trades = 0
        scalp_trades = 0
        all_stock_names = set()
        daily_pnls = []
        best_day = ("", 0)
        worst_day = ("", 0)

        for d in sorted(daily_data.keys()):
            dd = daily_data[d]
            buys = dd["buys"]
            sells = dd["sells"]
            total_buys += len(buys)
            total_sells_count += len(sells)

            day_pnl = 0
            for s in sells:
                entry_price = s.get("entry_price", 0)
                realized = (s.get("price", 0) - entry_price) * s.get("qty", 0)
                day_pnl += realized
                pnl_pct = s.get("pnl_pct", 0)
                if pnl_pct > 0:
                    total_wins += 1
                elif pnl_pct < 0:
                    total_losses += 1
                if "익절" in s.get("exit_type", ""):
                    total_tp += 1
                if "손절" in s.get("exit_type", ""):
                    total_sl += 1
                st = s.get("strategy_type", "regular")
                if st == "scalping":
                    scalp_pnl += realized
                    scalp_trades += 1
                else:
                    regular_pnl += realized
                    regular_trades += 1

            for b in buys:
                st = b.get("strategy_type", "regular")
                if st == "scalping":
                    scalp_trades += 1
                else:
                    regular_trades += 1

            for t in buys + sells:
                all_stock_names.add(t.get("name", t.get("code", "")))

            total_pnl += day_pnl
            daily_pnls.append((d, day_pnl))
            if day_pnl > best_day[1]:
                best_day = (d, day_pnl)
            if day_pnl < worst_day[1]:
                worst_day = (d, day_pnl)

        trading_days = len(daily_data)
        win_days = sum(1 for _, p in daily_pnls if p > 0)
        loss_days = sum(1 for _, p in daily_pnls if p < 0)
        win_rate = (
            total_wins / (total_wins + total_losses)
            if (total_wins + total_losses) > 0 else 0
        )

        # 총자산 추정 (마지막 거래일의 마지막 매도)
        total_equity = 0
        last_date = sorted(daily_data.keys())[-1]
        last_sells = daily_data[last_date]["sells"]
        if last_sells:
            last = last_sells[-1]
            dpnl = last.get("daily_pnl", 0)
            dpnl_pct = last.get("daily_pnl_pct", 0)
            if dpnl_pct != 0:
                total_equity = int(dpnl / (dpnl_pct / 100))

        # 월간 수익률
        monthly_pnl_pct = (total_pnl / total_equity) if total_equity > 0 else 0

        year, month = map(int, target_month.split("-"))

        return {
            "month": target_month,
            "month_label": f"{year}년 {month}월",
            "total_pnl": total_pnl,
            "monthly_pnl_pct": monthly_pnl_pct,
            "total_equity": total_equity,
            "trading_days": trading_days,
            "win_days": win_days,
            "loss_days": loss_days,
            "total_trades": total_buys + total_sells_count,
            "total_sells": total_sells_count,
            "total_wins": total_wins,
            "total_losses": total_losses,
            "win_rate": win_rate,
            "tp_count": total_tp,
            "sl_count": total_sl,
            "regular_pnl": regular_pnl,
            "scalp_pnl": scalp_pnl,
            "regular_trades": regular_trades,
            "scalp_trades": scalp_trades,
            "stock_names": ", ".join(sorted(all_stock_names)),
            "daily_pnls": daily_pnls,
            "best_day": best_day,
            "worst_day": worst_day,
        }

    def _build_monthly_properties(
        self, summary: dict, summary_date: str
    ) -> dict:
        """월간 요약 페이지 속성"""
        pnl = summary["total_pnl"]
        if pnl > 0:
            title = f"📊 {summary['month_label']} 월간 요약 | +{pnl:,.0f}원"
            status = "이익"
        elif pnl < 0:
            title = f"📊 {summary['month_label']} 월간 요약 | {pnl:,.0f}원"
            status = "손실"
        else:
            title = f"📊 {summary['month_label']} 월간 요약 | 0원"
            status = "보합"

        return {
            "거래명": {"title": [{"text": {"content": title}}]},
            "거래일": {"date": {"start": summary_date}},
            "손익금": {"number": summary["total_pnl"]},
            "손익률": {"number": summary["monthly_pnl_pct"]},
            "승률": {"number": summary["win_rate"]},
            "총거래": {"number": summary["total_trades"]},
            "익절": {"number": summary["tp_count"]},
            "손절": {"number": summary["sl_count"]},
            "총자산": {"number": summary["total_equity"]},
            "매매종목": {
                "rich_text": [{
                    "text": {"content": summary["stock_names"][:2000]},
                }],
            },
            "거래 상태": {"status": {"name": status}},
            "일반거래": {"number": summary["regular_trades"]},
            "스캘핑거래": {"number": summary["scalp_trades"]},
            "일반손익": {"number": summary["regular_pnl"]},
            "스캘핑손익": {"number": summary["scalp_pnl"]},
        }

    def _build_monthly_content(self, summary: dict) -> list:
        """월간 요약 본문 블록 생성"""
        pnl = summary["total_pnl"]
        equity = summary["total_equity"]
        pnl_pct = summary["monthly_pnl_pct"]

        # 1. 월간 성과 콜아웃
        perf_lines = (
            f"총 손익: {pnl:+,.0f}원 "
            f"({pnl_pct:+.2%})\n"
            f"총자산: {equity:,}원\n"
            f"거래일: {summary['trading_days']}일 "
            f"(이익 {summary['win_days']}일 / 손실 {summary['loss_days']}일)\n"
            f"승률: {summary['win_rate']:.0%} "
            f"({summary['total_wins']}승 {summary['total_losses']}패)\n"
            f"익절: {summary['tp_count']}건 | 손절: {summary['sl_count']}건"
        )
        callout = {
            "type": "callout",
            "callout": {
                "icon": {"type": "emoji", "emoji": "📊"},
                "rich_text": [self._cell(perf_lines, bold=True)],
            },
        }

        # 2. 전략별 성과 콜아웃
        strategy_parts = []
        if summary["regular_trades"] > 0:
            strategy_parts.append(
                f"일반: {summary['regular_pnl']:+,.0f}원 "
                f"({summary['regular_trades']}건)")
        if summary["scalp_trades"] > 0:
            strategy_parts.append(
                f"스캘핑: {summary['scalp_pnl']:+,.0f}원 "
                f"({summary['scalp_trades']}건)")

        strategy_callout = {
            "type": "callout",
            "callout": {
                "icon": {"type": "emoji", "emoji": "⚡"},
                "rich_text": [self._cell(
                    "전략별 성과\n" + " | ".join(strategy_parts),
                    bold=True,
                )],
            },
        }

        divider = {"type": "divider", "divider": {}}

        # 3. 일별 손익 테이블
        daily_heading = {
            "type": "heading_2",
            "heading_2": {
                "rich_text": [self._cell("📅 일별 손익", bold=True)],
            },
        }

        day_headers = ["날짜", "손익금", "누적손익"]
        day_header_row = {
            "type": "table_row",
            "table_row": {
                "cells": [[self._cell(h, bold=True)] for h in day_headers],
            },
        }

        day_rows = []
        cumulative = 0
        for d, day_pnl in summary["daily_pnls"]:
            cumulative += day_pnl
            emoji = "🟢" if day_pnl > 0 else ("🔴" if day_pnl < 0 else "⚪")
            color = "green" if day_pnl > 0 else (
                "red" if day_pnl < 0 else "default")
            cum_color = "green" if cumulative > 0 else (
                "red" if cumulative < 0 else "default")
            day_rows.append({
                "type": "table_row",
                "table_row": {
                    "cells": [
                        [self._cell(d)],
                        [self._cell(
                            f"{emoji} {day_pnl:+,.0f}원", color=color)],
                        [self._cell(
                            f"{cumulative:+,.0f}원", color=cum_color)],
                    ],
                },
            })

        daily_table = {
            "type": "table",
            "table": {
                "table_width": len(day_headers),
                "has_column_header": True,
                "has_row_header": False,
                "children": [day_header_row] + day_rows,
            },
        }

        # 4. 베스트/워스트
        best = summary["best_day"]
        worst = summary["worst_day"]
        highlight = {
            "type": "callout",
            "callout": {
                "icon": {"type": "emoji", "emoji": "🏆"},
                "rich_text": [self._cell(
                    f"최고 수익일: {best[0]} ({best[1]:+,.0f}원)\n"
                    f"최대 손실일: {worst[0]} ({worst[1]:+,.0f}원)",
                    bold=True,
                )],
            },
        }

        return [
            callout,
            strategy_callout,
            divider,
            highlight,
            divider,
            daily_heading,
            daily_table,
        ]

    # ─── 매매 기록 분석 ───

    def _build_daily_summary(self, target_date: str) -> Optional[dict]:
        """trades.jsonl에서 특정 날짜 매매를 집계"""
        path = Path(self.trade_log_path)
        if not path.exists():
            return None

        buys = []
        sells = []
        with open(path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    trade = json.loads(line)
                except json.JSONDecodeError:
                    continue
                ts = trade.get("timestamp", "")
                if not ts.startswith(target_date):
                    continue
                if trade["action"] == "BUY":
                    buys.append(trade)
                elif trade["action"] == "SELL":
                    sells.append(trade)

        if not buys and not sells:
            return None

        # 집계
        total_trades = len(buys) + len(sells)
        tp_count = sum(
            1 for s in sells if "익절" in s.get("exit_type", ""))
        sl_count = sum(
            1 for s in sells if "손절" in s.get("exit_type", ""))

        # 전략별 집계
        regular_buys = sum(
            1 for b in buys if b.get("strategy_type", "regular") == "regular")
        scalp_buys = sum(
            1 for b in buys if b.get("strategy_type") == "scalping")
        regular_sells = [
            s for s in sells if s.get("strategy_type", "regular") == "regular"]
        scalp_sells = [
            s for s in sells if s.get("strategy_type") == "scalping"]

        # 손익 계산 (매도 기록 기반 — 수수료/세금 반영)
        total_pnl = 0
        total_commission = 0
        total_tax = 0
        win_count = 0
        loss_count = 0
        for s in sells:
            pnl_pct = s.get("pnl_pct", 0)
            qty = s.get("qty", 0)
            entry_price = s.get("entry_price", 0)
            sell_price = s.get("price", 0)
            gross = (sell_price - entry_price) * qty
            # 로그에 수수료/세금이 있으면 사용, 없으면 계산
            commission = s.get("commission",
                               (entry_price * qty + sell_price * qty) * 0.00015)
            tax = s.get("tax", sell_price * qty * 0.0018)
            net = gross - commission - tax
            total_pnl += net
            total_commission += commission
            total_tax += tax
            # 순수익 기준 승패 판정
            if net > 0:
                win_count += 1
            elif net < 0:
                loss_count += 1

        win_rate = (
            win_count / (win_count + loss_count)
            if (win_count + loss_count) > 0 else 0
        )

        # 총자산 (마지막 매매 기록의 daily_pnl 기반 역산)
        total_equity = 0
        last_sell = sells[-1] if sells else None
        if last_sell:
            dpnl = last_sell.get("daily_pnl", 0)
            dpnl_pct = last_sell.get("daily_pnl_pct", 0)
            if dpnl_pct != 0:
                total_equity = int(dpnl / (dpnl_pct / 100))

        # 매매 종목 목록
        stock_names = set()
        for t in buys + sells:
            stock_names.add(t.get("name", t.get("code", "")))

        # 손익률
        avg_pnl_pct = 0
        if sells:
            avg_pnl_pct = sum(s.get("pnl_pct", 0) for s in sells) / len(sells)

        # 결과 판정
        if total_pnl > 0:
            status = "이익"
            title_text = f"🟢 +{total_pnl:,.0f}원"
        elif total_pnl < 0:
            status = "손실"
            title_text = f"🔴 {total_pnl:,.0f}원"
        else:
            status = "보합"
            title_text = "⚪ 0원"

        # 개별 매매 기록 (시간순 정렬)
        all_trades = buys + sells
        all_trades.sort(key=lambda t: t.get("timestamp", ""))

        # 전략별 손익 (수수료/세금 반영)
        def _net_pnl(s):
            ep = s.get("entry_price", 0)
            sp = s.get("price", 0)
            q = s.get("qty", 0)
            gross = (sp - ep) * q
            comm = s.get("commission", (ep * q + sp * q) * 0.00015)
            tx = s.get("tax", sp * q * 0.0018)
            return gross - comm - tx

        regular_pnl = sum(_net_pnl(s) for s in regular_sells)
        scalp_pnl = sum(_net_pnl(s) for s in scalp_sells)

        return {
            "date": target_date,
            "title": title_text,
            "pnl_amount": total_pnl,
            "pnl_pct": avg_pnl_pct / 100,  # Notion percent format
            "win_rate": win_rate,
            "total_trades": total_trades,
            "tp_count": tp_count,
            "sl_count": sl_count,
            "total_equity": total_equity,
            "stock_names": ", ".join(sorted(stock_names)),
            "status": status,
            "trades": all_trades,
            # 전략별 집계
            "regular_trades": regular_buys + len(regular_sells),
            "scalp_trades": scalp_buys + len(scalp_sells),
            "regular_pnl": regular_pnl,
            "scalp_pnl": scalp_pnl,
        }

    def _get_all_trade_dates(self) -> set:
        """trades.jsonl에서 모든 고유 거래일 추출"""
        path = Path(self.trade_log_path)
        if not path.exists():
            return set()

        dates = set()
        with open(path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    trade = json.loads(line)
                    ts = trade.get("timestamp", "")
                    if len(ts) >= 10:
                        dates.add(ts[:10])
                except json.JSONDecodeError:
                    continue
        return dates

    # ─── Notion API ───

    async def _find_page_by_date(self, target_date: str) -> Optional[str]:
        """날짜로 기존 페이지 검색"""
        try:
            resp = await self._client.post(
                f"{self.BASE_URL}/databases/{self.database_id}/query",
                json={
                    "filter": {
                        "property": "거래일",
                        "date": {"equals": target_date},
                    },
                    "page_size": 1,
                },
            )
            data = resp.json()
            results = data.get("results", [])
            if results:
                return results[0]["id"]
        except Exception as e:
            logger.error(f"Notion 검색 실패: {e}")
        return None

    async def _create_page(self, summary: dict):
        """새 페이지 생성 + 본문에 매매 상세 테이블 추가"""
        try:
            resp = await self._client.post(
                f"{self.BASE_URL}/pages",
                json={
                    "parent": {"database_id": self.database_id},
                    "properties": self._build_properties(summary),
                    "children": self._build_trade_table(summary),
                },
            )
            if resp.status_code != 200:
                logger.error(
                    f"Notion 생성 실패: {resp.status_code} {resp.text[:200]}")
        except Exception as e:
            logger.error(f"Notion 생성 오류: {e}")

    async def _update_page(self, page_id: str, summary: dict):
        """기존 페이지 업데이트 + 본문 교체"""
        try:
            # 1. 속성 업데이트
            resp = await self._client.patch(
                f"{self.BASE_URL}/pages/{page_id}",
                json={"properties": self._build_properties(summary)},
            )
            if resp.status_code != 200:
                logger.error(
                    f"Notion 업데이트 실패: {resp.status_code} {resp.text[:200]}")
                return

            # 2. 기존 본문 블록 삭제
            await self._clear_page_content(page_id)

            # 3. 새 테이블 추가
            await self._client.patch(
                f"{self.BASE_URL}/blocks/{page_id}/children",
                json={"children": self._build_trade_table(summary)},
            )
        except Exception as e:
            logger.error(f"Notion 업데이트 오류: {e}")

    async def _clear_page_content(self, page_id: str):
        """페이지 본문의 기존 블록을 모두 삭제"""
        try:
            resp = await self._client.get(
                f"{self.BASE_URL}/blocks/{page_id}/children",
                params={"page_size": 100},
            )
            data = resp.json()
            for block in data.get("results", []):
                await self._client.delete(
                    f"{self.BASE_URL}/blocks/{block['id']}")
        except Exception as e:
            logger.error(f"Notion 블록 삭제 오류: {e}")

    # ─── 본문 테이블 빌드 ───

    @staticmethod
    def _cell(text: str, bold: bool = False, color: str = "default") -> dict:
        """Notion 테이블 셀 생성"""
        return {
            "type": "text",
            "text": {"content": str(text)},
            "annotations": {"bold": bold, "color": color},
        }

    def _build_trade_table(self, summary: dict) -> list:
        """매매 상세 테이블 블록 생성"""
        trades = summary.get("trades", [])
        if not trades:
            return []

        # 헤더 행
        headers = ["시간", "매수/매도", "종목", "전략", "가격", "수량", "금액", "수익률", "유형"]
        header_row = {
            "type": "table_row",
            "table_row": {
                "cells": [[self._cell(h, bold=True)] for h in headers],
            },
        }

        # 데이터 행
        data_rows = []
        for t in trades:
            ts = t.get("timestamp", "")
            time_str = ts[11:19] if len(ts) >= 19 else ts
            action = t["action"]
            name = t.get("name", t.get("code", ""))
            price = t.get("price", 0)
            qty = t.get("qty", 0)
            amount = int(price * qty)

            # 전략 구분
            st = t.get("strategy_type", "regular")
            strategy_label = "스캘핑" if st == "scalping" else "일반"

            if action == "BUY":
                action_text = "🔵 매수"
                action_color = "blue"
                pnl_text = ""
                exit_type = ""
            else:
                pnl_pct = t.get("pnl_pct", 0)
                if pnl_pct > 0:
                    pnl_text = f"+{pnl_pct:.1f}%"
                    action_color = "green"
                elif pnl_pct < 0:
                    pnl_text = f"{pnl_pct:.1f}%"
                    action_color = "red"
                else:
                    pnl_text = "0.0%"
                    action_color = "default"
                action_text = "🔴 매도" if pnl_pct < 0 else "🟢 매도"
                exit_type = t.get("exit_type", "")

            row = {
                "type": "table_row",
                "table_row": {
                    "cells": [
                        [self._cell(time_str)],
                        [self._cell(action_text)],
                        [self._cell(name)],
                        [self._cell(strategy_label)],
                        [self._cell(f"{price:,.0f}")],
                        [self._cell(f"{qty:,}")],
                        [self._cell(f"{amount:,}")],
                        [self._cell(pnl_text, color=action_color)] if pnl_text else [self._cell("")],
                        [self._cell(exit_type)],
                    ],
                },
            }
            data_rows.append(row)

        # 요약 헤딩
        heading = {
            "type": "heading_2",
            "heading_2": {
                "rich_text": [self._cell(
                    f"📋 매매 내역 ({len(trades)}건)", bold=True)],
            },
        }

        # 구분선
        divider = {"type": "divider", "divider": {}}

        # 요약 텍스트
        pnl = summary["pnl_amount"]
        regular_pnl = summary.get("regular_pnl", 0)
        scalp_pnl = summary.get("scalp_pnl", 0)
        regular_trades = summary.get("regular_trades", 0)
        scalp_trades = summary.get("scalp_trades", 0)

        strategy_parts = []
        if regular_trades > 0:
            strategy_parts.append(f"일반: {regular_pnl:+,.0f}원({regular_trades}건)")
        if scalp_trades > 0:
            strategy_parts.append(f"스캘핑: {scalp_pnl:+,.0f}원({scalp_trades}건)")
        strategy_line = " | ".join(strategy_parts)

        summary_text = {
            "type": "callout",
            "callout": {
                "icon": {"type": "emoji", "emoji": "💰"},
                "rich_text": [
                    self._cell(
                        f"당일 손익: {pnl:+,.0f}원 | "
                        f"승률: {summary['win_rate']:.0%} | "
                        f"익절: {summary['tp_count']}건 | "
                        f"손절: {summary['sl_count']}건\n"
                        f"📊 {strategy_line}" if strategy_line else
                        f"당일 손익: {pnl:+,.0f}원 | "
                        f"승률: {summary['win_rate']:.0%} | "
                        f"익절: {summary['tp_count']}건 | "
                        f"손절: {summary['sl_count']}건",
                        bold=True,
                    ),
                ],
            },
        }

        # 테이블 블록
        table = {
            "type": "table",
            "table": {
                "table_width": len(headers),
                "has_column_header": True,
                "has_row_header": False,
                "children": [header_row] + data_rows,
            },
        }

        blocks = [summary_text, divider, heading, table]

        # 스캘핑 종목별 매매내역 섹션 추가
        scalp_blocks = self._build_scalping_stock_detail(summary)
        if scalp_blocks:
            blocks.extend(scalp_blocks)

        return blocks

    # 종목별 컬러 이모지 순환
    _STOCK_EMOJIS = ["🔴", "🟡", "🔵", "🟣", "🟠", "🟤", "⚪", "🟢", "⚫"]

    def _build_scalping_stock_detail(self, summary: dict) -> list:
        """스캘핑 종목별 매매내역 블록 생성 (3/23 노션 포맷 동일)"""
        trades = summary.get("trades", [])
        scalp_sells = [
            t for t in trades
            if t["action"] == "SELL" and t.get("strategy_type") == "scalping"
        ]
        if not scalp_sells:
            return []

        # 종목별 그룹핑 (매도 기준)
        stock_groups = defaultdict(lambda: {"sells": []})
        for t in scalp_sells:
            key = t.get("code", "")
            stock_groups[key]["sells"].append(t)
            stock_groups[key]["name"] = t.get("name", key)

        # 종목별 통계 계산 + 수익순 정렬
        stock_stats = []
        for code, grp in stock_groups.items():
            sells = grp["sells"]
            name = grp["name"]
            wins = sum(1 for s in sells if s.get("pnl_pct", 0) > 0)
            losses = sum(1 for s in sells if s.get("pnl_pct", 0) < 0)
            pnl = sum(
                (s.get("price", 0) - s.get("entry_price", 0)) * s.get("qty", 0)
                for s in sells
            )
            avg_pnl_pct = (
                sum(s.get("pnl_pct", 0) for s in sells) / len(sells)
                if sells else 0
            )
            stock_stats.append({
                "code": code,
                "name": name,
                "sells": sells,
                "wins": wins,
                "losses": losses,
                "pnl": pnl,
                "avg_pnl_pct": avg_pnl_pct,
                "n_sells": len(sells),
            })
        # 수익금 내림차순
        stock_stats.sort(key=lambda x: x["pnl"], reverse=True)

        total_stocks = len(stock_stats)
        total_sell_count = sum(s["n_sells"] for s in stock_stats)
        total_wins = sum(s["wins"] for s in stock_stats)
        total_losses = sum(s["losses"] for s in stock_stats)
        total_pnl = sum(s["pnl"] for s in stock_stats)

        blocks = []

        # ─── 구분선 + 헤딩 ───
        blocks.append({"type": "divider", "divider": {}})
        blocks.append({
            "type": "heading_2",
            "heading_2": {
                "rich_text": [self._cell(
                    "🎯 스캘핑 종목별 매매 요약", bold=True,
                )],
            },
        })

        # ─── quote: 총 요약 ───
        pnl_color = "green" if total_pnl > 0 else (
            "red" if total_pnl < 0 else "default")
        blocks.append({
            "type": "quote",
            "quote": {
                "rich_text": [
                    self._cell(
                        f"총 {total_stocks}종목 | "
                        f"{total_sell_count}건 매도 | "
                        f"{total_wins}승 {total_losses}패 | 합계 "
                    ),
                    self._cell(f"{total_pnl:+,.0f}원", bold=True),
                ],
            },
        })

        # ─── 종목별 상세 (수익순) ───
        for idx, st in enumerate(stock_stats):
            emoji = self._STOCK_EMOJIS[idx % len(self._STOCK_EMOJIS)]
            n = st["n_sells"]
            avg = st["avg_pnl_pct"]

            # heading_3: 이모지 종목명 (N건) — 평균 수익률
            if n == 1:
                title = f"{emoji} {st['name']} ({n}건) — {avg:+.1f}%"
            else:
                title = f"{emoji} {st['name']} ({n}건) — 평균 {avg:+.1f}%"

            blocks.append({
                "type": "heading_3",
                "heading_3": {
                    "rich_text": [self._cell(title)],
                },
            })

            # 거래 테이블: #, 시간, 매수가, 매도가, 수량, 수익률, 수익금, 결과
            detail_headers = ["#", "시간", "매수가", "매도가", "수량", "수익률", "수익금", "결과"]
            detail_header_row = {
                "type": "table_row",
                "table_row": {
                    "cells": [[self._cell(h, bold=True)] for h in detail_headers],
                },
            }
            detail_rows = []
            for si, s in enumerate(st["sells"], 1):
                ts = s.get("timestamp", "")
                # 시간: HH:MM 형태
                time_str = ts[11:16] if len(ts) >= 16 else ts
                entry_price = s.get("entry_price", 0)
                sell_price = s.get("price", 0)
                qty = s.get("qty", 0)
                pnl_pct = s.get("pnl_pct", 0)
                pnl_amt = int((sell_price - entry_price) * qty)
                exit_type = s.get("exit_type", "")

                pnl_color = "green" if pnl_pct > 0 else (
                    "red" if pnl_pct < 0 else "default")

                detail_rows.append({
                    "type": "table_row",
                    "table_row": {
                        "cells": [
                            [self._cell(str(si))],
                            [self._cell(time_str)],
                            [self._cell(f"{entry_price:,.0f}")],
                            [self._cell(f"{sell_price:,.0f}")],
                            [self._cell(f"{qty:,}")],
                            [self._cell(f"{pnl_pct:+.1f}%", color=pnl_color)],
                            [self._cell(f"{pnl_amt:+,.0f}", color=pnl_color)],
                            [self._cell(exit_type)],
                        ],
                    },
                })

            blocks.append({
                "type": "table",
                "table": {
                    "table_width": len(detail_headers),
                    "has_column_header": True,
                    "has_row_header": False,
                    "children": [detail_header_row] + detail_rows,
                },
            })

            # callout: 종목 소계
            wins = st["wins"]
            losses = st["losses"]
            pnl = st["pnl"]
            if n == 1:
                sub_text = (
                    f"{st['name']} 소계: {pnl:+,.0f}원 | "
                    f"{n}전 {wins}승"
                )
            else:
                sub_text = (
                    f"{st['name']} 소계: {pnl:+,.0f}원 | "
                    f"{wins}승 {losses}패 | "
                    f"평균 수익률 {avg:+.1f}%"
                )
            blocks.append({
                "type": "callout",
                "callout": {
                    "icon": {"type": "emoji", "emoji": "📊"},
                    "rich_text": [self._cell(sub_text, bold=True)],
                },
            })

        # ─── 구분선 + 수익 순위 callout ───
        blocks.append({"type": "divider", "divider": {}})

        # 순위 리스트 (numbered_list_item as children of callout)
        ranking_children = []
        for ri, st in enumerate(stock_stats):
            if ri == 0:
                medal = "🥇 "
            elif ri == 1:
                medal = "🥈 "
            elif ri == 2:
                medal = "🥉 "
            else:
                medal = ""
            ranking_children.append({
                "type": "numbered_list_item",
                "numbered_list_item": {
                    "rich_text": [self._cell(
                        f"{medal}{st['name']}: {st['pnl']:+,.0f}원 "
                        f"({st['n_sells']}건)"
                    )],
                },
            })

        blocks.append({
            "type": "callout",
            "callout": {
                "icon": {"type": "emoji", "emoji": "🏆"},
                "rich_text": [self._cell("스캘핑 종목별 수익 순위")],
                "children": ranking_children,
            },
        })

        return blocks

    def _build_properties(self, summary: dict) -> dict:
        """Notion 페이지 속성 빌드"""
        return {
            "거래명": {
                "title": [{"text": {"content": summary["title"]}}],
            },
            "거래일": {
                "date": {"start": summary["date"]},
            },
            "손익금": {"number": summary["pnl_amount"]},
            "손익률": {"number": summary["pnl_pct"]},
            "승률": {"number": summary["win_rate"]},
            "총거래": {"number": summary["total_trades"]},
            "익절": {"number": summary["tp_count"]},
            "손절": {"number": summary["sl_count"]},
            "총자산": {"number": summary["total_equity"]},
            "매매종목": {
                "rich_text": [{"text": {"content": summary["stock_names"][:2000]}}],
            },
            "거래 상태": {"status": {"name": summary["status"]}},
            "일반거래": {"number": summary.get("regular_trades", 0)},
            "스캘핑거래": {"number": summary.get("scalp_trades", 0)},
            "일반손익": {"number": summary.get("regular_pnl", 0)},
            "스캘핑손익": {"number": summary.get("scalp_pnl", 0)},
        }
