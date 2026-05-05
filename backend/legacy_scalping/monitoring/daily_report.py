"""
일일 리포트 생성기
장 마감 후 당일 매매 내역을 분석하여 리포트 생성
"""

import json
import logging
from datetime import date, datetime
from pathlib import Path
from typing import List, Dict

logger = logging.getLogger(__name__)


class DailyReportGenerator:
    """일일 매매 리포트 생성"""

    def __init__(self, config: dict):
        self.trade_log_path = config.get('logging', {}).get(
            'trade_log', './logs/trades.jsonl')
        self.log_dir = config.get('logging', {}).get('dir', './logs')

    def generate(self, target_date: str = None) -> dict:
        """
        일일 리포트 생성

        Args:
            target_date: YYYY-MM-DD (None이면 오늘)

        Returns:
            리포트 dict
        """
        if target_date is None:
            target_date = date.today().isoformat()

        trades = self._load_trades(target_date)

        if not trades:
            return {
                'date': target_date,
                'total_trades': 0,
                'message': '당일 매매 내역 없음',
            }

        buys = [t for t in trades if t.get('action') == 'BUY']
        sells = [t for t in trades if t.get('action') == 'SELL']

        # 종목별 손익 계산
        stock_pnl = {}
        for sell in sells:
            code = sell.get('code', '')
            pnl_pct = sell.get('pnl_pct', 0)
            if code not in stock_pnl:
                stock_pnl[code] = {
                    'name': sell.get('name', code),
                    'trades': [],
                    'total_pnl': 0,
                }
            entry_p = sell.get('entry_price', 0)
            sell_p = sell.get('price', 0)
            qty = sell.get('qty', 0)
            realized = (sell_p - entry_p) * qty
            stock_pnl[code]['total_pnl'] += realized
            stock_pnl[code]['trades'].append({
                'exit_type': sell.get('exit_type', ''),
                'pnl_pct': pnl_pct,
                'qty': qty,
                'price': sell_p,
            })

        # 승/패 분류
        winners = [c for c, v in stock_pnl.items() if v['total_pnl'] > 0]
        losers = [c for c, v in stock_pnl.items() if v['total_pnl'] < 0]
        breakeven = [c for c, v in stock_pnl.items() if v['total_pnl'] == 0]

        total_realized = sum(v['total_pnl'] for v in stock_pnl.values())
        win_rate = len(winners) / len(stock_pnl) * 100 if stock_pnl else 0

        report = {
            'date': target_date,
            'total_trades': len(trades),
            'buy_count': len(buys),
            'sell_count': len(sells),
            'unique_stocks': len(stock_pnl),
            'winners': len(winners),
            'losers': len(losers),
            'breakeven': len(breakeven),
            'win_rate': round(win_rate, 1),
            'total_realized_pnl': round(total_realized),
            'stock_details': stock_pnl,
        }

        # 리포트 파일 저장
        self._save_report(report, target_date)
        return report

    def format_text_report(self, report: dict) -> str:
        """리포트를 텍스트로 포맷팅"""
        lines = [
            f"📊 일일 매매 리포트 ({report['date']})",
            "━" * 30,
            f"총 거래: {report['total_trades']}회 (매수 {report['buy_count']} / 매도 {report['sell_count']})",
            f"매매 종목: {report['unique_stocks']}개",
            f"승률: {report['win_rate']}% ({report['winners']}승 {report['losers']}패 {report['breakeven']}무)",
            f"실현 손익: {report['total_realized_pnl']:+,}원",
            "",
        ]

        details = report.get('stock_details', {})
        if details:
            lines.append("종목별 상세:")
            for code, info in details.items():
                emoji = "🟢" if info['total_pnl'] > 0 else "🔴" if info['total_pnl'] < 0 else "⚪"
                lines.append(
                    f"  {emoji} [{code}] {info['name']} | "
                    f"손익: {info['total_pnl']:+,.0f}원"
                )
                for t in info['trades']:
                    lines.append(
                        f"      └ {t['exit_type']} | {t['pnl_pct']:+.1f}% | {t['qty']}주"
                    )

        return "\n".join(lines)

    def _load_trades(self, target_date: str) -> List[dict]:
        """JSONL 파일에서 당일 매매 내역 로드"""
        trades = []
        try:
            with open(self.trade_log_path, 'r', encoding='utf-8') as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    trade = json.loads(line)
                    ts = trade.get('timestamp', '')
                    if ts.startswith(target_date):
                        trades.append(trade)
        except FileNotFoundError:
            logger.warning(f"매매 로그 없음: {self.trade_log_path}")
        except Exception as e:
            logger.error(f"매매 로그 파싱 오류: {e}")
        return trades

    def _save_report(self, report: dict, target_date: str):
        """리포트를 JSON 파일로 저장"""
        filepath = f"{self.log_dir}/report_{target_date}.json"
        try:
            with open(filepath, 'w', encoding='utf-8') as f:
                json.dump(report, f, ensure_ascii=False, indent=2, default=str)
            logger.info(f"리포트 저장: {filepath}")
        except Exception as e:
            logger.error(f"리포트 저장 실패: {e}")
