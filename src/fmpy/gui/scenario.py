"""GUI adapter for the same deterministic runner used by the command line."""
import json
from copy import deepcopy
from datetime import datetime
from pathlib import Path

from PySide6.QtCore import QThread, Signal, QUrl, Qt
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import (QDialog, QFileDialog, QHBoxLayout, QLabel, QLineEdit,
                               QPushButton, QTabWidget, QTextEdit, QVBoxLayout,
                               QSplitter, QTreeWidget, QTreeWidgetItem, QHeaderView)

from fmpy import read_model_description
from fmpy.scenario import run_scenario, validate_scenario, write_results
from .scenario_results import ScenarioResults


class ScenarioWorker(QThread):
    completed = Signal(object, str)
    failed = Signal(str)

    def __init__(self, fmu, scenario, directory, parent=None):
        super().__init__(parent)
        self.fmu = fmu
        self.scenario = deepcopy(scenario)
        self.directory = directory

    def run(self):
        try:
            result = run_scenario(self.fmu, self.scenario, self.isInterruptionRequested)
            write_results(self.directory, self.scenario, result)
            self.completed.emit(result, str(self.directory))
        except Exception as error:
            self.failed.emit(str(error))


class ScenarioDialog(QDialog):
    scenarioApplied = Signal(str, str)
    scenarioCleared = Signal()
    resultReady = Signal(object)

    def __init__(self, fmu=None, parent=None):
        super().__init__(parent)
        self.setWindowTitle("FMI 2.0 Scenario / 시나리오 실행")
        self.resize(980, 700)
        self.scenario = None
        self.worker = None
        self.result = None
        self.resultDirectory = None
        self.entries = {}
        self.currentEntry = None
        layout = QVBoxLayout(self)
        self.fmuPath = QLineEdit(str(fmu or ""))
        self.fmuPath.setReadOnly(True)
        self.fmuButton = QPushButton("FMU 선택...")
        self.fmuButton.clicked.connect(self.chooseFmu)
        self.scenarioPath = QLineEdit()
        self.scenarioPath.setReadOnly(True)
        self.loadButton = QPushButton("시나리오 로드...")
        self.loadButton.clicked.connect(self.chooseScenario)
        self.outputPath = QLineEdit(str(Path.cwd() / "scenario-results" / "gui"))
        self.outputButton = QPushButton("저장 위치...")
        self.outputButton.clicked.connect(self.chooseOutput)
        for name, edit, button in (("FMU", self.fmuPath, self.fmuButton),
                                   ("JSON", self.scenarioPath, self.loadButton),
                                   ("결과 폴더", self.outputPath, self.outputButton)):
            row = QHBoxLayout()
            row.addWidget(QLabel(name))
            row.addWidget(edit, 1)
            row.addWidget(button)
            layout.addLayout(row)
        layout.addWidget(QLabel("여러 JSON을 로드하고 트리에서 선택하세요. 선택한 시나리오가 메인 재생에 적용됩니다."))
        self.scenarioTree = QTreeWidget()
        self.scenarioTree.setHeaderLabels(["시나리오", "상태"])
        self.scenarioTree.header().setStretchLastSection(False)
        self.scenarioTree.header().setSectionResizeMode(0, QHeaderView.Stretch)
        self.scenarioTree.header().setSectionResizeMode(1, QHeaderView.ResizeToContents)
        self.scenarioTree.setMinimumWidth(260)
        self.scenarioTree.currentItemChanged.connect(self.selectScenario)
        self.tabs = QTabWidget()
        self.preview = QTextEdit()
        self.preview.setReadOnly(True)
        self.tabs.addTab(self.preview, "시나리오")
        self.plot = ScenarioResults()
        self.tabs.addTab(self.plot, "결과 그래프")
        self.report = QTextEdit()
        self.report.setReadOnly(True)
        self.tabs.addTab(self.report, "판정 / 결과")
        splitter = QSplitter()
        splitter.addWidget(self.scenarioTree)
        splitter.addWidget(self.tabs)
        splitter.setStretchFactor(1, 1)
        splitter.setSizes([300, 680])
        layout.addWidget(splitter, 1)
        self.status = QLabel("FMU와 JSON 시나리오를 선택하세요.")
        self.status.setWordWrap(True)
        layout.addWidget(self.status)
        buttons = QHBoxLayout()
        self.runButton = QPushButton("시나리오 실행")
        self.runButton.setEnabled(False)
        self.runButton.clicked.connect(self.startRun)
        self.applyButton = QPushButton("메인 재생에 적용")
        self.applyButton.setEnabled(False)
        self.applyButton.clicked.connect(self.applyToMain)
        self.cancelButton = QPushButton("실행 중지")
        self.cancelButton.setEnabled(False)
        self.cancelButton.clicked.connect(self.cancelRun)
        self.openResultsButton = QPushButton("결과 폴더 열기")
        self.openResultsButton.setEnabled(False)
        self.openResultsButton.clicked.connect(self.openResults)
        for button in (self.applyButton, self.runButton, self.cancelButton, self.openResultsButton):
            buttons.addWidget(button)
        layout.addLayout(buttons)

    def chooseFmu(self):
        path, _ = QFileDialog.getOpenFileName(self, "FMU 선택", self.fmuPath.text(), "FMU (*.fmu)")
        if path:
            self.scenarioCleared.emit()
            self.fmuPath.setText(path)
            for entry in self.entries.values():
                entry["result"] = entry["directory"] = None
                entry["item"].setText(1, "미검증")
            self.showStoredResult()
            self.applyToMain()

    def chooseScenario(self):
        paths, _ = QFileDialog.getOpenFileNames(self, "시나리오 로드 (여러 파일 선택 가능)",
                                               self.scenarioPath.text(), "JSON (*.json)")
        for path in paths:
            self.loadScenario(path)

    def loadScenario(self, path):
        if self.worker is not None and self.worker.isRunning():
            return False
        path = str(Path(path).resolve())
        entry = self.entries.get(path)
        if entry is None:
            parent_path = str(Path(path).parent)
            group = next((self.scenarioTree.topLevelItem(i)
                          for i in range(self.scenarioTree.topLevelItemCount())
                          if self.scenarioTree.topLevelItem(i).toolTip(0) == parent_path), None)
            if group is None:
                group = QTreeWidgetItem(self.scenarioTree, [Path(parent_path).name])
                group.setToolTip(0, parent_path)
                group.setFlags(group.flags() & ~Qt.ItemIsSelectable)
            item = QTreeWidgetItem(group, [Path(path).name])
            item.setData(0, Qt.UserRole, path)
            item.setToolTip(0, path)
            group.setExpanded(True)
            entry = {"item": item}
            self.entries[path] = entry
        entry.update(scenario=None, error=None, result=None, directory=None)
        try:
            entry["scenario"] = json.loads(Path(path).read_text(encoding="utf-8-sig"))
        except Exception as error:
            entry["error"] = str(error)
        self.scenarioTree.blockSignals(True)
        self.scenarioTree.setCurrentItem(entry["item"])
        self.scenarioTree.blockSignals(False)
        return self.selectScenario(entry["item"])

    def selectScenario(self, item, previous=None):
        if item is None or (self.worker is not None and self.worker.isRunning()):
            return False
        path = item.data(0, Qt.UserRole)
        if path not in self.entries:
            return False
        self.scenarioCleared.emit()
        self.currentEntry = self.entries[path]
        self.scenario = self.currentEntry["scenario"]
        self.scenarioPath.setText(path)
        self.preview.clear()
        self.runButton.setEnabled(False)
        self.applyButton.setEnabled(False)
        self.showStoredResult()
        self.tabs.setCurrentWidget(self.preview)
        if self.currentEntry["error"]:
            self.status.setText(f"로드 실패: {self.currentEntry['error']}")
            item.setText(1, "로드 실패")
            return False
        self.preview.setPlainText(json.dumps(self.scenario, ensure_ascii=False, indent=2))
        valid = self.applyToMain()
        if self.result is not None and valid:
            verdict = self.result["passed"]
            item.setText(1, "합격" if verdict is True else "실패" if verdict is False else "판정 기준 없음")
        else:
            item.setText(1, "검증 완료" if valid else "검증 실패")
        return valid

    def showStoredResult(self):
        entry = self.currentEntry or {}
        if entry.get("resultFmu") != self.fmuPath.text():
            entry["result"] = entry["directory"] = None
        self.result, self.resultDirectory = entry.get("result"), entry.get("directory")
        self.plot.clear()
        self.report.clear()
        self.openResultsButton.setEnabled(self.resultDirectory is not None)
        if self.result is not None:
            self.plot.setResult(self.result["samples"], self.scenario["outputs"])
            report = {k: v for k, v in self.result.items() if k != "samples"}
            self.report.setPlainText(json.dumps(report, ensure_ascii=False, indent=2)
                                     + "\n\n" + self.resultDirectory)

    def validate(self):
        self.runButton.setEnabled(False)
        self.applyButton.setEnabled(False)
        try:
            if self.scenario is None or not self.fmuPath.text():
                self.status.setText("FMU와 JSON 시나리오를 모두 선택하세요.")
                return False
            md = read_model_description(self.fmuPath.text())
            _, count = validate_scenario(self.scenario, md)
            self.status.setText(f"검증 완료: {count} 스텝 / 통신 간격 {self.scenario['step_size']}초")
            self.runButton.setEnabled(True)
            self.applyButton.setEnabled(True)
            return True
        except Exception as error:
            self.status.setText(f"검증 실패: {error}")
            return False

    def applyToMain(self):
        if not self.validate():
            return False
        self.scenarioApplied.emit(self.fmuPath.text(), self.scenarioPath.text())
        return True

    def chooseOutput(self):
        path = QFileDialog.getExistingDirectory(self, "결과 저장 위치", self.outputPath.text())
        if path:
            self.outputPath.setText(path)

    def startRun(self):
        if self.worker is not None and self.worker.isRunning():
            return
        if not self.validate():
            return
        if not self.outputPath.text().strip():
            self.status.setText("결과 저장 위치를 입력하세요.")
            return
        directory = Path(self.outputPath.text()) / datetime.now().strftime("%Y%m%d-%H%M%S-%f")
        self.result = self.resultDirectory = None
        if self.currentEntry is not None:
            self.currentEntry.update(result=None, directory=None)
            self.currentEntry["item"].setText(1, "실행 중")
        self.plot.clear()
        self.report.clear()
        self.openResultsButton.setEnabled(False)
        self.setBusy(True)
        self.status.setText("실행 중...")
        self.worker = ScenarioWorker(self.fmuPath.text(), self.scenario, directory, self)
        self.worker.completed.connect(self.completed)
        self.worker.failed.connect(self.failed)
        self.worker.finished.connect(lambda: self.setBusy(False))
        self.worker.start()

    def setBusy(self, busy):
        for widget in (self.fmuButton, self.loadButton, self.outputButton, self.outputPath,
                       self.scenarioTree):
            widget.setEnabled(not busy)
        self.runButton.setEnabled(not busy and self.scenario is not None)
        self.applyButton.setEnabled(not busy and self.scenario is not None)
        self.cancelButton.setEnabled(busy)

    def failed(self, message):
        self.status.setText(f"실행 실패/중지: {message}")
        if self.currentEntry is not None:
            self.currentEntry["item"].setText(1, "실패 / 중지")

    def cancelRun(self):
        if self.worker is not None and self.worker.isRunning():
            self.worker.requestInterruption()
            self.status.setText("중지 요청됨: 현재 FMU 계산 구간이 끝나면 중지합니다.")

    def completed(self, result, directory):
        self.result, self.resultDirectory = result, directory
        rows = result["samples"]
        self.plot.setResult(rows, self.scenario["outputs"])
        report = {k: v for k, v in result.items() if k != "samples"}
        self.report.setPlainText(json.dumps(report, ensure_ascii=False, indent=2) + "\n\n" + directory)
        verdict = "합격" if result["passed"] is True else "실패" if result["passed"] is False else "판정 기준 없음"
        if self.currentEntry is not None:
            self.currentEntry.update(result=result, directory=directory, resultFmu=self.fmuPath.text())
            self.currentEntry["item"].setText(1, verdict)
        summary = result["summary"]
        # The FMU's own messages are the only account of why it refused a call.
        noise = "".join(f" / FMU {label} {count}건" for label, count in
                        (("오류", summary["log_errors"]), ("경고", summary["log_warnings"]))
                        if count)
        if summary["termination"] == "fmu_terminated":
            noise += " / FMU가 조기 종료를 요청함"
        self.status.setText(f"완료 — {verdict} / {len(rows)}개 샘플{noise} / 저장: {directory}")
        self.openResultsButton.setEnabled(True)
        self.tabs.setCurrentWidget(self.plot)
        self.resultReady.emit(result)

    def openResults(self):
        if self.resultDirectory:
            QDesktopServices.openUrl(QUrl.fromLocalFile(self.resultDirectory))

    def reject(self):
        if self.worker is not None and self.worker.isRunning():
            self.cancelRun()
            return
        super().reject()

    def closeEvent(self, event):
        if self.worker is not None and self.worker.isRunning():
            self.cancelRun()
            event.ignore()
        else:
            super().closeEvent(event)
