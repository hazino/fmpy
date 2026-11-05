"""Chat-style GUI for the generate -> validate -> run loop in fmpy.scenario_backends.

The user types a request in words; a model (local Ollama, or Claude via the
Anthropic API) turns it into a scenario, fmpy.scenario_agent.validate checks
it, and fmpy.scenario_agent.run executes it against the real FMU. The model
never touches the FMU directly -- this panel only ever shows what the model
produced and what actually happened when it ran, the same separation the CLI
loops in examples/agent/ enforce.

A plain QWidget (despite the class's name), so ScenarioDialog can embed it
as one of its own tabs instead of popping it up as a separate window.
"""
import html
import json
from datetime import datetime
from pathlib import Path

from PySide6.QtCore import QThread, QUrl, Signal
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import (QComboBox, QFileDialog, QFrame, QHBoxLayout, QLabel,
                               QLineEdit, QPushButton, QSplitter, QTextEdit, QVBoxLayout,
                               QWidget)

from fmpy.scenario_backends import ollama_models, run_with_claude, run_with_ollama
from . import agent_settings
from .scenario_results import ScenarioResults

#: Passed to scenario_agent.run so every recorded row comes back for plotting,
#: not just a short prefix (scenario.py caps a run at this many steps anyway).
ALL_SAMPLES = 1_000_000 + 1


class AgentChatWorker(QThread):
    """Runs one generate-validate-run turn off the UI thread."""

    logLine = Signal(str)
    resultReady = Signal(dict)

    def __init__(self, backend, kwargs, parent=None):
        super().__init__(parent)
        self.backend = backend  # "ollama" or "claude"
        self.kwargs = kwargs

    def run(self):
        try:
            if self.backend == "ollama":
                result = run_with_ollama(on_log=self.logLine.emit,
                                         cancelled=self.isInterruptionRequested, **self.kwargs)
            else:
                result = run_with_claude(on_log=self.logLine.emit,
                                         cancelled=self.isInterruptionRequested, **self.kwargs)
        except ImportError:
            result = {"ok": False, "tool": "claude_loop",
                      "errors": [{"code": "missing_dependency", "message": "pip install anthropic"}]}
        except Exception as error:
            result = {"ok": False, "tool": "agent_chat",
                      "errors": [{"code": "unexpected_error", "message": str(error)}]}
        self.resultReady.emit(result)


