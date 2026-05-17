from __future__ import annotations

import json
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
FIXTURE = ROOT / "tests" / "fixtures" / "sample_notebook.ipynb"
EXPORT_SCRIPT = ROOT / "scripts" / "notebook_to_context_md.py"
APPLY_SCRIPT = ROOT / "scripts" / "context_md_to_notebook.py"
VALIDATE_SCRIPT = ROOT / "scripts" / "validate_notebook.py"
INSPECT_SCRIPT = ROOT / "scripts" / "inspect_notebook_outputs.py"
EXTRACT_SCRIPT = ROOT / "scripts" / "extract_notebook_media.py"
COMPARE_SCRIPT = ROOT / "scripts" / "compare_context_size.py"
CHART_SCRIPT = ROOT / "scripts" / "generate_readme_chart.py"


class NotebookContextTests(unittest.TestCase):
    def run_script(self, *args: str | Path, check: bool = True) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [sys.executable, *(str(arg) for arg in args)],
            cwd=ROOT,
            text=True,
            capture_output=True,
            check=check,
        )

    def load_json(self, path: Path) -> dict:
        return json.loads(path.read_text(encoding="utf-8"))

    def test_unedited_roundtrip_preserves_notebook_data(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            notebook = tmp_path / "sample.ipynb"
            context = tmp_path / "sample.context.md"
            output = tmp_path / "roundtrip.ipynb"
            shutil.copyfile(FIXTURE, notebook)

            self.run_script(EXPORT_SCRIPT, notebook, "--out", context, "--include-small-text-outputs")
            self.run_script(APPLY_SCRIPT, context, "--base", notebook, "--out", output)

            self.assertEqual(self.load_json(output), self.load_json(notebook))

    def test_apply_edits_only_cell_source(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            notebook = tmp_path / "sample.ipynb"
            context = tmp_path / "sample.context.md"
            output = tmp_path / "edited.ipynb"
            shutil.copyfile(FIXTURE, notebook)

            self.run_script(EXPORT_SCRIPT, notebook, "--out", context)
            text = context.read_text(encoding="utf-8")
            context.write_text(text.replace('print("hello")', 'print("goodbye")'), encoding="utf-8", newline="\n")
            self.run_script(APPLY_SCRIPT, context, "--base", notebook, "--out", output)

            original = self.load_json(notebook)
            edited = self.load_json(output)
            expected = json.loads(json.dumps(original))
            expected["cells"][1]["source"] = ['print("goodbye")\n']
            self.assertEqual(edited, expected)

    def test_apply_fails_on_base_drift(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            notebook = tmp_path / "sample.ipynb"
            context = tmp_path / "sample.context.md"
            drifted = tmp_path / "drifted.ipynb"
            output = tmp_path / "should_not_write.ipynb"
            shutil.copyfile(FIXTURE, notebook)

            self.run_script(EXPORT_SCRIPT, notebook, "--out", context)
            drifted_data = self.load_json(notebook)
            drifted_data["cells"][0]["source"] = ["# Changed elsewhere\n"]
            drifted.write_text(json.dumps(drifted_data, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")

            result = self.run_script(APPLY_SCRIPT, context, "--base", drifted, "--out", output, check=False)

            self.assertEqual(result.returncode, 1)
            self.assertIn("source hash does not match", result.stderr)
            self.assertFalse(output.exists())

    def test_validate_and_inspect_outputs(self) -> None:
        validate = self.run_script(VALIDATE_SCRIPT, FIXTURE)
        self.assertIn("OK:", validate.stdout)
        self.assertIn("outputs: total=2", validate.stdout)

        inspect = self.run_script(INSPECT_SCRIPT, FIXTURE, "--cell", "calc")
        self.assertIn("Cell: index=1 type=code id=calc execution_count=3", inspect.stdout)
        self.assertIn("Summary: stream stdout 6B", inspect.stdout)
        self.assertIn("image/png 4B sha256=", inspect.stdout)
        self.assertIn("hello", inspect.stdout)

    def test_extract_notebook_media_png_and_svg(self) -> None:
        one_pixel_png = (
            "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+/p9sAAAAASUVORK5CYII="
        )
        svg = '<svg xmlns="http://www.w3.org/2000/svg" width="1" height="1"><rect width="1" height="1"/></svg>'
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            notebook = tmp_path / "media.ipynb"
            png_out = tmp_path / "plot.png"
            svg_out = tmp_path / "plot.svg"
            notebook.write_text(
                json.dumps(
                    {
                        "nbformat": 4,
                        "nbformat_minor": 5,
                        "metadata": {"language_info": {"name": "python"}},
                        "cells": [
                            {
                                "cell_type": "code",
                                "id": "plot",
                                "metadata": {},
                                "execution_count": 1,
                                "source": "display(fig)\n",
                                "outputs": [
                                    {
                                        "output_type": "display_data",
                                        "data": {
                                            "image/png": one_pixel_png,
                                            "image/svg+xml": svg,
                                            "text/plain": "<Figure>",
                                        },
                                        "metadata": {},
                                    }
                                ],
                            }
                        ],
                    },
                    ensure_ascii=False,
                    indent=1,
                )
                + "\n",
                encoding="utf-8",
            )

            png = self.run_script(
                EXTRACT_SCRIPT,
                notebook,
                "--cell",
                "plot",
                "--output",
                "0",
                "--mime",
                "image/png",
                "--out",
                png_out,
            )
            self.assertIn("image/png", png.stdout)
            self.assertEqual(png_out.read_bytes()[:8], b"\x89PNG\r\n\x1a\n")

            svg_result = self.run_script(
                EXTRACT_SCRIPT,
                notebook,
                "--cell",
                "plot",
                "--output",
                "0",
                "--mime",
                "image/svg+xml",
                "--out",
                svg_out,
            )
            self.assertIn("image/svg+xml", svg_result.stdout)
            self.assertIn("<svg", svg_out.read_text(encoding="utf-8"))

            missing = self.run_script(
                EXTRACT_SCRIPT,
                notebook,
                "--cell",
                "plot",
                "--output",
                "0",
                "--mime",
                "application/pdf",
                check=False,
            )
            self.assertEqual(missing.returncode, 1)
            self.assertIn("not found", missing.stderr)

    def test_compare_context_size_json(self) -> None:
        result = self.run_script(COMPARE_SCRIPT, FIXTURE, "--json")
        data = json.loads(result.stdout)

        self.assertEqual(data["notebook"], str(FIXTURE))
        self.assertGreater(data["direct_ipynb_json"]["chars"], 0)
        self.assertGreater(data["skill_context_md"]["chars"], 0)
        self.assertIn("tokens", data["reduction_percent"])
        self.assertLess(
            data["skill_context_md"]["chars"],
            data["direct_ipynb_json"]["chars"],
        )

    def test_compare_context_size_realistic_long_outputs(self) -> None:
        table_text = "\n".join(
            " | ".join(f"{row * col / 13:.4f}" for col in range(1, 9))
            for row in range(180)
        )
        table_html = "<table>" + "".join(
            "<tr>" + "".join(f"<td>{row * col / 13:.4f}</td>" for col in range(1, 9)) + "</tr>"
            for row in range(180)
        ) + "</table>"
        long_log = "".join(f"step={step} loss={1 / (step + 1):.6f} rows={step * 128}\n" for step in range(700))

        with tempfile.TemporaryDirectory() as tmp:
            notebook = Path(tmp) / "realistic-long-output.ipynb"
            notebook.write_text(
                json.dumps(
                    {
                        "nbformat": 4,
                        "nbformat_minor": 5,
                        "metadata": {"language_info": {"name": "python"}},
                        "cells": [
                            {
                                "cell_type": "code",
                                "id": "training-log",
                                "metadata": {},
                                "execution_count": 1,
                                "source": "run_training()\n",
                                "outputs": [
                                    {
                                        "output_type": "stream",
                                        "name": "stdout",
                                        "text": long_log,
                                    }
                                ],
                            },
                            {
                                "cell_type": "code",
                                "id": "metrics-table",
                                "metadata": {},
                                "execution_count": 2,
                                "source": "metrics_df.describe()\n",
                                "outputs": [
                                    {
                                        "output_type": "execute_result",
                                        "execution_count": 2,
                                        "data": {"text/plain": table_text, "text/html": table_html},
                                        "metadata": {},
                                    }
                                ],
                            },
                            {
                                "cell_type": "code",
                                "id": "plot",
                                "metadata": {},
                                "execution_count": 3,
                                "source": "plot_metrics()\n",
                                "outputs": [
                                    {
                                        "output_type": "display_data",
                                        "data": {"image/png": "a" * 100_000, "text/plain": "<Figure size 1000x600>"},
                                        "metadata": {},
                                    }
                                ],
                            }
                        ],
                    },
                    ensure_ascii=False,
                    indent=1,
                )
                + "\n",
                encoding="utf-8",
            )

            result = self.run_script(COMPARE_SCRIPT, notebook, "--json")
            data = json.loads(result.stdout)

            self.assertLess(data["skill_context_md"]["chars"], data["direct_ipynb_json"]["chars"] / 10)
            self.assertGreater(data["reduction_percent"]["chars"], 90)

    def test_generate_readme_chart(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "chart.svg"

            result = self.run_script(CHART_SCRIPT, "--out", out, "--json")
            data = json.loads(result.stdout)

            self.assertTrue(out.exists())
            svg = out.read_text(encoding="utf-8")
            self.assertIn("<svg", svg)
            self.assertIn("long table, long log, and image output", svg)
            self.assertGreater(data["reduction_percent"]["tokens"], 90)


if __name__ == "__main__":
    unittest.main()
