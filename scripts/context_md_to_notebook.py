#!/usr/bin/env python3
"""Apply a notebook context Markdown projection back to a base notebook."""

from __future__ import annotations

import argparse
import html
import json
import re
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from notebook_to_context_md import ConversionError, read_notebook, source_hash, source_to_text


COMMENT_RE = re.compile(r"^\s*<!--\s*nbctx:([A-Za-z-]+)(.*?)-->\s*$")
ATTR_RE = re.compile(r"\s*([A-Za-z_][A-Za-z0-9_-]*)=(?:\"([^\"]*)\"|([^\s\"]+))")
FENCE_RE = re.compile(r"^(`{3,})([^`]*)$")


@dataclass(frozen=True)
class ContextCell:
    index: str
    cell_id: str | None
    cell_type: str
    source: str
    header_line: int


@dataclass(frozen=True)
class ContextDocument:
    meta: dict[str, str]
    cells: list[ContextCell]


class ApplyError(Exception):
    """Raised when context Markdown cannot be safely applied."""


def parse_attrs(raw: str, line_no: int) -> dict[str, str]:
    attrs: dict[str, str] = {}
    pos = 0
    while pos < len(raw):
        match = ATTR_RE.match(raw, pos)
        if not match:
            if raw[pos:].strip():
                raise ApplyError(f"Line {line_no}: could not parse attributes near {raw[pos:].strip()!r}")
            break

        key = match.group(1)
        value = match.group(2) if match.group(2) is not None else match.group(3)
        if key in attrs:
            raise ApplyError(f"Line {line_no}: duplicate attribute {key!r}")
        attrs[key] = html.unescape(value)
        pos = match.end()
    return attrs


def parse_comment(line: str) -> tuple[str, str] | None:
    match = COMMENT_RE.match(line)
    if not match:
        return None
    return match.group(1), match.group(2).strip()


def parse_bool_attr(attrs: dict[str, str], name: str, default: bool = False) -> bool:
    value = attrs.get(name)
    if value is None:
        return default
    normalized = value.lower()
    if normalized == "true":
        return True
    if normalized == "false":
        return False
    raise ApplyError(f"Attribute {name!r} must be true or false")


def join_source_lines(lines: list[str], source_ends_with_newline: bool) -> str:
    source = "\n".join(lines)
    if source_ends_with_newline:
        source += "\n"
    return source


def is_closing_fence(line: str, fence_len: int) -> bool:
    stripped = line.strip()
    return len(stripped) >= fence_len and set(stripped) == {"`"}


def skip_inline_output_block(lines: list[str], start: int, header_line: int) -> int:
    index = start + 1
    while index < len(lines):
        comment = parse_comment(lines[index])
        if comment and comment[0] == "end-output":
            return index + 1
        index += 1
    raise ApplyError(f"Line {header_line}: nbctx:output block is missing nbctx:end-output")


def validate_post_source_lines(lines: list[str], start: int, cell_type: str, header_line: int) -> None:
    index = start
    while index < len(lines):
        line = lines[index]
        if not line.strip():
            index += 1
            continue

        comment = parse_comment(line)
        if cell_type == "code" and comment:
            tag = comment[0]
            if tag == "outputs":
                index += 1
                continue
            if tag == "output":
                index = skip_inline_output_block(lines, index, header_line)
                continue

        raise ApplyError(
            f"Line {header_line}: unexpected content after {cell_type} source block; "
            "only nbctx output annotations are allowed there"
        )


def parse_fenced_source(lines: list[str], attrs: dict[str, str], cell_type: str, header_line: int) -> str:
    start = 0
    while start < len(lines) and not lines[start].strip():
        start += 1
    if start >= len(lines):
        raise ApplyError(f"Line {header_line}: {cell_type} cell is missing a fenced source block")

    opening = FENCE_RE.match(lines[start])
    if not opening:
        raise ApplyError(f"Line {header_line}: {cell_type} cell source must start with a backtick fence")

    fence_len = len(opening.group(1))
    end = start + 1
    while end < len(lines):
        if is_closing_fence(lines[end], fence_len):
            source_lines = lines[start + 1 : end]
            validate_post_source_lines(lines, end + 1, cell_type, header_line)
            return join_source_lines(
                source_lines,
                parse_bool_attr(attrs, "source_ends_with_newline", default=False),
            )
        end += 1

    raise ApplyError(f"Line {header_line}: {cell_type} cell fenced source block is not closed")


