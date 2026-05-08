"""BAR-OPS-19/23 — CSV history → 누적 markdown 리포트 + 텔레그램 전송.

사용:
    python scripts/generate_daily_report.py data/simulation_log.csv \
        --output reports/2026-05-08.md

    # 텔레그램 자동 전송
    python scripts/generate_daily_report.py data/simulation_log.csv \
        --output reports/2026-05-08.md --telegram
"""
from __future__ import annotations

import argparse
import asyncio
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from backend.core.journal.markdown_report import (
    render_history_by_run,
    render_history_by_strategy,
)
from backend.core.journal.simulation_log import SimulationLogger
from backend.core.notify.telegram import TelegramNotifier


async def _send_telegram(md: str, title: str) -> None:
    notifier = TelegramNotifier.from_env()
    # markdown 표는 Telegram Markdown 과 충돌 — plain text 로 전송
    notifier._parse_mode = "HTML"
    text = f"<b>{title}</b>\n<pre>{md}</pre>"
    results = await notifier.send_chunks(text)
    print(f"📨 텔레그램 전송 {len(results)} chunk")


def main() -> None:
    ap = argparse.ArgumentParser(description="누적 history markdown 리포트 (BAR-OPS-19/23)")
    ap.add_argument("path", help="시뮬 로그 CSV 경로")
    ap.add_argument("--output", help="출력 markdown 경로 (생략 시 stdout)")
    ap.add_argument("--title", default="시뮬 누적 history",
                    help="리포트 제목")
    ap.add_argument("--telegram", action="store_true",
                    help="텔레그램 자동 전송 (BAR-OPS-23)")
    args = ap.parse_args()

    entries = SimulationLogger(args.path).read_all()
    if not entries:
        print(f"비어있음: {args.path}")
        return

    sections = [
        f"# {args.title}",
        f"_생성: {datetime.now(timezone.utc).isoformat(timespec='seconds')}_",
        f"_총 {len(entries)} entries / {args.path}_",
        "",
        "## 전략별 누적",
        render_history_by_strategy(entries),
        "",
        "## 실행별 시계열",
        render_history_by_run(entries),
    ]
    md = "\n".join(sections) + "\n"

    if args.output:
        out = Path(args.output)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(md, encoding="utf-8")
        print(f"📝 markdown 리포트 → {args.output} ({len(md):,} bytes)")
    else:
        print(md)

    if args.telegram:
        try:
            asyncio.run(_send_telegram(md, args.title))
        except Exception as e:
            print(f"⚠️ telegram 전송 실패: {e}")


if __name__ == "__main__":
    main()
