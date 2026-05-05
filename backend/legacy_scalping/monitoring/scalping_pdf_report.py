"""
스캘핑 타이밍 팀 에이전트 일일 성과 PDF 보고서

매일 장 마감 후 ScalpingDailyReport 데이터를 전문 PDF 보고서로 변환.
reportlab + matplotlib 기반.
"""

import io
import logging
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional

import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
import matplotlib.ticker as mticker
import matplotlib.font_manager as fm

from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER, TA_LEFT, TA_RIGHT
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.platypus import (
    BaseDocTemplate, Frame, Image, KeepTogether, NextPageTemplate,
    PageBreak, PageTemplate, Paragraph, SimpleDocTemplate, Spacer,
    Table, TableStyle,
)

logger = logging.getLogger(__name__)

# ─── 폰트 등록 ───
_FONT_PATHS = [
    '/System/Library/Fonts/Supplemental/AppleGothic.ttf',
    '/Library/Fonts/NanumGothic.ttf',
    '/usr/share/fonts/truetype/nanum/NanumGothic.ttf',
    '/Library/Fonts/Arial Unicode.ttf',
]

_KR_FONT = 'Helvetica'  # 폴백
_MPL_FONT_PATH = None    # matplotlib 한글 폰트 경로

for fp in _FONT_PATHS:
    if Path(fp).exists():
        try:
            name = Path(fp).stem
            pdfmetrics.registerFont(TTFont(name, fp))
            _KR_FONT = name
            _MPL_FONT_PATH = fp
            break
        except Exception:
            continue

# matplotlib 한글 폰트 설정
if _MPL_FONT_PATH:
    _mpl_font_prop = fm.FontProperties(fname=_MPL_FONT_PATH)
    plt.rcParams['font.family'] = _mpl_font_prop.get_name()
    fm.fontManager.addfont(_MPL_FONT_PATH)
    plt.rcParams['axes.unicode_minus'] = False
else:
    _mpl_font_prop = None

# ─── 색상 팔레트 ───
C_PRIMARY = colors.HexColor('#1a237e')      # 남색
C_ACCENT = colors.HexColor('#0d47a1')       # 파란색
C_PROFIT = colors.HexColor('#1b5e20')       # 녹색
C_LOSS = colors.HexColor('#b71c1c')         # 적색
C_WARN = colors.HexColor('#e65100')         # 주황
C_BG_HEADER = colors.HexColor('#e3f2fd')    # 연파랑
C_BG_ALT = colors.HexColor('#f5f5f5')       # 연회색
C_BG_GOOD = colors.HexColor('#e8f5e9')      # 연녹색
C_BG_BAD = colors.HexColor('#ffebee')       # 연빨강
C_DIVIDER = colors.HexColor('#bbdefb')      # 구분선