def parse_cell(block_lines: list[str], attrs: dict[str, str], header_line: int) -> ContextCell:
    raw_index = attrs.get("index")
    if raw_index is None:
        raise ApplyError(f"Line {header_line}: nbctx:cell is missing index")
    if raw_index == "new":
        raise ApplyError("New cells are not supported in this MVP; edit the base notebook first")

    cell_type = attrs.get("type")
    if cell_type not in {"code", "markdown", "raw"}:
        raise ApplyError(f"Line {header_line}: unsupported cell type {cell_type!r}")

    cell_id = attrs.get("id") or None

    if cell_type == "markdown":
        source = join_source_lines(
            block_lines,
            parse_bool_attr(attrs, "source_ends_with_newline", default=False),
        )
    else:
        source = parse_fenced_source(block_lines, attrs, cell_type, header_line)

    return ContextCell(
        index=raw_index,
        cell_id=cell_id,
        cell_type=cell_type,
        source=source,
        header_line=header_line,
    )


def parse_context_md(path: Path) -> ContextDocument:
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError as exc:
        raise ApplyError(f"Could not read {path}: {exc}") from exc

    meta: dict[str, str] | None = None
    cells: list[ContextCell] = []
    index = 0
    while index < len(lines):
        comment = parse_comment(lines[index])
        if not comment:
            index += 1
            continue

        tag, raw_attrs = comment
        line_no = index + 1
        if tag == "meta":
            if meta is not None:
                raise ApplyError(f"Line {line_no}: duplicate nbctx:meta header")
            meta = parse_attrs(raw_attrs, line_no)
            index += 1
            continue

        if tag == "delete-cell":
            raise ApplyError("Deleting cells is not supported in this MVP")

        if tag != "cell":
            index += 1
            continue

        attrs = parse_attrs(raw_attrs, line_no)
        block_start = index + 1
        index = block_start
        while index < len(lines):
            nested_comment = parse_comment(lines[index])
            if nested_comment and nested_comment[0] == "cell":
                raise ApplyError(f"Line {index + 1}: nested nbctx:cell before nbctx:end-cell")
            if nested_comment and nested_comment[0] == "end-cell":
                cells.append(parse_cell(lines[block_start:index], attrs, line_no))
                index += 1
                break
            index += 1
        else:
            raise ApplyError(f"Line {line_no}: nbctx:cell is missing nbctx:end-cell")

    return ContextDocument(meta=meta or {}, cells=cells)


def text_to_source_like(original_source: Any, new_source: str) -> Any:
    if isinstance(original_source, list):
        return new_source.splitlines(keepends=True)
    if isinstance(original_source, str):
        return new_source
    return new_source


def build_id_index(cells: list[Any]) -> dict[str, int]:
    id_to_index: dict[str, int] = {}
    for index, cell in enumerate(cells):
        if not isinstance(cell, dict):
            raise ApplyError(f"Base cell {index} must be a JSON object")
        cell_id = cell.get("id")
        if not isinstance(cell_id, str) or not cell_id:
            continue
        if cell_id in id_to_index:
            raise ApplyError(f"Base notebook has duplicate cell id {cell_id!r}")
        id_to_index[cell_id] = index
    return id_to_index


def parse_context_index(raw_index: str, header_line: int) -> int:
    try:
        index = int(raw_index)
    except ValueError as exc:
        raise ApplyError(f"Line {header_line}: cell index must be an integer") from exc
    if index < 0:
        raise ApplyError(f"Line {header_line}: cell index must be non-negative")
    return index


def match_base_cell(context_cell: ContextCell, base_cells: list[Any], id_to_index: dict[str, int]) -> int:
    if context_cell.cell_id is not None:
        if context_cell.cell_id not in id_to_index:
            raise ApplyError(
                f"Line {context_cell.header_line}: base notebook has no cell id {context_cell.cell_id!r}"
            )
        index = id_to_index[context_cell.cell_id]
    else:
        index = parse_context_index(context_cell.index, context_cell.header_line)
        if index >= len(base_cells):
            raise ApplyError(f"Line {context_cell.header_line}: base notebook has no cell at index {index}")

    base_cell = base_cells[index]
    if not isinstance(base_cell, dict):
        raise ApplyError(f"Base cell {index} must be a JSON object")
    base_type = base_cell.get("cell_type")
    if base_type != context_cell.cell_type:
        raise ApplyError(
            f"Line {context_cell.header_line}: context cell type {context_cell.cell_type!r} "
            f"does not match base cell {index} type {base_type!r}"
        )
    return index


