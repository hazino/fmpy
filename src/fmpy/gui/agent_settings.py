"""Persisted defaults for the agent chat backends (fmpy.gui.agent_chat).

Stored with QSettings as a plain INI file under the user's profile -- not the
Windows registry -- so where this lives is visible rather than hidden
(organization "fmpy", application "scenario_agent"; QSettings.fileName() on a
loaded instance gives the exact path). The Claude API key field is optional
and, if set, is stored there in plain text; leave it blank and set the
ANTHROPIC_API_KEY environment variable instead if that is not acceptable.
Nothing here ever appears in a scenario, a report, or a log message.
"""
from PySide6.QtCore import QSettings, Signal
from PySide6.QtWidgets import (QComboBox, QDialog, QFileDialog, QHBoxLayout, QLabel, QLineEdit,
                               QPushButton, QSpinBox, QVBoxLayout)

#: One entry per setting: its default value, and (for the dialog) a label.
DEFAULTS = {
    "backend": "ollama",  # "ollama" | "claude"
    "ollama_host": "http://localhost:11434",
    "ollama_model": "gemma:2b",
    "ollama_max_attempts": 4,
    "claude_model": "claude-sonnet-5",
    "claude_api_key": "",
    "claude_max_turns": 8,
    "output_root": "",  # "" means: let the caller use its own default
}


def _settings():
    return QSettings(QSettings.IniFormat, QSettings.UserScope, "fmpy", "scenario_agent")


def load():
    """Every setting, with defaults filled in for anything never saved."""
    settings = _settings()
    values = {}
    for key, default in DEFAULTS.items():
        value = settings.value(key, default)
        if isinstance(default, int) and not isinstance(default, bool):
            value = int(value)
        values[key] = value
    return values


def save(values):
    """Persist exactly the given keys; anything else already saved is kept as is."""
    settings = _settings()
    for key, value in values.items():
        settings.setValue(key, value)
    settings.sync()


class AgentSettingsDialog(QDialog):
    """Edit the defaults AgentChatDialog starts from. Applies only on Save."""

    saved = Signal(dict)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("에이전트 설정")
        self.resize(460, 420)
        values = load()

        layout = QVBoxLayout(self)

        layout.addWidget(QLabel("기본 백엔드"))
        self.backendCombo = QComboBox()
        self.backendCombo.addItems(["Ollama (로컬)", "Claude (API)"])
        self.backendCombo.setCurrentIndex(0 if values["backend"] == "ollama" else 1)
        layout.addWidget(self.backendCombo)

        layout.addWidget(QLabel("Ollama Host"))
        self.hostEdit = QLineEdit(values["ollama_host"])
        layout.addWidget(self.hostEdit)

        layout.addWidget(QLabel("Ollama 기본 모델"))
        self.ollamaModelEdit = QLineEdit(values["ollama_model"])
        layout.addWidget(self.ollamaModelEdit)

        layout.addWidget(QLabel("Ollama 최대 재시도 횟수 (검증 실패 시 다시 생성)"))
        self.maxAttemptsSpin = QSpinBox()
        self.maxAttemptsSpin.setRange(1, 20)
        self.maxAttemptsSpin.setValue(values["ollama_max_attempts"])
        layout.addWidget(self.maxAttemptsSpin)

        layout.addWidget(QLabel("Claude 기본 모델"))
        self.claudeModelEdit = QLineEdit(values["claude_model"])
        layout.addWidget(self.claudeModelEdit)

        layout.addWidget(QLabel("Claude API 키 (선택 — 비워두면 환경변수 ANTHROPIC_API_KEY 사용)"))
        self.apiKeyEdit = QLineEdit(values["claude_api_key"])
        self.apiKeyEdit.setEchoMode(QLineEdit.Password)
        self.apiKeyEdit.setToolTip(
            "여기에 입력하면 이 PC의 설정 파일에 평문으로 저장됩니다. "
            "원치 않으면 비워두고 환경변수 ANTHROPIC_API_KEY를 쓰세요.")
        layout.addWidget(self.apiKeyEdit)

        layout.addWidget(QLabel("Claude 최대 턴 수 (도구 호출 반복 한도)"))
        self.maxTurnsSpin = QSpinBox()
        self.maxTurnsSpin.setRange(1, 30)
        self.maxTurnsSpin.setValue(values["claude_max_turns"])
        layout.addWidget(self.maxTurnsSpin)

        layout.addWidget(QLabel("기본 결과 폴더 (비워두면 호출한 화면의 기본값 사용)"))
        outputRow = QHBoxLayout()
        self.outputEdit = QLineEdit(values["output_root"])
        self.outputButton = QPushButton("찾아보기...")
        self.outputButton.clicked.connect(self.chooseOutput)
        outputRow.addWidget(self.outputEdit, 1)
        outputRow.addWidget(self.outputButton)
        layout.addLayout(outputRow)

        layout.addStretch(1)
        buttons = QHBoxLayout()
        self.saveButton = QPushButton("저장")
        self.saveButton.clicked.connect(self.saveAndClose)
        self.cancelButton = QPushButton("취소")
        self.cancelButton.clicked.connect(self.reject)
        buttons.addWidget(self.saveButton)
        buttons.addWidget(self.cancelButton)
        layout.addLayout(buttons)

    def chooseOutput(self):
        path = QFileDialog.getExistingDirectory(self, "기본 결과 폴더", self.outputEdit.text())
        if path:
            self.outputEdit.setText(path)

    def values(self):
        return {
            "backend": "ollama" if self.backendCombo.currentIndex() == 0 else "claude",
            "ollama_host": self.hostEdit.text(),
            "ollama_model": self.ollamaModelEdit.text(),
            "ollama_max_attempts": self.maxAttemptsSpin.value(),
            "claude_model": self.claudeModelEdit.text(),
            "claude_api_key": self.apiKeyEdit.text(),
            "claude_max_turns": self.maxTurnsSpin.value(),
            "output_root": self.outputEdit.text(),
        }

    def saveAndClose(self):
        values = self.values()
        save(values)
        self.saved.emit(values)
        self.accept()
