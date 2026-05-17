#!/usr/bin/env python3
"""Validate a Jupyter notebook structure without requiring a Jupyter server."""

from __future__ import annotations

import argparse
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from notebook_to_context_md import ConversionError, read_notebook, source_to_text


class ValidationError(Exception):
    """Raised when a notebook fails validation."""


@dataclass(frozen=True)
class NotebookStats:
    cells: int
    code_cells: int
    markdown_cells: int
    raw_cells: int
    outputs: int
    cells_with_metadata: int
    notebook_has_metadata: bool


def validate_output(output: Any, cell_index: int, output_index: int) -> None:
    if not isinstance(output, dict):
        raise ValidationError(f"Cell {cell_index} output {output_index} must be an object")

    output_type = output.get("output_type")
    if not isinstance(output_type, str) or not output_type:
        raise ValidationError(f"Cell {cell_index} output {output_index} is missing output_type")

    if output_type == "stream":
        if "text" in output:
            source_to_text(output["text"])
        name = output.get("name")
        if name is not None and not isinstance(name, str):
            raise ValidationError(f"Cell {cell_index} output {output_index} stream name must be a string")
        return

    if output_type in {"display_data", "execute_result", "update_display_data"}:
        data = output.get("data", {})
        metadata = output.get("metadata", {})
        if not isinstance(data, dict):
            raise ValidationError(f"Cell {cell_index} output {output_index} data must be an object")
        if not isinstance(metadata, dict):
            raise ValidationError(f"Cell {cell_index} output {output_index} metadata must be an object")
        return

    if output_type == "error":
        traceback = output.get("traceback", [])
        if not isinstance(traceback, list):
            raise ValidationError(f"Cell {cell_index} output {output_index} traceback must be an array")
        for line_index, line in enumerate(traceback):
            if not isinstance(line, str):
                raise ValidationError(
                    f"Cell {cell_index} output {output_index} traceback line {line_index} must be a string"
                )
        return

    raise ValidationError(f"Cell {cell_index} output {output_index} has unsupported output_type {output_type!r}")


def validate_notebook_structure(notebook: dict[str, Any]) -> NotebookStats:
    nbformat = notebook.get("nbformat")
    if not isinstance(nbformat, int):
        raise ValidationError("Notebook nbformat must be an integer")

    nbformat_minor = notebook.get("nbformat_minor")
    if not isinstance(nbformat_minor, int):
        raise ValidationError("Notebook nbformat_minor must be an integer")

    metadata = notebook.get("metadata", {})
    if not isinstance(metadata, dict):
        raise ValidationError("Notebook metadata must be an object")

    cells = notebook.get("cells")
    if not isinstance(cells, list):
        raise ValidationError("Notebook cells must be an array")

    seen_ids: set[str] = set()
    code_cells = 0
    markdown_cells = 0
    raw_cells = 0
    output_count = 0
    cells_with_metadata = 0

    for index, cell in enumerate(cells):
        if not isinstance(cell, dict):
            raise ValidationError(f"Cell {index} must be an object")

        cell_id = cell.get("id")
        if cell_id is not None:
            if not isinstance(cell_id, str) or not cell_id:
                raise ValidationError(f"Cell {index} id must be a non-empty string")
            if cell_id in seen_ids:
                raise ValidationError(f"Duplicate cell id {cell_id!r}")
            seen_ids.add(cell_id)

        cell_type = cell.get("cell_type")
        if cell_type not in {"code", "markdown", "raw"}:
            raise ValidationError(f"Cell {index} has unsupported cell_type {cell_type!r}")

        source_to_text(cell.get("source", ""))

        cell_metadata = cell.get("metadata", {})
        if not isinstance(cell_metadata, dict):
            raise ValidationError(f"Cell {index} metadata must be an object")
        if cell_metadata:
            cells_with_metadata += 1

        if cell_type == "code":
            code_cells += 1
            execution_count = cell.get("execution_count")
            if execution_count is not None and not isinstance(execution_count, int):
                raise ValidationError(f"Cell {index} execution_count must be an integer or null")
            outputs = cell.get("outputs", [])
            if not isinstance(outputs, list):
                raise ValidationError(f"Cell {index} outputs must be an array")
            output_count += len(outputs)
            for output_index, output in enumerate(outputs):
                validate_output(output, index, output_index)
        else:
            if "outputs" in cell:
                raise ValidationError(f"Cell {index} is {cell_type} but has outputs")
            if "execution_count" in cell:
                raise ValidationError(f"Cell {index} is {cell_type} but has execution_count")
            if cell_type == "markdown":
                markdown_cells += 1
            else:
                raw_cells += 1

    return NotebookStats(
        cells=len(cells),
        code_cells=code_cells,
        markdown_cells=markdown_cells,
        raw_cells=raw_cells,
        outputs=output_count,
        cells_with_metadata=cells_with_metadata,
        notebook_has_metadata=bool(metadata),
    )


def run_nbformat_validate(notebook: dict[str, Any]) -> str:
    try:
        import nbformat  # type: ignore[import-not-found]
    except ImportError:
        return "skipped (nbformat is not installed)"

    try:
        nbformat.validate(notebook)
    except Exception as exc:  # nbformat raises several validation exception classes.
        raise ValidationError(f"nbformat schema validation failed: {exc}") from exc
    return "passed"


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Validate a .ipynb notebook file.")
    parser.add_argument("notebook", type=Path, help="Path to the .ipynb file")
    parser.add_argument(
        "--skip-nbformat-validate",
        action="store_true",
        help="Skip optional nbformat package schema validation",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv or sys.argv[1:])

    try:
        notebook = read_notebook(args.notebook)
        stats = validate_notebook_structure(notebook)
        schema_status = "skipped"
        if not args.skip_nbformat_validate:
            schema_status = run_nbformat_validate(notebook)
    except (ConversionError, ValidationError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1

    print(f"OK: {args.notebook}")
    print(f"nbformat: {notebook.get('nbformat')}.{notebook.get('nbformat_minor')}")
    print(
        "cells: "
        f"total={stats.cells} "
        f"code={stats.code_cells} "
        f"markdown={stats.markdown_cells} "
        f"raw={stats.raw_cells}"
    )
    print(f"outputs: total={stats.outputs}")
    print(
        "metadata: "
        f"notebook={'yes' if stats.notebook_has_metadata else 'no'} "
        f"cells_with_metadata={stats.cells_with_metadata}"
    )
    print(f"schema: {schema_status}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
