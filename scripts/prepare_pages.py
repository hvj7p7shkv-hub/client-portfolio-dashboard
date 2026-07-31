#!/usr/bin/env python3
"""Copy the rebuilt full dashboard into the GitHub Pages root."""

from __future__ import annotations

import argparse
from pathlib import Path


def add_noindex(html: str) -> str:
    if 'name="robots"' in html:
        return html
    marker = '<meta name="viewport" content="width=device-width, initial-scale=1">'
    return html.replace(marker, marker + '\n  <meta name="robots" content="noindex,nofollow">', 1)


def main() -> int:
    parser = argparse.ArgumentParser(description="Prepare GitHub Pages files.")
    parser.add_argument("--dashboard", type=Path, default=Path("build/local/index.html"))
    parser.add_argument("--output-dir", type=Path, default=Path("."))
    args = parser.parse_args()

    html = add_noindex(args.dashboard.read_text(encoding="utf-8"))
    args.output_dir.mkdir(parents=True, exist_ok=True)
    (args.output_dir / "index.html").write_text(html, encoding="utf-8")
    (args.output_dir / "404.html").write_text(html, encoding="utf-8")
    (args.output_dir / ".nojekyll").write_text("", encoding="utf-8")
    print(f"Prepared {args.output_dir / 'index.html'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
