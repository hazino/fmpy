"""GUI adapter for the same deterministic runner used by the command line."""
import csv
import json
from copy import deepcopy
from datetime import datetime
from pathlib import Path

import pyqtgraph as pg
from PySide6.QtCore import QThread, Signal, QUrl
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import (QDialog, QFileDialog, QHBoxLayout, QLabel, QLineEdit,
                               QPushButton, QTabWidget, QTextEdit, QVBoxLayout, QWidget)

from fmpy import read_model_description
from fmpy.scenario import run_scenario, validate_scenario


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
            self.directory.mkdir(parents=True, exist_ok=False)
            with (self.directory / "results.csv").open("w", newline="", encoding="utf-8") as stream:
                writer = csv.DictWriter(stream, fieldnames=["time", *self.scenario["outputs"]])
                writer.writeheader()
                writer.writerows(result["samples"])
            report = {k: v for k, v in result.items() if k != "samples"}
            (self.directory / "report.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
            (self.directory / "scenario.json").write_text(json.dumps(self.scenario, indent=2), encoding="utf-8")
            self.completed.emit(result, str(self.directory))
        except Exception as error:
            self.failed.emit(str(error))


class ScenarioDialog(QDialog):
    def __init__(self, fmu=None, parent=None):
        super().__init__(parent)
        self.setWindowTitle("FMI 2.0 Scenario / 시나리오 실행")
        self.resize(980, 700)
        self.scenario = None
        self.worker = None
        self.result = None
        self.resultDirectory = None
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
        layout.addWidget(QLabel("JSON의 초기값·시간·입력을 사용합니다. 기본 Simulate 화면의 설정과는 별도로 실행합니다."))
        self.tabs = QTabWidget()
        self.preview = QTextEdit()
        self.preview.setReadOnly(True)
        self.tabs.addTab(self.preview, "시나리오")
        self.plot = pg.PlotWidget()
        self.plot.setLabel("bottom", "Time", units="s")
        self.plot.showGrid(x=True, y=True)
        self.plot.addLegend()
        self.tabs.addTab(self.plot, "결과 그래프")
        self.report = QTextEdit()
        self.report.setReadOnly(True)
        self.tabs.addTab(self.report, "판정 / 결과")
        layout.addWidget(self.tabs, 1)
        self.status = QLabel("FMU와 JSON 시나리오를 선택하세요.")
        self.status.setWordWrap(True)
        layout.addWidget(self.status)
        buttons = QHBoxLayout()
        self.runButton = QPushButton("시나리오 실행")
        self.runButton.setEnabled(False)
        self.runButton.clicked.connect(self.startRun)
        self.cancelButton = QPushButton("실행 중지")
        self.cancelButton.setEnabled(False)
        self.cancelButton.clicked.connect(self.cancelRun)
        self.openResultsButton = QPushButton("결과 폴더 열기")
        self.openResultsButton.setEnabled(False)
        self.openResultsButton.clicked.connect(self.openResults)
        for button in (self.runButton, self.cancelButton, self.openResultsButton):
            buttons.addWidget(button)
        layout.addLayout(buttons)

    def chooseFmu(self):
        path, _ = QFileDialog.getOpenFileName(self, "FMU 선택", self.fmuPath.text(), "FMU (*.fmu)")
        if path:
            self.fmuPath.setText(path)
            self.validate()

    def chooseScenario(self):
        path, _ = QFileDialog.getOpenFileName(self, "시나리오 로드", self.scenarioPath.text(), "JSON (*.json)")
        if path:
            self.loadScenario(path)

    def loadScenario(self, path):
        self.scenario = None
        self.scenarioPath.setText(str(path))
        self.preview.clear()
        self.runButton.setEnabled(False)
        try:
            self.scenario = json.loads(Path(path).read_text(encoding="utf-8-sig"))
            self.preview.setPlainText(json.dumps(self.scenario, ensure_ascii=False, indent=2))
            self.tabs.setCurrentWidget(self.preview)
            return self.validate()
        except Exception as error:
            self.status.setText(f"로드 실패: {error}")
            return False

    def validate(self):
        self.runButton.setEnabled(False)
        try:
            if self.scenario is None or not self.fmuPath.text():
                self.status.setText("FMU와 JSON 시나리오를 모두 선택하세요.")
                return False
            md = read_model_description(self.fmuPath.text())
            _, count = validate_scenario(self.scenario, md)
            self.status.setText(f"검증 완료: {count} 스텝 / 통신 간격 {self.scenario['step_size']}초")
            self.runButton.setEnabled(True)
            return True
        except Exception as error:
            self.status.setText(f"검증 실패: {error}")
            return False

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
        self.plot.clear()
        self.report.clear()
        self.openResultsButton.setEnabled(False)
        self.setBusy(True)
        self.status.setText("실행 중...")
        self.worker = ScenarioWorker(self.fmuPath.text(), self.scenario, directory, self)
        self.worker.completed.connect(self.completed)
        self.worker.failed.connect(lambda message: self.status.setText(f"실행 실패/중지: {message}"))
        self.worker.finished.connect(lambda: self.setBusy(False))
        self.worker.start()

    def setBusy(self, busy):
        for widget in (self.fmuButton, self.loadButton, self.outputButton, self.outputPath):
            widget.setEnabled(not busy)
        self.runButton.setEnabled(not busy and self.scenario is not None)
        self.cancelButton.setEnabled(busy)

    def cancelRun(self):
        if self.worker is not None and self.worker.isRunning():
            self.worker.requestInterruption()
            self.status.setText("중지 요청됨: 현재 FMU 계산 구간이 끝나면 중지합니다.")

    def completed(self, result, directory):
        self.result, self.resultDirectory = result, directory
        rows = result["samples"]
        times = [row["time"] for row in rows]
        for i, name in enumerate(self.scenario["outputs"]):
            if rows and isinstance(rows[0][name], (int, float, bool)):
                self.plot.plot(times, [float(row[name]) for row in rows],
                               name=name, pen=pg.mkPen(pg.intColor(i), width=2))
        report = {k: v for k, v in result.items() if k != "samples"}
        self.report.setPlainText(json.dumps(report, ensure_ascii=False, indent=2) + "\n\n" + directory)
        verdict = "합격" if result["passed"] is True else "실패" if result["passed"] is False else "판정 기준 없음"
        self.status.setText(f"완료 — {verdict} / {len(rows)}개 샘플 / 저장: {directory}")
        self.openResultsButton.setEnabled(True)
        self.tabs.setCurrentWidget(self.plot)

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
