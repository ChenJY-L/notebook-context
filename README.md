# Notebook Context

[简体中文](README.zh-CN.md)

A Codex skill for working with Jupyter notebooks without loading full `.ipynb` JSON into the model context.

It converts notebooks into compact, cell-addressable Markdown projections, applies source edits back to the original notebook, preserves notebook fidelity, validates structure, and lets agents inspect outputs one cell at a time.

## Why This Exists

Jupyter notebooks often contain large outputs, base64 images, widget state, metadata, execution counts, and escaped JSON strings. That is noisy and risky for coding agents.

Notebook Context is not a replacement for Jupytext or nbconvert. It is an agent workflow:

- `.ipynb` remains the authority.
- `.context.md` is a temporary editing projection.
- Apply uses the base notebook and updates only cell `source`.
- Metadata, outputs, execution counts, attachments, and nbformat fields are preserved by default.
- Drift detection prevents stale Markdown from silently overwriting newer notebook edits.

## Context Savings

![Context size comparison for a synthetic notebook with long outputs](assets/context-size-comparison.svg)

This chart uses a synthetic analysis notebook with a long training log, a long table rendered as `text/plain` and `text/html`, and a large `image/png` output. It is intended to show the kind of savings this workflow targets, not a universal benchmark.

Reproduce the chart:

```bash
python scripts/generate_readme_chart.py
```

## Repository Layout

```text
.
├── SKILL.md
├── assets/
│   └── context-size-comparison.svg
├── scripts/
│   ├── notebook_to_context_md.py
│   ├── context_md_to_notebook.py
│   ├── validate_notebook.py
│   ├── inspect_notebook_outputs.py
│   ├── extract_notebook_media.py
│   ├── compare_context_size.py
│   └── generate_readme_chart.py
└── tests/
    ├── fixtures/
    └── test_roundtrip.py
```

## Quick Start

Export a notebook to a compact Markdown view:

```bash
python scripts/notebook_to_context_md.py analysis.ipynb --out analysis.context.md
```

Edit `analysis.context.md`, then apply it back:

```bash
python scripts/context_md_to_notebook.py analysis.context.md --base analysis.ipynb --out analysis.ipynb
```

Validate the result:

```bash
python scripts/validate_notebook.py analysis.ipynb
```

Inspect one cell's outputs:

```bash
python scripts/inspect_notebook_outputs.py analysis.ipynb --cell CELL_ID_OR_INDEX
```

Extract one image or rich media payload when you need to view it:

```bash
python scripts/extract_notebook_media.py analysis.ipynb --cell CELL_ID_OR_INDEX --output 0 --mime image/png --out output.png
```

Compare context size for direct JSON reading versus the skill projection:

```bash
python scripts/compare_context_size.py analysis.ipynb
```

## Common Options

Inline small text outputs during export:

```bash
python scripts/notebook_to_context_md.py analysis.ipynb --include-small-text-outputs
```

Inspect only one output:

```bash
python scripts/inspect_notebook_outputs.py analysis.ipynb --cell plot --output 0
```

Inspect summaries only:

```bash
python scripts/inspect_notebook_outputs.py analysis.ipynb --cell plot --summary-only
```

Export the default image/SVG/PDF-like payload from an output:

```bash
python scripts/extract_notebook_media.py analysis.ipynb --cell plot --output 0
```

Print machine-readable context-size metrics:

```bash
python scripts/compare_context_size.py analysis.ipynb --json
```

If `tiktoken` is installed, `compare_context_size.py` uses tokenizer-based counts. Otherwise it reports estimated tokens as `ceil(chars / 4)` and still reports exact bytes, characters, and lines.

## Safety Model

`context_md_to_notebook.py` checks the source hash recorded in `.context.md` against the base notebook before applying edits. If the notebook changed after export, apply fails by default.

Use `--allow-drift` only after reviewing the conflict:

```bash
python scripts/context_md_to_notebook.py analysis.context.md --base analysis.ipynb --allow-drift
```

The MVP supports modifying existing cell sources. It intentionally does not support adding or deleting cells yet.

## Install As A Codex Skill

Clone this repository into your Codex skills directory:

```bash
git clone <repo-url> ~/.codex/skills/notebook-context
```

Then invoke it explicitly:

```text
Use $notebook-context to edit analysis.ipynb.
```

## Install As A Claude Code Skill

One-liner (user scope, available to all projects):

```bash
curl -fsSL https://raw.githubusercontent.com/ChenJY-L/notebook-context/main/install.sh | bash
```

Or clone manually:

```bash
git clone https://github.com/ChenJY-L/notebook-context.git ~/.claude/skills/notebook-context
```

Override the target to install at project scope:

```bash
NOTEBOOK_CONTEXT_TARGET="$PWD/.claude/skills/notebook-context" \
  bash <(curl -fsSL https://raw.githubusercontent.com/ChenJY-L/notebook-context/main/install.sh)
```

Update later:

```bash
git -C ~/.claude/skills/notebook-context pull
```

Requires Python 3.10+ on `PATH` for the skill scripts to run (the installer warns if the version is older).

Inside Claude Code, ask it to use the skill on a notebook, for example: *"use notebook-context to edit analysis.ipynb"*.

## Test

Run the standard-library test suite:

```bash
python -m unittest discover -s tests
```

## License

MIT

## Thanks
https://linux.do