class ScalpingPDFReport:
    """스캘핑 일일 성과 PDF 보고서 생성기"""

    PAGE_W, PAGE_H = A4  # 210mm x 297mm
    MARGIN = 15 * mm

    def __init__(self, config: dict):
        self.config = config
        self.report_dir = Path(
            config.get('logging', {}).get('dir', './logs')) / 'scalping_reports'
        self.report_dir.mkdir(parents=True, exist_ok=True)

        # 스타일 시트
        self.styles = self._build_styles()

    def generate_pdf(self, report: dict) -> str:
        """
        ScalpingDailyReport.generate() 결과 → PDF 파일 생성

        Returns:
            생성된 PDF 파일 경로
        """
        target_date = report.get('date', 'unknown')
        filepath = str(self.report_dir / f'scalping_{target_date}.pdf')

        doc = SimpleDocTemplate(
            filepath,
            pagesize=A4,
            leftMargin=self.MARGIN,
            rightMargin=self.MARGIN,
            topMargin=20 * mm,
            bottomMargin=15 * mm,
        )

        story = []
        story += self._build_cover(report)
        story += self._build_summary(report)

        # 각 분석 섹션을 KeepTogether로 감싸 페이지 경계 분리 방지
        for section_fn in [
            self._build_time_window_section,
            self._build_score_section,
            self._build_consensus_section,
            self._build_slippage_section,
        ]:
            section = section_fn(report)
            if section:
                story.append(KeepTogether(section))

        story += self._build_stock_detail_section(report)
        story += self._build_unsettled_section(report)
        story += self._build_optimal_timing_section(report)
        story += self._build_suggestions_section(report)
        story += self._build_footer(report)

        doc.build(story, onFirstPage=self._page_frame, onLaterPages=self._page_frame)

        logger.info(f"스캘핑 PDF 리포트 생성: {filepath}")
        return filepath

    # ─── 스타일 정의 ───

    def _build_styles(self) -> dict:
        s = {}
        s['title'] = ParagraphStyle(
            'Title', fontName=_KR_FONT, fontSize=22, leading=28,
            textColor=C_PRIMARY, alignment=TA_CENTER, spaceAfter=4 * mm)
        s['subtitle'] = ParagraphStyle(
            'Subtitle', fontName=_KR_FONT, fontSize=11, leading=14,
            textColor=colors.grey, alignment=TA_CENTER, spaceAfter=8 * mm)
        s['h1'] = ParagraphStyle(
            'H1', fontName=_KR_FONT, fontSize=14, leading=18,
            textColor=C_PRIMARY, spaceBefore=6 * mm, spaceAfter=3 * mm)
        s['h2'] = ParagraphStyle(
            'H2', fontName=_KR_FONT, fontSize=11, leading=14,
            textColor=C_ACCENT, spaceBefore=4 * mm, spaceAfter=2 * mm)
        s['body'] = ParagraphStyle(
            'Body', fontName=_KR_FONT, fontSize=9, leading=12,
            textColor=colors.black)
        s['body_small'] = ParagraphStyle(
            'BodySmall', fontName=_KR_FONT, fontSize=8, leading=10,
            textColor=colors.HexColor('#424242'))
        s['metric_big'] = ParagraphStyle(
            'MetricBig', fontName=_KR_FONT, fontSize=16, leading=20,
            alignment=TA_CENTER)
        s['metric_label'] = ParagraphStyle(
            'MetricLabel', fontName=_KR_FONT, fontSize=8, leading=10,
            textColor=colors.grey, alignment=TA_CENTER)
        s['cell'] = ParagraphStyle(
            'Cell', fontName=_KR_FONT, fontSize=8, leading=10)
        s['cell_r'] = ParagraphStyle(
            'CellR', fontName=_KR_FONT, fontSize=8, leading=10,
            alignment=TA_RIGHT)
        s['cell_c'] = ParagraphStyle(
            'CellC', fontName=_KR_FONT, fontSize=8, leading=10,
            alignment=TA_CENTER)
        s['cell_header'] = ParagraphStyle(
            'CellHeader', fontName=_KR_FONT, fontSize=8, leading=10,
            textColor=C_PRIMARY, alignment=TA_CENTER)
        s['suggestion'] = ParagraphStyle(
            'Suggestion', fontName=_KR_FONT, fontSize=9, leading=12,
            textColor=C_WARN, leftIndent=10)
        s['footer'] = ParagraphStyle(
            'Footer', fontName=_KR_FONT, fontSize=7, leading=9,
            textColor=colors.grey, alignment=TA_CENTER)
        return s

    # ─── 페이지 프레임 ───

    def _page_frame(self, canvas, doc):
        canvas.saveState()
        # 상단 바
        canvas.setFillColor(C_PRIMARY)
        canvas.rect(0, self.PAGE_H - 12 * mm, self.PAGE_W, 12 * mm, fill=1, stroke=0)
        canvas.setFillColor(colors.white)
        canvas.setFont(_KR_FONT, 8)
        canvas.drawString(
            self.MARGIN, self.PAGE_H - 8 * mm,
            'SCALPING TIMING TEAM AGENT - DAILY PERFORMANCE REPORT')
        canvas.drawRightString(
            self.PAGE_W - self.MARGIN, self.PAGE_H - 8 * mm,
            f'Page {doc.page}')
        # 하단 라인
        canvas.setStrokeColor(C_DIVIDER)
        canvas.setLineWidth(0.5)
        canvas.line(self.MARGIN, 10 * mm, self.PAGE_W - self.MARGIN, 10 * mm)
        canvas.setFont(_KR_FONT, 6)
        canvas.setFillColor(colors.grey)
        canvas.drawCentredString(
            self.PAGE_W / 2, 6 * mm,
            'AI Trade System - Confidential')
        canvas.restoreState()

    # ─── 섹션 빌더 ───

    def _build_cover(self, report: dict) -> list:
        d = report.get('date', '')
        elems = [
            Spacer(1, 15 * mm),
            Paragraph('SCALPING TIMING TEAM', self.styles['title']),
            Paragraph('DAILY PERFORMANCE REPORT', self.styles['title']),
            Spacer(1, 5 * mm),
            Paragraph(
                f'{d}  |  10 Expert Agents  |  Automated Analysis',
                self.styles['subtitle']),
            self._divider(),
        ]

        if report.get('total_scalping_trades', 0) == 0:
            elems.append(Paragraph(
                '당일 스캘핑 매매 내역이 없습니다.',
                self.styles['body']))
            return elems

        # KPI 카드
        p = report.get('performance', {})

        # 순손익 포맷: 만원 단위로 표시
        net_pnl = p.get('total_net_pnl', 0)
        if abs(net_pnl) >= 10000:
            pnl_str = f'{net_pnl/10000:+,.1f}만'
        else:
            pnl_str = f'{net_pnl:+,.0f}'

        kpi_data = [
            (f'{p.get("total_pairs", 0)}건', '매칭 거래'),
            (f'{p.get("win_rate_pct", 0):.0f}%', '승률'),
            (pnl_str, '순손익'),
            (f'{p.get("profit_factor", 0):.2f}', '수익팩터'),
            (f'{p.get("avg_hold_minutes", 0):.0f}분', '평균보유'),
        ]

        cards = []
        for value, label in kpi_data:
            color = C_PROFIT if '순손익' in label and net_pnl > 0 else (
                C_LOSS if '순손익' in label and net_pnl < 0 else colors.black)
            style = ParagraphStyle(
                'kpi', parent=self.styles['metric_big'], textColor=color)
            cards.append([
                Paragraph(value, style),
                Paragraph(label, self.styles['metric_label']),
            ])

        # 5열 KPI 테이블
        col_w = (self.PAGE_W - 2 * self.MARGIN) / 5
        kpi_table = Table(
            [[c[0] for c in cards], [c[1] for c in cards]],
            colWidths=[col_w] * 5,
            rowHeights=[26, 14],
        )
        kpi_table.setStyle(TableStyle([
            ('ALIGN', (0, 0), (-1, -1), 'CENTER'),
            ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
            ('BACKGROUND', (0, 0), (-1, -1), C_BG_HEADER),
            ('BOX', (0, 0), (-1, -1), 0.5, C_DIVIDER),
            ('INNERGRID', (0, 0), (-1, -1), 0.3, C_DIVIDER),
            ('TOPPADDING', (0, 0), (-1, 0), 6),
            ('BOTTOMPADDING', (0, 1), (-1, 1), 4),
        ]))
        elems.append(kpi_table)
        elems.append(Spacer(1, 3 * mm))

        # 승패 요약
        wins = p.get('wins', 0)
        losses = p.get('losses', 0)
        be = p.get('breakeven', 0)
        avg_w = p.get('avg_win_pct', 0)
        avg_l = p.get('avg_loss_pct', 0)
        elems.append(Paragraph(
            f'승 {wins} / 패 {losses} / 무 {be}  |  '
            f'평균수익 +{avg_w:.2f}%  |  평균손실 {avg_l:.2f}%  |  '
            f'총이익 {p.get("gross_profit",0):+,}원  |  '
            f'총손실 {p.get("gross_loss",0):,}원',
            self.styles['body']))

        return elems

    def _build_summary(self, report: dict) -> list:
        """종합 성과 차트"""
        if report.get('total_scalping_trades', 0) == 0:
            return []

        p = report.get('performance', {})
        elems = [
            Spacer(1, 3 * mm),
            Paragraph('1. 종합 성과 분석', self.styles['h1']),
        ]

        # 승률 + 수익팩터 파이차트
        fig, axes = plt.subplots(1, 2, figsize=(7, 2.8))

        # 파이차트: 승/패/무
        wins = p.get('wins', 0)
        losses = p.get('losses', 0)
        be = p.get('breakeven', 0)
        vals = [v for v in [wins, losses, be] if v > 0]
        labels = []
        pie_colors = []
        if wins > 0:
            labels.append(f'Win {wins}')
            pie_colors.append('#4caf50')
        if losses > 0:
            labels.append(f'Loss {losses}')
            pie_colors.append('#f44336')
        if be > 0:
            labels.append(f'B/E {be}')
            pie_colors.append('#9e9e9e')

        if vals:
            axes[0].pie(vals, labels=labels, colors=pie_colors,
                        autopct='%1.0f%%', startangle=90,
                        textprops={'fontsize': 9})
        axes[0].set_title('Win/Loss Ratio', fontsize=10, color='#1a237e')

        # 바차트: 이익 vs 손실
        categories = ['Gross Profit', 'Gross Loss']
        values = [p.get('gross_profit', 0), p.get('gross_loss', 0)]
        bar_colors = ['#4caf50', '#f44336']
        bars = axes[1].bar(categories, values, color=bar_colors, width=0.5)
        axes[1].set_title('Profit vs Loss', fontsize=10, color='#1a237e')
        axes[1].axhline(y=0, color='grey', linewidth=0.5)
        axes[1].yaxis.set_major_formatter(
            mticker.FuncFormatter(lambda x, p: f'{x:,.0f}'))
        for bar, val in zip(bars, values):
            axes[1].text(bar.get_x() + bar.get_width() / 2, val,
                         f'{val:+,.0f}', ha='center',
                         va='bottom' if val >= 0 else 'top',
                         fontsize=8)

        plt.tight_layout()
        img_buf = self._fig_to_image(fig)
        elems.append(img_buf)

        return elems

    def _build_time_window_section(self, report: dict) -> list:
        tw = report.get('time_window_analysis', [])
        active = [w for w in tw if w.get('count', 0) > 0]
        if not active:
            return []

        elems = [
            Paragraph('2. 시간대별 진입 성과', self.styles['h1']),
        ]

        # 테이블
        header = ['시간대', '구분', '건수', '승률', '평균P&L', '순손익']
        rows = [header]
        for w in active:
            pnl = w.get('total_pnl', 0)
            pnl_color = 'green' if pnl > 0 else ('red' if pnl < 0 else 'black')
            rows.append([
                w['window'],
                w['label'],
                f'{w["count"]}',
                f'{w.get("win_rate_pct", 0):.0f}%',
                f'{w.get("avg_pnl_pct", 0):+.2f}%',
                f'{pnl:+,}',
            ])

        table = self._styled_table(rows, col_widths=[28, 22, 14, 14, 18, 24])
        elems.append(table)

        # 시간대 차트
        fig, ax = plt.subplots(figsize=(7, 2.8))
        # X축 라벨: 시간대만 (구분은 범례 대신 차트 위에 표시)
        x_labels = [f'{w["window"]}\n{w["label"]}' for w in active]
        x_pos = range(len(active))
        pnls = [w.get('total_pnl', 0) for w in active]
        win_rates = [w.get('win_rate_pct', 0) for w in active]
        bar_colors = ['#4caf50' if p > 0 else '#f44336' for p in pnls]

        bars = ax.bar(x_pos, pnls, color=bar_colors, alpha=0.8)
        ax.set_ylabel('P&L (KRW)', fontsize=8)
        ax.set_title('Entry Time Window Performance', fontsize=10, color='#1a237e')
        ax.axhline(y=0, color='grey', linewidth=0.5)
        ax.set_xticks(x_pos)
        ax.set_xticklabels(x_labels, fontsize=7)
        ax.yaxis.set_major_formatter(
            mticker.FuncFormatter(lambda x, p: f'{x:,.0f}'))

        # 승률 라인 (2차 축)
        ax2 = ax.twinx()
        ax2.plot(x_pos, win_rates, 'o-', color='#1a237e',
                 linewidth=1.5, markersize=5, label='Win Rate %')
        ax2.set_ylabel('Win Rate %', fontsize=8)
        ax2.set_ylim(0, 110)
        ax2.legend(loc='upper right', fontsize=7)

        for bar, val in zip(bars, pnls):
            offset = max(abs(max(pnls, default=0) - min(pnls, default=0)) * 0.05, 1000)
            ax.text(bar.get_x() + bar.get_width() / 2,
                    val + (offset if val >= 0 else -offset),
                    f'{val:+,.0f}', ha='center', fontsize=7,
                    va='bottom' if val >= 0 else 'top')

        plt.tight_layout()
        elems.append(self._fig_to_image(fig))

        return elems

    def _build_score_section(self, report: dict) -> list:
        sb = report.get('score_analysis', [])
        active = [s for s in sb if s.get('count', 0) > 0]
        if not active:
            return []

        elems = [
            Paragraph('3. 점수대별 승률 분석', self.styles['h1']),
        ]

        header = ['점수대', '건수', '승률', '평균P&L', '순손익']
        rows = [header]
        for s in active:
            pnl = s.get('total_pnl', 0)
            rows.append([
                s['band'],
                f'{s["count"]}',
                f'{s.get("win_rate_pct", 0):.0f}%',
                f'{s.get("avg_pnl_pct", 0):+.2f}%',
                f'{pnl:+,}',
            ])

        table = self._styled_table(rows, col_widths=[24, 14, 16, 20, 26])
        elems.append(table)

        # 차트
        fig, ax = plt.subplots(figsize=(7, 2.2))
        bands = [s['band'] for s in active]
        win_rates = [s.get('win_rate_pct', 0) for s in active]
        counts = [s['count'] for s in active]
        pnls = [s.get('total_pnl', 0) for s in active]

        x = range(len(bands))
        bars = ax.bar(x, pnls, color=['#4caf50' if p > 0 else '#f44336' for p in pnls],
                      alpha=0.7, label='P&L')
        ax.set_ylabel('P&L (KRW)', fontsize=8)
        ax.set_xticks(x)
        ax.set_xticklabels(bands, fontsize=7)
        ax.set_title('Score Band Performance', fontsize=10, color='#1a237e')
        ax.axhline(y=0, color='grey', linewidth=0.5)
        ax.yaxis.set_major_formatter(
            mticker.FuncFormatter(lambda x, p: f'{x:,.0f}'))

        ax2 = ax.twinx()
        ax2.plot(x, win_rates, 'D-', color='#e65100', markersize=5,
                 linewidth=1.5, label='Win Rate %')
        ax2.set_ylabel('Win Rate %', fontsize=8)
        ax2.set_ylim(0, 110)
        ax2.legend(loc='upper right', fontsize=7)

        for i, (bar, cnt) in enumerate(zip(bars, counts)):
            ax.text(bar.get_x() + bar.get_width() / 2, 0, f'n={cnt}',
                    ha='center', va='bottom', fontsize=7, color='grey')

        plt.tight_layout()
        elems.append(self._fig_to_image(fig))

        return elems

    def _build_consensus_section(self, report: dict) -> list:
        ca = report.get('consensus_analysis', [])
        active = [c for c in ca if c.get('count', 0) > 0]
        if not active:
            return []

        elems = [
            Paragraph('4. 합의수준별 승률 분석', self.styles['h1']),
        ]

        header = ['합의수준', '건수', '승률', '평균P&L', '순손익']
        rows = [header]
        for c in active:
            rows.append([
                c['level'],
                f'{c["count"]}',
                f'{c.get("win_rate_pct", 0):.0f}%',
                f'{c.get("avg_pnl_pct", 0):+.2f}%',
                f'{c.get("total_pnl", 0):+,}',
            ])

        table = self._styled_table(rows, col_widths=[24, 14, 16, 20, 26])
        elems.append(table)

        # 차트
        fig, ax = plt.subplots(figsize=(5, 2.2))
        levels = [c['level'] for c in active]
        pnls = [c.get('total_pnl', 0) for c in active]
        wr = [c.get('win_rate_pct', 0) for c in active]

        ax.barh(levels, pnls,
                color=['#4caf50' if p > 0 else '#f44336' for p in pnls],
                height=0.5)
        ax.set_xlabel('P&L (KRW)', fontsize=8)
        ax.set_title('Consensus Level Performance', fontsize=10, color='#1a237e')
        ax.axvline(x=0, color='grey', linewidth=0.5)
        ax.xaxis.set_major_formatter(
            mticker.FuncFormatter(lambda x, p: f'{x:,.0f}'))

        for i, (p, w) in enumerate(zip(pnls, wr)):
            ax.text(p, i, f' WR {w:.0f}%', va='center', fontsize=7,
                    ha='left' if p >= 0 else 'right')

        plt.tight_layout()
        elems.append(self._fig_to_image(fig))

        return elems

    def _build_slippage_section(self, report: dict) -> list:
        sa = report.get('slippage_analysis', {})
        if not sa:
            return []

        elems = [
            Paragraph('5. 슬리피지 분석', self.styles['h1']),
        ]

        # KPI 요약
        elems.append(Paragraph(
            f'평균 슬리피지: {sa.get("avg_slippage_pct",0):.1f}%  |  '
            f'최대: {sa.get("max_slippage_pct",0):.1f}%  |  '
            f'최소: {sa.get("min_slippage_pct",0):.1f}%  |  '
            f'2% 초과: {sa.get("over_2pct_count",0)}/{sa.get("total_buys",0)}건 '
            f'({sa.get("over_2pct_ratio",0):.0f}%)',
            self.styles['body']))
        elems.append(Spacer(1, 2 * mm))

        # 상세 테이블
        details = sa.get('details', [])
        if details:
            header = ['시간', '종목코드', '종목명', '신호가', '체결가', '슬리피지']
            rows = [header]
            for d in details:
                slip = d['slippage_pct']
                slip_str = f'{slip:+.1f}%'
                rows.append([
                    d['time'],
                    d['code'],
                    d['name'],
                    f'{d["signal_price"]:,}',
                    f'{d["fill_price"]:,}',
                    slip_str,
                ])

            table = self._styled_table(
                rows, col_widths=[18, 18, 22, 20, 20, 18],
                highlight_col=5, highlight_threshold=2.0)
            elems.append(table)

        # 슬리피지 분포 차트
        if details:
            n = len(details)
            fig_h = max(2.5, 2.5 + n * 0.05)
            fig, ax = plt.subplots(figsize=(7, fig_h))
            slips = [d['slippage_pct'] for d in details]
            # X축: 코드+시간 (한글 종목명 대신 코드 사용으로 깔끔)
            labels = [f'{d["code"]}\n{d["time"]}' for d in details]
            bar_colors = ['#f44336' if s > 2.0 else '#4caf50' for s in slips]
            bars = ax.bar(range(n), slips, color=bar_colors, alpha=0.8)
            ax.axhline(y=2.0, color='#e65100', linewidth=1.5,
                       linestyle='--', label='Threshold 2.0%')
            ax.set_ylabel('Slippage %', fontsize=8)
            ax.set_title('Per-Trade Slippage Distribution', fontsize=10,
                         color='#1a237e')
            ax.set_xticks(range(n))
            ax.set_xticklabels(labels, fontsize=6, rotation=45, ha='right')
            ax.legend(fontsize=7)
            for bar, val in zip(bars, slips):
                ax.text(bar.get_x() + bar.get_width() / 2, val + 0.3,
                        f'{val:.1f}%', ha='center', fontsize=6)
            plt.subplots_adjust(bottom=0.25)
            plt.tight_layout()
            elems.append(self._fig_to_image(fig))

        return elems

    def _build_stock_detail_section(self, report: dict) -> list:
        sd = report.get('stock_details', [])
        if not sd:
            return []

        elems = [
            Paragraph('6. 종목별 상세 매매 내역', self.styles['h1']),
        ]

        for stock in sd:
            pnl = stock.get('total_pnl', 0)
            pnl_color = 'green' if pnl > 0 else ('red' if pnl < 0 else 'black')

            elems.append(Paragraph(
                f'[{stock["code"]}] {stock["name"]}  |  '
                f'{stock["trade_count"]}건  |  '
                f'승률 {stock["win_rate_pct"]:.0f}%  |  '
                f'<font color="{pnl_color}">{pnl:+,}원</font>  |  '
                f'평균보유 {stock["avg_hold_minutes"]:.0f}분  |  '
                f'슬리피지 {stock["avg_slippage_pct"]:.1f}%',
                self.styles['h2']))

            header = ['매수시간', '매도시간', '매수가', '매도가',
                      '수익률', '순손익', '청산유형']
            rows = [header]
            for t in stock.get('trades', []):
                net = t.get('net_pnl_pct', 0)
                rows.append([
                    t['buy_time'][11:16],
                    t['sell_time'][11:16],
                    f'{t["buy_price"]:,}',
                    f'{t["sell_price"]:,}',
                    f'{net:+.2f}%',
                    f'{t["net_amount"]:+,}',
                    t.get('exit_type', ''),
                ])

            table = self._styled_table(
                rows, col_widths=[18, 18, 20, 20, 16, 20, 26])
            elems.append(table)
            elems.append(Spacer(1, 2 * mm))

        return elems

    def _build_unsettled_section(self, report: dict) -> list:
        us = report.get('unsettled', [])
        if not us:
            return []

        elems = [
            Paragraph('7. 미청산 포지션 (익일 이월)', self.styles['h1']),
        ]

        header = ['종목코드', '종목명', '매수(주)', '매도(주)', '잔량(주)', '추정가(원)']
        rows = [header]
        total_val = 0
        for u in us:
            val = u.get('estimated_value', 0)
            total_val += val
            rows.append([
                u['code'],
                u['name'],
                f'{u["buy_total"]:,}',
                f'{u["sell_total"]:,}',
                f'{u["remaining_qty"]:,}',
                f'{val:,}',
            ])
        rows.append(['', '', '', '', 'TOTAL', f'{total_val:,}'])

        table = self._styled_table(rows, col_widths=[18, 22, 18, 18, 18, 26])
        elems.append(table)

        elems.append(Spacer(1, 2 * mm))
        elems.append(Paragraph(
            '* 미청산 포지션은 익일 시가 기준 carryover 전략이 적용됩니다.',
            self.styles['body_small']))

        return elems

    def _build_optimal_timing_section(self, report: dict) -> list:
        ot = report.get('optimal_timing', {})
        if not ot or 'message' in ot:
            return []

        elems = [
            Paragraph('8. 최적 타이밍 패턴 (코드 활용 데이터)', self.styles['h1']),
        ]

        # 최적 윈도우
        bw = ot.get('best_entry_windows', [])
        if bw:
            elems.append(Paragraph('최고 수익 시간대 순위', self.styles['h2']))
            header = ['순위', '시간대', '구분', '건수', '승률', '순손익']
            rows = [header]
            for i, w in enumerate(bw):
                rows.append([
                    f'#{i+1}',
                    w['window'],
                    w['label'],
                    f'{w["count"]}',
                    f'{w["win_rate"]:.0f}%',
                    f'{w["pnl"]:+,}',
                ])
            table = self._styled_table(rows, col_widths=[12, 26, 22, 14, 16, 26])
            elems.append(table)

        # 점수 임계값
        sc = ot.get('optimal_score_threshold', {})
        if sc:
            elems.append(Spacer(1, 2 * mm))
            elems.append(Paragraph(
                f'수익 거래 평균 점수: <b>{sc.get("win_avg_score",0):.0f}점</b>  |  '
                f'전체 평균: {sc.get("all_avg_score",0):.0f}점  |  '
                f'최소 수익 점수: <b>{sc.get("min_profitable_score",0):.0f}점</b>',
                self.styles['body']))

        # 보유 시간
        hr = ot.get('optimal_hold_minutes', {})
        if hr:
            elems.append(Paragraph(
                f'수익 평균 보유: <b>{hr.get("win_avg",0):.0f}분</b>  |  '
                f'범위: {hr.get("best_range","N/A")}',
                self.styles['body']))

        # 슬리피지 임팩트
        si = ot.get('slippage_impact', {})
        if si:
            elems.append(Paragraph(
                f'수익 거래 평균 슬리피지: {si.get("win_avg_slip",0):.1f}%  |  '
                f'권장 최대: <b>{si.get("recommended_max",2.0):.1f}%</b>',
                self.styles['body']))

        return elems

    def _build_suggestions_section(self, report: dict) -> list:
        ps = report.get('parameter_suggestions', {})
        if not ps:
            return []

        elems = [
            Paragraph('9. settings.yaml 조정 제안', self.styles['h1']),
            Paragraph(
                '아래 제안은 당일 매매 결과 기반 자동 산출값으로, '
                '수일간 누적 분석 후 적용을 권장합니다.',
                self.styles['body_small']),
            Spacer(1, 2 * mm),
        ]

        header = ['파라미터', '현재값', '제안', '근거']
        rows = [header]

        for key, val in ps.items():
            current = val.get('current', val.get('current_default', '-'))
            suggested = val.get('suggested', '-')
            reason = val.get('reason', '')

            if key == 'min_score':
                rows.append([
                    'strategy.scalping.min_score',
                    str(current),
                    str(suggested),
                    reason,
                ])
            elif key == 'max_slippage_pct':
                rows.append([
                    'strategy.scalping.max_slippage_pct',
                    str(current),
                    f'{val.get("pass_ratio",0):.0f}% pass',
                    reason,
                ])
            elif key == 'entry_window':
                rows.append([
                    'strategy.scalping.entry_start/end',
                    '-',
                    val.get('best_window', '-'),
                    reason,
                ])
            elif key == 'exit_analysis':
                rows.append([
                    'TP/SL exit pattern',
                    '-',
                    f'TP:{val.get("tp_hit",0)} TO:{val.get("timeout",0)}',
                    reason,
                ])
            elif key == 'hold_minutes':
                rows.append([
                    'strategy.scalping.default_hold_minutes',
                    str(current),
                    f'{val.get("avg_loss_hold",0):.0f}분',
                    reason,
                ])
            elif key == 'poll_interval':
                rows.append([
                    'strategy.scalping.poll_interval_seconds',
                    str(current),
                    'increase',
                    reason,
                ])

        table = self._styled_table(rows, col_widths=[35, 16, 18, 51],
                                    font_size=7)
        elems.append(table)

        return elems

    def _build_footer(self, report: dict) -> list:
        now = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        # 페이지 하단 프레임에 이미 표시되므로, 본문 마지막에는
        # 생성 정보만 간결하게 추가 (빈 페이지 방지)
        return [
            Spacer(1, 4 * mm),
            Paragraph(
                f'<font size=7 color="grey">'
                f'Generated: {now}  |  '
                f'Agents: VWAP, Momentum, Pullback, Breakout, '
                f'Candle, Volume, GoldenTime, RelStrength, RiskReward, Tape'
                f'</font>',
                self.styles['footer']),
        ]

    # ─── 유틸 ───

    def _styled_table(
        self, rows: list, col_widths: list,
        highlight_col: int = -1, highlight_threshold: float = 0,
        font_size: int = 8,
    ) -> Table:
        """공통 테이블 스타일"""
        usable_w = self.PAGE_W - 2 * self.MARGIN
        total_units = sum(col_widths)
        widths = [usable_w * w / total_units for w in col_widths]

        # 폰트 크기별 스타일 생성
        cell_s = ParagraphStyle('_cell', parent=self.styles['cell'],
                                fontSize=font_size, leading=font_size + 2)
        cell_r_s = ParagraphStyle('_cell_r', parent=self.styles['cell_r'],
                                  fontSize=font_size, leading=font_size + 2)
        cell_h_s = ParagraphStyle('_cell_h', parent=self.styles['cell_header'],
                                  fontSize=font_size, leading=font_size + 2)

        # Paragraph으로 감싸기
        styled_rows = []
        for r_idx, row in enumerate(rows):
            styled_row = []
            for c_idx, cell in enumerate(row):
                if r_idx == 0:
                    styled_row.append(Paragraph(str(cell), cell_h_s))
                elif c_idx >= len(row) - 2:
                    styled_row.append(Paragraph(str(cell), cell_r_s))
                else:
                    styled_row.append(Paragraph(str(cell), cell_s))
            styled_rows.append(styled_row)

        table = Table(styled_rows, colWidths=widths)

        style_cmds = [
            ('BACKGROUND', (0, 0), (-1, 0), C_BG_HEADER),
            ('TEXTCOLOR', (0, 0), (-1, 0), C_PRIMARY),
            ('ALIGN', (0, 0), (-1, 0), 'CENTER'),
            ('FONTNAME', (0, 0), (-1, -1), _KR_FONT),
            ('FONTSIZE', (0, 0), (-1, -1), font_size),
            ('BOTTOMPADDING', (0, 0), (-1, -1), 2),
            ('TOPPADDING', (0, 0), (-1, -1), 2),
            ('GRID', (0, 0), (-1, -1), 0.3, C_DIVIDER),
            ('BOX', (0, 0), (-1, -1), 0.5, C_ACCENT),
            ('ROWBACKGROUNDS', (0, 1), (-1, -1), [colors.white, C_BG_ALT]),
        ]

        # 수익/손실 행 하이라이트
        for r_idx in range(1, len(rows)):
            for c_idx, cell in enumerate(rows[r_idx]):
                if isinstance(cell, str) and cell.startswith('+') and '%' in cell:
                    style_cmds.append(
                        ('TEXTCOLOR', (c_idx, r_idx), (c_idx, r_idx), C_PROFIT))
                elif isinstance(cell, str) and cell.startswith('-') and (
                        '%' in cell or cell.replace(',', '').replace('-', '').isdigit()):
                    style_cmds.append(
                        ('TEXTCOLOR', (c_idx, r_idx), (c_idx, r_idx), C_LOSS))

            # 슬리피지 >2% 행 하이라이트
            if highlight_col >= 0 and highlight_col < len(rows[r_idx]):
                try:
                    val_str = rows[r_idx][highlight_col].replace(
                        '%', '').replace('+', '')
                    if float(val_str) > highlight_threshold:
                        style_cmds.append(
                            ('BACKGROUND', (0, r_idx), (-1, r_idx), C_BG_BAD))
                except (ValueError, AttributeError):
                    pass

        table.setStyle(TableStyle(style_cmds))
        return table

    def _fig_to_image(self, fig, width=170 * mm) -> Image:
        """matplotlib figure → reportlab Image"""
        buf = io.BytesIO()
        fig.savefig(buf, format='png', dpi=150, bbox_inches='tight',
                    facecolor='white')
        plt.close(fig)
        buf.seek(0)

        img = Image(buf, width=width)
        # 비율 유지
        orig_w, orig_h = fig.get_size_inches()
        ratio = orig_h / orig_w
        img.drawHeight = width * ratio
        img.drawWidth = width
        return img

    def _divider(self) -> Table:
        """구분선"""
        t = Table([['']], colWidths=[self.PAGE_W - 2 * self.MARGIN],
                  rowHeights=[1])
        t.setStyle(TableStyle([
            ('LINEABOVE', (0, 0), (-1, 0), 1, C_DIVIDER),
        ]))
        return t
