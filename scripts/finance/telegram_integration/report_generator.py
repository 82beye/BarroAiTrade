"""
종목 분석 리포트 생성기

EntrySignal 객체를 받아 Telegram 마크다운 형식의 리포트를 생성합니다.
리포트 포맷:
  - 종목명, 현재가, 변동률
  - 기술적 신호 (추천 강도)
  - 목표가 1/2, 손절가
  - 매매 전략 요약
"""
from __future__ import annotations

from datetime import datetime
from typing import List, Optional

# 전략별 목표가/손절가 기준
_STRATEGY_LEVELS = {
    "f_zone":          {"tp1": 0.03, "tp2": 0.05, "sl": -0.02, "label": "F존"},
    "sf_zone":         {"tp1": 0.05, "tp2": 0.10, "sl": -0.03, "label": "SF존(슈퍼존)"},
    "blue_line":       {"tp1": 0.03, "tp2": 0.07, "sl": -0.02, "label": "파란점선"},
    "watermelon":      {"tp1": 0.04, "tp2": 0.08, "sl": -0.02, "label": "수박"},
    "crypto_breakout": {"tp1": 0.05, "tp2": 0.10, "sl": -0.03, "label": "돌파"},
}

# 점수별 신호 강도 레이블
_SCORE_LABELS = [
    (8.0, "🔥 강력 추천"),
    (6.0, "⭐ 추천"),
    (4.0, "👀 관심"),
    (0.0, "⚠️ 주의"),
]


def _score_label(score: float) -> str:
    for threshold, label in _SCORE_LABELS:
        if score >= threshold:
            return label
    return "⚠️ 주의"


class ReportGenerator:
    """종목 분석 리포트 생성기"""

    def generate_signal_report(self, signal) -> str:
        """단일 종목 분석 리포트 생성 (Telegram Markdown)

        Args:
            signal: EntrySignal 객체

        Returns:
            Telegram Markdown 형식의 리포트 문자열
        """
        levels = _STRATEGY_LEVELS.get(
            signal.signal_type,
            {"tp1": 0.03, "tp2": 0.05, "sl": -0.02, "label": signal.signal_type},
        )
        price = signal.price
        tp1 = price * (1 + levels["tp1"])
        tp2 = price * (1 + levels["tp2"])
        sl = price * (1 + levels["sl"])
        strength = _score_label(signal.score)
        strategy_label = levels["label"]
        now = datetime.now().strftime("%Y-%m-%d %H:%M")

        lines = [
            f"📊 *종목 분석 — {signal.symbol}*",
            f"_{now}_",
            "",
            f"신호 강도: {strength} (점수: {signal.score:.1f})",
            f"전략: {strategy_label}",
            "",
            f"현재가: {price:,.0f}원",
            f"목표가1: {tp1:,.0f}원 (+{levels['tp1']*100:.0f}%)",
            f"목표가2: {tp2:,.0f}원 (+{levels['tp2']*100:.0f}%)",
            f"손절가: {sl:,.0f}원 ({levels['sl']*100:.0f}%)",
        ]
        return "\n".join(lines)

    def generate_scan_report(self, signals: List) -> str:
        """다수 종목 스캔 결과 종합 리포트 (최대 10종목)

        Args:
            signals: EntrySignal 목록 (score 내림차순 정렬 권장)

        Returns:
            Telegram Markdown 형식의 스캔 리포트
        """
        if not signals:
            return "📭 *스캔 결과 없음*\n감지된 진입 신호가 없습니다."

        top = sorted(signals, key=lambda s: s.score, reverse=True)[:10]
        now = datetime.now().strftime("%Y-%m-%d %H:%M")
        lines = [f"🔍 *종목 스캔 리포트 — {now}*", f"감지 종목: {len(top)}개", ""]

        for i, sig in enumerate(top, 1):
            levels = _STRATEGY_LEVELS.get(
                sig.signal_type,
                {"label": sig.signal_type, "tp1": 0.03, "sl": -0.02},
            )
            strength = _score_label(sig.score)
            lines.append(
                f"{i}. *{sig.symbol}* — {strength} ({sig.score:.1f}점)\n"
                f"   {levels['label']} | 현재가: {sig.price:,.0f}원"
            )

        return "\n".join(lines)

    def generate_urgent_alert(self, signal, reason: str = "") -> str:
        """긴급 종목 알림 생성 (confidence >= 85% 등 고신뢰 신호)

        Args:
            signal: EntrySignal 객체
            reason: 긴급 전송 사유

        Returns:
            Telegram Markdown 형식의 긴급 알림 문자열
        """
        levels = _STRATEGY_LEVELS.get(
            signal.signal_type,
            {"tp1": 0.03, "tp2": 0.05, "sl": -0.02, "label": signal.signal_type},
        )
        price = signal.price
        tp1 = price * (1 + levels["tp1"])
        sl = price * (1 + levels["sl"])
        now = datetime.now().strftime("%Y-%m-%d %H:%M")

        lines = [
            f"🚨 *긴급 신호 — {signal.symbol}*",
            f"_{now}_",
            "",
        ]
        if reason:
            lines += [f"사유: {reason}", ""]
        lines += [
            f"전략: {levels['label']} | 점수: {signal.score:.1f}",
            f"현재가: {price:,.0f}원",
            f"목표가: {tp1:,.0f}원 | 손절가: {sl:,.0f}원",
            "",
            "_즉시 확인 요망_",
        ]
        return "\n".join(lines)


# 전역 인스턴스
report_generator = ReportGenerator()
