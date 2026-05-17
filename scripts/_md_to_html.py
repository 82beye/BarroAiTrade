"""MD → HTML 변환 — markdown 모듈 + minimal CSS."""
import sys
from pathlib import Path

import markdown

CSS = """
body {
  font-family: -apple-system, BlinkMacSystemFont, 'Helvetica Neue', sans-serif;
  max-width: 1100px; margin: 2em auto; padding: 0 1.5em;
  color: #222; line-height: 1.6; background: #fafafa;
}
h1, h2, h3 { color: #1a3a5f; }
h1 { border-bottom: 3px solid #1a3a5f; padding-bottom: 0.3em; }
h2 { border-bottom: 1px solid #ccc; padding-bottom: 0.2em; margin-top: 2em; }
h3 { margin-top: 1.5em; }
table { border-collapse: collapse; margin: 1em 0; width: 100%; font-size: 0.92em; }
th, td { border: 1px solid #ddd; padding: 6px 10px; text-align: left; }
th { background: #1a3a5f; color: white; }
tr:nth-child(even) { background: #f0f4f8; }
td:has(+ td:last-child), td:last-child { text-align: right; font-variant-numeric: tabular-nums; }
td { font-variant-numeric: tabular-nums; }
code { background: #e8eef4; padding: 2px 6px; border-radius: 3px; font-size: 0.92em; }
blockquote {
  border-left: 4px solid #1a3a5f; padding: 0.5em 1em;
  background: #eef2f6; margin: 1em 0; color: #444;
}
strong { color: #c0392b; }
"""


def convert(src_path: str, dst_path: str) -> None:
    md_text = Path(src_path).read_text(encoding="utf-8")
    html_body = markdown.markdown(
        md_text,
        extensions=["tables", "fenced_code", "toc"],
    )
    title = Path(src_path).stem
    html = f"""<!doctype html>
<html lang="ko">
<head>
<meta charset="utf-8">
<title>{title}</title>
<style>{CSS}</style>
</head>
<body>
{html_body}
</body>
</html>
"""
    Path(dst_path).write_text(html, encoding="utf-8")
    print(f"✓ {src_path} → {dst_path}")


if __name__ == "__main__":
    if len(sys.argv) != 3:
        print("usage: _md_to_html.py <src.md> <dst.html>")
        sys.exit(1)
    convert(sys.argv[1], sys.argv[2])
