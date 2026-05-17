#!/usr/bin/env python3
"""Convert a Jupyter notebook into a compact Markdown context view."""

from __future__ import annotations

import argparse
import hashlib
import html
import json
import re
import sys
from pathlib import Path
from typing import Any


TOOL_NAME = "notebook-context-skill"
DANGEROUS_MARKERS = (
    "<!-- nbctx:cell",
    "<!-- nbctx:end-cell",
    "<!-- nbctx:outputs",
    "<!-- nbctx:output",
)


class ConversionError(Exception):
    """Raised when a notebook cannot be projected safely."""


def read_notebook(path: Path) -> dict[str, Any]:
    try:
        with path.open("r", encoding="utf-8") as f:
            notebook = json.load(f)
    except json.JSONDecodeError as exc:
        raise ConversionError(f"{path} is not valid JSON: {exc}") from exc
    except OSError as exc:
        raise ConversionError(f"Could not read {path}: {exc}") from exc

    if not isinstance(notebook, dict):
        raise ConversionError("Notebook root must be a JSON object")
    cells = notebook.get("cells")
    if not isinstance(cells, list):
        raise ConversionError("Notebook must contain a cells array")
    return notebook


def source_to_text(source: Any) -> str:
    if source is None:
        return ""
    if isinstance(source, str):
        return source
    if isinstance(source, list) and all(isinstance(part, str) for part in source):
        return "".join(source)
    raise ConversionError("Cell source must be a string or a list of strings")


def data_to_text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    if isinstance(value, list) and all(isinstance(part, str) for part in value):
        return "".join(value)
    return json.dumps(value, ensure_ascii=False, sort_keys=True)


def data_to_bytes(value: Any) -> bytes:
    return data_to_text(value).encode("utf-8")


def attr(value: Any) -> str:
    return html.escape(str(value), quote=True)


