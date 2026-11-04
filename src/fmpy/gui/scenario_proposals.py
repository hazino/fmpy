"""Review scenarios derived from an FMU's declared structure, and run the ones picked.

Unlike ScenarioDialog, which runs one hand-written scenario JSON, this lists what
`fmpy.scenario_agent.plan` derives from the FMU itself -- a smoke test, one step
per input, a dependency-coverage sweep, parameters at their declared bounds --
and lets the user check which ones to actually execute. None of them carry a
verdict: the FMU does not state what counts as a pass, so running them tells
the user whether the ported code runs and what it did, not whether it is right.
"""
from datetime import datetime
from pathlib import Path

from PySide6.QtCore import QThread, Qt, QUrl, Signal
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import (QAbstractItemView, QDialog, QFileDialog, QHBoxLayout, QHeaderView,
                               QLabel, QLineEdit, QPushButton, QTableWidget, QTableWidgetItem,
                               QTextEdit, QVBoxLayout)

from fmpy.scenario import run_scenario, write_results
from fmpy.scenario_agent import plan

COLUMNS = ("ID", "제목", "필요한 도메인 정보", "결과")


class ProposalRunner(QThread):
    """Run the checked proposals one at a time; report each one as it finishes."""

    progress = Signal(int, object, str)  # row, result-or-None, directory-or-message
    allFinished = Signal()

    def __init__(self, fmu, jobs, output_root, parent=None):
        super().__init__(parent)
        self.fmu = fmu
        self.jobs = jobs  # [(row, proposal_id, scenario_dict), ...]
        self.output_root = output_root

    def run(self):
        for row, proposal_id, scenario in self.jobs:
            if self.isInterruptionRequested():
                self.progress.emit(row, None, "건너뜀 (중지됨)")
                continue
            try:
                result = run_scenario(self.fmu, scenario, self.isInterruptionRequested)
                directory = (self.output_root / proposal_id /
                            datetime.now().strftime("%Y%m%d-%H%M%S-%f"))
                write_results(directory, scenario, result)
                self.progress.emit(row, result, str(directory))
            except Exception as error:
                self.progress.emit(row, None, f"실행 실패: {error}")
        self.allFinished.emit()


