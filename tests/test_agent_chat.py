"""Exercise the agent chat dialog: construction, backend switching, and a real
generate -> validate -> run turn against a local Ollama server.

The Ollama-backed tests are skipped where no server with a pulled model is
reachable (this machine has one: Ollama 0.40.4 with gemma:2b). They are not
mocked -- the point is the same real loop verified in
docs/agent-integration-ko.md, run from the dialog instead of the CLI.
"""
import json
import time
from pathlib import Path

import pytest

pytest.importorskip("PySide6")
from PySide6.QtCore import QSettings
from PySide6.QtWidgets import QApplication, QFileDialog

from fmpy.gui import agent_settings
from fmpy.gui.agent_chat import AgentChatDialog
from fmpy.gui.MainWindow import MainWindow
from fmpy.scenario_backends import ollama_models


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication(["test", "-platform", "offscreen"])


@pytest.fixture(autouse=True)
def isolated_agent_settings(tmp_path, monkeypatch):
    """Never read or write the real user's saved agent settings from a test."""
    ini_path = tmp_path / "agent_settings.ini"
    monkeypatch.setattr(agent_settings, "_settings",
                        lambda: QSettings(str(ini_path), QSettings.IniFormat))


@pytest.fixture(scope="module")
def ollama_ready():
    return bool(ollama_models())


def run_turn(app, dialog, request, timeout=30):
    dialog.input.setText(request)
    dialog.send()
    deadline = time.monotonic() + timeout
    while dialog.worker.isRunning() and time.monotonic() < deadline:
        app.processEvents()
        time.sleep(0.02)
    assert dialog.worker.wait(2000)
    app.processEvents()


def test_dialog_constructs_with_the_given_fmu_and_lists_ollama_models(app, reference_fmus_dist_dir):
    path = reference_fmus_dist_dir / "2.0" / "Feedthrough.fmu"
    dialog = AgentChatDialog(str(path))
    assert dialog.fmuPath.text() == str(path)
    assert dialog.backendCombo.currentIndex() == 0  # Ollama is the default
    dialog.close()


def test_switching_to_claude_disables_the_host_field_and_defaults_the_model(app):
    dialog = AgentChatDialog()
    dialog.backendCombo.setCurrentIndex(1)
    assert not dialog.hostEdit.isEnabled()
    assert dialog.modelCombo.currentText() == "claude-sonnet-5"
    dialog.backendCombo.setCurrentIndex(0)
    assert dialog.hostEdit.isEnabled()
    dialog.close()


def test_sending_without_an_fmu_or_request_is_refused_without_starting_a_worker(app):
    dialog = AgentChatDialog()
    dialog.input.setText("do something")
    dialog.send()
    assert dialog.worker is None
    assert "FMU" in dialog.transcript.toPlainText()

    dialog.fmuPath.setText("model.fmu")
    dialog.input.setText("")
    dialog.send()
    assert dialog.worker is None  # empty request is a no-op, not an error
    dialog.close()


def test_saving_the_generated_scenario_writes_it_verbatim(app, tmp_path, monkeypatch):
    dialog = AgentChatDialog()
    dialog.lastScenario = {"version": 1, "stop_time": 1.0, "step_size": 0.1, "outputs": ["y"]}
    saved = tmp_path / "scenario.json"
    monkeypatch.setattr(QFileDialog, "getSaveFileName", lambda *a, **k: (str(saved), "JSON"))
    dialog.saveScenario()
    assert json.loads(saved.read_text(encoding="utf-8")) == dialog.lastScenario
    dialog.close()


def test_the_panel_can_still_be_constructed_standalone(app):
    # Not how the GUI reaches it any more (that's the single "Scenario..."
    # toolbar action -> ScenarioDialog's "에이전트에게 요청" tab), but the
    # class stays usable on its own for anything else that wants to embed it.
    window = MainWindow()
    dialog = AgentChatDialog(window.filename, window)
    assert dialog.fmuPath.text() == ""
    dialog.close()
    window.close()


def test_main_window_toolbar_opens_one_scenario_dialog_with_the_agent_tab_inside(
        app, reference_fmus_dist_dir):
    window = MainWindow()
    window.filename = str(reference_fmus_dist_dir / "2.0" / "Feedthrough.fmu")
    assert not hasattr(window, "agentChatAction")  # no separate toolbar entry any more
    # What the "Scenario..." action opens, minus the blocking .exec() a
    # headless test cannot close; the agent tab is reached inside that window.
    dialog = window.ensureScenarioDialog()
    dialog.showAgentChatTab()
    assert dialog.tabs.currentWidget() is dialog.agentChatTab
    assert dialog.agentChatTab.fmuPath.text() == window.filename
    dialog.close()
    window.close()


