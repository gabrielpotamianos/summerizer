"""Qt based desktop UI for macOS to display channel summaries."""

from __future__ import annotations

import logging
from queue import Queue, Empty
from typing import Dict, Optional

from PyQt6 import QtCore, QtGui, QtWidgets

from .mattermost import MattermostClient
from .service import ChannelSummary

LOGGER = logging.getLogger(__name__)


class LoginDialog(QtWidgets.QDialog):
    """Dialog prompting for Mattermost credentials before connecting."""

    def __init__(self, base_url: str, parent: Optional[QtWidgets.QWidget] = None) -> None:
        super().__init__(parent)
        self._base_url = base_url
        self._token: Optional[str] = None
        self.setWindowTitle("Connect to Mattermost")
        self.setModal(True)

        layout = QtWidgets.QVBoxLayout(self)
        description = QtWidgets.QLabel(
            "Enter your Mattermost username (or email), password, and MFA code if required."
        )
        description.setWordWrap(True)
        layout.addWidget(description)

        form = QtWidgets.QFormLayout()
        self._username = QtWidgets.QLineEdit()
        self._username.setPlaceholderText("Username or email")
        self._password = QtWidgets.QLineEdit()
        self._password.setEchoMode(QtWidgets.QLineEdit.EchoMode.Password)
        self._mfa_code = QtWidgets.QLineEdit()
        self._mfa_code.setPlaceholderText("One-time code (optional)")
        self._mfa_code.setMaxLength(10)
        form.addRow("Username", self._username)
        form.addRow("Password", self._password)
        form.addRow("MFA code", self._mfa_code)
        layout.addLayout(form)

        self._status = QtWidgets.QLabel()
        self._status.setStyleSheet("color: #b00020;")
        layout.addWidget(self._status)

        self._buttons = QtWidgets.QDialogButtonBox(
            QtWidgets.QDialogButtonBox.StandardButton.Cancel
            | QtWidgets.QDialogButtonBox.StandardButton.Ok
        )
        self._buttons.accepted.connect(self._attempt_login)
        self._buttons.rejected.connect(self.reject)
        layout.addWidget(self._buttons)

    @property
    def token(self) -> Optional[str]:
        return self._token

    def _attempt_login(self) -> None:
        username = self._username.text().strip()
        password = self._password.text()
        if not username or not password:
            self._status.setText("Username and password are required.")
            return
        mfa_code = self._mfa_code.text().strip()
        self._set_busy(True)
        try:
            token = MattermostClient.login_with_credentials(
                self._base_url, username, password, mfa_code or None
            )
        except Exception as exc:  # pragma: no cover - UI feedback
            self._status.setText(f"Login failed: {exc}")
            return
        finally:
            self._set_busy(False)
        self._token = token
        self.accept()

    def _set_busy(self, busy: bool) -> None:
        self._buttons.setEnabled(not busy)
        if busy:
            QtWidgets.QApplication.setOverrideCursor(
                QtCore.Qt.CursorShape.WaitCursor
            )
        else:
            QtWidgets.QApplication.restoreOverrideCursor()


class SummaryListModel(QtCore.QAbstractListModel):
    """List model mapping channel identifiers to summary text."""

    def __init__(self) -> None:
        super().__init__()
        self._items: Dict[str, ChannelSummary] = {}
        self._order: list[str] = []

    def rowCount(self, parent: QtCore.QModelIndex = QtCore.QModelIndex()) -> int:  # type: ignore[override]
        if parent.isValid():
            return 0
        return len(self._order)

    def data(self, index: QtCore.QModelIndex, role: int = QtCore.Qt.ItemDataRole.DisplayRole):  # type: ignore[override]
        if not index.isValid():
            return None
        channel_id = self._order[index.row()]
        item = self._items[channel_id]
        if role == QtCore.Qt.ItemDataRole.DisplayRole:
            unread_suffix = "" if item.unread.unread_count == 0 else f" ({item.unread.unread_count})"
            return f"{item.unread.display_name}{unread_suffix}"
        if role == QtCore.Qt.ItemDataRole.ToolTipRole:
            return item.summary
        return None

    def update_summary(self, summary: ChannelSummary) -> None:
        channel_id = summary.unread.channel_id
        if channel_id in self._items:
            row = self._order.index(channel_id)
            self._items[channel_id] = summary
            top_left = self.index(row, 0)
            self.dataChanged.emit(top_left, top_left)
        else:
            self.beginInsertRows(QtCore.QModelIndex(), len(self._order), len(self._order))
            self._order.append(channel_id)
            self._items[channel_id] = summary
            self.endInsertRows()

    def get_summary(self, index: QtCore.QModelIndex) -> str:
        if not index.isValid():
            return ""
        channel_id = self._order[index.row()]
        return self._items[channel_id].summary


class SummaryWindow(QtWidgets.QMainWindow):
    """Main window showing channel list and summary pane."""

    def __init__(self, queue: Queue[ChannelSummary], refresh_interval: float) -> None:
        super().__init__()
        self._queue = queue
        self.setWindowTitle("Mattermost Summaries")
        self.resize(900, 600)
        self._model = SummaryListModel()

        central = QtWidgets.QWidget(self)
        layout = QtWidgets.QHBoxLayout(central)
        self._list = QtWidgets.QListView()
        self._list.setModel(self._model)
        self._list.clicked.connect(self._on_selection_changed)

        self._summary = QtWidgets.QTextEdit()
        self._summary.setReadOnly(True)
        font = QtGui.QFont("Menlo", 12)
        self._summary.setFont(font)

        layout.addWidget(self._list, 1)
        layout.addWidget(self._summary, 2)
        self.setCentralWidget(central)

        self._timer = QtCore.QTimer(self)
        self._timer.timeout.connect(self._poll_queue)  # type: ignore[arg-type]
        self._timer.start(int(refresh_interval * 1000))

    def _poll_queue(self) -> None:
        while True:
            try:
                summary = self._queue.get_nowait()
            except Empty:
                break
            LOGGER.debug("UI received summary for %s", summary.unread.display_name)
            self._model.update_summary(summary)
        if self._model.rowCount() > 0 and not self._list.currentIndex().isValid():
            self._list.setCurrentIndex(self._model.index(0))

    def _on_selection_changed(self, index: QtCore.QModelIndex) -> None:
        summary_text = self._model.get_summary(index)
        self._summary.setPlainText(summary_text)
