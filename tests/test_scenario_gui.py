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
    assert len(dialog.plot.graphs[0].plot.listDataItems()) == 1
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


def test_multiple_graph_selection_expansion_and_reset(app):
    from PySide6.QtCore import Qt
    from fmpy.gui.scenario_results import ScenarioResults, ResultGraph
    viewer = ScenarioResults()
    rows = [{"time": 0, "speed": 0, "enabled": False, "label": "idle"},
            {"time": 1, "speed": 10, "enabled": True, "label": "run"}]
    viewer.setResult(rows, ["speed", "enabled", "label"])
    first = viewer.graphs[0]
    assert first.selectedNames() == ["speed", "enabled"]
    first.variables.item(1).setCheckState(Qt.Unchecked)
    assert len(first.plot.listDataItems()) == 1
    second = viewer.addGraph(["enabled"])
    assert second.selectedNames() == ["enabled"]
    assert first.selectedNames() == ["speed"]
    second.search.setText("speed")
    assert second.variables.item(1).isHidden()
    assert len(second.plot.listDataItems()) == 1  # filtering does not change selection
    expanded = viewer.expandGraph(first)
    app.processEvents()
    enlarged = expanded.findChild(ResultGraph)
    assert enlarged.selectedNames() == ["speed"]
    enlarged.selectAll(True)
    assert first.selectedNames() == ["speed"]
    viewer.removeGraph(second)
    assert len(viewer.graphs) == 1
    assert not first.removeButton.isEnabled()
    viewer.setResult(rows, ["label"])
    app.processEvents()
    assert not viewer.expanded
    assert not viewer.graphs
    assert not viewer.addButton.isEnabled()
    viewer.close()


def test_main_play_retains_scenario_after_dialog_close(app, reference_fmus_dist_dir, tmp_path):
    from PySide6.QtCore import QTimer
    from fmpy.gui.MainWindow import MainWindow
    window = MainWindow()
    QTimer.singleShot(0, lambda: window.scenarioDialog.reject())
    window.openScenario()
    dialog = window.scenarioDialog
    fmu = reference_fmus_dist_dir / "2.0" / "Feedthrough.fmu"
    dialog.fmuPath.setText(str(fmu))
    source = tmp_path / "main-scenario.json"
    source.write_text(json.dumps({"version": 1, "stop_time": 0.2, "step_size": 0.1,
                                 "initial": {"Float64_continuous_input": 7},
                                 "outputs": ["Float64_continuous_output"]}))
    dialog.outputPath.setText(str(tmp_path / "runs"))
    assert dialog.loadScenario(source)
    dialog.close()
    assert window.activeScenario is not None
    assert "main-scenario.json" in window.scenarioLabel.text()
    window.ui.actionSimulate.trigger()
    deadline = time.monotonic() + 20
    while dialog.worker.isRunning() and time.monotonic() < deadline:
        app.processEvents()
        time.sleep(0.01)
    assert dialog.worker.wait(1000)
    app.processEvents()
    assert dialog.result is not None, dialog.status.text()
    assert dialog.result["samples"][-1]["Float64_continuous_output"] == 7
    dialog.close()
    assert window.activeScenario is not None
    window.clearScenarioAction.trigger()
    assert window.activeScenario is None
    assert dialog.applyToMain()
    assert window.activeScenario is not None
    window.load(str(fmu))
    assert window.activeScenario is None
    source.write_text("bad json")
    assert not dialog.loadScenario(source)
    assert window.activeScenario is None
    window.close()
