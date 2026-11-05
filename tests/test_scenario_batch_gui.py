"""Exercise the batch scenario dialog: building a file list, running it for
real against the reference FMUs, and the aggregate summary it produces.
"""
import time
from pathlib import Path

import pytest

pytest.importorskip("PySide6")
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication, QFileDialog

from fmpy.gui.MainWindow import MainWindow
from fmpy.gui.scenario import ScenarioDialog
from fmpy.gui.scenario_batch import ScenarioBatchDialog


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication(["test", "-platform", "offscreen"])


def write(path, scenario):
    import json
    path.write_text(json.dumps(scenario), encoding="utf-8")
    return path


def good(**overrides):
    scenario = {"version": 1, "name": "demo", "stop_time": 1.0, "step_size": 0.1,
               "initial": {"Float64_continuous_input": 0.0},
               "events": [{"id": "step", "time": 0.2, "set": {"Float64_continuous_input": 2.0}}],
               "outputs": ["Float64_continuous_output"],
               "checks": [{"id": "band", "variable": "Float64_continuous_output",
                          "min": 0.0, "max": 2.0}]}
    scenario.update(overrides)
    return scenario


def run_batch(app, dialog, timeout=30):
    dialog.runSelected()
    deadline = time.monotonic() + timeout
    while dialog.worker.isRunning() and time.monotonic() < deadline:
        app.processEvents()
        time.sleep(0.02)
    assert dialog.worker.wait(2000)
    app.processEvents()


def test_adding_files_deduplicates_and_populates_the_table(app, tmp_path):
    a = write(tmp_path / "a.json", good())
    b = write(tmp_path / "b.json", good())
    dialog = ScenarioBatchDialog()
    dialog.addPaths([a, b, a])  # a listed twice
    assert dialog.table.rowCount() == 2
    assert [dialog.table.item(row, 0).text() for row in range(2)] == ["a.json", "b.json"]
    assert all(dialog.table.item(row, 0).checkState() == Qt.Checked for row in range(2))
    assert dialog.runButton.isEnabled()
    dialog.close()


def test_select_none_then_remove_then_clear(app, tmp_path):
    a = write(tmp_path / "a.json", good())
    b = write(tmp_path / "b.json", good())
    dialog = ScenarioBatchDialog()
    dialog.addPaths([a, b])
    dialog.setAllChecked(False)
    assert all(dialog.table.item(row, 0).checkState() == Qt.Unchecked for row in range(2))

    dialog.table.selectRow(0)
    dialog.removeSelected()
    assert dialog.table.rowCount() == 1
    assert dialog.table.item(0, 0).text() == "b.json"

    dialog.clearAll()
    assert dialog.table.rowCount() == 0
    assert not dialog.runButton.isEnabled()
    dialog.close()


def test_running_without_an_fmu_is_refused(app, tmp_path):
    dialog = ScenarioBatchDialog()
    dialog.addPaths([write(tmp_path / "a.json", good())])
    dialog.runSelected()
    assert dialog.worker is None
    assert "FMU" in dialog.summaryLabel.text()
    dialog.close()


def test_a_real_batch_tallies_pass_fail_no_verdict_and_error(
        app, reference_fmus_dist_dir, tmp_path):
    path = reference_fmus_dist_dir / "2.0" / "Feedthrough.fmu"
    passing = write(tmp_path / "01_passing.json", good())
    failing_scenario = good()
    failing_scenario["checks"][0]["max"] = 1.0
    failing = write(tmp_path / "02_failing.json", failing_scenario)
    no_verdict = write(tmp_path / "03_no_verdict.json",
                       {k: v for k, v in good().items() if k != "checks"})
    broken_scenario = good()
    broken_scenario["outputs"] = ["nope"]
    broken = write(tmp_path / "04_broken.json", broken_scenario)

    dialog = ScenarioBatchDialog(str(path))
    dialog.addPaths([passing, failing, no_verdict, broken])
    dialog.outputPath.setText(str(tmp_path / "out"))
    run_batch(app, dialog)

    assert dialog.summaryLabel.text() == (
        "4개 중 합격 1 · 불합격 1 · 판정 기준 없음 1 · 실행 불가 1")
    status = [dialog.table.item(row, 1).text() for row in range(4)]
    assert "합격" in status[0]
    assert "불합격" in status[1]
    assert "판정 없음" in status[2]
    assert status[3].startswith("오류:")
    assert dialog.saveReportButton.isEnabled()
    assert (tmp_path / "out" / "01_passing" / "report.json").is_file()
    assert not (tmp_path / "out" / "04_broken").exists()

    dialog.table.selectRow(1)
    assert "실패한 검사" in dialog.detail.toPlainText()
    dialog.close()


