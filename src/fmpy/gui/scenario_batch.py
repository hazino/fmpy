"""Run a user-picked collection of scenario files against one FMU and tally
the pass/fail verdicts into one report.

Unlike ScenarioProposalsDialog (which runs what fmpy.scenario_agent.plan
derives from the FMU's own declared structure, with no acceptance criteria),
this runs scenarios the user already wrote or picked up elsewhere -- e.g. the
seven hand-authored ones in examples/agent/pid_demo/scenarios/ -- and expects
most of them to actually carry checks. The point is the aggregate: how many
passed, how many failed, how many have no verdict, how many could not even
run, in one place, rather than reading seven separate report.json files.

A plain QWidget (despite the class's name), so ScenarioDialog can embed it as
one of its own tabs instead of popping it up as a separate window.
"""
from pathlib import Path

from PySide6.QtCore import QThread, Qt, QUrl, Signal
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import (QAbstractItemView, QFileDialog, QHBoxLayout, QHeaderView,
                               QLabel, QLineEdit, QPushButton, QTableWidget, QTableWidgetItem,
                               QTextEdit, QVBoxLayout, QWidget)

from fmpy.scenario_agent import run_many

COLUMNS = ("파일", "결과")


class BatchRunner(QThread):
    """Runs fmpy.scenario_agent.run_many off the UI thread, reporting as it goes."""

    progress = Signal(int, dict)  # 0-based position among the files handed to this worker, entry
    allFinished = Signal(dict)  # the full run_many envelope

    def __init__(self, fmu, paths, output, parent=None):
        super().__init__(parent)
        self.fmu = fmu
        self.paths = paths
        self.output = output

    def run(self):
        envelope = run_many(self.fmu, self.paths, output=self.output,
                            on_progress=lambda index, entry: self.progress.emit(index, entry),
                            cancelled=self.isInterruptionRequested)
        self.allFinished.emit(envelope)


