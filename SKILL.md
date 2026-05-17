---
name: notebook-context
description: Safely read and edit Jupyter notebooks for coding-agent workflows by projecting .ipynb files into compact Markdown views, applying Markdown edits back to the base notebook, preserving metadata/outputs/execution counts, validating notebook structure, and inspecting selected cell outputs. Use when working with .ipynb files, notebook JSON, notebook cells, notebook outputs, or requests to read, explain, modify, validate, or inspect Jupyter notebooks without loading the full JSON into context.
---

# Notebook Context

Use this skill whenever a task involves reading, explaining, editing, validating, or inspecting a Jupyter `.ipynb` file.

## Core Rule

Treat the `.ipynb` file as the authority. Use `.context.md` only as a temporary agent-facing projection. Do not hand-edit notebook JSON unless the user explicitly asks for raw JSON editing or the projection workflow fails and you explain why.

## Workflow

1. Export a compact context view:

   ```bash
   python scripts/notebook_to_context_md.py NOTEBOOK.ipynb --out NOTEBOOK.context.md
   ```

2. Read and edit `NOTEBOOK.context.md`, not the raw `.ipynb`.

3. Apply the edited context back to the base notebook:

   ```bash
   python scripts/context_md_to_notebook.py NOTEBOOK.context.md --base NOTEBOOK.ipynb --out NOTEBOOK.ipynb
   ```

4. Validate the notebook:

   ```bash
   python scripts/validate_notebook.py NOTEBOOK.ipynb
   ```

5. Report the changed files and validation result.

## Output Inspection

When the task requires viewing outputs for a specific cell, inspect only that cell:

```bash
python scripts/inspect_notebook_outputs.py NOTEBOOK.ipynb --cell CELL_ID_OR_INDEX
```

Use `--summary-only` for a compact report, `--output N` for one output, and `--include-rich-text` only when rich text payloads such as HTML/JSON/SVG are needed.

When the task requires viewing an image or other rich media output, extract only that payload:

```bash
python scripts/extract_notebook_media.py NOTEBOOK.ipynb --cell CELL_ID_OR_INDEX --output N --mime image/png --out output.png
```

## Context Size Experiment

When asked to quantify context savings, compare raw notebook JSON with the projected view:

```bash
python scripts/compare_context_size.py NOTEBOOK.ipynb
```

Use `--json` for machine-readable results. If `tiktoken` is installed, the script reports tokenizer-based counts; otherwise it reports estimated tokens as `ceil(chars / 4)`.

## Safety Rules

- Preserve notebook metadata, cell metadata, outputs, execution counts, attachments, and nbformat fields by default.
- Let `context_md_to_notebook.py` fail on drift unless you have reviewed the base notebook changes and intentionally pass `--allow-drift`.
- Missing cells in `.context.md` are not deletions. This MVP does not support adding or deleting cells.
- If export, apply, or validation fails, stop and report the exact failure instead of trying to reconstruct the notebook.
- Prefer rerunning export after external notebook edits instead of applying stale `.context.md`.

## Script Summary

- `scripts/notebook_to_context_md.py`: `.ipynb -> .context.md`; summarizes outputs and keeps cells addressable.
- `scripts/context_md_to_notebook.py`: `.context.md + base .ipynb -> .ipynb`; updates only existing cell sources.
- `scripts/validate_notebook.py`: validates basic structure and optionally `nbformat` schema when available.
- `scripts/inspect_notebook_outputs.py`: prints selected cell output summaries and safe text payloads.
- `scripts/extract_notebook_media.py`: exports one selected rich media payload such as PNG, JPEG, SVG, or PDF.
- `scripts/compare_context_size.py`: compares raw `.ipynb` JSON size against the skill `.context.md` projection.
- `scripts/generate_readme_chart.py`: regenerates the README SVG from a synthetic long-output benchmark.
