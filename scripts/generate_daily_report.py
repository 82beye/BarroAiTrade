"""BAR-OPS-19 — CSV history → 누적 markdown 리포트 변환.

사용:
    python scripts/generate_daily_report.py data/simulation_log.csv \
        --output reports/2026-05-08.md
"""
from __future__ import annotations

import argparse
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from backend.core.journal.markdown_report import (
    render_history_by_run,
    render_history_by_strategy,
)
from backend.core.journal.simulation_log import SimulationLogger


def main() -> None:
    ap = argparse.ArgumentParser(description="누적 history markdown 리포트 (BAR-OPS-19)")
    ap.add_argument("path", help="시뮬 로그 CSV 경로")
    ap.add_argument("--output", help="출력 markdown 경로 (생략 시 stdout)")
    ap.add_argument("--title", default="시뮬 누적 history",
                    help="리포트 제목")
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


if __name__ == "__main__":
    main()