class ScenarioBatchDialog(QWidget):
    """Pick several scenario files, run the checked ones, read off one summary."""

    def __init__(self, fmu=None, parent=None):
        super().__init__(parent)
        self.setWindowTitle("여러 시나리오 실행")
        self.resize(1040, 680)
        self.worker = None
        self.paths = []  # list[Path], one per table row, same order
        self.activeRows = []  # table row for each position in the current run's job list
        self.results = {}  # table row -> run_many result entry
        self.lastReport = ""

        layout = QVBoxLayout(self)

        fmuRow = QHBoxLayout()
        self.fmuPath = QLineEdit(str(fmu or ""))
        self.fmuPath.setReadOnly(True)
        self.fmuButton = QPushButton("FMU 선택...")
        self.fmuButton.clicked.connect(self.chooseFmu)
        fmuRow.addWidget(QLabel("FMU"))
        fmuRow.addWidget(self.fmuPath, 1)
        fmuRow.addWidget(self.fmuButton)
        layout.addLayout(fmuRow)

        self.summaryLabel = QLabel("시나리오 파일을 추가하세요.")
        self.summaryLabel.setWordWrap(True)
        layout.addWidget(self.summaryLabel)

        self.table = QTableWidget(0, len(COLUMNS))
        self.table.setHorizontalHeaderLabels(COLUMNS)
        self.table.horizontalHeader().setSectionResizeMode(0, QHeaderView.Stretch)
        self.table.horizontalHeader().setSectionResizeMode(1, QHeaderView.Stretch)
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.itemSelectionChanged.connect(self.showDetail)
        layout.addWidget(self.table, 1)

        self.detail = QTextEdit()
        self.detail.setReadOnly(True)
        self.detail.setMaximumHeight(170)
        layout.addWidget(self.detail)

        outputRow = QHBoxLayout()
        self.outputPath = QLineEdit(str(Path.cwd() / "scenario-results" / "batch"))
        self.outputButton = QPushButton("저장 위치...")
        self.outputButton.clicked.connect(self.chooseOutput)
        outputRow.addWidget(QLabel("결과 폴더"))
        outputRow.addWidget(self.outputPath, 1)
        outputRow.addWidget(self.outputButton)
        layout.addLayout(outputRow)

        addRow = QHBoxLayout()
        self.addFilesButton = QPushButton("파일 추가...")
        self.addFilesButton.clicked.connect(self.addFiles)
        self.addFolderButton = QPushButton("폴더에서 추가...")
        self.addFolderButton.clicked.connect(self.addFolder)
        self.removeButton = QPushButton("선택 제거")
        self.removeButton.clicked.connect(self.removeSelected)
        self.clearButton = QPushButton("전체 지우기")
        self.clearButton.clicked.connect(self.clearAll)
        for button in (self.addFilesButton, self.addFolderButton, self.removeButton,
                       self.clearButton):
            addRow.addWidget(button)
        layout.addLayout(addRow)

        buttons = QHBoxLayout()
        self.selectAllButton = QPushButton("전체 선택")
        self.selectAllButton.clicked.connect(lambda: self.setAllChecked(True))
        self.selectNoneButton = QPushButton("전체 해제")
        self.selectNoneButton.clicked.connect(lambda: self.setAllChecked(False))
        self.runButton = QPushButton("선택 실행")
        self.runButton.setEnabled(False)
        self.runButton.clicked.connect(self.runSelected)
        self.cancelButton = QPushButton("중지")
        self.cancelButton.setEnabled(False)
        self.cancelButton.clicked.connect(self.cancelRun)
        self.openResultsButton = QPushButton("결과 폴더 열기")
        self.openResultsButton.setEnabled(False)
        self.openResultsButton.clicked.connect(self.openResults)
        self.saveReportButton = QPushButton("보고서 저장...")
        self.saveReportButton.setEnabled(False)
        self.saveReportButton.clicked.connect(self.saveReport)
        for button in (self.selectAllButton, self.selectNoneButton, self.runButton,
                       self.cancelButton, self.openResultsButton, self.saveReportButton):
            buttons.addWidget(button)
        layout.addLayout(buttons)

    # -- building the list -----------------------------------------------------

    def chooseFmu(self):
        path, _ = QFileDialog.getOpenFileName(self, "FMU 선택", self.fmuPath.text(), "FMU (*.fmu)")
        if path:
            self.fmuPath.setText(path)

    def chooseOutput(self):
        path = QFileDialog.getExistingDirectory(self, "결과 저장 위치", self.outputPath.text())
        if path:
            self.outputPath.setText(path)

    def addFiles(self):
        paths, _ = QFileDialog.getOpenFileNames(self, "시나리오 파일 추가", "", "JSON (*.json)")
        self.addPaths(Path(p) for p in paths)

    def addFolder(self):
        folder = QFileDialog.getExistingDirectory(self, "시나리오 폴더 추가")
        if folder:
            self.addPaths(sorted(Path(folder).glob("*.json")))

    def addPaths(self, paths):
        existing = set(self.paths)
        added = [p for p in paths if p not in existing and not existing.add(p)]
        self.paths.extend(added)
        self.refreshTable()

    def removeSelected(self):
        selected = {index.row() for index in self.table.selectedIndexes()}
        self.paths = [p for row, p in enumerate(self.paths) if row not in selected]
        self.results = {}
        self.refreshTable()

    def clearAll(self):
        self.paths = []
        self.results = {}
        self.refreshTable()

    def refreshTable(self):
        self.table.setRowCount(len(self.paths))
        for row, path in enumerate(self.paths):
            checkable = QTableWidgetItem(path.name)
            checkable.setFlags(Qt.ItemIsUserCheckable | Qt.ItemIsEnabled | Qt.ItemIsSelectable)
            checkable.setCheckState(Qt.Checked)
            checkable.setToolTip(str(path))
            self.table.setItem(row, 0, checkable)
            self.table.setItem(row, 1, QTableWidgetItem("대기"))
        self.table.resizeRowsToContents()
        self.runButton.setEnabled(bool(self.paths))
        self.summaryLabel.setText(f"{len(self.paths)}개 파일. 체크된 것만 실행합니다."
                                  if self.paths else "시나리오 파일을 추가하세요.")

    def setAllChecked(self, checked):
        state = Qt.Checked if checked else Qt.Unchecked
        for row in range(self.table.rowCount()):
            self.table.item(row, 0).setCheckState(state)

    # -- running ---------------------------------------------------------------

    def runSelected(self):
        if self.worker is not None and self.worker.isRunning():
            return
        if not self.fmuPath.text():
            self.summaryLabel.setText("먼저 FMU를 선택하세요.")
            return
        self.activeRows = [row for row in range(self.table.rowCount())
                           if self.table.item(row, 0).checkState() == Qt.Checked]
        if not self.activeRows:
            self.summaryLabel.setText("실행할 항목을 선택하세요.")
            return
        if not self.outputPath.text().strip():
            self.summaryLabel.setText("결과 저장 위치를 입력하세요.")
            return
        for row in self.activeRows:
            self.table.setItem(row, 1, QTableWidgetItem("실행 대기"))
        self.results = {}
        self.saveReportButton.setEnabled(False)
        self.setBusy(True)
        self.summaryLabel.setText(f"{len(self.activeRows)}개 실행 중...")
        jobs = [self.paths[row] for row in self.activeRows]
        self.worker = BatchRunner(self.fmuPath.text(), jobs, self.outputPath.text(), self)
        self.worker.progress.connect(self.onProgress)
        self.worker.allFinished.connect(self.onAllFinished)
        self.worker.start()

    def onProgress(self, index, entry):
        row = self.activeRows[index]
        self.results[row] = entry
        if entry.get("ok"):
            passed = entry.get("passed")
            verdict = "합격" if passed is True else "불합격" if passed is False else "판정 없음"
            samples = entry.get("sample_count")
            text = verdict + (f" / {samples}개 샘플" if samples else "")
            directory = (entry.get("artifacts") or {}).get("directory")
            if directory:
                self.openResultsButton.setEnabled(True)
        else:
            text = f"오류: {entry['errors'][0]['message']}"
        self.table.setItem(row, 1, QTableWidgetItem(text))
        if row in {index.row() for index in self.table.selectedIndexes()}:
            self.showDetail()

    def onAllFinished(self, envelope):
        self.setBusy(False)
        if not envelope.get("ok"):
            self.summaryLabel.setText(f"실행할 수 없습니다: {envelope['errors'][0]['message']}")
            return
        summary = envelope["summary"]
        self.lastReport = envelope["report"]
        self.summaryLabel.setText(
            f"{summary['total']}개 중 합격 {summary['passed']} · 불합격 {summary['failed']} · "
            f"판정 기준 없음 {summary['no_verdict']} · 실행 불가 {summary['error']}")
        self.saveReportButton.setEnabled(True)

    def setBusy(self, busy):
        for widget in (self.runButton, self.selectAllButton, self.selectNoneButton,
                       self.addFilesButton, self.addFolderButton, self.removeButton,
                       self.clearButton, self.outputButton, self.fmuButton):
            widget.setEnabled(not busy)
        self.cancelButton.setEnabled(busy)

    def cancelRun(self):
        if self.worker is not None and self.worker.isRunning():
            self.worker.requestInterruption()
            self.summaryLabel.setText("중지 요청됨: 현재 시나리오가 끝나면 나머지는 건너뜁니다.")

    # -- detail / artifacts ------------------------------------------------------

    def showDetail(self):
        rows = {index.row() for index in self.table.selectedIndexes()}
        if not rows:
            return
        row = next(iter(rows))
        path = self.paths[row]
        text = f"파일: {path}"
        entry = self.results.get(row)
        if entry is not None:
            if entry.get("ok"):
                summary = (entry.get("report") or {}).get("summary", {})
                text += (f"\n\nID: {entry.get('id')}\n"
                        f"종료 이유: {summary.get('termination')}\n"
                        f"판정: {entry.get('passed')}\n"
                        f"실패한 검사: {', '.join(summary.get('failed_checks') or []) or '(없음)'}\n"
                        f"FMU 오류 {summary.get('log_errors', 0)}건, "
                        f"경고 {summary.get('log_warnings', 0)}건")
                directory = (entry.get("artifacts") or {}).get("directory")
                if directory:
                    text += f"\n저장 위치: {directory}"
            else:
                text += "\n\n오류: " + "; ".join(e["message"] for e in entry["errors"])
        self.detail.setPlainText(text)

    def openResults(self):
        rows = {index.row() for index in self.table.selectedIndexes()}
        row = next(iter(rows), None)
        entry = self.results.get(row)
        directory = (entry or {}).get("artifacts", {}).get("directory")
        if not directory:
            directory = next((e.get("artifacts", {}).get("directory")
                             for e in self.results.values() if e.get("artifacts", {}).get("directory")),
                            None)
        if directory:
            QDesktopServices.openUrl(QUrl.fromLocalFile(directory))

    def saveReport(self):
        if not self.lastReport:
            return
        path, _ = QFileDialog.getSaveFileName(self, "보고서 저장",
                                              str(Path(self.outputPath.text()) / "summary.md"),
                                              "Markdown (*.md);;All Files (*.*)")
        if path:
            Path(path).write_text(self.lastReport, encoding="utf-8")
            self.summaryLabel.setText(f"보고서 저장함: {path}")

    def isBusy(self):
        return self.worker is not None and self.worker.isRunning()

    def closeEvent(self, event):
        if self.isBusy():
            self.cancelRun()
            event.ignore()
        else:
            super().closeEvent(event)
