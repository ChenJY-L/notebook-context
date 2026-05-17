#!/usr/bin/env python3
"""Compare raw notebook JSON size against the notebook-context projection."""

from __future__ import annotations

import argparse
import json
import math
import sys
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Callable

from notebook_to_context_md import ConversionError, read_notebook, render_context_md


@dataclass(frozen=True)
class TextMetrics:
    chars: int
    bytes: int
    lines: int
    tokens: int


@dataclass(frozen=True)
class Comparison:
    notebook: str
    token_counter: str
    direct_ipynb_json: TextMetrics
    skill_context_md: TextMetrics
    reduction_percent: dict[str, float | None]


def count_lines(text: str) -> int:
    if text == "":
        return 0
    return len(text.splitlines())


def estimate_tokens(text: str) -> int:
    return math.ceil(len(text) / 4)


def resolve_token_counter(model: str | None) -> tuple[Callable[[str], int], str]:
    try:
        import tiktoken  # type: ignore[import-not-found]
    except ImportError:
        return estimate_tokens, "estimated tokens: ceil(chars / 4); install tiktoken for model-aware counts"

    try:
        encoding = tiktoken.encoding_for_model(model) if model else tiktoken.get_encoding("cl100k_base")
    except Exception:
        encoding = tiktoken.get_encoding("cl100k_base")
        label = f"tiktoken cl100k_base fallback; model {model!r} was not recognized"
    else:
        label = f"tiktoken {getattr(encoding, 'name', 'encoding')}"
        if model:
            label += f" for {model}"

    return lambda text: len(encoding.encode(text)), label


def metrics_for(text: str, token_counter: Callable[[str], int]) -> TextMetrics:
    return TextMetrics(
        chars=len(text),
        bytes=len(text.encode("utf-8")),
        lines=count_lines(text),
        tokens=token_counter(text),
    )


def reduction_percent(direct: int, projected: int) -> float | None:
    if direct == 0:
        return None
    return round((1 - projected / direct) * 100, 2)


def compare_notebook(
    notebook_path: Path,
    include_small_text_outputs: bool,
    max_text_output_chars: int,
    model: str | None,
) -> Comparison:
    try:
        raw_json = notebook_path.read_text(encoding="utf-8")
    except OSError as exc:
        raise ConversionError(f"Could not read {notebook_path}: {exc}") from exc

    notebook = read_notebook(notebook_path)
    context_md = render_context_md(
        notebook,
        notebook_path,
        include_small_text_outputs=include_small_text_outputs,
        max_text_output_chars=max_text_output_chars,
    )
    token_counter, counter_label = resolve_token_counter(model)
    direct = metrics_for(raw_json, token_counter)
    projected = metrics_for(context_md, token_counter)

    return Comparison(
        notebook=str(notebook_path),
        token_counter=counter_label,
        direct_ipynb_json=direct,
        skill_context_md=projected,
        reduction_percent={
            "chars": reduction_percent(direct.chars, projected.chars),
            "bytes": reduction_percent(direct.bytes, projected.bytes),
            "lines": reduction_percent(direct.lines, projected.lines),
            "tokens": reduction_percent(direct.tokens, projected.tokens),
        },
    )


def format_int(value: int) -> str:
    return f"{value:,}"


def format_percent(value: float | None) -> str:
    if value is None:
        return "n/a"
    return f"{value:.2f}%"


def render_markdown(comparison: Comparison) -> str:
    direct = comparison.direct_ipynb_json
    projected = comparison.skill_context_md
    reduction = comparison.reduction_percent

    lines = [
        f"# Context Size Comparison: {Path(comparison.notebook).name}",
        "",
        f"Token counter: {comparison.token_counter}",
        "",
        "| View | Chars | Bytes | Lines | Tokens | Reduction vs raw |",
        "| --- | ---: | ---: | ---: | ---: | ---: |",
        (
            "| Raw `.ipynb` JSON | "
            f"{format_int(direct.chars)} | "
            f"{format_int(direct.bytes)} | "
            f"{format_int(direct.lines)} | "
            f"{format_int(direct.tokens)} | "
            "baseline |"
        ),
        (
            "| Skill `.context.md` | "
            f"{format_int(projected.chars)} | "
            f"{format_int(projected.bytes)} | "
            f"{format_int(projected.lines)} | "
            f"{format_int(projected.tokens)} | "
            f"{format_percent(reduction['tokens'])} tokens |"
        ),
        "",
        "Reductions:",
        f"- chars: {format_percent(reduction['chars'])}",
        f"- bytes: {format_percent(reduction['bytes'])}",
        f"- lines: {format_percent(reduction['lines'])}",
        f"- tokens: {format_percent(reduction['tokens'])}",
    ]
    return "\n".join(lines) + "\n"


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Compare full .ipynb JSON context size with the compact .context.md projection."
    )
    parser.add_argument("notebook", type=Path, help="Path to the .ipynb file")
    parser.add_argument("--model", help="Optional tiktoken model name for token counting")
    parser.add_argument(
        "--include-small-text-outputs",
        action="store_true",
        help="Match export mode that inlines small text outputs",
    )
    parser.add_argument(
        "--max-text-output-chars",
        type=int,
        default=2000,
        help="Maximum characters to inline for each small text output",
    )
    parser.add_argument("--json", action="store_true", dest="json_output", help="Print machine-readable JSON")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv or sys.argv[1:])
    if args.max_text_output_chars < 0:
        print("error: --max-text-output-chars must be non-negative", file=sys.stderr)
        return 2

    try:
        comparison = compare_notebook(
            args.notebook,
            include_small_text_outputs=args.include_small_text_outputs,
            max_text_output_chars=args.max_text_output_chars,
            model=args.model,
        )
    except ConversionError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1

    if args.json_output:
        print(json.dumps(asdict(comparison), ensure_ascii=False, indent=2))
    else:
        print(render_markdown(comparison), end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