@pytest.mark.skipif(not ollama_models(), reason="no Ollama server with a pulled model reachable")
def test_a_real_turn_generates_validates_and_runs_against_the_fmu(
        app, ollama_ready, reference_fmus_dist_dir, tmp_path):
    path = reference_fmus_dist_dir / "2.0" / "Feedthrough.fmu"
    dialog = AgentChatDialog(str(path))
    dialog.outputPath.setText(str(tmp_path / "out"))

    run_turn(app, dialog, "Step Float64_continuous_input from 0 to 1 at time 0.2, "
                         "record Float64_continuous_output")

    assert dialog.lastScenario is not None
    assert dialog.saveScenarioButton.isEnabled()
    transcript = dialog.transcript.toPlainText()
    assert "완료" in transcript or "실패" in transcript  # the loop reached a verdict either way
    if dialog.lastDirectory is not None:
        directory = Path(dialog.lastDirectory)
        assert (directory / "scenario.json").is_file()
        assert (directory / "report.json").is_file()
    dialog.close()


@pytest.mark.skipif(not ollama_models(), reason="no Ollama server with a pulled model reachable")
def test_cancel_during_a_turn_leaves_the_dialog_usable(app, reference_fmus_dist_dir, tmp_path):
    path = reference_fmus_dist_dir / "2.0" / "Feedthrough.fmu"
    dialog = AgentChatDialog(str(path))
    dialog.outputPath.setText(str(tmp_path / "out"))

    dialog.input.setText("Any request")
    dialog.send()
    assert dialog.worker is not None
    dialog.cancelRun()
    deadline = time.monotonic() + 30
    while dialog.worker.isRunning() and time.monotonic() < deadline:
        app.processEvents()
        time.sleep(0.02)
    assert dialog.worker.wait(2000)
    app.processEvents()  # let the queued `finished` signal's slot (setBusy(False)) run
    assert dialog.sendButton.isEnabled()  # busy state was cleared
    dialog.close()


def test_settings_button_dialog_saves_and_the_chat_dialog_picks_it_up(app):
    dialog = AgentChatDialog()
    settings_dialog = agent_settings.AgentSettingsDialog(dialog)
    settings_dialog.backendCombo.setCurrentIndex(1)  # Claude
    settings_dialog.claudeModelEdit.setText("claude-opus-5")
    settings_dialog.apiKeyEdit.setText("sk-test-123")
    settings_dialog.maxTurnsSpin.setValue(3)
    settings_dialog.outputEdit.setText(str(Path.cwd() / "custom-output"))

    settings_dialog.saved.connect(dialog.applySettings)
    settings_dialog.saveAndClose()

    assert dialog.backendCombo.currentIndex() == 1
    assert dialog.modelCombo.currentText() == "claude-opus-5"
    assert dialog.settings["claude_api_key"] == "sk-test-123"
    assert dialog.settings["claude_max_turns"] == 3
    assert dialog.outputPath.text() == str(Path.cwd() / "custom-output")

    # And it persisted: a freshly loaded dialog starts from the same values.
    fresh = AgentChatDialog()
    assert fresh.backendCombo.currentIndex() == 1
    assert fresh.modelCombo.currentText() == "claude-opus-5"
    dialog.close()
    fresh.close()


def test_settings_dialog_cancel_does_not_persist_anything(app):
    before = agent_settings.load()
    settings_dialog = agent_settings.AgentSettingsDialog()
    settings_dialog.ollamaModelEdit.setText("qwen2.5")
    settings_dialog.reject()
    assert agent_settings.load() == before


def test_a_turn_passes_the_configured_max_attempts_and_api_key_through(app, monkeypatch):
    dialog = AgentChatDialog("model.fmu")
    agent_settings.save({"backend": "claude", "claude_model": "claude-sonnet-5",
                        "claude_api_key": "sk-configured", "claude_max_turns": 5})
    dialog.settings = agent_settings.load()
    dialog.applySettings(dialog.settings)
    dialog.outputPath.setText(str(Path.cwd()))
    dialog.input.setText("do something")

    captured = {}

    class FakeWorker:
        def __init__(self, backend, kwargs, parent=None):
            captured["backend"], captured["kwargs"] = backend, kwargs
            self.logLine = type("S", (), {"connect": lambda *a: None})()
            self.resultReady = type("S", (), {"connect": lambda *a: None})()
            self.finished = type("S", (), {"connect": lambda *a: None})()

        def start(self):
            pass

        def isRunning(self):
            return False

    monkeypatch.setattr("fmpy.gui.agent_chat.AgentChatWorker", FakeWorker)
    dialog.send()

    assert captured["backend"] == "claude"
    assert captured["kwargs"]["api_key"] == "sk-configured"
    assert captured["kwargs"]["max_turns"] == 5
    dialog.close()
