from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime
from queue import Empty

from PySide6 import QtCore, QtGui, QtWidgets

from .service import MattermostMonitor
from .storage import ChannelSnapshot

logger = logging.getLogger(__name__)


@dataclass
class SummaryEntry:
    channel_id: str
    channel_name: str
    summary: str
    last_updated: datetime


class SummaryListModel(QtCore.QAbstractListModel):
    def __init__(self) -> None:
        super().__init__()
        self._entries: list[SummaryEntry] = []

    def data(self, index: QtCore.QModelIndex, role: int = QtCore.Qt.DisplayRole):  # type: ignore[override]
        if not index.isValid():
            return None
        entry = self._entries[index.row()]
        if role == QtCore.Qt.DisplayRole:
            return f"{entry.channel_name} — {entry.last_updated.strftime('%H:%M:%S')}"
        if role == QtCore.Qt.ToolTipRole:
            return entry.summary
        return None

    def rowCount(self, parent: QtCore.QModelIndex = QtCore.QModelIndex()) -> int:  # type: ignore[override]
        return len(self._entries)

    def get_entry(self, row: int) -> SummaryEntry:
        return self._entries[row]

    def upsert_entry(self, snapshot: ChannelSnapshot) -> None:
        channel_name = snapshot.channel.display_name or snapshot.channel.name
        entry = SummaryEntry(
            channel_id=snapshot.channel.id,
            channel_name=channel_name,
            summary=snapshot.summary,
            last_updated=datetime.utcnow(),
        )
        for idx, existing in enumerate(self._entries):
            if existing.channel_id == entry.channel_id:
                self._entries[idx] = entry
                self.dataChanged.emit(self.index(idx), self.index(idx))
                break
        else:
            self.beginInsertRows(QtCore.QModelIndex(), len(self._entries), len(self._entries))
            self._entries.append(entry)
            self.endInsertRows()


class MainWindow(QtWidgets.QMainWindow):
    def __init__(self, monitor: MattermostMonitor) -> None:
        super().__init__()
        self.monitor = monitor
        self.monitor.start()
        self.queue = monitor.get_queue()

        self.setWindowTitle("Mattermost Summaries")
        self.resize(900, 600)

        container = QtWidgets.QWidget()
        layout = QtWidgets.QHBoxLayout(container)

        self.list_model = SummaryListModel()
        self.list_view = QtWidgets.QListView()
        self.list_view.setModel(self.list_model)
        self.list_view.setSelectionMode(QtWidgets.QAbstractItemView.SingleSelection)
        self.list_view.selectionModel().selectionChanged.connect(self._on_selection_changed)

        self.summary_view = QtWidgets.QTextEdit()
        self.summary_view.setReadOnly(True)
        self.summary_view.setFont(QtGui.QFont("Menlo", 12))

        layout.addWidget(self.list_view, 1)
        layout.addWidget(self.summary_view, 2)

        self.setCentralWidget(container)

        self.timer = QtCore.QTimer(self)
        self.timer.setInterval(1000)
        self.timer.timeout.connect(self._poll_queue)
        self.timer.start()

    def closeEvent(self, event: QtGui.QCloseEvent) -> None:  # type: ignore[override]
        logger.info("Closing application")
        self.monitor.stop()
        super().closeEvent(event)

    def _poll_queue(self) -> None:
        while True:
            try:
                snapshot = self.queue.get_nowait()
            except Empty:
                break
            else:
                self.list_model.upsert_entry(snapshot)
                if self.list_model.rowCount() == 1:
                    self.list_view.setCurrentIndex(self.list_model.index(0))

    def _on_selection_changed(self) -> None:
        indexes = self.list_view.selectedIndexes()
        if not indexes:
            self.summary_view.clear()
            return
        entry = self.list_model.get_entry(indexes[0].row())
        self.summary_view.setPlainText(entry.summary)


def run_ui(monitor: MattermostMonitor) -> None:
    app = QtWidgets.QApplication([])
    window = MainWindow(monitor)
    window.show()
    app.exec()


__all__ = ["run_ui", "MainWindow"]
