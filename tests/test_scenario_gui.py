"""Exercise real dialog loading and threaded execution with a reference FMU."""
import json
import time

import pytest

pytest.importorskip("PySide6")
from PySide6.QtCore import Qt
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


def test_main_play_retains_scenario_after_dialog_close(app, reference_fmus_dist_dir, tmp_path, monkeypatch):
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
    alternative = tmp_path / "alternative.json"
    alternative.write_text(json.dumps({"version": 1, "stop_time": 0.2, "step_size": 0.1,
                                      "initial": {"Float64_continuous_input": 12},
                                      "outputs": ["Float64_continuous_output"]}))
    assert dialog.loadScenario(alternative)
    assert "alternative.json" in window.scenarioLabel.text()
    dialog.scenarioTree.setCurrentItem(dialog.entries[str(source.resolve())]["item"])
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
    assert window.result["Float64_continuous_output"][-1] == 7
    assert window.ui.stackedWidget.currentWidget() == window.ui.resultPage
    assert window.ui.actionSaveResult.isEnabled()
    assert not dialog.isVisible()
    assert (window.unzipdir / "plot.html").is_file()
    variable = next(v for v in window.modelDescription.modelVariables
                    if v.name == "Float64_continuous_output")
    window.deselectVariable(variable)
    assert window.ui.resultStackedWidget.currentWidget() == window.ui.noResultPage
    missing = next(v for v in window.modelDescription.modelVariables if v.name == "Int32_output")
    window.selectVariable(missing)  # not collected; must not crash the main plot
    window.selectVariable(variable)
    assert window.ui.resultStackedWidget.currentWidget() == window.ui.resultViewPage
    from PySide6.QtWidgets import QFileDialog
    saved = tmp_path / "main-result.csv"
    monkeypatch.setattr(QFileDialog, "getSaveFileName", lambda **kwargs: (str(saved), "CSV"))
    window.saveResult(plotted=True)
    import csv
    with saved.open() as stream:
        exported = list(csv.DictReader(stream))
    assert float(exported[-1]["Float64_continuous_output"]) == 7
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


def test_scenario_tree_load_select_run_and_restore(app, reference_fmus_dist_dir, tmp_path, monkeypatch):
    from pathlib import Path
    from PySide6.QtWidgets import QFileDialog
    fmu = reference_fmus_dist_dir / "2.0" / "Feedthrough.fmu"
    dialog = ScenarioDialog(str(fmu))
    dialog.show()
    app.processEvents()
    paths = []
    for name, value in (("first", 3), ("second", 9)):
        source = tmp_path / f"{name}.json"
        source.write_text(json.dumps({"version": 1, "stop_time": 0.2, "step_size": 0.1,
                                     "initial": {"Float64_continuous_input": value},
                                     "outputs": ["Float64_continuous_output"]}))
        paths.append(str(source.resolve()))
    monkeypatch.setattr(QFileDialog, "getOpenFileNames", lambda *args: (paths, "JSON"))
    dialog.loadButton.click()
    assert dialog.scenarioTree.topLevelItemCount() == 1
    group = dialog.scenarioTree.topLevelItem(0)
    assert group.childCount() == 2
    assert not hasattr(dialog, "proposalsButton")
    assert dialog.scenario["initial"]["Float64_continuous_input"] == 9
    applied = []
    dialog.scenarioApplied.connect(lambda fmu, path: applied.append(path))
    dialog.scenarioTree.setCurrentItem(group.child(0))
    assert applied[-1] == paths[0]
    assert dialog.scenario["initial"]["Float64_continuous_input"] == 3
    dialog.outputPath.setText(str(tmp_path / "runs"))
    for index, expected in ((0, 3), (1, 9)):
        dialog.scenarioTree.setCurrentItem(group.child(index))
        dialog.runButton.click()
        assert not dialog.scenarioTree.isEnabled()
        assert not dialog.loadScenario(paths[0])
        deadline = time.monotonic() + 20
        while dialog.worker.isRunning() and time.monotonic() < deadline:
            app.processEvents()
            time.sleep(0.01)
        assert dialog.worker.wait(1000)
        app.processEvents()
        assert dialog.result["samples"][-1]["Float64_continuous_output"] == expected
        assert dialog.scenarioTree.isEnabled()
    second_directory = dialog.resultDirectory
    dialog.scenarioTree.setCurrentItem(group.child(0))
    assert dialog.result["samples"][-1]["Float64_continuous_output"] == 3
    assert dialog.resultDirectory != second_directory
    assert dialog.resultDirectory in dialog.report.toPlainText()
    assert len(dialog.plot.graphs[0].plot.listDataItems()) == 1
    assert dialog.openResultsButton.isEnabled()
    # Reload updates an existing entry and discards only its old result.
    assert dialog.loadScenario(paths[0])
    assert group.childCount() == 2
    assert dialog.result is None
    dialog.scenarioTree.setCurrentItem(group.child(1))
    assert dialog.resultDirectory == second_directory
    bad = tmp_path / "bad.json"
    bad.write_text("not JSON")
    assert not dialog.loadScenario(bad)
    assert not dialog.runButton.isEnabled()
    assert dialog.result is None
    dialog.scenarioTree.setCurrentItem(group.child(1))
    assert dialog.runButton.isEnabled()
    assert dialog.resultDirectory == second_directory
    # Equal filenames in different folders remain separate tree entries.
    other = tmp_path / "other"
    other.mkdir()
    duplicate = other / "first.json"
    duplicate.write_text(Path(paths[0]).read_text())
    assert dialog.loadScenario(duplicate)
    assert dialog.scenarioTree.topLevelItemCount() == 2
    dialog.close()
