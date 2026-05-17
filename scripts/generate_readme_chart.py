#!/usr/bin/env python3
"""Generate the README context-size comparison SVG from a synthetic notebook."""

from __future__ import annotations

import argparse
import html
import json
import tempfile
from dataclasses import asdict
from pathlib import Path

from compare_context_size import compare_notebook, format_int, format_percent


def make_table_text(rows: int = 160, cols: int = 8) -> str:
    headers = [f"metric_{col}" for col in range(cols)]
    lines = [" | ".join(headers)]
    lines.append("-+-".join("-" * len(header) for header in headers))
    for row in range(rows):
        values = [f"{(row + 1) * (col + 3) / 17:.4f}" for col in range(cols)]
        lines.append(" | ".join(values))
    return "\n".join(lines) + "\n"


def make_table_html(rows: int = 160, cols: int = 8) -> str:
    header = "".join(f"<th>metric_{col}</th>" for col in range(cols))
    body_rows = []
    for row in range(rows):
        cells = "".join(f"<td>{(row + 1) * (col + 3) / 17:.4f}</td>" for col in range(cols))
        body_rows.append(f"<tr>{cells}</tr>")
    return "<table><thead><tr>" + header + "</tr></thead><tbody>" + "".join(body_rows) + "</tbody></table>"


def make_log_output(lines: int = 650) -> str:
    return "".join(
        f"[2026-05-17 10:{minute % 60:02d}:{second % 60:02d}] fold={minute % 5} "
        f"loss={1 / (minute + 1):.6f} rows={50000 + minute * 137} status=ok\n"
        for minute, second in ((line, line * 7) for line in range(lines))
    )


def make_synthetic_notebook() -> dict:
    return {
        "nbformat": 4,
        "nbformat_minor": 5,
        "metadata": {
            "kernelspec": {
                "display_name": "Python 3",
                "language": "python",
                "name": "python3",
            },
            "language_info": {"name": "python"},
            "benchmark": "synthetic long table, long log, and image output",
        },
        "cells": [
            {
                "cell_type": "markdown",
                "id": "intro",
                "metadata": {},
                "source": "# Synthetic Analysis Notebook\n\nLong outputs are intentionally included.\n",
            },
            {
                "cell_type": "code",
                "id": "imports",
                "metadata": {},
                "execution_count": 1,
                "source": "import pandas as pd\nimport matplotlib.pyplot as plt\n",
                "outputs": [],
            },
            {
                "cell_type": "code",
                "id": "training-log",
                "metadata": {},
                "execution_count": 2,
                "source": "run_training_pipeline()\n",
                "outputs": [
                    {
                        "output_type": "stream",
                        "name": "stdout",
                        "text": make_log_output(),
                    }
                ],
            },
            {
                "cell_type": "code",
                "id": "long-table",
                "metadata": {},
                "execution_count": 3,
                "source": "metrics_df.describe()\n",
                "outputs": [
                    {
                        "output_type": "execute_result",
                        "execution_count": 3,
                        "data": {
                            "text/plain": make_table_text(),
                            "text/html": make_table_html(),
                        },
                        "metadata": {},
                    }
                ],
            },
            {
                "cell_type": "code",
                "id": "plot",
                "metadata": {},
                "execution_count": 4,
                "source": "plot_feature_importance(model)\n",
                "outputs": [
                    {
                        "output_type": "display_data",
                        "data": {
                            "image/png": "a" * 140_000,
                            "text/plain": "<Figure size 1000x600>",
                        },
                        "metadata": {},
                    }
                ],
            },
        ],
    }


def bar(width: int, x: int, y: int, fill: str) -> str:
    return f'<rect x="{x}" y="{y}" width="{width}" height="34" rx="6" fill="{fill}" />'


def text(
    x: int,
    y: int,
    content: str,
    size: int = 16,
    weight: str = "400",
    fill: str = "#111827",
    anchor: str = "start",
) -> str:
    return (
        f'<text x="{x}" y="{y}" font-family="Inter, Segoe UI, Arial, sans-serif" '
        f'font-size="{size}" font-weight="{weight}" fill="{fill}" text-anchor="{anchor}">'
        f"{html.escape(content)}</text>"
    )


