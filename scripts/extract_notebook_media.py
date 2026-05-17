#!/usr/bin/env python3
"""Extract one rich media payload from a notebook output."""

from __future__ import annotations

import argparse
import base64
import binascii
import sys
from pathlib import Path
from typing import Any

from inspect_notebook_outputs import InspectError, cell_outputs, find_cell
from notebook_to_context_md import ConversionError, data_to_text, read_notebook


BINARY_MIME_EXTENSIONS = {
    "image/png": ".png",
    "image/jpeg": ".jpg",
    "image/gif": ".gif",
    "image/webp": ".webp",
    "application/pdf": ".pdf",
}

TEXT_MIME_EXTENSIONS = {
    "image/svg+xml": ".svg",
    "text/html": ".html",
    "text/plain": ".txt",
    "application/json": ".json",
    "application/javascript": ".js",
}


class ExtractError(Exception):
    """Raised when media extraction cannot continue safely."""


def output_data(output: Any, output_index: int) -> dict[str, Any]:
    if not isinstance(output, dict):
        raise ExtractError(f"Output {output_index} must be an object")
    output_type = output.get("output_type")
    if output_type not in {"display_data", "execute_result", "update_display_data"}:
        raise ExtractError(f"Output {output_index} has no rich data payload; output_type={output_type!r}")
    data = output.get("data")
    if not isinstance(data, dict):
        raise ExtractError(f"Output {output_index} data must be an object")
    return data


def choose_mime(data: dict[str, Any], requested_mime: str | None) -> str:
    if requested_mime:
        if requested_mime not in data:
            available = ", ".join(sorted(data)) or "none"
            raise ExtractError(f"MIME {requested_mime!r} not found; available: {available}")
        return requested_mime

    for mime in (
        "image/png",
        "image/jpeg",
        "image/svg+xml",
        "image/webp",
        "image/gif",
        "application/pdf",
    ):
        if mime in data:
            return mime

    available = ", ".join(sorted(data)) or "none"
    raise ExtractError(f"No default extractable media MIME found; available: {available}; pass --mime")


def extension_for_mime(mime: str) -> str:
    if mime in BINARY_MIME_EXTENSIONS:
        return BINARY_MIME_EXTENSIONS[mime]
    if mime in TEXT_MIME_EXTENSIONS:
        return TEXT_MIME_EXTENSIONS[mime]
    subtype = mime.split("/", 1)[-1].replace("+", ".").replace("-", "_")
    return f".{subtype or 'bin'}"


def decode_base64_payload(value: Any, mime: str) -> bytes:
    text = data_to_text(value).strip()
    try:
        return base64.b64decode(text, validate=True)
    except binascii.Error as exc:
        raise ExtractError(f"MIME {mime!r} payload is not valid base64") from exc


def encode_payload(value: Any, mime: str) -> tuple[bytes, str]:
    if mime in BINARY_MIME_EXTENSIONS:
        return decode_base64_payload(value, mime), "binary"
    return data_to_text(value).encode("utf-8"), "text"


def default_output_path(notebook_path: Path, cell_selector: str, output_index: int, mime: str) -> Path:
    safe_cell = "".join(char if char.isalnum() or char in "-_" else "_" for char in cell_selector)
    return notebook_path.with_name(
        f"{notebook_path.stem}.cell-{safe_cell}.output-{output_index}{extension_for_mime(mime)}"
    )


def extract_media(
    notebook_path: Path,
    cell_selector: str,
    output_index: int,
    requested_mime: str | None,
    output_path: Path | None,
) -> tuple[Path, str, str, int]:
    notebook = read_notebook(notebook_path)
    cell_index, cell = find_cell(notebook, cell_selector)
    outputs = cell_outputs(cell, cell_index)
    if output_index < 0 or output_index >= len(outputs):
        raise ExtractError(f"No output at index {output_index}")

    data = output_data(outputs[output_index], output_index)
    mime = choose_mime(data, requested_mime)
    payload, mode = encode_payload(data[mime], mime)
    target = output_path or default_output_path(notebook_path, cell_selector, output_index, mime)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(payload)
    return target, mime, mode, len(payload)


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Extract one rich media payload from a notebook output.")
    parser.add_argument("notebook", type=Path, help="Path to the .ipynb file")
    parser.add_argument("--cell", required=True, help="Cell id or numeric cell index")
    parser.add_argument("--output", type=int, required=True, help="Output index within the selected cell")
    parser.add_argument("--mime", help="MIME type to extract, for example image/png")
    parser.add_argument("--out", type=Path, help="Output file path")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv or sys.argv[1:])

    try:
        target, mime, mode, size = extract_media(
            args.notebook,
            cell_selector=args.cell,
            output_index=args.output,
            requested_mime=args.mime,
            output_path=args.out,
        )
    except (ConversionError, InspectError, ExtractError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    except OSError as exc:
        print(f"error: could not write output file: {exc}", file=sys.stderr)
        return 1

    print(f"Wrote {target} ({mime}, {mode}, {size} bytes)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
