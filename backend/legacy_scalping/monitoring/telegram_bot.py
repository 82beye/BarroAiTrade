"""
텔레그램 알림 봇
매매 신호, 체결 결과, 일일 리포트 발송
"""

import os
import logging
from datetime import datetime
from typing import Optional, List

import httpx

logger = logging.getLogger(__name__)


class TelegramBot:
    """텔레그램 알림 봇"""

    BASE_URL = "https://api.telegram.org"

    def __init__(self, config: dict):
        tg_config = config.get('telegram', {})
        self.bot_token = os.getenv('TELEGRAM_BOT_TOKEN', tg_config.get('bot_token', ''))
        self.chat_id = os.getenv('TELEGRAM_CHAT_ID', tg_config.get('chat_id', ''))
        self.notifications = tg_config.get('notifications', {})
        self.share_chat_ids = tg_config.get('share_chat_ids', [])
        self._client: Optional[httpx.AsyncClient] = None

        # 매매 전용 봇 (미설정 시 기본 봇 사용)
        trade_cfg = tg_config.get('trade_bot', {})
        self.trade_bot_token = os.getenv(
            'TELEGRAM_TRADE_BOT_TOKEN',
            trade_cfg.get('bot_token', ''),
        )
        self.trade_chat_id = os.getenv(
            'TELEGRAM_TRADE_CHAT_ID',
            trade_cfg.get('chat_id', ''),
        )
        self._trade_enabled = bool(self.trade_bot_token and self.trade_chat_id)

    async def initialize(self):
        self._client = httpx.AsyncClient(timeout=10.0)
        if self.bot_token and self.chat_id:
            await self.send_message("🤖 단테 데이트레이딩 시스템 시작")
            logger.info("텔레그램 봇 초기화 완료")
        else:
            logger.warning("텔레그램 설정 없음 - 알림 비활성화")
        if self._trade_enabled:
            await self._send_to(
                self.trade_chat_id, "🤖 매매 전용 봇 연결 완료",
                bot_token=self.trade_bot_token,
            )
            logger.info("텔레그램 매매 전용 봇 초기화 완료")

    async def send_trade_message(self, text: str, parse_mode: str = "HTML"):
        """매매 전용 봇으로 발송 (미설정 시 기본 봇으로 폴백)"""
        if self._trade_enabled:
            await self._send_to(
                self.trade_chat_id, text, parse_mode,
                bot_token=self.trade_bot_token,
            )
        else:
            await self.send_message(text, parse_mode)
    
    async def close(self):
        if self._client:
            await self._client.aclose()
    
    async def send_message(self, text: str, parse_mode: str = "HTML"):
        """메시지 발송 (본인 chat_id)"""
        await self._send_to(self.chat_id, text, parse_mode)

    async def send_to_shared(self, text: str, parse_mode: str = "HTML"):
        """공유 수신자에게 메시지 발송"""
        for cid in self.share_chat_ids:
            await self._send_to(cid, text, parse_mode)

    async def send_document(self, file_path: str, caption: str = ""):
        """파일(PDF 등) 발송"""
        if not self.bot_token or not self.chat_id:
            return

        url = f"{self.BASE_URL}/bot{self.bot_token}/sendDocument"
        try:
            with open(file_path, 'rb') as f:
                files = {'document': (os.path.basename(file_path), f)}
                data = {'chat_id': self.chat_id}
                if caption:
                    data['caption'] = caption
                    data['parse_mode'] = 'HTML'
                await self._client.post(url, data=data, files=files)
                logger.info(f"텔레그램 파일 발송 완료: {file_path}")
        except Exception as e:
            logger.error(f"텔레그램 파일 발송 실패: {e}")

    async def _send_to(self, chat_id: str, text: str, parse_mode: str = "HTML",
                       bot_token: str = ""):
        """특정 chat_id에 메시지 발송"""
        token = bot_token or self.bot_token
        if not token or not chat_id:
            return

        url = f"{self.BASE_URL}/bot{token}/sendMessage"
        try:
            await self._client.post(url, json={
                "chat_id": chat_id,
                "text": text,
                "parse_mode": parse_mode,
            })
        except Exception as e:
            logger.error(f"텔레그램 발송 실패 (chat_id={chat_id}): {e}")
    
    # =========================================================================
    # 알림 메시지 포맷
    # =========================================================================
    
    async def notify_scan_result(self, watchlist: list):
        """스캔 결과 알림"""
        if not self.notifications.get('scan_result'):
            return

        now = datetime.now().strftime('%H:%M')
        lines = [f"📊 <b>종목 스캔 완료</b> ({now})", f"감시 종목: {len(watchlist)}개", ""]

        for i, stock in enumerate(watchlist, 1):
            wm = "🍉" if stock.get('watermelon_signal') else "  "
            status = stock.get('blue_line_status', '')
            lines.append(
                f"{i}. {wm} [{stock['code']}] {stock['name']} "
                f"| {status} | 점수:{stock['score']}"
            )

        msg = "\n".join(lines)
        await self.send_message(msg)
        await self.send_to_shared(msg)
    
    async def notify_entry(self, code: str, name: str, price: float, qty: int, reason: str):
        """매수 신호 알림 → 매매 전용 봇"""
        if not self.notifications.get('entry_signal'):
            return

        now = datetime.now().strftime('%H:%M:%S')
        text = (
            f"🔵 <b>매수</b> ({now})\n"
            f"[{code}] {name}\n"
            f"💰 {price:,.0f}원 × {qty}주 = {int(price * qty):,}원\n"
            f"📝 {reason}"
        )
        await self.send_trade_message(text)
    
    async def notify_exit(self, code: str, name: str, price: float, qty: int,
                          pnl_pct: float, exit_type: str, reason: str):
        """매도 신호 알림 → 매매 전용 봇"""
        if not self.notifications.get('exit_signal'):
            return

        now = datetime.now().strftime('%H:%M:%S')
        emoji = "🟢" if pnl_pct >= 0 else "🔴"
        text = (
            f"{emoji} <b>매도 ({exit_type})</b> ({now})\n"
            f"[{code}] {name}\n"
            f"💰 {price:,.0f}원 × {qty}주\n"
            f"📊 수익률: {pnl_pct:+.1f}%\n"
            f"📝 {reason}"
        )
        await self.send_trade_message(text)
    
    async def notify_force_liquidation(self, count: int):
        """강제 청산 알림 → 매매 전용 봇"""
        if not self.notifications.get('force_liquidation'):
            return

        now = datetime.now().strftime('%H:%M:%S')
        text = f"⚠️ <b>14:50 강제 청산</b> ({now})\n{count}종목 전량 시장가 매도 완료"
        await self.send_trade_message(text)
    
    async def notify_daily_report(self, summary: dict):
        """일일 리포트 알림"""
        if not self.notifications.get('daily_report'):
            return
        
        pnl = summary.get('daily_pnl', 0)
        pnl_pct = summary.get('daily_pnl_pct', 0)
        emoji = "📈" if pnl >= 0 else "📉"
        
        text = (
            f"{emoji} <b>일일 리포트</b> ({summary.get('date', '')})\n"
            f"━━━━━━━━━━━━━━\n"
            f"총자산: {summary.get('total_equity', 0):,}원\n"
            f"당일 손익: {pnl:+,.0f}원 ({pnl_pct:+.2f}%)\n"
            f"거래 횟수: {summary.get('total_trades', 0)}회\n"
            f"잔여 포지션: {summary.get('open_positions', 0)}종목"
        )
        await self.send_message(text)
    
    async def notify_error(self, error_msg: str):
        """오류 알림"""
        if not self.notifications.get('error'):
            return
        
        now = datetime.now().strftime('%H:%M:%S')
        text = f"🚨 <b>시스템 오류</b> ({now})\n{error_msg}"
        await self.send_message(text)
    
    async def notify_position_update(self, positions: list, daily_pnl_pct: float):
        """포지션 업데이트 (5분 주기) → 매매 전용 봇"""
        if not positions:
            return

        now = datetime.now().strftime('%H:%M')
        lines = [f"📋 <b>포지션 현황</b> ({now}) | 당일 {daily_pnl_pct:+.1f}%", ""]

        for pos in positions:
            emoji = "🟢" if pos.get('pnl_pct', 0) >= 0 else "🔴"
            lines.append(
                f"{emoji} [{pos['code']}] {pos['name']} "
                f"| {pos.get('pnl_pct', 0):+.1f}% | {pos['qty']}주"
            )

        await self.send_trade_message("\n".join(lines))

    async def notify_ymgp_scan_result(self, results: list):
        """
        역매공파 검색 결과 알림

        Args:
            results: [YeokMaeGongPaResult, ...] (dataclass 리스트)
        """
        if not self.notifications.get('scan_result'):
            return

        now = datetime.now().strftime('%H:%M')
        lines = [
            f"<b>역매공파 검색 완료</b> ({now})",
            f"통과 종목: {len(results)}개",
            "",
        ]

        degraded_count = sum(1 for r in results if getattr(r, 'degraded', False))
        if degraded_count > 0:
            lines.append(
                f"⚠ {degraded_count}종목 데이터 부족 (일부 조건 미검증)"
            )
            lines.append("")

        for i, r in enumerate(results[:15], 1):
            tv_billions = r.avg_trade_value_5d / 1_000_000_000
            conditions = ",".join(r.conditions_met)
            degraded_mark = " ⚠" if getattr(r, 'degraded', False) else ""
            lines.append(
                f"{i}. [{r.code}] {r.name}{degraded_mark}\n"
                f"   {r.current_price:,.0f}원 | "
                f"점수:{r.score:.0f} | "
                f"거래대금:{tv_billions:.0f}억 | "
                f"조건:{conditions}"
            )

        await self.send_message("\n".join(lines))

    async def notify_prediction(self, results: list):
        """
        팀 에이전트 상승 예측 결과 알림

        Args:
            results: [PredictionResult, ...] (rank 순)
        """
        if not self.notifications.get('scan_result'):
            return

        now = datetime.now().strftime('%H:%M')
        lines = [
            f"<b>팀 에이전트 상승 예측</b> ({now})",
            f"예측 종목: {len(results)}개",
            "",
        ]

        # 합의 수준별 통계
        consensus_counts = {}
        for r in results:
            consensus_counts[r.consensus_level] = (
                consensus_counts.get(r.consensus_level, 0) + 1
            )
        stats = " | ".join(f"{k}:{v}" for k, v in consensus_counts.items())
        lines.append(f"{stats}")
        lines.append("")

        for r in results:
            # 에이전트 합의 표시
            agent_bar = ""
            for a in ["momentum", "volume", "technical", "breakout"]:
                agent_bar += "O" if a in r.agent_scores else "-"

            lines.append(
                f"{r.rank}. [{r.code}] {r.name} "
                f"<b>{r.total_score:.1f}점</b> [{agent_bar}] "
                f"{r.consensus_level}\n"
                f"   신뢰도:{r.confidence:.0%}"
            )

            # 에이전트별 점수
            score_parts = []
            for a_name, a_score in r.agent_scores.items():
                short = a_name[:3].upper()
                score_parts.append(f"{short}:{a_score:.0f}")
            lines.append(f"   {' | '.join(score_parts)}")

            # 핵심 사유 (최대 2개)
            for reason in r.top_reasons[:2]:
                lines.append(f"   {reason}")

        msg = "\n".join(lines)
        await self.send_message(msg)
        await self.send_to_shared(msg)

    async def notify_leading_stocks(self, results: list):
        """
        주도주 순위 알림

        Args:
            results: [LeadingStockResult, ...] (rank 순 정렬됨)
        """
        if not self.notifications.get('scan_result'):
            return

        now = datetime.now().strftime('%H:%M')
        lines = [
            f"<b>당일 주도주 실시간 순위</b> ({now})",
            f"상승 종목: {len(results)}개",
            "",
        ]

        for r in results:
            tv_billions = r.trade_value / 1_000_000_000

            # 상승률 구간별 이모지
            if r.change_pct >= 15:
                bar = "🔴"
            elif r.change_pct >= 10:
                bar = "🟠"
            elif r.change_pct >= 5:
                bar = "🟡"
            else:
                bar = "🟢"

            lines.append(
                f"{r.rank}. {bar} [{r.code}] {r.name} "
                f"<b>{r.change_pct:+.1f}%</b>\n"
                f"   {r.current_price:,}원 | "
                f"거래대금:{tv_billions:.0f}억 | "
                f"거래량x{r.volume_ratio:.1f} | "
                f"{r.category}"
            )

        # 요약 통계
        if results:
            avg_change = sum(r.change_pct for r in results) / len(results)
            total_tv = sum(r.trade_value for r in results) / 1_000_000_000
            lines.append("")
            lines.append(
                f"평균 상승률: {avg_change:+.1f}% | "
                f"총 거래대금: {total_tv:,.0f}억"
            )

        await self.send_message("\n".join(lines))

    async def notify_market_condition(self, condition):
        """시장 상태 알림"""
        from scanner.market_condition import MarketLevel

        level = condition.overall_level
        level_emoji = {
            MarketLevel.NORMAL: "🟢",
            MarketLevel.CAUTION: "🟡",
            MarketLevel.WARNING: "🟠",
            MarketLevel.EXTREME: "🔴",
        }

        emoji = level_emoji.get(level, "⚪")
        lines = [
            f"{emoji} <b>시장 상태: {level.value}</b>",
            "━━━━━━━━━━━━━━",
        ]

        for idx in [condition.kospi, condition.kosdaq]:
            if idx is None:
                continue
            ma_arrow = "▼" if idx.below_ma20 else "▲"
            lines.append(
                f"<b>{idx.name}</b>: {idx.close:,.1f} ({idx.daily_change_pct:+.1f}%)\n"
                f"  MA20: {idx.ma20:,.1f} {ma_arrow} | "
                f"ATR: {idx.atr_short:.1f}/{idx.atr_long:.1f} = {idx.atr_ratio:.2f} → {idx.level.value}"
            )

        lines.append("━━━━━━━━━━━━━━")
        lines.append(
            f"매수허용: {'예' if condition.entry_allowed else '아니오'} | "
            f"포지션배율: x{condition.position_size_multiplier}"
        )
        if condition.stop_loss_override is not None:
            lines.append(f"손절 오버라이드: {condition.stop_loss_override}%")

        await self.send_message("\n".join(lines))

    async def notify_account_status(self, balance: dict):
        """
        실시간 계좌 현황 알림 → 매매 전용 봇

        Args:
            balance: KiwoomRestAPI.get_balance() 반환값
        """
        now = datetime.now().strftime('%Y-%m-%d %H:%M')
        total_equity = balance.get('total_equity', 0)
        cash = balance.get('cash', 0)
        total_pnl = balance.get('total_pnl', 0)
        total_pnl_pct = balance.get('total_pnl_pct', 0)
        positions = balance.get('positions', [])

        invested = sum(p.get('amount', 0) for p in positions)
        exposure_pct = (invested / total_equity * 100) if total_equity > 0 else 0

        pnl_emoji = "📈" if total_pnl >= 0 else "📉"

        lines = [
            f"💰 <b>계좌 현황</b> ({now})",
            "━━━━━━━━━━━━━━",
            f"총 평가금액: {total_equity:,}원",
            f"예수금: {cash:,}원",
            f"투자금: {invested:,}원 ({exposure_pct:.1f}%)",
            f"{pnl_emoji} 평가손익: {total_pnl:+,}원 ({total_pnl_pct:+.2f}%)",
        ]

        if positions:
            lines.append(f"\n<b>보유 종목 ({len(positions)}개)</b>")
            for p in positions:
                pnl_pct = p.get('pnl_pct', 0)
                emoji = "🟢" if pnl_pct >= 0 else "🔴"
                amount = p.get('amount', 0)
                lines.append(
                    f"{emoji} [{p['code']}] {p['name']}\n"
                    f"    {p['qty']}주 | 매입:{p['entry_price']:,.0f} | "
                    f"현재:{p['current_price']:,.0f} | {pnl_pct:+.1f}% | {amount:,}원"
                )
        else:
            lines.append("\n보유 종목 없음")

        await self.send_trade_message("\n".join(lines))