def render_svg(comparison) -> str:
    direct = comparison.direct_ipynb_json
    projected = comparison.skill_context_md
    reduction = comparison.reduction_percent["tokens"]

    max_bar = 620
    raw_bar = max_bar
    projected_bar = max(8, round(max_bar * projected.tokens / direct.tokens))

    raw_label = f"{format_int(direct.tokens)} estimated tokens"
    projected_label = f"{format_int(projected.tokens)} estimated tokens"
    reduction_label = f"{format_percent(reduction)} fewer estimated tokens"

    return "\n".join(
        [
            '<svg xmlns="http://www.w3.org/2000/svg" width="960" height="430" viewBox="0 0 960 430" role="img" aria-labelledby="title desc">',
            "<title id=\"title\">Notebook Context Size Comparison</title>",
            (
                "<desc id=\"desc\">Synthetic notebook benchmark comparing direct ipynb JSON "
                "against notebook-context Markdown projection.</desc>"
            ),
            '<rect width="960" height="430" fill="#F8FAFC" />',
            '<rect x="32" y="28" width="896" height="374" rx="8" fill="#FFFFFF" stroke="#CBD5E1" />',
            text(64, 72, "Context Size Comparison", 28, "700"),
            text(64, 104, "Synthetic notebook: long table, long log, and image output", 15, "400", "#475569"),
            text(64, 145, "Raw .ipynb JSON", 16, "700"),
            bar(raw_bar, 246, 122, "#DC2626"),
            text(246 + raw_bar - 12, 145, raw_label, 14, "700", "#FFFFFF", anchor="end"),
            text(64, 207, "Skill .context.md", 16, "700"),
            bar(projected_bar, 246, 184, "#2563EB"),
            text(246 + projected_bar + 12, 207, projected_label, 14, "700", "#1E3A8A"),
            '<line x1="246" y1="248" x2="866" y2="248" stroke="#E2E8F0" stroke-width="1" />',
            text(64, 292, reduction_label, 34, "800", "#047857"),
            text(
                64,
                324,
                (
                    f"Raw: {format_int(direct.chars)} chars / {format_int(direct.bytes)} bytes. "
                    f"Projection: {format_int(projected.chars)} chars / {format_int(projected.bytes)} bytes."
                ),
                14,
                "400",
                "#475569",
            ),
            text(
                64,
                350,
                "Token count uses the repository experiment default: ceil(chars / 4). Install tiktoken for model-aware counts.",
                13,
                "400",
                "#64748B",
            ),
            text(
                64,
                374,
                "Regenerate with: python scripts/generate_readme_chart.py",
                13,
                "400",
                "#64748B",
            ),
            "</svg>",
        ]
    )


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Generate assets/context-size-comparison.svg")
    parser.add_argument("--out", type=Path, default=Path("assets/context-size-comparison.svg"))
    parser.add_argument("--model", help="Optional tiktoken model name for token counting")
    parser.add_argument("--notebook-out", type=Path, help="Optional path to write the synthetic notebook")
    parser.add_argument("--json", action="store_true", dest="json_output", help="Print benchmark metrics as JSON")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    notebook = make_synthetic_notebook()

    with tempfile.TemporaryDirectory() as tmp:
        notebook_path = args.notebook_out or Path(tmp) / "synthetic-analysis.ipynb"
        notebook_path.parent.mkdir(parents=True, exist_ok=True)
        notebook_path.write_text(json.dumps(notebook, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
        comparison = compare_notebook(
            notebook_path,
            include_small_text_outputs=False,
            max_text_output_chars=2000,
            model=args.model,
        )

    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(render_svg(comparison), encoding="utf-8", newline="\n")

    if args.json_output:
        print(json.dumps(asdict(comparison), ensure_ascii=False, indent=2))
    else:
        print(f"Wrote {args.out}")
        print(f"Raw tokens: {format_int(comparison.direct_ipynb_json.tokens)}")
        print(f"Projected tokens: {format_int(comparison.skill_context_md.tokens)}")
        print(f"Token reduction: {format_percent(comparison.reduction_percent['tokens'])}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
