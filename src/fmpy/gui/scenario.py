"""GUI adapter for the same deterministic runner used by the command line."""
import json
from copy import deepcopy
from datetime import datetime
from pathlib import Path

from PySide6.QtCore import Qt, QThread, Signal, QUrl
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import (QAbstractItemView, QComboBox, QDialog, QFileDialog, QHBoxLayout,
                               QHeaderView, QLabel, QLineEdit, QPushButton, QSplitter,
                               QTableWidget, QTableWidgetItem, QTabWidget, QTextEdit,
                               QVBoxLayout, QWidget)

from fmpy import read_model_description
from fmpy.scenario import run_scenario, validate_scenario, write_results
from fmpy.scenario_backends import ollama_models
from . import agent_settings
from .agent_chat import AgentChatDialog
from .scenario_batch import AnalysisRunner, BatchRunner
from .scenario_proposals import ScenarioProposalsDialog
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
    """FMI 2.0 scenario window: a left-hand list of scenario files (any number
    of them can be loaded, picked via a multi-select file dialog) and, on the
    right, what the one currently highlighted in that list looks like and did
    when run. Clicking a row loads that scenario exactly as the old
    single-file "시나리오 로드..." did -- including applying it to the main
    window's own Play button -- so every single-scenario test and workflow
    this already had keeps working unchanged. What is new is the list itself:
    check off several rows and "선택 항목 실행" runs all of them at once
    (fmpy.scenario_agent.run_many under the hood) and tallies pass/fail/no-
    verdict/error into the "판정 / 결과" tab, where a model can also be asked
    to summarize that same tally in words (the execution-and-analysis agent;
    see fmpy.scenario_backends). There used to be a separate "여러 시나리오
    실행" tab for this; it is gone now that the list lives here instead.
    """

    scenarioApplied = Signal(str, str)
    scenarioCleared = Signal()
    resultReady = Signal(object)

    def __init__(self, fmu=None, parent=None):
        super().__init__(parent)
        self.setWindowTitle("FMI 2.0 Scenario / 시나리오 실행")
        # A QDialog has no maximize button by default; this window can hold a
        # long scenario list and wide result graphs, so let it fill the
        # screen like any other main window when the user wants that.
        self.setWindowFlags(self.windowFlags() | Qt.WindowMaximizeButtonHint
                            | Qt.WindowMinimizeButtonHint)
        self.resize(1200, 760)

        # The single "currently loaded" scenario -- what loadScenario()/
        # validate()/startRun()/applyToMain() operate on. This is exactly the
        # state the pre-existing single-scenario flow (and MainWindow's own
        # Play-button integration) already used; the file list below just
        # gives it more than one way to get populated.
        self.scenario = None
        self.scenarioPath = ""
        self.worker = None
        self.result = None
        self.resultDirectory = None
        self._runningRow = None  # table row the current single run belongs to, if any

        # The file list and its batch run (new).
        self.paths = []  # list[Path], one per table row, same order
        self.results = {}  # table row -> run result entry (single or batch)
        self.activeRows = []  # table row for each position in the current batch job list
        self.batchWorker = None
        self.analysisWorker = None
        self.lastReport = ""
        self.lastSummary = None
        self.lastResults = None
        self.settings = agent_settings.load()

        outer = QVBoxLayout(self)

        fmuRow = QHBoxLayout()
        self.fmuPath = QLineEdit(str(fmu or ""))
        self.fmuPath.setReadOnly(True)
        self.fmuPath.textChanged.connect(self.syncFmu)
        self.fmuButton = QPushButton("FMU 선택...")
        self.fmuButton.clicked.connect(self.chooseFmu)
        fmuRow.addWidget(QLabel("FMU"))
        fmuRow.addWidget(self.fmuPath, 1)
        fmuRow.addWidget(self.fmuButton)
        self.toggleSidebarButton = QPushButton("목록 숨기기")
        self.toggleSidebarButton.setToolTip("왼쪽 시나리오 목록을 숨기거나 다시 보여줍니다.")
        self.toggleSidebarButton.clicked.connect(self.toggleSidebar)
        fmuRow.addWidget(self.toggleSidebarButton)
        outer.addLayout(fmuRow)

        splitter = QSplitter()
        outer.addWidget(splitter, 1)
        self.sidebar = self._buildFileListSidebar()
        splitter.addWidget(self.sidebar)
        splitter.addWidget(self._buildMainArea())
        splitter.setStretchFactor(0, 0)
        splitter.setStretchFactor(1, 1)
        splitter.setSizes([340, 860])

        self.status = QLabel("FMU를 선택하고 시나리오를 로드하세요.")
        self.status.setWordWrap(True)
        outer.addWidget(self.status)

    # -- building the window ----------------------------------------------------

    def _buildFileListSidebar(self):
        sidebar = QWidget()
        sidebar.setMinimumWidth(280)
        sidebar.setMaximumWidth(480)
        layout = QVBoxLayout(sidebar)
        layout.setContentsMargins(8, 8, 8, 8)

        layout.addWidget(QLabel("<b>시나리오 목록</b>"))

        addRow = QHBoxLayout()
        self.loadButton = QPushButton("시나리오 로드...")
        self.loadButton.setToolTip("JSON 파일 여러 개를 한 번에 선택할 수 있습니다.")
        self.loadButton.clicked.connect(self.chooseScenario)
        self.addFolderButton = QPushButton("폴더에서 추가...")
        self.addFolderButton.clicked.connect(self.addFolder)
        addRow.addWidget(self.loadButton)
        addRow.addWidget(self.addFolderButton)
        layout.addLayout(addRow)

        manageRow = QHBoxLayout()
        self.removeButton = QPushButton("선택 제거")
        self.removeButton.clicked.connect(self.removeSelectedFiles)
        self.clearFilesButton = QPushButton("전체 지우기")
        self.clearFilesButton.clicked.connect(self.clearFiles)
        manageRow.addWidget(self.removeButton)
        manageRow.addWidget(self.clearFilesButton)
        layout.addLayout(manageRow)

        self.fileTable = QTableWidget(0, 2)
        self.fileTable.setHorizontalHeaderLabels(("파일", "결과"))
        self.fileTable.horizontalHeader().setSectionResizeMode(0, QHeaderView.Stretch)
        self.fileTable.horizontalHeader().setSectionResizeMode(1, QHeaderView.Stretch)
        self.fileTable.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.fileTable.itemSelectionChanged.connect(self.onFileSelectionChanged)
        layout.addWidget(self.fileTable, 1)

        checkRow = QHBoxLayout()
        self.selectAllButton = QPushButton("전체 선택")
        self.selectAllButton.clicked.connect(lambda: self.setAllChecked(True))
        self.selectNoneButton = QPushButton("전체 해제")
        self.selectNoneButton.clicked.connect(lambda: self.setAllChecked(False))
        checkRow.addWidget(self.selectAllButton)
        checkRow.addWidget(self.selectNoneButton)
        layout.addLayout(checkRow)

        outputLabelRow = QHBoxLayout()
        outputLabelRow.addWidget(QLabel("결과 폴더"))
        self.outputButton = QPushButton("저장 위치...")
        self.outputButton.clicked.connect(self.chooseOutput)
        outputLabelRow.addWidget(self.outputButton)
        layout.addLayout(outputLabelRow)
        self.outputPath = QLineEdit(str(Path.cwd() / "scenario-results" / "gui"))
        layout.addWidget(self.outputPath)

        runRow = QHBoxLayout()
        self.runButton = QPushButton("시나리오 실행")
        self.runButton.setEnabled(False)
        self.runButton.setToolTip("왼쪽에서 선택(강조)한 시나리오 하나를 실행합니다.")
        self.runButton.clicked.connect(self.startRun)
        self.runSelectedButton = QPushButton("선택 항목 실행")
        self.runSelectedButton.setEnabled(False)
        self.runSelectedButton.setToolTip(
            "체크된 파일을 전부 실행하고 합격/불합격을 종합합니다(아래 '판정 / 결과' 탭).")
        self.runSelectedButton.clicked.connect(self.runCheckedFiles)
        runRow.addWidget(self.runButton)
        runRow.addWidget(self.runSelectedButton)
        layout.addLayout(runRow)

        actionRow = QHBoxLayout()
        self.applyButton = QPushButton("메인 재생에 적용")
        self.applyButton.setEnabled(False)
        self.applyButton.clicked.connect(self.applyToMain)
        self.cancelButton = QPushButton("중지")
        self.cancelButton.setEnabled(False)
        self.cancelButton.clicked.connect(self.cancelRun)
        actionRow.addWidget(self.applyButton)
        actionRow.addWidget(self.cancelButton)
        layout.addLayout(actionRow)

        self.openResultsButton = QPushButton("결과 폴더 열기")
        self.openResultsButton.setEnabled(False)
        self.openResultsButton.clicked.connect(self.openResults)
        layout.addWidget(self.openResultsButton)

        self.proposalsButton = QPushButton("시나리오 제안 보기...")
        self.proposalsButton.setToolTip(
            "선택한 FMU가 선언한 구조에서 시나리오 후보를 뽑아 보여줍니다. JSON 로드는 필요 없습니다.")
        self.proposalsButton.clicked.connect(self.openProposals)
        layout.addWidget(self.proposalsButton)

        return sidebar

    def _buildMainArea(self):
        main = QWidget()
        layout = QVBoxLayout(main)

        self.tabs = QTabWidget()
        self.preview = QTextEdit()
        self.preview.setReadOnly(True)
        self.tabs.addTab(self.preview, "시나리오")
        self.plot = ScenarioResults()
        self.tabs.addTab(self.plot, "결과 그래프")
        self.tabs.addTab(self._buildReportTab(), "판정 / 결과")
        self.agentChatTab = AgentChatDialog(self.fmuPath.text())
        self.tabs.addTab(self.agentChatTab, "시나리오 생성")
        layout.addWidget(self.tabs)

        self.onAnalysisBackendChanged(self.analysisBackendCombo.currentIndex())
        return main

    def _buildReportTab(self):
        """판정 / 결과: the highlighted row's own report, the batch summary
        (when "선택 항목 실행" has been used), and the analysis agent."""
        tab = QWidget()
        layout = QVBoxLayout(tab)

        self.report = QTextEdit()
        self.report.setReadOnly(True)
        layout.addWidget(self.report, 1)

        self.summaryLabel = QLabel("")
        self.summaryLabel.setWordWrap(True)
        layout.addWidget(self.summaryLabel)
        self.saveReportButton = QPushButton("보고서 저장...")
        self.saveReportButton.setEnabled(False)
        self.saveReportButton.clicked.connect(self.saveReport)
        layout.addWidget(self.saveReportButton)

        layout.addWidget(QLabel("<b>분석 에이전트</b> — '선택 항목 실행'의 결과를 모델에게 보여주고 "
                                "설명을 요청합니다. 모델은 아무것도 다시 실행하지 않습니다."))
        analysisRow = QHBoxLayout()
        self.analysisBackendCombo = QComboBox()
        self.analysisBackendCombo.addItems(["Ollama (로컬)", "Claude (API)"])
        self.analysisBackendCombo.setCurrentIndex(0 if self.settings["backend"] == "ollama" else 1)
        self.analysisBackendCombo.currentIndexChanged.connect(self.onAnalysisBackendChanged)
        self.analysisModelCombo = QComboBox()
        self.analysisModelCombo.setEditable(True)
        self.analysisRequestButton = QPushButton("분석 요청")
        self.analysisRequestButton.setEnabled(False)
        self.analysisRequestButton.clicked.connect(self.requestAnalysis)
        self.analysisSettingsButton = QPushButton("설정...")
        self.analysisSettingsButton.setToolTip(
            "기본 백엔드·모델·API 키·결과 폴더를 설정합니다. '시나리오 생성' 탭과 같은 설정입니다.")
        self.analysisSettingsButton.clicked.connect(self.openSettings)
        analysisRow.addWidget(QLabel("백엔드"))
        analysisRow.addWidget(self.analysisBackendCombo)
        analysisRow.addWidget(QLabel("모델"))
        analysisRow.addWidget(self.analysisModelCombo, 1)
        analysisRow.addWidget(self.analysisRequestButton)
        analysisRow.addWidget(self.analysisSettingsButton)
        layout.addLayout(analysisRow)
        self.analysisText = QTextEdit()
        self.analysisText.setReadOnly(True)
        self.analysisText.setMaximumHeight(140)
        layout.addWidget(self.analysisText)

        return tab

    # -- FMU / list management ----------------------------------------------------

    def chooseFmu(self):
        path, _ = QFileDialog.getOpenFileName(self, "FMU 선택", self.fmuPath.text(), "FMU (*.fmu)")
        if path:
            self.scenarioCleared.emit()
            self.fmuPath.setText(path)
            self.applyToMain()

    def syncFmu(self, text):
        """Keep the embedded agent-chat tab on the same FMU as this dialog."""
        self.agentChatTab.fmuPath.setText(text)

    def showAgentChatTab(self):
        self.tabs.setCurrentWidget(self.agentChatTab)

    def toggleSidebar(self):
        hide = self.sidebar.isVisible()
        self.sidebar.setVisible(not hide)
        self.toggleSidebarButton.setText("목록 보이기" if hide else "목록 숨기기")

    def openProposals(self):
        if not self.fmuPath.text():
            self.status.setText("먼저 FMU를 선택하세요.")
            return
        ScenarioProposalsDialog(self.fmuPath.text(), self).exec()

    def chooseOutput(self):
        path = QFileDialog.getExistingDirectory(self, "결과 저장 위치", self.outputPath.text())
        if path:
            self.outputPath.setText(path)

    def chooseScenario(self):
        paths, _ = QFileDialog.getOpenFileNames(self, "시나리오 로드", "", "JSON (*.json)")
        if paths:
            self.addScenarioPaths(Path(p) for p in paths)

    def addFolder(self):
        folder = QFileDialog.getExistingDirectory(self, "시나리오 폴더 추가")
        if folder:
            self.addScenarioPaths(sorted(Path(folder).glob("*.json")))

    def addScenarioPaths(self, paths):
        existing = set(self.paths)
        added = [p for p in paths if p not in existing and not existing.add(p)]
        if not added:
            return
        first_new_row = len(self.paths)
        self.paths.extend(added)
        self.refreshFileTable()
        # Highlighting the first newly added file loads it exactly like the
        # old single-file "시나리오 로드..." did (see onFileSelectionChanged).
        self.fileTable.selectRow(first_new_row)

    def removeSelectedFiles(self):
        selected = {index.row() for index in self.fileTable.selectedIndexes()}
        if not selected:
            return
        self.paths = [p for row, p in enumerate(self.paths) if row not in selected]
        self.results = {}
        self.refreshFileTable()
        self.scenarioCleared.emit()
        self.scenario = None
        self.preview.clear()
        self.applyButton.setEnabled(False)
        self.runButton.setEnabled(False)

    def clearFiles(self):
        if not self.paths:
            return
        self.paths = []
        self.results = {}
        self.refreshFileTable()
        self.scenarioCleared.emit()
        self.scenario = None
        self.preview.clear()
        self.applyButton.setEnabled(False)
        self.runButton.setEnabled(False)

    def refreshFileTable(self):
        self.fileTable.blockSignals(True)
        self.fileTable.setRowCount(len(self.paths))
        for row, path in enumerate(self.paths):
            checkable = QTableWidgetItem(path.name)
            checkable.setFlags(Qt.ItemIsUserCheckable | Qt.ItemIsEnabled | Qt.ItemIsSelectable)
            checkable.setCheckState(Qt.Checked)
            checkable.setToolTip(str(path))
            self.fileTable.setItem(row, 0, checkable)
            self.fileTable.setItem(row, 1, QTableWidgetItem("대기"))
        self.fileTable.resizeRowsToContents()
        self.fileTable.blockSignals(False)
        self.runSelectedButton.setEnabled(bool(self.paths))

    def setAllChecked(self, checked):
        state = Qt.Checked if checked else Qt.Unchecked
        for row in range(self.fileTable.rowCount()):
            self.fileTable.item(row, 0).setCheckState(state)

    def onFileSelectionChanged(self):
        rows = {index.row() for index in self.fileTable.selectedIndexes()}
        if len(rows) != 1:
            return
        row = next(iter(rows))
        self.loadScenario(self.paths[row])
        entry = self.results.get(row)
        if entry is not None:
            self.showEntryResult(entry)
        else:
            self.plot.clear()

    # -- the single "currently loaded" scenario: unchanged from before ------------

    def loadScenario(self, path):
        self.scenarioCleared.emit()
        self.scenario = None
        self.scenarioPath = str(path)
        self.preview.clear()
        self.runButton.setEnabled(False)
        self.applyButton.setEnabled(False)
        try:
            self.scenario = json.loads(Path(path).read_text(encoding="utf-8-sig"))
            self.preview.setPlainText(json.dumps(self.scenario, ensure_ascii=False, indent=2))
            self.tabs.setCurrentWidget(self.preview)
            return self.applyToMain()
        except Exception as error:
            self.status.setText(f"로드 실패: {error}")
            return False

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
        self.scenarioApplied.emit(self.fmuPath.text(), self.scenarioPath)
        return True

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
        self._runningRow = self._rowForPath(self.scenarioPath)
        self.setBusy(True)
        self.status.setText("실행 중...")
        self.worker = ScenarioWorker(self.fmuPath.text(), self.scenario, directory, self)
        self.worker.completed.connect(self.completed)
        self.worker.failed.connect(lambda message: self.status.setText(f"실행 실패/중지: {message}"))
        self.worker.finished.connect(lambda: self.setBusy(False))
        self.worker.start()

    def _rowForPath(self, path_text):
        for row, path in enumerate(self.paths):
            if str(path) == path_text:
                return row
        return None

    def setBusy(self, busy):
        for widget in (self.fmuButton, self.loadButton, self.addFolderButton, self.removeButton,
                       self.clearFilesButton, self.selectAllButton, self.selectNoneButton,
                       self.outputButton, self.outputPath):
            widget.setEnabled(not busy)
        self.runButton.setEnabled(not busy and self.scenario is not None)
        self.runSelectedButton.setEnabled(not busy and bool(self.paths))
        self.applyButton.setEnabled(not busy and self.scenario is not None)
        self.cancelButton.setEnabled(busy)

    def cancelRun(self):
        if self.worker is not None and self.worker.isRunning():
            self.worker.requestInterruption()
            self.status.setText("중지 요청됨: 현재 FMU 계산 구간이 끝나면 중지합니다.")
        elif self.batchWorker is not None and self.batchWorker.isRunning():
            self.batchWorker.requestInterruption()
            self.status.setText("중지 요청됨: 현재 시나리오가 끝나면 나머지는 건너뜁니다.")

    def completed(self, result, directory):
        self.result, self.resultDirectory = result, directory
        rows = result["samples"]
        self.plot.setResult(rows, self.scenario["outputs"])
        report = {k: v for k, v in result.items() if k != "samples"}
        self.report.setPlainText(json.dumps(report, ensure_ascii=False, indent=2) + "\n\n" + directory)
        verdict = "합격" if result["passed"] is True else "실패" if result["passed"] is False else "판정 기준 없음"
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
        if self._runningRow is not None:
            entry = {"ok": True, "passed": result["passed"], "sample_count": len(rows),
                    "report": report, "artifacts": {"directory": directory}}
            self.results[self._runningRow] = entry
            samples_text = f"{verdict} / {len(rows)}개 샘플"
            self.fileTable.setItem(self._runningRow, 1, QTableWidgetItem(samples_text))
            self._runningRow = None
        self.resultReady.emit(result)

    def openResults(self):
        directory = self.resultDirectory
        if not directory:
            rows = {index.row() for index in self.fileTable.selectedIndexes()}
            row = next(iter(rows), None)
            entry = self.results.get(row)
            directory = (entry or {}).get("artifacts", {}).get("directory")
        if not directory:
            directory = next((e.get("artifacts", {}).get("directory")
                             for e in self.results.values() if e.get("artifacts", {}).get("directory")),
                            None)
        if directory:
            QDesktopServices.openUrl(QUrl.fromLocalFile(directory))

    def showEntryResult(self, entry):
        """A batch-run row's own report, shown when it is highlighted again.
        BatchRunner asks run_many for the full sample table (see ALL_SAMPLES
        in scenario_batch.py), so the graph tab can show this row's trace
        exactly as a single "시나리오 실행" would."""
        if entry.get("ok"):
            report_summary = (entry.get("report") or {}).get("summary") or {}
            text = (f"ID: {entry.get('id', '')}\n"
                   f"종료 이유: {report_summary.get('termination')}\n"
                   f"판정: {entry.get('passed')}\n"
                   f"실패한 검사: {', '.join(report_summary.get('failed_checks') or []) or '(없음)'}\n"
                   f"FMU 오류 {report_summary.get('log_errors', 0)}건, "
                   f"경고 {report_summary.get('log_warnings', 0)}건")
            directory = (entry.get("artifacts") or {}).get("directory")
            if directory:
                text += f"\n저장 위치: {directory}"
            samples = entry.get("samples")
            if samples:
                self.plot.setResult(samples, list(report_summary.get("outputs", {}).keys()))
            else:
                self.plot.clear()
        else:
            text = "오류: " + "; ".join(e["message"] for e in entry.get("errors", []))
            self.plot.clear()
        self.report.setPlainText(text)

    # -- running several checked files at once (new) ---------------------------

    def runCheckedFiles(self):
        if self.worker is not None and self.worker.isRunning():
            return
        if self.batchWorker is not None and self.batchWorker.isRunning():
            return
        if not self.fmuPath.text():
            self.status.setText("먼저 FMU를 선택하세요.")
            return
        self.activeRows = [row for row in range(self.fileTable.rowCount())
                           if self.fileTable.item(row, 0).checkState() == Qt.Checked]
        if not self.activeRows:
            self.status.setText("실행할 항목을 선택하세요.")
            return
        if not self.outputPath.text().strip():
            self.status.setText("결과 저장 위치를 입력하세요.")
            return
        for row in self.activeRows:
            self.fileTable.setItem(row, 1, QTableWidgetItem("실행 대기"))
        self.saveReportButton.setEnabled(False)
        self.analysisRequestButton.setEnabled(False)
        self.setBusy(True)
        self.status.setText(f"{len(self.activeRows)}개 실행 중...")
        jobs = [self.paths[row] for row in self.activeRows]
        self.batchWorker = BatchRunner(self.fmuPath.text(), jobs, self.outputPath.text(), self)
        self.batchWorker.progress.connect(self.onBatchProgress)
        self.batchWorker.allFinished.connect(self.onBatchAllFinished)
        self.batchWorker.start()

    def onBatchProgress(self, index, entry):
        row = self.activeRows[index]
        self.results[row] = entry
        if entry.get("ok"):
            passed = entry.get("passed")
            verdict = "합격" if passed is True else "불합격" if passed is False else "판정 없음"
            samples = entry.get("sample_count")
            text = verdict + (f" / {samples}개 샘플" if samples else "")
            if (entry.get("artifacts") or {}).get("directory"):
                self.openResultsButton.setEnabled(True)
        else:
            text = f"오류: {entry['errors'][0]['message']}"
        self.fileTable.setItem(row, 1, QTableWidgetItem(text))
        if row in {index.row() for index in self.fileTable.selectedIndexes()}:
            self.showEntryResult(entry)

    def onBatchAllFinished(self, envelope):
        self.setBusy(False)
        if not envelope.get("ok"):
            self.status.setText(f"실행할 수 없습니다: {envelope['errors'][0]['message']}")
            return
        summary = envelope["summary"]
        self.lastReport = envelope["report"]
        self.lastSummary, self.lastResults = summary, envelope["results"]
        self.summaryLabel.setText(
            f"{summary['total']}개 중 합격 {summary['passed']} · 불합격 {summary['failed']} · "
            f"판정 기준 없음 {summary['no_verdict']} · 실행 불가 {summary['error']}")
        self.status.setText("선택 항목 실행 완료.")
        self.saveReportButton.setEnabled(True)
        self.analysisRequestButton.setEnabled(True)

    def saveReport(self):
        if not self.lastReport:
            return
        path, _ = QFileDialog.getSaveFileName(self, "보고서 저장",
                                              str(Path(self.outputPath.text()) / "summary.md"),
                                              "Markdown (*.md);;All Files (*.*)")
        if path:
            Path(path).write_text(self.lastReport, encoding="utf-8")
            self.status.setText(f"보고서 저장함: {path}")

    # -- analysis agent: describe an already-run batch in words --------------------

    def openSettings(self):
        """Opens the same agent settings dialog as the '시나리오 생성' tab's own
        '설정...' button -- both tabs read/write the one saved settings file."""
        dialog = agent_settings.AgentSettingsDialog(self)
        dialog.saved.connect(self.applySettings)
        dialog.exec()

    def applySettings(self, values):
        self.settings = values
        self.analysisBackendCombo.setCurrentIndex(0 if values["backend"] == "ollama" else 1)
        self.onAnalysisBackendChanged(self.analysisBackendCombo.currentIndex())
        self.agentChatTab.applySettings(values)

    def onAnalysisBackendChanged(self, index):
        ollama = index == 0
        self.analysisModelCombo.clear()
        if ollama:
            models = ollama_models(self.settings["ollama_host"])
            self.analysisModelCombo.addItems(models or [self.settings["ollama_model"]])
        else:
            self.analysisModelCombo.addItem(self.settings["claude_model"])

    def requestAnalysis(self):
        if self.analysisWorker is not None and self.analysisWorker.isRunning():
            return
        if self.lastSummary is None:
            return
        self.analysisText.setPlainText("분석 요청 중...")
        self.analysisRequestButton.setEnabled(False)
        if self.analysisBackendCombo.currentIndex() == 0:
            backend = "ollama"
            kwargs = dict(fmu=self.fmuPath.text(), summary=self.lastSummary,
                         results=self.lastResults, host=self.settings["ollama_host"],
                         model=self.analysisModelCombo.currentText())
        else:
            backend = "claude"
            kwargs = dict(fmu=self.fmuPath.text(), summary=self.lastSummary,
                         results=self.lastResults, model=self.analysisModelCombo.currentText(),
                         api_key=self.settings["claude_api_key"] or None)
        self.analysisWorker = AnalysisRunner(backend, kwargs, self)
        self.analysisWorker.logLine.connect(lambda line: self.analysisText.setPlainText(line))
        self.analysisWorker.finishedWith.connect(self.onAnalysisFinished)
        self.analysisWorker.start()

    def onAnalysisFinished(self, text, error):
        self.analysisRequestButton.setEnabled(self.lastSummary is not None)
        if text:
            self.analysisText.setPlainText(text)
        else:
            self.analysisText.setPlainText(f"분석 요청 실패: {error}")

    # -- lifecycle -------------------------------------------------------------

    def reject(self):
        if (self.worker is not None and self.worker.isRunning()) or \
           (self.batchWorker is not None and self.batchWorker.isRunning()):
            self.cancelRun()
            return
        if self.agentChatTab.isBusy():
            self.agentChatTab.cancelRun()
            return
        if self.analysisWorker is not None and self.analysisWorker.isRunning():
            return  # one quick model call, nothing to cancel; just wait it out
        super().reject()

    def closeEvent(self, event):
        if (self.worker is not None and self.worker.isRunning()) or \
           (self.batchWorker is not None and self.batchWorker.isRunning()):
            self.cancelRun()
            event.ignore()
        elif self.agentChatTab.isBusy():
            self.agentChatTab.cancelRun()
            event.ignore()
        elif self.analysisWorker is not None and self.analysisWorker.isRunning():
            event.ignore()
        else:
            super().closeEvent(event)
