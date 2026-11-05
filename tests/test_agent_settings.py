"""fmpy.gui.agent_settings: persisted defaults for the agent chat backends."""
import pytest

pytest.importorskip("PySide6")
from PySide6.QtCore import QSettings
from PySide6.QtWidgets import QApplication, QDialog, QLineEdit

from fmpy.gui import agent_settings


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication(["test", "-platform", "offscreen"])


@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch):
    """Never read or write the real user's saved agent settings from a test."""
    ini_path = tmp_path / "agent_settings.ini"
    monkeypatch.setattr(agent_settings, "_settings",
                        lambda: QSettings(str(ini_path), QSettings.IniFormat))
    return ini_path


def test_load_with_nothing_saved_returns_every_default():
    assert agent_settings.load() == agent_settings.DEFAULTS


def test_save_persists_and_load_reflects_it_with_the_right_types():
    agent_settings.save({"backend": "claude", "ollama_max_attempts": 7,
                        "claude_api_key": "sk-abc"})
    loaded = agent_settings.load()
    assert loaded["backend"] == "claude"
    assert loaded["ollama_max_attempts"] == 7 and isinstance(loaded["ollama_max_attempts"], int)
    assert loaded["claude_api_key"] == "sk-abc"
    # Untouched keys keep their default.
    assert loaded["ollama_host"] == agent_settings.DEFAULTS["ollama_host"]


def test_save_only_overwrites_the_given_keys():
    agent_settings.save({"ollama_model": "qwen2.5"})
    agent_settings.save({"claude_model": "claude-opus-5"})
    loaded = agent_settings.load()
    assert loaded["ollama_model"] == "qwen2.5"
    assert loaded["claude_model"] == "claude-opus-5"


def test_settings_are_written_to_an_ini_file_not_the_registry(isolated):
    agent_settings.save({"backend": "claude"})
    assert isolated.is_file()
    assert "backend" in isolated.read_text(encoding="utf-8")


def test_settings_dialog_reflects_what_was_saved_and_round_trips(app):
    agent_settings.save({"backend": "claude", "ollama_host": "http://example:1234",
                        "ollama_model": "qwen2.5", "ollama_max_attempts": 9,
                        "claude_model": "claude-opus-5", "claude_api_key": "sk-xyz",
                        "claude_max_turns": 12, "output_root": "/tmp/out"})
    dialog = agent_settings.AgentSettingsDialog()
    assert dialog.backendCombo.currentIndex() == 1
    assert dialog.hostEdit.text() == "http://example:1234"
    assert dialog.ollamaModelEdit.text() == "qwen2.5"
    assert dialog.maxAttemptsSpin.value() == 9
    assert dialog.claudeModelEdit.text() == "claude-opus-5"
    assert dialog.apiKeyEdit.text() == "sk-xyz"
    assert dialog.apiKeyEdit.echoMode() == QLineEdit.Password
    assert dialog.maxTurnsSpin.value() == 12
    assert dialog.outputEdit.text() == "/tmp/out"
    assert dialog.values() == agent_settings.load()
    dialog.close()


def test_settings_dialog_save_emits_the_new_values_and_accepts(app):
    dialog = agent_settings.AgentSettingsDialog()
    received = []
    dialog.saved.connect(received.append)
    dialog.ollamaModelEdit.setText("new-model")
    dialog.saveAndClose()
    assert received and received[0]["ollama_model"] == "new-model"
    assert agent_settings.load()["ollama_model"] == "new-model"
    assert dialog.result() == QDialog.Accepted
