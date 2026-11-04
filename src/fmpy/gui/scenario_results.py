"""Selectable, independently configurable plots for scenario results."""
import pyqtgraph as pg
from PySide6.QtCore import Qt
from PySide6.QtWidgets import (QDialog, QHBoxLayout, QLabel, QLineEdit, QListWidget,
                               QListWidgetItem, QPushButton, QScrollArea, QSplitter,
                               QVBoxLayout, QWidget)


class ResultGraph(QWidget):
    def __init__(self, rows, names, selected=(), parent=None):
        super().__init__(parent)
        self.rows, self.names = rows, names
        layout = QVBoxLayout(self)
        self.toolbar = QHBoxLayout()
        layout.addLayout(self.toolbar)
        splitter = QSplitter(Qt.Horizontal)
        selector = QWidget()
        left = QVBoxLayout(selector)
        left.setContentsMargins(0, 0, 0, 0)
        self.search = QLineEdit()
        self.search.setPlaceholderText("변수 검색...")
        left.addWidget(self.search)
        self.variables = QListWidget()
        left.addWidget(self.variables)
        buttons = QHBoxLayout()
        for label, checked in (("전체 선택", True), ("전체 해제", False)):
            button = QPushButton(label)
            button.clicked.connect(lambda _, value=checked: self.selectAll(value))
            buttons.addWidget(button)
        left.addLayout(buttons)
        self.plot = pg.PlotWidget()
        self.plot.setLabel("bottom", "Time", units="s")
        self.plot.showGrid(x=True, y=True)
        self.plot.addLegend()
        splitter.addWidget(selector)
        splitter.addWidget(self.plot)
        splitter.setSizes([260, 700])
        layout.addWidget(splitter, 1)
        for name in names:
            item = QListWidgetItem(name)
            item.setToolTip(name)
            item.setFlags(item.flags() | Qt.ItemIsUserCheckable)
            item.setCheckState(Qt.Checked if name in selected else Qt.Unchecked)
            self.variables.addItem(item)
        self.variables.itemChanged.connect(self.redraw)
        self.search.textChanged.connect(self.filterVariables)
        self.redraw()

    def selectedNames(self):
        return [self.variables.item(i).text() for i in range(self.variables.count())
                if self.variables.item(i).checkState() == Qt.Checked]

    def selectAll(self, checked):
        self.variables.blockSignals(True)
        for i in range(self.variables.count()):
            self.variables.item(i).setCheckState(Qt.Checked if checked else Qt.Unchecked)
        self.variables.blockSignals(False)
        self.redraw()

    def filterVariables(self, text):
        for i in range(self.variables.count()):
            item = self.variables.item(i)
            item.setHidden(text.casefold() not in item.text().casefold())

    def redraw(self, *_):
        self.plot.clear()
        selected = set(self.selectedNames())
        times = [row["time"] for row in self.rows]
        for i, name in enumerate(self.names):
            if name in selected:
                self.plot.plot(times, [float(row[name]) for row in self.rows], name=name,
                               pen=pg.mkPen(pg.intColor(i), width=2))


class ScenarioResults(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.rows, self.names, self.graphs, self.expanded = [], [], [], []
        layout = QVBoxLayout(self)
        bar = QHBoxLayout()
        self.addButton = QPushButton("그래프 추가")
        self.addButton.clicked.connect(lambda: self.addGraph())
        self.addButton.setEnabled(False)
        bar.addWidget(self.addButton)
        self.hint = QLabel("실행 후 표시할 변수를 선택할 수 있습니다.")
        self.hint.setWordWrap(True)
        bar.addWidget(self.hint, 1)
        layout.addLayout(bar)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        self.container = QWidget()
        self.graphLayout = QVBoxLayout(self.container)
        self.graphLayout.addStretch()
        scroll.setWidget(self.container)
        layout.addWidget(scroll)

    def clear(self):
        for window in list(self.expanded):
            window.close()
        for graph in self.graphs:
            self.graphLayout.removeWidget(graph)
            graph.deleteLater()
        self.graphs = []
        self.rows, self.names = [], []
        self.addButton.setEnabled(False)
        self.hint.setText("실행 후 표시할 변수를 선택할 수 있습니다.")

    def setResult(self, rows, names):
        self.clear()
        self.rows = rows
        self.names = [n for n in names if rows and isinstance(rows[0][n], (int, float, bool))]
        self.addButton.setEnabled(bool(self.names))
        self.hint.setText("체크한 변수만 표시됩니다. 그래프별 선택 · 마우스 확대/이동 · 확대 보기 가능. 문자열은 CSV에서 확인하세요.")
        if self.names:
            self.addGraph(self.names)
        else:
            self.hint.setText("그래프로 표시할 수치/Boolean 결과가 없습니다. CSV를 확인하세요.")

    def addGraph(self, selected=None):
        if not self.names:
            return
        graph = ResultGraph(self.rows, self.names, selected or (), self)
        graph.setMinimumHeight(330)
        graph.title = QLabel()
        graph.toolbar.addWidget(graph.title)
        graph.toolbar.addStretch()
        expand = QPushButton("확대 보기")
        expand.clicked.connect(lambda: self.expandGraph(graph))
        graph.toolbar.addWidget(expand)
        graph.removeButton = QPushButton("삭제")
        graph.removeButton.clicked.connect(lambda: self.removeGraph(graph))
        graph.toolbar.addWidget(graph.removeButton)
        self.graphs.append(graph)
        self.graphLayout.insertWidget(self.graphLayout.count() - 1, graph)
        self.updateTitles()
        return graph

    def removeGraph(self, graph):
        if len(self.graphs) <= 1:
            return
        self.graphs.remove(graph)
        self.graphLayout.removeWidget(graph)
        graph.deleteLater()
        self.updateTitles()

    def updateTitles(self):
        for i, graph in enumerate(self.graphs, 1):
            graph.title.setText(f"그래프 {i}")
            graph.removeButton.setEnabled(len(self.graphs) > 1)

    def expandGraph(self, graph):
        window = QDialog(self)
        window.setAttribute(Qt.WA_DeleteOnClose)
        window.setWindowTitle(f"{graph.title.text()} — 확대 보기 (독립 선택)")
        window.setWindowFlags(window.windowFlags() | Qt.WindowMaximizeButtonHint)
        window.resize(1200, 800)
        layout = QVBoxLayout(window)
        layout.addWidget(ResultGraph(self.rows, self.names, graph.selectedNames(), window))
        self.expanded.append(window)
        window.finished.connect(lambda _: self.expanded.remove(window) if window in self.expanded else None)
        window.show()
        return window