def apply_context(context: ContextDocument, base_notebook: dict[str, Any]) -> int:
    base_cells = base_notebook.get("cells")
    if not isinstance(base_cells, list):
        raise ApplyError("Base notebook must contain a cells array")

    id_to_index = build_id_index(base_cells)
    touched: set[int] = set()
    changed = 0

    for context_cell in context.cells:
        base_index = match_base_cell(context_cell, base_cells, id_to_index)
        if base_index in touched:
            raise ApplyError(f"Line {context_cell.header_line}: duplicate edit for base cell {base_index}")
        touched.add(base_index)

        base_cell = base_cells[base_index]
        old_source = source_to_text(base_cell.get("source", ""))
        if old_source == context_cell.source:
            continue
        base_cell["source"] = text_to_source_like(base_cell.get("source", ""), context_cell.source)
        changed += 1

    return changed


def check_base_drift(context: ContextDocument, base_notebook: dict[str, Any], allow_drift: bool) -> None:
    expected_hash = context.meta.get("source_sha256")
    if allow_drift:
        return
    if not expected_hash:
        raise ApplyError("Context file has no nbctx:meta source_sha256; use --allow-drift to override")

    actual_hash = source_hash(base_notebook)
    if actual_hash != expected_hash:
        raise ApplyError(
            "Base notebook source hash does not match context metadata; "
            "regenerate the context file or use --allow-drift if you have reviewed the conflict"
        )


def validate_basic_structure(notebook: dict[str, Any]) -> None:
    cells = notebook.get("cells")
    if not isinstance(cells, list):
        raise ApplyError("Notebook must contain a cells array")
    for index, cell in enumerate(cells):
        if not isinstance(cell, dict):
            raise ApplyError(f"Cell {index} must be a JSON object")
        cell_type = cell.get("cell_type")
        if cell_type not in {"code", "markdown", "raw"}:
            raise ApplyError(f"Cell {index} has unsupported cell_type {cell_type!r}")
        source_to_text(cell.get("source", ""))
        metadata = cell.get("metadata", {})
        if not isinstance(metadata, dict):
            raise ApplyError(f"Cell {index} metadata must be an object")
        if cell_type == "code":
            outputs = cell.get("outputs", [])
            if not isinstance(outputs, list):
                raise ApplyError(f"Cell {index} outputs must be an array")
            execution_count = cell.get("execution_count")
            if execution_count is not None and not isinstance(execution_count, int):
                raise ApplyError(f"Cell {index} execution_count must be an integer or null")


def write_notebook(path: Path, notebook: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    text = json.dumps(notebook, ensure_ascii=False, indent=1)
    path.write_text(text + "\n", encoding="utf-8", newline="\n")


def default_output_path(base_path: Path) -> Path:
    return base_path


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Apply a .context.md projection back to a base .ipynb file."
    )
    parser.add_argument("context_md", type=Path, help="Path to the edited .context.md file")
    parser.add_argument("--base", type=Path, required=True, help="Base .ipynb notebook to patch")
    parser.add_argument("--out", type=Path, help="Output .ipynb path; defaults to --base")
    parser.add_argument(
        "--preserve-outputs",
        action="store_true",
        default=True,
        help="Accepted for DESIGN compatibility; outputs are preserved by default",
    )
    parser.add_argument(
        "--fail-on-drift",
        action="store_true",
        default=True,
        help="Fail if the base source hash differs from context metadata (default)",
    )
    parser.add_argument(
        "--allow-drift",
        action="store_true",
        help="Apply edits even if the base notebook source hash differs from context metadata",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv or sys.argv[1:])
    output_path = args.out or default_output_path(args.base)

    try:
        context = parse_context_md(args.context_md)
        base_notebook = read_notebook(args.base)
        check_base_drift(context, base_notebook, allow_drift=args.allow_drift)
        changed = apply_context(context, base_notebook)
        validate_basic_structure(base_notebook)
        write_notebook(output_path, base_notebook)
    except (ApplyError, ConversionError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    except OSError as exc:
        print(f"error: could not write {output_path}: {exc}", file=sys.stderr)
        return 1

    print(f"Wrote {output_path} ({changed} cell sources changed)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