class AgentChatDialog(QWidget):
    """Pick a backend, type a request, watch it generate/validate/run against the FMU."""

    def __init__(self, fmu=None, parent=None):
        super().__init__(parent)
        self.setWindowTitle("시나리오 생성")
        self.resize(1100, 720)
        self.worker = None
        self.turn = 0
        self.lastScenario = None
        self.lastDirectory = None
        self.settings = agent_settings.load()

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        splitter = QSplitter()
        outer.addWidget(splitter, 1)

        # The chat column sits on the right (the FMU/backend controls and the
        # result graph read left-to-right like the rest of the window; the
        # conversation itself is the thing you glance at last).
        splitter.addWidget(self._buildMainArea(fmu))
        splitter.addWidget(self._buildChatSidebar())
        splitter.setStretchFactor(0, 1)
        splitter.setStretchFactor(1, 0)
        splitter.setSizes([760, 340])

        self.appendSystem("모델은 시나리오 JSON만 만듭니다. 검증과 실제 FMU 실행은 항상 이 코드가 "
                          "직접 하고, 판정 기준이 없는 요청은 '판정 기준 없음'으로 표시됩니다.")
        self.onBackendChanged(self.backendCombo.currentIndex())

    def _buildChatSidebar(self):
        """The persistent chat column: transcript on top, input pinned at the bottom."""
        sidebar = QWidget()
        sidebar.setMinimumWidth(280)
        sidebar.setMaximumWidth(480)
        layout = QVBoxLayout(sidebar)
        layout.setContentsMargins(8, 8, 8, 8)

        header = QLabel("<b>시나리오 생성</b>")
        layout.addWidget(header)
        rule = QFrame()
        rule.setFrameShape(QFrame.HLine)
        layout.addWidget(rule)

        self.transcript = QTextEdit()
        self.transcript.setReadOnly(True)
        layout.addWidget(self.transcript, 1)

        self.input = QLineEdit()
        self.input.setPlaceholderText(
            "자연어로 시나리오를 요청하세요. 예: setpoint를 0.2초에 0에서 2로 올리고 "
            "output이 -10~10 안에 있는지 확인")
        self.input.returnPressed.connect(self.send)
        layout.addWidget(self.input)

        inputButtons = QHBoxLayout()
        self.sendButton = QPushButton("보내기")
        self.sendButton.clicked.connect(self.send)
        self.cancelButton = QPushButton("중지")
        self.cancelButton.setEnabled(False)
        self.cancelButton.clicked.connect(self.cancelRun)
        inputButtons.addWidget(self.sendButton, 1)
        inputButtons.addWidget(self.cancelButton)
        layout.addLayout(inputButtons)

        return sidebar

    def _buildMainArea(self, fmu):
        """FMU/backend/output controls on top, the result graph filling the rest."""
        main = QWidget()
        layout = QVBoxLayout(main)

        fmuRow = QHBoxLayout()
        self.fmuPath = QLineEdit(str(fmu or ""))
        self.fmuPath.setReadOnly(True)
        self.fmuButton = QPushButton("FMU 선택...")
        self.fmuButton.clicked.connect(self.chooseFmu)
        fmuRow.addWidget(QLabel("FMU"))
        fmuRow.addWidget(self.fmuPath, 1)
        fmuRow.addWidget(self.fmuButton)
        layout.addLayout(fmuRow)

        backendRow = QHBoxLayout()
        self.backendCombo = QComboBox()
        self.backendCombo.addItems(["Ollama (로컬)", "Claude (API)"])
        self.backendCombo.setCurrentIndex(0 if self.settings["backend"] == "ollama" else 1)
        self.backendCombo.currentIndexChanged.connect(self.onBackendChanged)
        self.hostEdit = QLineEdit(self.settings["ollama_host"])
        self.hostEdit.editingFinished.connect(self.refreshModels)
        self.modelCombo = QComboBox()
        self.modelCombo.setEditable(True)
        self.refreshModelsButton = QPushButton("모델 목록 새로고침")
        self.refreshModelsButton.clicked.connect(self.refreshModels)
        self.settingsButton = QPushButton("설정...")
        self.settingsButton.setToolTip("기본 백엔드·모델·API 키·결과 폴더를 설정합니다.")
        self.settingsButton.clicked.connect(self.openSettings)
        backendRow.addWidget(QLabel("백엔드"))
        backendRow.addWidget(self.backendCombo)
        backendRow.addWidget(QLabel("Host"))
        backendRow.addWidget(self.hostEdit)
        backendRow.addWidget(QLabel("모델"))
        backendRow.addWidget(self.modelCombo, 1)
        backendRow.addWidget(self.refreshModelsButton)
        backendRow.addWidget(self.settingsButton)
        layout.addLayout(backendRow)

        outputRow = QHBoxLayout()
        self.outputPath = QLineEdit(
            self.settings["output_root"] or str(Path.cwd() / "scenario-results" / "agent-chat"))
        self.outputButton = QPushButton("저장 위치...")
        self.outputButton.clicked.connect(self.chooseOutput)
        outputRow.addWidget(QLabel("결과 폴더"))
        outputRow.addWidget(self.outputPath, 1)
        outputRow.addWidget(self.outputButton)
        layout.addLayout(outputRow)

        self.plot = ScenarioResults()
        layout.addWidget(self.plot, 1)

        buttons = QHBoxLayout()
        self.openResultsButton = QPushButton("결과 폴더 열기")
        self.openResultsButton.setEnabled(False)
        self.openResultsButton.clicked.connect(self.openResults)
        self.saveScenarioButton = QPushButton("시나리오 JSON 저장...")
        self.saveScenarioButton.setEnabled(False)
        self.saveScenarioButton.clicked.connect(self.saveScenario)
        buttons.addWidget(self.openResultsButton)
        buttons.addWidget(self.saveScenarioButton)
        layout.addLayout(buttons)

        return main

    # -- setup -------------------------------------------------------------

    def openSettings(self):
        dialog = agent_settings.AgentSettingsDialog(self)
        dialog.saved.connect(self.applySettings)
        dialog.exec()

    def applySettings(self, values):
        self.settings = values
        self.backendCombo.setCurrentIndex(0 if values["backend"] == "ollama" else 1)
        self.hostEdit.setText(values["ollama_host"])
        if values["output_root"]:
            self.outputPath.setText(values["output_root"])
        self.onBackendChanged(self.backendCombo.currentIndex())
        self.appendSystem("설정을 반영했습니다.")

    def chooseFmu(self):
        path, _ = QFileDialog.getOpenFileName(self, "FMU 선택", self.fmuPath.text(), "FMU (*.fmu)")
        if path:
            self.fmuPath.setText(path)

    def chooseOutput(self):
        path = QFileDialog.getExistingDirectory(self, "결과 저장 위치", self.outputPath.text())
        if path:
            self.outputPath.setText(path)

    def onBackendChanged(self, index):
        ollama = index == 0
        self.hostEdit.setEnabled(ollama)
        self.refreshModelsButton.setEnabled(ollama)
        self.modelCombo.clear()
        if ollama:
            self.refreshModels()
        else:
            self.modelCombo.addItem(self.settings["claude_model"])
            if not self.settings["claude_api_key"]:
                self.appendSystem("Claude 백엔드는 환경 변수 ANTHROPIC_API_KEY와 "
                                  "`pip install anthropic`이 필요합니다(설정에서 키를 저장해도 됨).")

    def refreshModels(self):
        if self.backendCombo.currentIndex() != 0:
            return
        models = ollama_models(self.hostEdit.text())
        self.modelCombo.clear()
        if models:
            self.modelCombo.addItems(models)
        else:
            self.modelCombo.addItem(self.settings["ollama_model"])
            self.appendSystem(f"Ollama({self.hostEdit.text()})에 연결할 수 없어 기본값을 둡니다.")

    # -- transcript ----------------------------------------------------------

    def appendUser(self, text):
        self.transcript.append(f"<p><b>나</b>: {html.escape(text)}</p>")

    def appendSystem(self, text):
        self.transcript.append(f"<p><i>{html.escape(text)}</i></p>")

    def appendAssistant(self, text):
        self.transcript.append(f"<p><b>에이전트</b>: {html.escape(text)}</p>")

    def appendLog(self, text):
        self.transcript.append(
            f"<p style='color:gray;margin-left:1em'>{html.escape(text)}</p>")

    # -- the actual turn -------------------------------------------------------

    def send(self):
        if self.worker is not None and self.worker.isRunning():
            return
        if not self.fmuPath.text():
            self.appendSystem("먼저 FMU를 선택하세요.")
            return
        request = self.input.text().strip()
        if not request:
            return
        if not self.outputPath.text().strip():
            self.appendSystem("결과 저장 위치를 입력하세요.")
            return
        self.input.clear()
        self.appendUser(request)
        self.turn += 1

        directory = (Path(self.outputPath.text())
                    / f"turn-{self.turn:03d}-{datetime.now().strftime('%H%M%S')}")
        common = dict(fmu=self.fmuPath.text(), request=request,
                     output=str(directory), include_samples=ALL_SAMPLES)
        if self.backendCombo.currentIndex() == 0:
            backend = "ollama"
            kwargs = {**common, "host": self.hostEdit.text(),
                     "model": self.modelCombo.currentText(),
                     "max_attempts": self.settings["ollama_max_attempts"]}
        else:
            backend = "claude"
            kwargs = {**common, "model": self.modelCombo.currentText(),
                     "max_turns": self.settings["claude_max_turns"],
                     "api_key": self.settings["claude_api_key"] or None}

        self.setBusy(True)
        self.worker = AgentChatWorker(backend, kwargs, self)
        self.worker.logLine.connect(self.appendLog)
        self.worker.resultReady.connect(self.onResult)
        self.worker.finished.connect(lambda: self.setBusy(False))
        self.worker.start()

    def setBusy(self, busy):
        for widget in (self.sendButton, self.input, self.backendCombo, self.fmuButton,
                       self.outputButton):
            widget.setEnabled(not busy)
        self.cancelButton.setEnabled(busy)

    def cancelRun(self):
        if self.worker is not None and self.worker.isRunning():
            self.worker.requestInterruption()
            self.appendSystem("중지 요청됨: 현재 턴이 끝나는 대로 멈춥니다.")

    def onResult(self, result):
        if not result.get("ok"):
            error = result["errors"][0]
            self.appendAssistant(f"실패 [{error['code']}]: {error['message']}")
            return

        scenario = result.get("generated_scenario")
        self.lastScenario = scenario
        self.saveScenarioButton.setEnabled(scenario is not None)

        passed = result.get("passed")
        verdict = ("합격" if passed is True else "실패" if passed is False else "판정 기준 없음")
        samples = result.get("sample_count")
        self.appendAssistant(f"완료 — {verdict}" + (f" / {samples}개 샘플" if samples else ""))

        rows = result.get("samples")
        if rows and scenario:
            self.plot.setResult(rows, scenario["outputs"])

        artifacts = result.get("artifacts") or {}
        if artifacts.get("directory"):
            self.lastDirectory = artifacts["directory"]
            self.openResultsButton.setEnabled(True)
            self.appendLog(f"저장 위치: {artifacts['directory']}")
        if scenario:
            self.appendLog(json.dumps(scenario, ensure_ascii=False))

    # -- artifacts -----------------------------------------------------------

    def openResults(self):
        if self.lastDirectory:
            QDesktopServices.openUrl(QUrl.fromLocalFile(self.lastDirectory))

    def saveScenario(self):
        if self.lastScenario is None:
            return
        path, _ = QFileDialog.getSaveFileName(self, "시나리오 저장",
                                              str(Path.cwd() / "scenario.json"),
                                              "JSON (*.json)")
        if path:
            Path(path).write_text(json.dumps(self.lastScenario, indent=2, ensure_ascii=False),
                                  encoding="utf-8")
            self.appendSystem(f"저장함: {path}")

    def isBusy(self):
        return self.worker is not None and self.worker.isRunning()

    def closeEvent(self, event):
        if self.isBusy():
            self.cancelRun()
            event.ignore()
        else:
            super().closeEvent(event)