def hash_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def hash_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def source_hash(notebook: dict[str, Any]) -> str:
    cells_view: list[dict[str, Any]] = []
    for index, cell in enumerate(notebook.get("cells", [])):
        if not isinstance(cell, dict):
            raise ConversionError(f"Cell {index} must be a JSON object")
        cells_view.append(
            {
                "index": index,
                "id": cell.get("id"),
                "cell_type": cell.get("cell_type"),
                "source": source_to_text(cell.get("source", "")),
            }
        )
    payload = json.dumps(cells_view, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hash_text(payload)


def language_name(notebook: dict[str, Any]) -> str:
    metadata = notebook.get("metadata")
    if not isinstance(metadata, dict):
        return "python"

    language_info = metadata.get("language_info")
    if isinstance(language_info, dict):
        name = language_info.get("name")
        if isinstance(name, str) and name.strip():
            return sanitize_fence_info(name)

    kernelspec = metadata.get("kernelspec")
    if isinstance(kernelspec, dict):
        language = kernelspec.get("language")
        if isinstance(language, str) and language.strip():
            return sanitize_fence_info(language)

    return "python"


def sanitize_fence_info(value: str) -> str:
    cleaned = value.strip().split()[0]
    cleaned = re.sub(r"[^A-Za-z0-9_+.#-]", "", cleaned)
    return cleaned or "text"


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


def fence_for(text: str) -> str:
    return "`" * max(3, longest_backtick_run(text) + 1)


def lines_for_join(text: str) -> list[str]:
    if text == "":
        return []
    lines = text.split("\n")
    if lines and lines[-1] == "":
        lines.pop()
    return lines


def ensure_safe_source(source: str, cell_index: int) -> None:
    for marker in DANGEROUS_MARKERS:
        if marker in source:
            raise ConversionError(
                f"Cell {cell_index} source contains reserved marker {marker!r}; "
                "refusing to generate an ambiguous context file"
            )


def human_size(num_bytes: int) -> str:
    if num_bytes < 1024:
        return f"{num_bytes}B"
    value = float(num_bytes)
    for unit in ("KB", "MB", "GB"):
        value /= 1024.0
        if value < 1024.0:
            return f"{value:.1f}{unit}"
    return f"{value:.1f}TB"


def output_data_items(output: dict[str, Any]) -> list[tuple[str, Any]]:
    data = output.get("data")
    if not isinstance(data, dict):
        return []
    return [(mime, data[mime]) for mime in sorted(data)]


def summarize_data_item(mime: str, value: Any) -> str:
    raw = data_to_bytes(value)
    summary = f"{mime} {human_size(len(raw))}"
    if not mime.startswith("text/"):
        summary += f" sha256={hash_bytes(raw)[:16]}"
    return summary


def summarize_output(output: dict[str, Any]) -> str:
    output_type = output.get("output_type")
    if output_type == "stream":
        name = output.get("name", "stream")
        text = data_to_text(output.get("text", ""))
        return f"stream {name} {human_size(len(text.encode('utf-8')))}"

    if output_type in {"display_data", "execute_result", "update_display_data"}:
        items = output_data_items(output)
        if not items:
            return f"{output_type} empty"
        return f"{output_type} " + ", ".join(summarize_data_item(mime, value) for mime, value in items)

    if output_type == "error":
        ename = output.get("ename", "Error")
        evalue = output.get("evalue", "")
        traceback = output.get("traceback", [])
        traceback_text = data_to_text(traceback)
        return f"error {ename}: {evalue} traceback {human_size(len(traceback_text.encode('utf-8')))}"

    return str(output_type or "unknown")


def output_summary(outputs: list[Any]) -> str:
    if not outputs:
        return "0 outputs"

    parts: list[str] = []
    for output in outputs:
        if isinstance(output, dict):
            parts.append(summarize_output(output))
        else:
            parts.append("invalid-output")

    label = "output" if len(outputs) == 1 else "outputs"
    return f"{len(outputs)} {label}: " + "; ".join(parts)


def inlineable_text(output: dict[str, Any]) -> str | None:
    output_type = output.get("output_type")
    if output_type == "stream":
        return data_to_text(output.get("text", ""))

    if output_type in {"display_data", "execute_result", "update_display_data"}:
        data = output.get("data")
        if isinstance(data, dict) and "text/plain" in data:
            return data_to_text(data["text/plain"])

    if output_type == "error":
        ename = output.get("ename", "Error")
        evalue = output.get("evalue", "")
        return f"{ename}: {evalue}"

    return None


def render_inline_outputs(outputs: list[Any], max_chars: int) -> list[str]:
    lines: list[str] = []
    for index, output in enumerate(outputs):
        if not isinstance(output, dict):
            continue
        text = inlineable_text(output)
        if text is None:
            continue
        if len(text) > max_chars:
            text = text[:max_chars] + "\n...[truncated]"
        fence = fence_for(text)
        output_type = output.get("output_type", "unknown")
        lines.append(f'<!-- nbctx:output index={index} type="{attr(output_type)}" format="text" -->')
        lines.append(f"{fence}text")
        lines.extend(lines_for_join(text))
        lines.append(fence)
        lines.append("<!-- nbctx:end-output -->")
    return lines


def outputs_mode(outputs: list[Any], include_small_text_outputs: bool) -> str:
    if not outputs:
        return "none"
    if include_small_text_outputs and any(isinstance(output, dict) and inlineable_text(output) is not None for output in outputs):
        return "inline"
    return "summary"


def render_cell(
    cell: dict[str, Any],
    index: int,
    notebook_language: str,
    include_small_text_outputs: bool,
    max_text_output_chars: int,
) -> list[str]:
    cell_type = cell.get("cell_type")
    if cell_type not in {"code", "markdown", "raw"}:
        raise ConversionError(f"Cell {index} has unsupported cell_type {cell_type!r}")

    source = source_to_text(cell.get("source", ""))
    ensure_safe_source(source, index)
    source_ends_with_newline = str(source.endswith("\n")).lower()

    attrs = [f"index={index}"]
    cell_id = cell.get("id")
    if isinstance(cell_id, str) and cell_id:
        attrs.append(f'id="{attr(cell_id)}"')
    attrs.append(f'type="{attr(cell_type)}"')
    attrs.append(f"source_ends_with_newline={source_ends_with_newline}")

    outputs: list[Any] = []
    if cell_type == "code":
        raw_outputs = cell.get("outputs", [])
        if not isinstance(raw_outputs, list):
            raise ConversionError(f"Cell {index} outputs must be an array")
        outputs = raw_outputs
        execution_count = cell.get("execution_count")
        attrs.append(f"execution_count={json.dumps(execution_count)}")
        attrs.append(f'outputs="{outputs_mode(outputs, include_small_text_outputs)}"')

    lines = [f"<!-- nbctx:cell {' '.join(attrs)} -->"]

    if cell_type == "markdown":
        lines.extend(lines_for_join(source))
    else:
        info = notebook_language if cell_type == "code" else "text"
        fence = fence_for(source)
        lines.append(f"{fence}{info}")
        lines.extend(lines_for_join(source))
        lines.append(fence)

    if cell_type == "code":
        lines.append("")
        lines.append(f'<!-- nbctx:outputs summary="{attr(output_summary(outputs))}" -->')
        if include_small_text_outputs:
            inline_lines = render_inline_outputs(outputs, max_text_output_chars)
            if inline_lines:
                lines.append("")
                lines.extend(inline_lines)

    lines.append("<!-- nbctx:end-cell -->")
    return lines


def render_context_md(
    notebook: dict[str, Any],
    notebook_path: Path,
    include_small_text_outputs: bool,
    max_text_output_chars: int,
) -> str:
    cells = notebook.get("cells", [])
    nbformat = notebook.get("nbformat")
    nbformat_minor = notebook.get("nbformat_minor")
    notebook_language = language_name(notebook)

    lines = [
        f"# Notebook Context: {notebook_path.name}",
        "",
        (
            "<!-- nbctx:meta "
            f"nbformat={attr(nbformat)} "
            f"nbformat_minor={attr(nbformat_minor)} "
            f"cells={len(cells)} "
            f'source_sha256="{source_hash(notebook)}" '
            f'created_by="{TOOL_NAME}" '
            "-->"
        ),
        "",
    ]

    for index, cell in enumerate(cells):
        if not isinstance(cell, dict):
            raise ConversionError(f"Cell {index} must be a JSON object")
        lines.extend(
            render_cell(
                cell,
                index,
                notebook_language,
                include_small_text_outputs,
                max_text_output_chars,
            )
        )
        lines.append("")

    return "\n".join(lines).rstrip() + "\n"


def default_output_path(notebook_path: Path) -> Path:
    if notebook_path.suffix == ".ipynb":
        return notebook_path.with_suffix(".context.md")
    return notebook_path.with_name(notebook_path.name + ".context.md")


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Convert a .ipynb file into a compact .context.md projection."
    )
    parser.add_argument("notebook", type=Path, help="Path to the source .ipynb file")
    parser.add_argument("--out", type=Path, help="Output .context.md path")
    parser.add_argument(
        "--max-text-output-chars",
        type=int,
        default=2000,
        help="Maximum characters to inline for each small text output",
    )
    parser.add_argument(
        "--include-small-text-outputs",
        action="store_true",
        help="Inline stream, text/plain, and error summary outputs up to the size limit",
    )
    parser.add_argument(
        "--omit-binary-outputs",
        action="store_true",
        help="Accepted for DESIGN compatibility; binary outputs are summarized by default",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv or sys.argv[1:])
    if args.max_text_output_chars < 0:
        print("--max-text-output-chars must be non-negative", file=sys.stderr)
        return 2

    notebook_path = args.notebook
    output_path = args.out or default_output_path(notebook_path)

    try:
        notebook = read_notebook(notebook_path)
        context_md = render_context_md(
            notebook,
            notebook_path,
            include_small_text_outputs=args.include_small_text_outputs,
            max_text_output_chars=args.max_text_output_chars,
        )
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(context_md, encoding="utf-8", newline="\n")
    except ConversionError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    except OSError as exc:
        print(f"error: could not write {output_path}: {exc}", file=sys.stderr)
        return 1

    print(f"Wrote {output_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
