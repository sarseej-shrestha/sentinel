import json
import subprocess
import sys

import nbformat


def test_spike_measures_real_calls_and_failures(tmp_path):
    subprocess.run(
        [
            sys.executable,
            "scripts/run_technical_spike.py",
            "--repeats",
            "1",
            "--output",
            str(tmp_path),
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    report = json.loads((tmp_path / "report.json").read_text())
    assert report["summary"]["calls"] == 12
    assert report["summary"]["expected_behavior_matches"] == 12
    assert report["summary"]["latency_ms"]["p95"] is None
    assert report["summary"]["unsafe_query_blocking"]["numerator"] == 2
    assert report["audit"]["events_verified"] == 17
    assert not report["human_gate_test"]["external_action_executed"]
    assert all(r["latency_ms"] > 0 and r["retrieved_schema"] for r in report["calls"])


def test_notebook_is_valid_reproducible_and_output_free():
    notebook = nbformat.read("notebooks/technical_spike.ipynb", as_version=4)
    nbformat.validate(notebook)
    for cell in notebook.cells:
        if cell.cell_type == "code":
            compile(cell.source, "technical_spike.ipynb", "exec")
            assert not cell.outputs