def test_running_only_what_is_checked_leaves_the_rest_untouched(
        app, reference_fmus_dist_dir, tmp_path):
    path = reference_fmus_dist_dir / "2.0" / "Feedthrough.fmu"
    a = write(tmp_path / "a.json", good())
    b = write(tmp_path / "b.json", good())
    dialog = ScenarioBatchDialog(str(path))
    dialog.addPaths([a, b])
    dialog.outputPath.setText(str(tmp_path / "out"))
    dialog.table.item(1, 0).setCheckState(Qt.Unchecked)
    run_batch(app, dialog)

    assert dialog.summaryLabel.text() == "1개 중 합격 1 · 불합격 0 · 판정 기준 없음 0 · 실행 불가 0"
    assert dialog.table.item(1, 1).text() == "대기"
    dialog.close()


def test_saving_the_report_writes_the_markdown_text(
        app, reference_fmus_dist_dir, tmp_path, monkeypatch):
    path = reference_fmus_dist_dir / "2.0" / "Feedthrough.fmu"
    dialog = ScenarioBatchDialog(str(path))
    dialog.addPaths([write(tmp_path / "a.json", good())])
    dialog.outputPath.setText(str(tmp_path / "out"))
    run_batch(app, dialog)

    saved = tmp_path / "summary.md"
    monkeypatch.setattr(QFileDialog, "getSaveFileName", lambda *a, **k: (str(saved), "Markdown"))
    dialog.saveReport()
    assert saved.read_text(encoding="utf-8") == dialog.lastReport
    assert "합격 1" in saved.read_text(encoding="utf-8")
    dialog.close()


def test_cancel_stops_the_remaining_scenarios(app, reference_fmus_dist_dir, tmp_path):
    path = reference_fmus_dist_dir / "2.0" / "Feedthrough.fmu"
    dialog = ScenarioBatchDialog(str(path))
    dialog.addPaths([write(tmp_path / f"{i}.json", good()) for i in range(3)])
    dialog.outputPath.setText(str(tmp_path / "out"))
    dialog.runSelected()
    dialog.cancelRun()
    deadline = time.monotonic() + 30
    while dialog.worker.isRunning() and time.monotonic() < deadline:
        app.processEvents()
        time.sleep(0.02)
    assert dialog.worker.wait(2000)
    app.processEvents()
    assert dialog.runButton.isEnabled()  # busy state cleared
    dialog.close()


def test_embedded_as_a_tab_in_scenario_dialog_and_kept_in_sync_with_its_fmu(
        app, reference_fmus_dist_dir):
    path = reference_fmus_dist_dir / "2.0" / "Feedthrough.fmu"
    other = reference_fmus_dist_dir / "2.0" / "BouncingBall.fmu"
    sd = ScenarioDialog(str(path))
    assert sd.batchTab.fmuPath.text() == str(path)
    sd.tabs.setCurrentWidget(sd.batchTab)
    assert sd.tabs.currentWidget() is sd.batchTab

    # Changing the FMU anywhere on the dialog updates the embedded tab too.
    sd.fmuPath.setText(str(other))
    assert sd.batchTab.fmuPath.text() == str(other)
    sd.close()

    window = MainWindow()
    assert hasattr(window, "batchAction") and window.batchAction.text()
    window.close()