class ScenarioProposalsDialog(QDialog):
    """List scenarios derived from the FMU's declared structure; run only what is checked."""

    def __init__(self, fmu, parent=None):
        super().__init__(parent)
        self.fmu = str(fmu)
        self.setWindowTitle(f"시나리오 제안 — {Path(self.fmu).name}")
        self.resize(1040, 660)
        self.worker = None
        self.proposals = []
        self.results = {}
        self.directories = {}

        layout = QVBoxLayout(self)
        self.status = QLabel("FMU가 선언한 구조에서 시나리오 제안을 생성하는 중...")
        self.status.setWordWrap(True)
        layout.addWidget(self.status)

        self.table = QTableWidget(0, len(COLUMNS))
        self.table.setHorizontalHeaderLabels(COLUMNS)
        self.table.horizontalHeader().setSectionResizeMode(1, QHeaderView.Stretch)
        self.table.horizontalHeader().setSectionResizeMode(2, QHeaderView.Stretch)
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.itemSelectionChanged.connect(self.showDetail)
        layout.addWidget(self.table, 1)

        self.detail = QTextEdit()
        self.detail.setReadOnly(True)
        self.detail.setMaximumHeight(170)
        layout.addWidget(self.detail)

        outputRow = QHBoxLayout()
        self.outputPath = QLineEdit(str(Path.cwd() / "scenario-results" / "proposals"))
        self.outputButton = QPushButton("저장 위치...")
        self.outputButton.clicked.connect(self.chooseOutput)
        outputRow.addWidget(QLabel("결과 폴더"))
        outputRow.addWidget(self.outputPath, 1)
        outputRow.addWidget(self.outputButton)
        layout.addLayout(outputRow)

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
        for button in (self.selectAllButton, self.selectNoneButton, self.runButton,
                       self.cancelButton, self.openResultsButton):
            buttons.addWidget(button)
        layout.addLayout(buttons)

        self.loadProposals()

    def loadProposals(self):
        derived = plan(self.fmu)
        if not derived["ok"]:
            self.status.setText(f"제안을 만들 수 없습니다: {derived['errors'][0]['message']}")
            return
        self.proposals = derived["proposals"]
        notes = "\n".join(f"- {note}" for note in derived["notes"])
        self.table.setRowCount(len(self.proposals))
        for row, proposal in enumerate(self.proposals):
            checkable = QTableWidgetItem(proposal["id"])
            checkable.setFlags(Qt.ItemIsUserCheckable | Qt.ItemIsEnabled | Qt.ItemIsSelectable)
            checkable.setCheckState(Qt.Checked if proposal["valid"] else Qt.Unchecked)
            if not proposal["valid"]:
                checkable.setToolTip("이 FMU에 대해 검증을 통과하지 못했습니다")
            self.table.setItem(row, 0, checkable)
            self.table.setItem(row, 1, QTableWidgetItem(proposal["title"]))
            self.table.setItem(row, 2, QTableWidgetItem("; ".join(proposal["needs_from_domain"])))
            self.table.setItem(row, 3, QTableWidgetItem("대기" if proposal["valid"] else "검증 실패"))
        self.table.resizeRowsToContents()
        self.status.setText(
            "이 제안들에는 합격 기준이 없습니다 — FMU는 자기가 무엇을 해야 하는지 말하지 않으므로, "
            "실행 결과는 '제대로 돌아가는지'만 보여주고 '맞는지'는 직접 판단해야 합니다.\n" + notes)
        self.runButton.setEnabled(any(p["valid"] for p in self.proposals))

    def setAllChecked(self, checked):
        state = Qt.Checked if checked else Qt.Unchecked
        for row, proposal in enumerate(self.proposals):
            if proposal["valid"]:
                self.table.item(row, 0).setCheckState(state)

    def showDetail(self):
        rows = {index.row() for index in self.table.selectedIndexes()}
        if not rows:
            return
        row = next(iter(rows))
        proposal = self.proposals[row]
        text = (f"{proposal['title']}\n\n근거: {proposal['rationale']}\n\n"
               f"대상 입력: {', '.join(proposal['covers'].get('inputs', [])) or '(없음)'}\n"
               f"기록 출력: {', '.join(proposal['covers'].get('outputs', []))}\n\n"
               "필요한 도메인 정보:\n" + "\n".join(f"- {n}" for n in proposal["needs_from_domain"]))
        if not proposal["valid"]:
            text += "\n\n검증 오류: " + "; ".join(e["message"] for e in proposal["errors"])
        result = self.results.get(row)
        if result is not None:
            summary = result["summary"]
            text += (f"\n\n실행 결과: {summary['termination']} / {len(result['samples'])}개 샘플 / "
                    f"판정 {result['passed']}\n"
                    f"FMU 오류 {summary['log_errors']}건, 경고 {summary['log_warnings']}건\n"
                    f"저장 위치: {self.directories.get(row, '')}")
        self.detail.setPlainText(text)

    def chooseOutput(self):
        path = QFileDialog.getExistingDirectory(self, "결과 저장 위치", self.outputPath.text())
        if path:
            self.outputPath.setText(path)

    def runSelected(self):
        if self.worker is not None and self.worker.isRunning():
            return
        jobs = [(row, self.proposals[row]["id"], self.proposals[row]["scenario"])
                for row in range(self.table.rowCount())
                if self.table.item(row, 0).checkState() == Qt.Checked]
        if not jobs:
            self.status.setText("실행할 항목을 선택하세요.")
            return
        if not self.outputPath.text().strip():
            self.status.setText("결과 저장 위치를 입력하세요.")
            return
        for row, _, _ in jobs:
            self.table.setItem(row, 3, QTableWidgetItem("실행 대기"))
        self.setBusy(True)
        self.status.setText(f"{len(jobs)}개 실행 중...")
        self.worker = ProposalRunner(self.fmu, jobs, Path(self.outputPath.text()), self)
        self.worker.progress.connect(self.onProgress)
        self.worker.allFinished.connect(self.onAllFinished)
        self.worker.start()

    def onProgress(self, row, result, message):
        if result is None:
            self.table.setItem(row, 3, QTableWidgetItem(message))
            return
        self.results[row] = result
        self.directories[row] = message
        verdict = "합격" if result["passed"] is True else "실패" if result["passed"] is False else "판정 없음"
        self.table.setItem(row, 3, QTableWidgetItem(f"{verdict} / {len(result['samples'])}개 샘플"))
        self.openResultsButton.setEnabled(True)
        if row in {index.row() for index in self.table.selectedIndexes()}:
            self.showDetail()

    def onAllFinished(self):
        self.setBusy(False)
        self.status.setText("실행 완료. 행을 선택하면 상세 내용과 저장 위치가 아래에 표시됩니다.")

    def setBusy(self, busy):
        for widget in (self.runButton, self.selectAllButton, self.selectNoneButton,
                       self.outputButton):
            widget.setEnabled(not busy)
        self.cancelButton.setEnabled(busy)

    def cancelRun(self):
        if self.worker is not None and self.worker.isRunning():
            self.worker.requestInterruption()
            self.status.setText("중지 요청됨: 현재 실행 중인 시나리오가 끝나면 멈춥니다.")

    def openResults(self):
        rows = {index.row() for index in self.table.selectedIndexes()}
        row = next(iter(rows), None)
        directory = self.directories.get(row) or next(iter(self.directories.values()), None)
        if directory:
            QDesktopServices.openUrl(QUrl.fromLocalFile(directory))

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
