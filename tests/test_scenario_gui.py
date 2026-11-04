"""Exercise real dialog loading and threaded execution with a reference FMU."""
import json
import time

import pytest

pytest.importorskip("PySide6")
from PySide6.QtWidgets import QApplication
from fmpy.gui.scenario import ScenarioDialog


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication(["test", "-platform", "offscreen"])


def test_gui_load_run_and_reject_invalid(app, reference_fmus_dist_dir, tmp_path):
    path = reference_fmus_dist_dir / "2.0" / "Feedthrough.fmu"
    scenario = {"version": 1, "stop_time": 1, "step_size": 0.1,
                "initial": {"Float64_continuous_input": 0},
                "events": [{"time": 0.2, "set": {"Float64_continuous_input": 10}}],
                "outputs": ["Float64_continuous_output"],
                "checks": [{"variable": "Float64_continuous_output", "max": 10}]}
    source = tmp_path / "scenario.json"
    source.write_text(json.dumps(scenario))
    dialog = ScenarioDialog(path)
    dialog.outputPath.setText(str(tmp_path / "results"))
    assert dialog.loadScenario(source)
    assert dialog.runButton.isEnabled()
    dialog.startRun()
    assert not dialog.loadButton.isEnabled()
    deadline = time.monotonic() + 20
    while dialog.worker.isRunning() and time.monotonic() < deadline:
        app.processEvents()
        time.sleep(0.01)
    assert dialog.worker.wait(1000)
    app.processEvents()
    assert dialog.result is not None, dialog.status.text()
    assert dialog.result["passed"] is True
    assert dialog.result["samples"][-1]["Float64_continuous_output"] == 10
    assert dialog.openResultsButton.isEnabled()
    assert len(dialog.plot.listDataItems()) == 1
    from pathlib import Path
    directory = Path(dialog.resultDirectory)
    assert (directory / "results.csv").is_file()
    assert json.loads((directory / "scenario.json").read_text()) == scenario
    assert json.loads((directory / "report.json").read_text())["passed"] is True
    scenario["outputs"] = ["nonexistent"]
    source.write_text(json.dumps(scenario))
    assert not dialog.loadScenario(source)
    assert not dialog.runButton.isEnabled()
    source.write_text("invalid JSON")
    assert not dialog.loadScenario(source)
    assert not dialog.runButton.isEnabled()
    dialog.close()


def test_cancel_cleans_up(reference_fmus_dist_dir):
    from fmpy.scenario import run_scenario
    path = reference_fmus_dist_dir / "2.0" / "Feedthrough.fmu"
    scenario = {"version": 1, "stop_time": 1, "step_size": 0.1,
                "outputs": ["Float64_continuous_output"]}
    with pytest.raises(RuntimeError, match="cancelled"):
        run_scenario(path, scenario, cancelled=lambda: True)
    assert len(run_scenario(path, scenario)["samples"]) == 11
