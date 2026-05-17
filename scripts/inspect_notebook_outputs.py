#!/usr/bin/env python3
"""Inspect outputs for one notebook cell without expanding the full notebook."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Any

from notebook_to_context_md import (
    ConversionError,
    data_to_text,
    read_notebook,
    source_to_text,
    summarize_data_item,
    summarize_output,
)


TEXTLIKE_RICH_MIMES = {
    "application/json",
    "application/javascript",
    "application/vnd.plotly.v1+json",
    "image/svg+xml",
}


class InspectError(Exception):
    """Raised when output inspection cannot continue safely."""


def truncate_text(text: str, max_chars: int) -> str:
    if len(text) <= max_chars:
        return text
    return text[:max_chars] + "\n...[truncated]"


def fenced_text(text: str, info: str = "text") -> list[str]:
    fence = "`" * max(3, longest_backtick_run(text) + 1)
    lines = [f"{fence}{info}"]
    if text:
        split = text.split("\n")
        if split and split[-1] == "":
            split.pop()
        lines.extend(split)
    lines.append(fence)
    return lines


def longest_backtick_run(text: str) -> int:
    longest = 0
    current = 0
    for char in text:
        if char == "`":
            current += 1
            longest = max(longest, current)
        else:
            current = 0
    return longest


def cell_label(index: int, cell: dict[str, Any]) -> str:
    parts = [f"index={index}", f"type={cell.get('cell_type')}"]
    cell_id = cell.get("id")
    if isinstance(cell_id, str) and cell_id:
        parts.append(f"id={cell_id}")
    if cell.get("cell_type") == "code":
        parts.append(f"execution_count={cell.get('execution_count')}")
    return " ".join(parts)


def find_cell(notebook: dict[str, Any], selector: str) -> tuple[int, dict[str, Any]]:
    cells = notebook.get("cells")
    if not isinstance(cells, list):
        raise InspectError("Notebook must contain a cells array")

    matches: list[tuple[int, dict[str, Any]]] = []
    for index, cell in enumerate(cells):
        if not isinstance(cell, dict):
            raise InspectError(f"Cell {index} must be a JSON object")
        if cell.get("id") == selector:
            matches.append((index, cell))

    if len(matches) == 1:
        return matches[0]
    if len(matches) > 1:
        raise InspectError(f"Multiple cells have id {selector!r}")

    try:
        index = int(selector)
    except ValueError as exc:
        raise InspectError(f"No cell id {selector!r}; use an existing id or numeric index") from exc

    if index < 0 or index >= len(cells):
        raise InspectError(f"No cell at index {index}")
    cell = cells[index]
    if not isinstance(cell, dict):
        raise InspectError(f"Cell {index} must be a JSON object")
    return index, cell


def cell_outputs(cell: dict[str, Any], index: int) -> list[Any]:
    cell_type = cell.get("cell_type")
    if cell_type != "code":
        raise InspectError(f"Cell {index} is {cell_type!r}; only code cells can have outputs")
    outputs = cell.get("outputs", [])
    if not isinstance(outputs, list):
        raise InspectError(f"Cell {index} outputs must be an array")
    return outputs


def select_outputs(outputs: list[Any], output_index: int | None) -> list[tuple[int, Any]]:
    if output_index is None:
        return list(enumerate(outputs))
    if output_index < 0 or output_index >= len(outputs):
        raise InspectError(f"No output at index {output_index}")
    return [(output_index, outputs[output_index])]


def should_render_mime_text(mime: str, include_rich_text: bool) -> bool:
    if mime == "text/plain":
        return True
    if not include_rich_text:
        return False
    return mime.startswith("text/") or mime in TEXTLIKE_RICH_MIMES


def render_stream(output: dict[str, Any], max_chars: int, summary_only: bool) -> list[str]:
    name = output.get("name", "stream")
    lines = [f"- stream: {name}"]
    if summary_only:
        return lines
    text = truncate_text(data_to_text(output.get("text", "")), max_chars)
    lines.extend(fenced_text(text))
    return lines


def render_error(output: dict[str, Any], max_chars: int, summary_only: bool) -> list[str]:
    ename = output.get("ename", "Error")
    evalue = output.get("evalue", "")
    lines = [f"- error: {ename}: {evalue}"]
    if summary_only:
        return lines

    traceback = output.get("traceback", [])
    if isinstance(traceback, list) and all(isinstance(line, str) for line in traceback):
        text = "\n".join(traceback)
    else:
        text = data_to_text(traceback)
    text = truncate_text(text, max_chars)
    if text:
        lines.extend(fenced_text(text))
    return lines


def render_data_output(
    output: dict[str, Any],
    max_chars: int,
    include_rich_text: bool,
    summary_only: bool,
) -> list[str]:
    data = output.get("data", {})
    if not isinstance(data, dict):
        return ["- data: invalid data object"]

    lines = ["- data:"]
    for mime in sorted(data):
        value = data[mime]
        lines.append(f"  - {summarize_data_item(mime, value)}")
        if summary_only or not should_render_mime_text(mime, include_rich_text):
            continue
        text = truncate_text(data_to_text(value), max_chars)
        info = "json" if mime.endswith("json") else "text"
        lines.extend(fenced_text(text, info=info))
    return lines


def render_output(
    output: Any,
    max_chars: int,
    include_rich_text: bool,
    summary_only: bool,
) -> list[str]:
    if not isinstance(output, dict):
        return ["- invalid output: expected object"]

    output_type = output.get("output_type")
    if output_type == "stream":
        return render_stream(output, max_chars, summary_only)
    if output_type in {"display_data", "execute_result", "update_display_data"}:
        return render_data_output(output, max_chars, include_rich_text, summary_only)
    if output_type == "error":
        return render_error(output, max_chars, summary_only)
    return [f"- unsupported output_type: {output_type!r}"]


def render_report(
    notebook_path: Path,
    cell_index: int,
    cell: dict[str, Any],
    selected_outputs: list[tuple[int, Any]],
    total_outputs: int,
    max_chars: int,
    include_rich_text: bool,
    summary_only: bool,
) -> str:
    lines = [
        f"# Notebook Outputs: {notebook_path.name}",
        "",
        f"Cell: {cell_label(cell_index, cell)}",
        f"Outputs: selected={len(selected_outputs)} total={total_outputs}",
    ]

    source = source_to_text(cell.get("source", ""))
    first_line = source.splitlines()[0] if source.splitlines() else ""
    if first_line:
        lines.append(f"Source first line: {first_line[:120]}")

    for output_index, output in selected_outputs:
        summary = summarize_output(output) if isinstance(output, dict) else "invalid output"
        lines.extend(["", f"## Output {output_index}", f"Summary: {summary}"])
        lines.extend(render_output(output, max_chars, include_rich_text, summary_only))

    return "\n".join(lines).rstrip() + "\n"


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Inspect outputs for one notebook cell by id or index."
    )
    parser.add_argument("notebook", type=Path, help="Path to the .ipynb file")
    parser.add_argument("--cell", required=True, help="Cell id or numeric cell index")
    parser.add_argument("--output", type=int, help="Inspect only one output index")
    parser.add_argument(
        "--max-chars",
        type=int,
        default=4000,
        help="Maximum characters to print for each rendered text payload",
    )
    parser.add_argument(
        "--summary-only",
        action="store_true",
        help="Print output summaries without rendering text payloads",
    )
    parser.add_argument(
        "--include-rich-text",
        action="store_true",
        help="Also render text/html, JSON, SVG, and other text-like rich MIME payloads",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv or sys.argv[1:])
    if args.max_chars < 0:
        print("error: --max-chars must be non-negative", file=sys.stderr)
        return 2

    try:
        notebook = read_notebook(args.notebook)
        cell_index, cell = find_cell(notebook, args.cell)
        outputs = cell_outputs(cell, cell_index)
        selected_outputs = select_outputs(outputs, args.output)
        report = render_report(
            args.notebook,
            cell_index,
            cell,
            selected_outputs,
            total_outputs=len(outputs),
            max_chars=args.max_chars,
            include_rich_text=args.include_rich_text,
            summary_only=args.summary_only,
        )
    except (ConversionError, InspectError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1

    print(report, end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
