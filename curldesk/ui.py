from __future__ import annotations

import asyncio
import ctypes
import json
import re
import sys
import threading
import time
from datetime import datetime
from pathlib import Path
from typing import Any

from PySide6.QtCore import QEvent, QPoint, QPointF, QRectF, QRegularExpression, QThread, QTimer, Qt, Signal
from PySide6.QtGui import (
    QColor,
    QImage,
    QKeySequence,
    QPainter,
    QPen,
    QPixmap,
    QSyntaxHighlighter,
    QTextCharFormat,
)
from PySide6.QtSvg import QSvgRenderer
from PySide6.QtWidgets import (
    QAbstractItemView,
    QApplication,
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QDoubleSpinBox,
    QFileDialog,
    QFormLayout,
    QHBoxLayout,
    QHeaderView,
    QInputDialog,
    QLabel,
    QLineEdit,
    QMainWindow,
    QMessageBox,
    QPlainTextEdit,
    QProgressBar,
    QPushButton,
    QSplitter,
    QStackedWidget,
    QStyle,
    QStyleOptionButton,
    QStylePainter,
    QTabWidget,
    QToolButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from .import_curl import CurlImportError, parse_curl
from .export_curl import export_curl
from .models import FormItem, RequestData, ResponseData
from .storage import Storage
from .transport import perform_request_async


def _asset_path(name: str) -> Path:
    return Path(__file__).resolve().parents[1] / "curldesk" / "assets" / name


def _logo_pixmap(size: int) -> QPixmap:
    renderer = QSvgRenderer(str(_asset_path("curldesk.svg")))
    image = QImage(size, size, QImage.Format.Format_ARGB32_Premultiplied)
    image.fill(Qt.GlobalColor.transparent)
    painter = QPainter(image)
    renderer.render(painter)
    painter.end()
    return QPixmap.fromImage(image)


def _format_json_or_jsonp(body: str) -> tuple[str, str] | None:
    """Return pretty JSON while keeping a JSONP callback wrapper intact."""
    source = body.lstrip("\ufeff").strip()
    prefix = ""
    kind = "JSON"

    callback = re.match(
        r"^(?P<prefix>(?:/\*\*/\s*)?[A-Za-z_$][\w$]*(?:\.[A-Za-z_$][\w$]*)*\s*\()\s*(?P<payload>.*)\s*\)\s*;?\s*$",
        source,
        re.DOTALL,
    )
    if callback:
        prefix = callback.group("prefix")
        source = callback.group("payload")
        kind = "JSONP · 回调名已保留"

    try:
        parsed = json.loads(source)
    except (json.JSONDecodeError, TypeError):
        return None

    formatted = json.dumps(parsed, ensure_ascii=False, indent=2)
    if prefix:
        formatted = f"{prefix}\n{formatted}\n);"
    return formatted, kind


class JsonSyntaxHighlighter(QSyntaxHighlighter):
    """Lightweight JSON coloring for the formatted response view."""

    def __init__(self, document: Any) -> None:
        super().__init__(document)
        self._rules: list[tuple[QRegularExpression, QTextCharFormat]] = []

        def add_rule(pattern: str, color: str, bold: bool = False) -> None:
            char_format = QTextCharFormat()
            char_format.setForeground(QColor(color))
            if bold:
                char_format.setFontWeight(700)
            self._rules.append((QRegularExpression(pattern), char_format))

        add_rule(r"(?<![\w.])-?(?:0|[1-9]\d*)(?:\.\d+)?(?:[eE][+-]?\d+)?", "#b05b17")
        add_rule(r"\b(?:true|false|null)\b", "#c04452", True)
        add_rule(r"[{}\[\],:]", "#75838b", True)
        add_rule(r'"(?:\\.|[^"\\])*"', "#147d68")
        add_rule(r'"(?:\\.|[^"\\])*"(?=\s*:)', "#7957b8", True)
        add_rule(r"^\s*/\*\*/\s*[A-Za-z_$][\w$]*(?:\.[\w$]+)*(?=\s*\()", "#087f78", True)
        add_rule(r"^\s*\)\s*;?\s*$", "#087f78", True)

    def highlightBlock(self, text: str) -> None:
        for expression, char_format in self._rules:
            match_iterator = expression.globalMatch(text)
            while match_iterator.hasNext():
                match = match_iterator.next()
                self.setFormat(match.capturedStart(), match.capturedLength(), char_format)


class StyledComboBox(QComboBox):
    def paintEvent(self, event: Any) -> None:
        super().paintEvent(event)
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setPen(QPen(QColor("#718184"), 1.5))
        x = self.width() - 15
        y = self.height() / 2 - 2
        painter.drawLine(QPointF(x - 4, y), QPointF(x, y + 4))
        painter.drawLine(QPointF(x, y + 4), QPointF(x + 4, y))


class StyledCheckBox(QCheckBox):
    def paintEvent(self, event: Any) -> None:
        option = QStyleOptionButton()
        self.initStyleOption(option)
        painter = QStylePainter(self)
        painter.drawControl(QStyle.ControlElement.CE_CheckBox, option)

        indicator = self.style().subElementRect(
            QStyle.SubElement.SE_CheckBoxIndicator, option, self
        )
        box = QRectF(indicator).adjusted(0.5, 0.5, -0.5, -0.5)
        checked = bool(option.state & QStyle.StateFlag.State_On)
        enabled = bool(option.state & QStyle.StateFlag.State_Enabled)
        hovered = bool(option.state & QStyle.StateFlag.State_MouseOver)

        color = "#087f78" if checked or hovered else "#aab9b9"
        fill = "#087f78" if checked else "#ffffff"
        if not enabled:
            color = "#d4dddd"
            fill = "#eef2f2"
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setPen(QPen(QColor(color), 1))
        painter.setBrush(QColor(fill))
        painter.drawRoundedRect(box, 3, 3)

        if checked:
            check_pen = QPen(QColor("#ffffff"), 1.8)
            check_pen.setCapStyle(Qt.PenCapStyle.RoundCap)
            painter.setPen(check_pen)
            painter.drawLine(
                QPointF(box.left() + 3.0, box.top() + 7.8),
                QPointF(box.left() + 6.0, box.top() + 10.6),
            )
            painter.drawLine(
                QPointF(box.left() + 6.0, box.top() + 10.6),
                QPointF(box.left() + 11.8, box.top() + 4.4),
            )


class AppTitleBar(QWidget):
    def __init__(self, window: MainWindow) -> None:
        super().__init__(window)
        self._window = window
        self._drag_offset: QPoint | None = None
        self.setObjectName("appTitleBar")
        self.setFixedHeight(36)

        layout = QHBoxLayout(self)
        layout.setContentsMargins(10, 0, 0, 0)
        layout.setSpacing(8)
        logo = QLabel()
        logo.setFixedSize(20, 20)
        logo.setPixmap(_logo_pixmap(20))
        logo.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        layout.addWidget(logo)
        title = QLabel("CurlDesk")
        title.setObjectName("windowTitleText")
        title.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        layout.addWidget(title)
        layout.addStretch(1)

        self.minimize_button = self._make_button("−", "最小化")
        self.maximize_button = self._make_button("□", "最大化")
        self.close_button = self._make_button("×", "关闭", close=True)
        self.minimize_button.clicked.connect(window.showMinimized)
        self.maximize_button.clicked.connect(self._toggle_maximize)
        self.close_button.clicked.connect(window.close)
        layout.addWidget(self.minimize_button)
        layout.addWidget(self.maximize_button)
        layout.addWidget(self.close_button)

    def _make_button(self, text: str, tooltip: str, close: bool = False) -> QToolButton:
        button = QToolButton(self)
        button.setText(text)
        button.setToolTip(tooltip)
        button.setObjectName("windowCloseButton" if close else "windowControlButton")
        button.setFixedSize(44, 36)
        return button

    def _toggle_maximize(self) -> None:
        if self._window.isMaximized():
            self._window.showNormal()
        else:
            self._window.showMaximized()
        self.update_maximize_button()

    def update_maximize_button(self) -> None:
        maximized = self._window.isMaximized()
        self.maximize_button.setText("❐" if maximized else "□")
        self.maximize_button.setToolTip("还原" if maximized else "最大化")

    def mousePressEvent(self, event: Any) -> None:
        if event.button() == Qt.MouseButton.LeftButton and sys.platform != "win32":
            handle = self._window.windowHandle()
            if handle is not None and handle.startSystemMove():
                event.accept()
                return
            self._drag_offset = (
                event.globalPosition().toPoint() - self._window.frameGeometry().topLeft()
            )
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event: Any) -> None:
        if self._drag_offset is not None and event.buttons() & Qt.MouseButton.LeftButton:
            self._window.move(event.globalPosition().toPoint() - self._drag_offset)
            event.accept()
            return
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event: Any) -> None:
        self._drag_offset = None
        super().mouseReleaseEvent(event)

    def mouseDoubleClickEvent(self, event: Any) -> None:
        if event.button() == Qt.MouseButton.LeftButton and sys.platform != "win32":
            self._toggle_maximize()
            event.accept()
            return
        super().mouseDoubleClickEvent(event)


def _line_edit(password: bool = False, placeholder: str = "") -> QLineEdit:
    edit = QLineEdit()
    edit.setPlaceholderText(placeholder)
    if password:
        edit.setEchoMode(QLineEdit.EchoMode.Password)
    return edit


class RowMoveHeader(QHeaderView):
    rowMoved = Signal(int, int, bool)

    def __init__(self, parent: QWidget) -> None:
        super().__init__(Qt.Orientation.Vertical, parent)
        self._source_row = -1
        self._press_position: QPoint | None = None
        self._dragging = False
        self._marker = QLabel(parent.viewport())
        self._marker.setStyleSheet("background: #15978e; border: none;")
        self._marker.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        self._marker.hide()
        self.setSectionsMovable(False)
        self.setMouseTracking(True)
        self.setCursor(Qt.CursorShape.OpenHandCursor)
        self.setToolTip("拖动行号可手动调整顺序")

    def mousePressEvent(self, event: Any) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            self._source_row = self.logicalIndexAt(event.position().toPoint())
            self._press_position = event.position().toPoint()
            self._dragging = False
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event: Any) -> None:
        if (
            self._source_row >= 0
            and self._press_position is not None
            and event.buttons() & Qt.MouseButton.LeftButton
        ):
            position = event.position().toPoint()
            if not self._dragging and (position - self._press_position).manhattanLength() >= QApplication.startDragDistance():
                self._dragging = True
                self.setCursor(Qt.CursorShape.ClosedHandCursor)
            if self._dragging:
                table = self.parentWidget()
                if position.y() < 20:
                    table.verticalScrollBar().setValue(table.verticalScrollBar().value() - 1)
                elif position.y() > self.viewport().height() - 20:
                    table.verticalScrollBar().setValue(table.verticalScrollBar().value() + 1)
                target = self.logicalIndexAt(position)
                if target >= 0:
                    top = self.sectionViewportPosition(target)
                    y = top + (self.sectionSize(target) if position.y() >= top + self.sectionSize(target) // 2 else 0)
                    self._marker.setGeometry(0, max(0, y - 1), table.viewport().width(), 3)
                    self._marker.show()
                    self._marker.raise_()
                event.accept()
                return
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event: Any) -> None:
        if self._dragging and event.button() == Qt.MouseButton.LeftButton:
            position = event.position().toPoint()
            target_row = self.logicalIndexAt(position)
            after = False
            if target_row >= 0:
                section_top = self.sectionViewportPosition(target_row)
                after = position.y() >= section_top + self.sectionSize(target_row) // 2
            self.rowMoved.emit(self._source_row, target_row, after)
            event.accept()
        else:
            super().mouseReleaseEvent(event)
        self._source_row = -1
        self._press_position = None
        self._dragging = False
        self._marker.hide()
        self.setCursor(Qt.CursorShape.OpenHandCursor)


class RequestTable(QTableWidget):
    def __init__(self, rows: int, columns: int) -> None:
        super().__init__(rows, columns)
        self.row_move_header = RowMoveHeader(self)
        self.setVerticalHeader(self.row_move_header)
        self.row_move_header.rowMoved.connect(self.move_row)

    def keyPressEvent(self, event: Any) -> None:
        if event.matches(QKeySequence.StandardKey.Copy):
            self._copy_selection()
            event.accept()
            return
        if event.matches(QKeySequence.StandardKey.Paste):
            self._paste_clipboard()
            event.accept()
            return
        if event.modifiers() & Qt.KeyboardModifier.AltModifier:
            row = self.currentRow()
            column = max(self.currentColumn(), 0)
            if event.key() == Qt.Key.Key_Up and row > 0:
                moved_to = self.move_row(row, row - 1, False)
                if moved_to is not None:
                    self.setCurrentCell(moved_to, column)
                event.accept()
                return
            if event.key() == Qt.Key.Key_Down and row >= 0 and row + 1 < self.rowCount():
                moved_to = self.move_row(row, row + 1, True)
                if moved_to is not None:
                    self.setCurrentCell(moved_to, column)
                event.accept()
                return
        super().keyPressEvent(event)

    def _copy_selection(self) -> None:
        indexes = self.selectedIndexes()
        if not indexes and self.currentIndex().isValid():
            indexes = [self.currentIndex()]
        if not indexes:
            return
        top = min(index.row() for index in indexes)
        bottom = max(index.row() for index in indexes)
        left = min(index.column() for index in indexes)
        right = max(index.column() for index in indexes)
        text = "\n".join(
            "\t".join(_cell_text(self, row, column) for column in range(left, right + 1))
            for row in range(top, bottom + 1)
        )
        QApplication.clipboard().setText(text)

    def _paste_clipboard(self) -> None:
        text = QApplication.clipboard().text()
        if not text:
            return
        rows = text.replace("\r\n", "\n").replace("\r", "\n").split("\n")
        if rows and rows[-1] == "":
            rows.pop()
        if not rows:
            return

        current = self.currentIndex()
        start_row = current.row() if current.isValid() else 0
        start_column = current.column() if current.isValid() else 0
        was_blocked = self.blockSignals(True)
        try:
            for row_offset, line in enumerate(rows):
                values = line.split("\t")
                target_row = start_row + row_offset
                while target_row >= self.rowCount():
                    self.insertRow(self.rowCount())
                for column_offset, value in enumerate(values):
                    target_column = start_column + column_offset
                    if target_column >= self.columnCount():
                        break
                    widget = self.cellWidget(target_row, target_column)
                    if isinstance(widget, QComboBox):
                        widget.setCurrentText(value)
                        continue
                    item = self.item(target_row, target_column)
                    if item is None:
                        item = QTableWidgetItem()
                        self.setItem(target_row, target_column, item)
                    item.setText(value)
        finally:
            self.blockSignals(was_blocked)
        if self.columnCount() >= 2:
            _ensure_empty_row(self)

    def _row_records(self) -> list[tuple[list[str], dict[int, tuple[type, list[str], str]]]]:
        records = []
        for row in range(self.rowCount()):
            values: list[str] = []
            combos: dict[int, tuple[type, list[str], str]] = {}
            for col in range(self.columnCount()):
                widget = self.cellWidget(row, col)
                if isinstance(widget, QComboBox):
                    options = [widget.itemText(i) for i in range(widget.count())]
                    value = widget.currentText()
                    combos[col] = (type(widget), options, value)
                else:
                    value = _cell_text(self, row, col)
                values.append(value)
            records.append((values, combos))
        return records

    def _replace_rows(
        self, records: list[tuple[list[str], dict[int, tuple[type, list[str], str]]]]
    ) -> None:
        was_blocked = self.blockSignals(True)
        self.setUpdatesEnabled(False)
        try:
            self.setRowCount(0)
            for values, combos in records:
                row = self.rowCount()
                self.insertRow(row)
                for col, value in enumerate(values):
                    combo_data = combos.get(col)
                    if combo_data is None:
                        self.setItem(row, col, QTableWidgetItem(value))
                    else:
                        combo_type, options, current = combo_data
                        widget = combo_type(self)
                        widget.addItems(options)
                        widget.setCurrentText(current)
                        self.setCellWidget(row, col, widget)
        finally:
            self.setUpdatesEnabled(True)
            self.blockSignals(was_blocked)

    def move_row(self, source: int, target: int, after: bool = False) -> int | None:
        records = self._row_records()
        if source < 0 or source >= len(records):
            return None
        meaningful_columns = min(2, self.columnCount())
        is_empty = lambda record: not any(
            value.strip() for value in record[0][:meaningful_columns]
        )
        data_rows = [(index, record) for index, record in enumerate(records) if not is_empty(record)]
        empty_rows = [record for record in records if is_empty(record)]
        source_position = next(
            (position for position, (index, _) in enumerate(data_rows) if index == source), None
        )
        if source_position is None:
            return None

        if target < 0 or target >= len(records) or is_empty(records[target]):
            insertion = len(data_rows)
        else:
            target_position = next(
                position for position, (index, _) in enumerate(data_rows) if index == target
            )
            insertion = target_position + int(after)

        remaining = [record for _, record in data_rows]
        moved = remaining.pop(source_position)
        if insertion > source_position:
            insertion -= 1
        insertion = max(0, min(insertion, len(remaining)))
        if insertion == source_position:
            return source
        remaining.insert(insertion, moved)
        current_column = max(self.currentColumn(), 0)
        self._replace_rows(remaining + empty_rows)
        self.setCurrentCell(insertion, current_column)
        return insertion


def _table(headers: list[str]) -> QTableWidget:
    table = RequestTable(0, len(headers))
    table.setHorizontalHeaderLabels(headers)
    table.verticalHeader().setVisible(True)
    table.verticalHeader().setFixedWidth(30)
    table.verticalHeader().setDefaultSectionSize(36)
    table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectItems)
    table.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
    table.setAlternatingRowColors(True)
    table.setShowGrid(True)
    table.setEditTriggers(
        QAbstractItemView.EditTrigger.EditKeyPressed
        | QAbstractItemView.EditTrigger.AnyKeyPressed
    )
    table.cellDoubleClicked.connect(
        lambda row, column, target=table: _edit_table_cell(target, row, column)
    )
    table.verticalHeader().setDefaultSectionSize(36)
    table.horizontalHeader().setFixedHeight(36)
    table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
    for column in range(1, len(headers)):
        table.horizontalHeader().setSectionResizeMode(column, QHeaderView.ResizeMode.Stretch)
    return table


def _edit_table_cell(table: QTableWidget, row: int, column: int) -> None:
    item = table.item(row, column)
    if item is None:
        return
    text = item.text()
    if len(text) < 48 and "\n" not in text:
        table.editItem(item)
        return

    header = table.horizontalHeaderItem(column)
    label = header.text() if header else "文本"
    dialog = QDialog(table.window())
    dialog.setWindowTitle(f"编辑{label}")
    dialog.resize(760, 500)
    dialog.setStyleSheet(
        """
        QDialog { background: #ffffff; color: #24343a; }
        QPlainTextEdit {
            background: #ffffff; color: #24343a; border: 1px solid #d7e0e1;
            border-radius: 4px; padding: 10px; font-family: 'Consolas', 'Microsoft YaHei UI';
        }
        QPushButton {
            color: #42575a; background: #ffffff; border: 1px solid #d7e0e1;
            border-radius: 4px; min-height: 32px; padding: 0 14px;
        }
        QPushButton:hover { background: #edf6f4; border-color: #a7cbc5; }
        """
    )
    layout = QVBoxLayout(dialog)
    layout.setContentsMargins(18, 16, 18, 16)
    layout.setSpacing(12)
    editor = QPlainTextEdit()
    editor.setObjectName("largeCellEditor")
    editor.setPlainText(text)
    layout.addWidget(editor, 1)
    buttons = QDialogButtonBox(
        QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel
    )
    buttons.button(QDialogButtonBox.StandardButton.Save).setText("保存")
    buttons.button(QDialogButtonBox.StandardButton.Cancel).setText("取消")
    buttons.accepted.connect(dialog.accept)
    buttons.rejected.connect(dialog.reject)
    layout.addWidget(buttons)
    editor.setFocus()
    if dialog.exec() == QDialog.DialogCode.Accepted:
        item.setText(editor.toPlainText())


def _cell_text(table: QTableWidget, row: int, column: int) -> str:
    widget = table.cellWidget(row, column)
    if isinstance(widget, QComboBox):
        return widget.currentText()
    item = table.item(row, column)
    return item.text() if item else ""


def _set_table(table: QTableWidget, rows: list[tuple[str, str]]) -> None:
    was_blocked = table.blockSignals(True)
    table.setRowCount(0)
    for key, value in rows:
        row = table.rowCount()
        table.insertRow(row)
        table.setItem(row, 0, QTableWidgetItem(str(key)))
        table.setItem(row, 1, QTableWidgetItem(str(value)))
    table.insertRow(table.rowCount())
    table.blockSignals(was_blocked)


def _ensure_empty_row(table: QTableWidget) -> None:
    row = table.rowCount() - 1
    if row >= 0 and any(_cell_text(table, row, column) for column in range(2)):
        table.insertRow(table.rowCount())


def _read_table(table: QTableWidget) -> list[tuple[str, str]]:
    result = []
    for row in range(table.rowCount()):
        key = _cell_text(table, row, 0).strip()
        if key:
            result.append((key, _cell_text(table, row, 1)))
    return result


class RequestWorker(QThread):
    completed = Signal(object, object)

    def __init__(self, request: RequestData) -> None:
        super().__init__()
        self.request_data = request
        self._cancel_requested = threading.Event()
        self._lock = threading.Lock()
        self._loop: asyncio.AbstractEventLoop | None = None
        self._task: asyncio.Task | None = None

    def cancel(self) -> None:
        self._cancel_requested.set()
        with self._lock:
            if self._loop is not None and self._task is not None:
                self._loop.call_soon_threadsafe(self._task.cancel)

    def run(self) -> None:
        start = time.perf_counter()
        loop = asyncio.SelectorEventLoop()
        asyncio.set_event_loop(loop)
        try:
            with self._lock:
                self._loop = loop
                self._task = loop.create_task(perform_request_async(self.request_data))
                if self._cancel_requested.is_set():
                    self._task.cancel()
            response = loop.run_until_complete(self._task)
        except asyncio.CancelledError:
            response = ResponseData(error="请求已取消", elapsed_ms=(time.perf_counter() - start) * 1000)
        except Exception as exc:
            response = ResponseData(error=f"{type(exc).__name__}: {exc}", elapsed_ms=(time.perf_counter() - start) * 1000)
        finally:
            with self._lock:
                self._loop = None
                self._task = None
            loop.close()
        self.completed.emit(self.request_data, response)


class RequestWorkspace(QMainWindow):
    title_changed = Signal(str)

    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle("CurlDesk")
        self.resize(1220, 820)
        self.setMinimumSize(900, 620)
        self.storage = Storage()
        self._workers: list[RequestWorker] = []
        self._active_worker: RequestWorker | None = None
        self._request_started = 0.0
        self._loading_timer = QTimer(self)
        self._loading_timer.setInterval(100)
        self._loading_timer.timeout.connect(self._update_loading)
        self._build_ui()
        self._refresh_lists()
        self.url_edit.textChanged.connect(self._update_title)
        self.method_combo.currentTextChanged.connect(self._update_title)

    def _update_title(self) -> None:
        from urllib.parse import urlsplit

        url = self.url_edit.text().strip()
        if not url:
            self.title_changed.emit("快速请求")
            return
        parsed = urlsplit(url if "://" in url else "//" + url)
        name = parsed.path.rstrip("/").rsplit("/", 1)[-1] or parsed.netloc or url
        self.title_changed.emit(f"{self.method_combo.currentText().upper()}  {name[:24]}")

    def _build_ui(self) -> None:
        central = QWidget()
        central.setObjectName("central")
        root = QVBoxLayout(central)
        root.setContentsMargins(20, 14, 20, 12)
        root.setSpacing(14)

        top = QHBoxLayout()
        logo = QLabel()
        logo.setFixedSize(36, 36)
        logo.setPixmap(_logo_pixmap(36))
        logo.setToolTip("CurlDesk")
        top.addWidget(logo)
        title = QLabel("CurlDesk")
        title.setObjectName("appTitle")
        top.addWidget(title)
        subtitle = QLabel("HTTP 请求工具")
        subtitle.setObjectName("subtitle")
        top.addSpacing(4)
        top.addWidget(subtitle)
        top.addStretch(1)
        self.import_button = QPushButton("导入 cURL")
        self.import_button.setObjectName("secondaryButton")
        self.import_button.clicked.connect(self._import_curl)
        top.addWidget(self.import_button)
        self.export_button = QPushButton("导出 cURL")
        self.export_button.setObjectName("secondaryButton")
        self.export_button.clicked.connect(self._export_curl)
        top.addWidget(self.export_button)
        self.save_button = QPushButton("保存请求")
        self.save_button.setObjectName("secondaryButton")
        self.save_button.clicked.connect(self._save_collection)
        top.addWidget(self.save_button)
        root.addLayout(top)

        splitter = QSplitter(Qt.Orientation.Horizontal)
        splitter.setHandleWidth(12)
        splitter.addWidget(self._build_sidebar())
        splitter.addWidget(self._build_workspace())
        splitter.setSizes([250, 930])
        root.addWidget(splitter, 1)
        self.setCentralWidget(central)
        self.statusBar().showMessage("就绪")
        self.setStyleSheet(
            """
            QWidget { font-family: 'Microsoft YaHei UI', 'Segoe UI'; font-size: 10pt; color: #24343a; }
            QMainWindow, QWidget#central { background: #f3f5f6; }
            QLabel#appTitle { font-size: 17pt; font-weight: 700; color: #17343a; }
            QLabel#subtitle { color: #7a8b8d; font-size: 9pt; }
            QLabel#sectionTitle { font-size: 11pt; font-weight: 700; color: #243b40; }
            QLabel#responseStatus { color: #637579; font-size: 9pt; }
            QLabel#formattedHint { color: #718184; font-size: 9pt; padding: 2px 4px; }
            QLabel#exportNotice { color: #617477; background: #f4f8f7; border: 1px solid #e3eaea; border-radius: 4px; padding: 8px; }
            QStatusBar { background: #f3f5f6; color: #78888b; }
            QSplitter::handle { background: transparent; }
            QTabWidget::pane { border: 1px solid #dce4e5; background: #ffffff; border-radius: 4px; top: -1px; }
            QTabBar::tab { background: transparent; color: #718084; padding: 10px 15px; border: none; border-bottom: 2px solid transparent; }
            QTabBar::tab:selected { color: #087e79; border-bottom: 2px solid #15978e; font-weight: 600; }
            QTabBar::tab:hover:!selected { color: #263f43; background: #e9f0ef; }
            QLineEdit, QComboBox, QDoubleSpinBox, QPlainTextEdit, QTableWidget, QListWidget {
                background: #ffffff; color: #24343a; border: 1px solid #d7e0e1; border-radius: 4px; selection-background-color: #d8efeb; selection-color: #173e3c;
            }
            QLineEdit, QComboBox, QDoubleSpinBox { min-height: 34px; padding: 0 10px; }
            QLineEdit:focus, QComboBox:focus, QDoubleSpinBox:focus, QPlainTextEdit:focus { border: 1px solid #15978e; }
            QComboBox QLineEdit { border: none; background: transparent; padding: 0; color: #147a73; font-weight: 700; }
            QComboBox::drop-down { border: none; width: 24px; }
            QComboBox QAbstractItemView { background: white; color: #24343a; selection-background-color: #d8efeb; selection-color: #173e3c; outline: none; }
            QPlainTextEdit { padding: 10px; font-family: 'Consolas', 'Microsoft YaHei UI'; }
            QTableWidget { gridline-color: #e8edee; alternate-background-color: #f8faf9; selection-background-color: #e4f2ef; selection-color: #24343a; }
            QScrollBar:vertical { width: 11px; margin: 2px; background: #f3f6f6; border: none; }
            QScrollBar::handle:vertical { min-height: 28px; margin: 1px; border-radius: 4px; background: #c5d1d1; }
            QScrollBar::handle:vertical:hover { background: #9aacab; }
            QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical { height: 0; border: none; background: none; }
            QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical { background: #f3f6f6; }
            QScrollBar:horizontal { height: 11px; margin: 2px; background: #f3f6f6; border: none; }
            QScrollBar::handle:horizontal { min-width: 28px; margin: 1px; border-radius: 4px; background: #c5d1d1; }
            QScrollBar::handle:horizontal:hover { background: #9aacab; }
            QScrollBar::add-line:horizontal, QScrollBar::sub-line:horizontal { width: 0; border: none; background: none; }
            QScrollBar::add-page:horizontal, QScrollBar::sub-page:horizontal { background: #f3f6f6; }
            QHeaderView::section { background: #f5f8f7; color: #718184; padding: 7px 10px; border: none; border-bottom: 1px solid #dfe7e6; border-right: 1px solid #e8edee; font-weight: 600; }
            QHeaderView:vertical { background: #f5f8f7; }
            QHeaderView::section:vertical { color: #8a9999; padding: 2px; border-right: 1px solid #e8edee; }
            QTableCornerButton::section { background: #f5f8f7; border: 0; border-right: 1px solid #dfe7e6; border-bottom: 1px solid #dfe7e6; }
            QListWidget { border: none; padding: 5px; }
            QListWidget::item { padding: 10px 8px; border-radius: 4px; border-bottom: 1px solid #edf1f1; }
            QListWidget::item:selected { background: #e1f2ef; color: #126e68; }
            QPushButton { background: #ffffff; color: #42575a; border: 1px solid #d7e0e1; border-radius: 4px; min-height: 32px; padding: 0 14px; }
            QPushButton:hover { background: #edf6f4; border-color: #a7cbc5; }
            QPushButton:pressed { background: #dcefeb; }
            QPushButton:disabled { background: #e7edec; color: #9caaa9; border-color: #e0e7e6; }
            QPushButton#sendButton { background: #087f78; color: white; border: 1px solid #087f78; font-weight: 700; min-width: 82px; min-height: 40px; }
            QPushButton#sendButton:hover { background: #096d67; }
            QPushButton#cancelButton { background: #fff4f2; color: #a13d36; border: 1px solid #e6bbb5; font-weight: 600; min-width: 82px; min-height: 40px; }
            QPushButton#cancelButton:hover { background: #ffe5e1; }
            QLabel#validationError { color: #b33d37; padding: 2px 0; }
            QProgressBar { border: 0; background: #e6f1ef; max-height: 3px; }
            QProgressBar::chunk { background: #15978e; }
            QMessageBox, QDialog { background-color: #ffffff; color: #24343a; }
            QMessageBox QLabel, QDialog QLabel { color: #24343a; }
            QDialog QPlainTextEdit#largeCellEditor { min-height: 340px; }
            QCheckBox { spacing: 8px; }
            QCheckBox:checked { color: #087870; font-weight: 600; }
            """
        )

    def _build_sidebar(self) -> QWidget:
        from PySide6.QtWidgets import QListWidget

        sidebar = QWidget()
        layout = QVBoxLayout(sidebar)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(10)
        self.sidebar_tabs = QTabWidget()
        self.sidebar_tabs.setObjectName("sidebarTabs")
        self.collection_list = QListWidget()
        self.history_list = QListWidget()
        self.collection_list.itemDoubleClicked.connect(self._load_collection)
        self.history_list.itemDoubleClicked.connect(self._load_history)
        self.sidebar_tabs.addTab(self.collection_list, "请求集合")
        self.sidebar_tabs.addTab(self.history_list, "请求历史")
        layout.addWidget(self.sidebar_tabs, 1)

        buttons = QHBoxLayout()
        self.sidebar_save = QPushButton("保存")
        self.sidebar_save.clicked.connect(self._save_collection)
        self.sidebar_delete = QPushButton("删除")
        self.sidebar_delete.clicked.connect(self._delete_selected)
        buttons.addWidget(self.sidebar_save)
        buttons.addWidget(self.sidebar_delete)
        layout.addLayout(buttons)
        sidebar.setMinimumWidth(190)
        sidebar.setMaximumWidth(340)
        return sidebar

    def _build_workspace(self) -> QWidget:
        workspace = QWidget()
        layout = QVBoxLayout(workspace)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(8)

        request_line = QHBoxLayout()
        self.method_combo = StyledComboBox()
        self.method_combo.setObjectName("methodCombo")
        self.method_combo.setEditable(True)
        self.method_combo.addItems(["GET", "POST", "PUT", "PATCH", "DELETE", "HEAD", "OPTIONS"])
        self.method_combo.setCurrentText("GET")
        self.method_combo.setFixedWidth(120)
        self.method_combo.setMinimumHeight(42)
        self.url_edit = _line_edit(placeholder="https://example.com/api")
        self.url_edit.setMinimumHeight(42)
        self.url_edit.returnPressed.connect(self._send_request)
        self.send_button = QPushButton("发送")
        self.send_button.setObjectName("sendButton")
        self.send_button.setDefault(True)
        self.send_button.clicked.connect(self._send_or_cancel)
        request_line.addWidget(self.method_combo)
        request_line.addWidget(self.url_edit, 1)
        request_line.addWidget(self.send_button)
        layout.addLayout(request_line)
        self.validation_error = QLabel()
        self.validation_error.setObjectName("validationError")
        self.validation_error.setWordWrap(True)
        self.validation_error.hide()
        layout.addWidget(self.validation_error)
        self.url_edit.textChanged.connect(self.validation_error.hide)
        self.loading_bar = QProgressBar()
        self.loading_bar.setRange(0, 0)
        self.loading_bar.setTextVisible(False)
        self.loading_bar.setFixedHeight(3)
        self.loading_bar.hide()
        layout.addWidget(self.loading_bar)

        self.request_tabs = QTabWidget()
        self.request_tabs.addTab(self._build_params_tab(), "参数")
        self.request_tabs.addTab(self._build_headers_tab(), "请求头")
        self.request_tabs.addTab(self._build_auth_tab(), "认证")
        self.request_tabs.addTab(self._build_body_tab(), "请求体")
        self.request_tabs.addTab(self._build_proxy_tab(), "代理")
        self.request_tabs.addTab(self._build_tls_tab(), "TLS / 选项")
        sections = QSplitter(Qt.Orientation.Vertical)
        sections.setHandleWidth(7)
        sections.addWidget(self.request_tabs)

        response_heading = QHBoxLayout()
        response_title = QLabel("响应")
        response_title.setObjectName("sectionTitle")
        response_heading.addWidget(response_title)
        self.response_status = QLabel("尚无响应")
        self.response_status.setObjectName("responseStatus")
        response_heading.addSpacing(12)
        response_heading.addWidget(self.response_status)
        response_heading.addStretch(1)
        response_container = QWidget()
        response_container_layout = QVBoxLayout(response_container)
        response_container_layout.setContentsMargins(0, 0, 0, 0)
        response_container_layout.setSpacing(3)
        response_container_layout.addLayout(response_heading)
        self.response_tabs = QTabWidget()
        response_panel = QWidget()
        response_layout = QVBoxLayout(response_panel)
        response_layout.setContentsMargins(6, 6, 6, 6)
        self.response_body = QPlainTextEdit()
        self.response_body.setReadOnly(True)
        self.response_body.setLineWrapMode(QPlainTextEdit.LineWrapMode.NoWrap)
        response_layout.addWidget(self.response_body, 1)

        formatted_panel = QWidget()
        formatted_layout = QVBoxLayout(formatted_panel)
        formatted_layout.setContentsMargins(6, 6, 6, 6)
        formatted_layout.setSpacing(5)
        self.formatted_hint = QLabel("自动识别 JSON / JSONP")
        self.formatted_hint.setObjectName("formattedHint")
        formatted_layout.addWidget(self.formatted_hint)
        self.formatted_body = QPlainTextEdit()
        self.formatted_body.setReadOnly(True)
        self.formatted_body.setLineWrapMode(QPlainTextEdit.LineWrapMode.NoWrap)
        self.formatted_body.setPlaceholderText("响应内容中未识别出 JSON 或 JSONP。")
        self._formatted_highlighter = JsonSyntaxHighlighter(self.formatted_body.document())
        formatted_layout.addWidget(self.formatted_body, 1)

        self.response_headers = QPlainTextEdit()
        self.response_headers.setReadOnly(True)
        self.response_tabs.addTab(response_panel, "响应体")
        self.formatted_tab_index = self.response_tabs.addTab(formatted_panel, "格式化")
        self.response_tabs.addTab(self.response_headers, "响应头")
        response_container_layout.addWidget(self.response_tabs, 1)
        sections.addWidget(response_container)
        sections.setSizes([320, 300])
        sections.setChildrenCollapsible(False)
        layout.addWidget(sections, 1)
        return workspace

    def _key_value_tab(self, title: str) -> tuple[QWidget, QTableWidget]:
        page = QWidget()
        layout = QVBoxLayout(page)
        table = _table(["键", "值"])
        table.setMinimumHeight(150)
        _set_table(table, [])
        table.cellChanged.connect(lambda row, column: _ensure_empty_row(table))
        controls = QHBoxLayout()
        add = QPushButton("添加行")
        remove = QPushButton("删除行")
        add.clicked.connect(lambda: self._add_table_row(table))
        remove.clicked.connect(lambda: self._remove_table_row(table))
        controls.addWidget(add)
        controls.addWidget(remove)
        self._add_move_buttons(controls, table)
        controls.addStretch(1)
        layout.addWidget(table, 1)
        layout.addLayout(controls)
        return page, table

    def _build_params_tab(self) -> QWidget:
        page, self.params_table = self._key_value_tab("Params")
        return page

    def _build_headers_tab(self) -> QWidget:
        page, self.headers_table = self._key_value_tab("Headers")
        return page

    def _build_auth_tab(self) -> QWidget:
        page = QWidget()
        layout = QFormLayout(page)
        self.auth_combo = StyledComboBox()
        self.auth_combo.addItems(["无", "Bearer Token", "Basic Auth"])
        self.auth_combo.currentTextChanged.connect(self._update_auth_fields)
        self.auth_stack = QStackedWidget()
        bearer_page = QWidget()
        bearer_layout = QFormLayout(bearer_page)
        self.bearer_edit = _line_edit(password=True, placeholder="令牌")
        bearer_layout.addRow("令牌", self.bearer_edit)
        basic_page = QWidget()
        basic_layout = QFormLayout(basic_page)
        self.basic_user_edit = _line_edit()
        self.basic_password_edit = _line_edit(password=True)
        basic_layout.addRow("用户名", self.basic_user_edit)
        basic_layout.addRow("密码", self.basic_password_edit)
        self.auth_stack.addWidget(QWidget())
        self.auth_stack.addWidget(bearer_page)
        self.auth_stack.addWidget(basic_page)
        layout.addRow("类型", self.auth_combo)
        layout.addRow(self.auth_stack)
        layout.addRow(QLabel("认证信息会与已保存的请求一起保存在本机。"))
        return page

    def _build_body_tab(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        body_row = QHBoxLayout()
        body_row.addWidget(QLabel("请求体类型"))
        self.body_combo = StyledComboBox()
        self.body_combo.addItems(["无", "JSON", "表单", "multipart/form-data", "原始文本"])
        self.body_combo.currentTextChanged.connect(self._update_body_page)
        self.body_combo.setMaximumWidth(240)
        body_row.addWidget(self.body_combo)
        body_row.addStretch(1)
        layout.addLayout(body_row)

        self.body_stack = QStackedWidget()
        self.body_stack.addWidget(QLabel("此请求不包含请求体。"))
        self.json_body_text = QPlainTextEdit()
        self.json_body_text.setPlaceholderText("JSON 请求体")
        self.body_stack.addWidget(self.json_body_text)

        form_page = QWidget()
        form_layout = QVBoxLayout(form_page)
        self.form_table = _table(["键", "值"])
        _set_table(self.form_table, [])
        self.form_table.cellChanged.connect(lambda row, column: _ensure_empty_row(self.form_table))
        form_buttons = QHBoxLayout()
        add_form = QPushButton("添加字段")
        remove_form = QPushButton("删除字段")
        add_form.clicked.connect(lambda: self._add_table_row(self.form_table))
        remove_form.clicked.connect(lambda: self._remove_table_row(self.form_table))
        form_buttons.addWidget(add_form)
        form_buttons.addWidget(remove_form)
        self._add_move_buttons(form_buttons, self.form_table)
        form_buttons.addStretch(1)
        form_layout.addWidget(self.form_table, 1)
        form_layout.addLayout(form_buttons)
        self.body_stack.addWidget(form_page)

        multipart_page = QWidget()
        multipart_layout = QVBoxLayout(multipart_page)
        self.multipart_table = _table(["名称", "值或文件路径", "类型"])
        self.multipart_table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        self.multipart_table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        self.multipart_table.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeMode.ResizeToContents)
        multipart_buttons = QHBoxLayout()
        add_part = QPushButton("添加字段")
        remove_part = QPushButton("删除字段")
        choose_file = QPushButton("选择文件…")
        add_part.clicked.connect(self._add_multipart_row)
        remove_part.clicked.connect(lambda: self._remove_table_row(self.multipart_table))
        choose_file.clicked.connect(self._choose_multipart_file)
        multipart_buttons.addWidget(add_part)
        multipart_buttons.addWidget(remove_part)
        multipart_buttons.addWidget(choose_file)
        self._add_move_buttons(multipart_buttons, self.multipart_table)
        multipart_buttons.addStretch(1)
        multipart_layout.addWidget(self.multipart_table, 1)
        multipart_layout.addLayout(multipart_buttons)
        self.body_stack.addWidget(multipart_page)
        self.raw_body_text = QPlainTextEdit()
        self.raw_body_text.setPlaceholderText("原始请求体")
        self.body_stack.addWidget(self.raw_body_text)
        layout.addWidget(self.body_stack, 1)
        return page

    def _build_proxy_tab(self) -> QWidget:
        page = QWidget()
        layout = QFormLayout(page)
        self.proxy_enabled = StyledCheckBox("此请求使用代理")
        self.proxy_enabled.setChecked(True)
        self.proxy_type = StyledComboBox()
        self.proxy_type.addItems(["http", "https", "socks5"])
        self.proxy_type.setCurrentText("socks5")
        self.proxy_host = _line_edit(placeholder="127.0.0.1")
        self.proxy_host.setText("127.0.0.1")
        self.proxy_port = QLineEdit("10809")
        self.proxy_username = _line_edit()
        self.proxy_password = _line_edit(password=True)
        layout.addRow(self.proxy_enabled)
        layout.addRow("协议", self.proxy_type)
        layout.addRow("主机", self.proxy_host)
        layout.addRow("端口", self.proxy_port)
        layout.addRow("用户名", self.proxy_username)
        layout.addRow("密码", self.proxy_password)
        return page

    def _build_tls_tab(self) -> QWidget:
        from typing import get_args

        from curl_cffi.requests import BrowserTypeLiteral

        page = QWidget()
        layout = QFormLayout(page)
        self.fingerprint_combo = StyledComboBox()
        self.fingerprint_combo.addItem("默认（curl_cffi）", "")
        for fingerprint in get_args(BrowserTypeLiteral):
            self.fingerprint_combo.addItem(fingerprint, fingerprint)
        self.fingerprint_combo.setCurrentIndex(self.fingerprint_combo.findData("chrome120"))
        self.timeout_spin = QDoubleSpinBox()
        self.timeout_spin.setRange(0.1, 600.0)
        self.timeout_spin.setDecimals(1)
        self.timeout_spin.setValue(30.0)
        self.timeout_spin.setSuffix(" s")
        self.http_version_combo = StyledComboBox()
        self.http_version_combo.addItem("自动协商", "auto")
        self.http_version_combo.addItem("HTTP/1.1", "1.1")
        self.http_version_combo.addItem("HTTP/2", "2")
        self.verify_ssl = StyledCheckBox("验证 TLS 证书")
        self.verify_ssl.setChecked(True)
        self.follow_redirects = StyledCheckBox("跟随重定向")
        self.follow_redirects.setChecked(True)
        layout.addRow("TLS 指纹", self.fingerprint_combo)
        layout.addRow("HTTP 版本", self.http_version_combo)
        layout.addRow("超时", self.timeout_spin)
        layout.addRow(self.verify_ssl)
        layout.addRow(self.follow_redirects)
        return page

    def _add_move_buttons(self, layout: QHBoxLayout, table: RequestTable) -> None:
        for label, direction, tooltip in (("↑", -1, "上移当前行"), ("↓", 1, "下移当前行")):
            button = QPushButton(label)
            button.setFixedWidth(36)
            button.setToolTip(tooltip)
            button.setAccessibleName(tooltip)
            button.clicked.connect(lambda checked=False, d=direction: self._move_table_row(table, d))
            layout.addWidget(button)

    def _move_table_row(self, table: RequestTable, direction: int) -> None:
        row = table.currentRow()
        target = row + direction
        if row >= 0 and 0 <= target < table.rowCount():
            table.move_row(row, target, direction > 0)

    def _add_table_row(self, table: QTableWidget) -> None:
        row = table.rowCount()
        table.insertRow(row)
        table.setItem(row, 0, QTableWidgetItem())
        table.setItem(row, 1, QTableWidgetItem())
        table.setCurrentCell(row, 0)
        table.editItem(table.item(row, 0))

    def _remove_table_row(self, table: QTableWidget) -> None:
        row = table.currentRow()
        if row >= 0:
            table.removeRow(row)
        if table.rowCount() == 0 and table.columnCount() == 2:
            table.insertRow(0)

    def _add_multipart_row(self) -> None:
        row = self.multipart_table.rowCount()
        self.multipart_table.insertRow(row)
        self.multipart_table.setItem(row, 0, QTableWidgetItem())
        self.multipart_table.setItem(row, 1, QTableWidgetItem())
        kind = StyledComboBox()
        kind.addItems(["text", "file"])
        self.multipart_table.setCellWidget(row, 2, kind)
        self.multipart_table.setCurrentCell(row, 0)
        self.multipart_table.editItem(self.multipart_table.item(row, 0))

    def _choose_multipart_file(self) -> None:
        row = self.multipart_table.currentRow()
        if row < 0:
            QMessageBox.information(self, "Choose file", "Select a multipart field first.")
            return
        path, _ = QFileDialog.getOpenFileName(self, "Choose upload file")
        if path:
            self.multipart_table.setItem(row, 1, QTableWidgetItem(path))
            kind = self.multipart_table.cellWidget(row, 2)
            if isinstance(kind, QComboBox):
                kind.setCurrentText("file")

    def _update_auth_fields(self, name: str) -> None:
        self.auth_stack.setCurrentIndex({"无": 0, "Bearer Token": 1, "Basic Auth": 2}.get(name, 0))

    def _update_body_page(self, name: str) -> None:
        page_index = {
            "无": 0,
            "JSON": 1,
            "表单": 2,
            "multipart/form-data": 3,
            "原始文本": 4,
        }.get(name, 0)
        self.body_stack.setCurrentIndex(page_index)

    def _collect_request(self) -> RequestData:
        try:
            proxy_port = int(self.proxy_port.text().strip() or "8080")
        except ValueError as exc:
            raise ValueError("代理端口必须是整数") from exc
        if not 1 <= proxy_port <= 65535:
            raise ValueError("代理端口必须在 1 到 65535 之间")

        multipart_items = []
        for row in range(self.multipart_table.rowCount()):
            key = _cell_text(self.multipart_table, row, 0).strip()
            if not key:
                continue
            kind_widget = self.multipart_table.cellWidget(row, 2)
            kind = kind_widget.currentText() if isinstance(kind_widget, QComboBox) else "text"
            multipart_items.append(
                FormItem(key=key, value=_cell_text(self.multipart_table, row, 1), kind=kind)
            )

        return RequestData(
            method=self.method_combo.currentText().strip().upper() or "GET",
            url=self.url_edit.text().strip(),
            params=_read_table(self.params_table),
            headers=_read_table(self.headers_table),
            auth_type={"无": "None", "Bearer Token": "Bearer Token", "Basic Auth": "Basic Auth"}.get(
                self.auth_combo.currentText(), "None"
            ),
            bearer_token=self.bearer_edit.text(),
            basic_username=self.basic_user_edit.text(),
            basic_password=self.basic_password_edit.text(),
            body_type={
                "无": "None",
                "JSON": "JSON",
                "表单": "Form URL Encoded",
                "multipart/form-data": "multipart/form-data",
                "原始文本": "Raw",
            }.get(self.body_combo.currentText(), "None"),
            body_text=(
                self.json_body_text.toPlainText()
                if self.body_combo.currentText() == "JSON"
                else self.raw_body_text.toPlainText()
                if self.body_combo.currentText() == "原始文本"
                else ""
            ),
            form_items=[FormItem(*row) for row in _read_table(self.form_table)],
            multipart_items=multipart_items,
            proxy_enabled=self.proxy_enabled.isChecked(),
            proxy_type=self.proxy_type.currentText(),
            proxy_host=self.proxy_host.text().strip(),
            proxy_port=proxy_port,
            proxy_username=self.proxy_username.text(),
            proxy_password=self.proxy_password.text(),
            impersonate=str(self.fingerprint_combo.currentData() or ""),
            http_version=str(self.http_version_combo.currentData() or "auto"),
            verify_ssl=self.verify_ssl.isChecked(),
            timeout=float(self.timeout_spin.value()),
            follow_redirects=self.follow_redirects.isChecked(),
        )

    def _set_request(self, request: RequestData) -> None:
        self.method_combo.setCurrentText(request.method)
        self.url_edit.setText(request.url)
        _set_table(self.params_table, request.params)
        _set_table(self.headers_table, request.headers)
        auth_names = {"None": "无", "Bearer Token": "Bearer Token", "Basic Auth": "Basic Auth"}
        self.auth_combo.setCurrentText(auth_names.get(request.auth_type, "无"))
        self._update_auth_fields(self.auth_combo.currentText())
        self.bearer_edit.setText(request.bearer_token)
        self.basic_user_edit.setText(request.basic_username)
        self.basic_password_edit.setText(request.basic_password)
        body_names = {
            "None": "无",
            "JSON": "JSON",
            "Form URL Encoded": "表单",
            "multipart/form-data": "multipart/form-data",
            "Raw": "原始文本",
        }
        self.body_combo.setCurrentText(body_names.get(request.body_type, "无"))
        if request.body_type == "JSON":
            self.json_body_text.setPlainText(request.body_text)
        else:
            self.raw_body_text.setPlainText(request.body_text)
        self._update_body_page(self.body_combo.currentText())
        _set_table(self.form_table, [(item.key, item.value) for item in request.form_items])
        self.multipart_table.setRowCount(0)
        for item in request.multipart_items:
            self._add_multipart_row()
            row = self.multipart_table.rowCount() - 1
            self.multipart_table.setItem(row, 0, QTableWidgetItem(item.key))
            self.multipart_table.setItem(row, 1, QTableWidgetItem(item.value))
            kind = self.multipart_table.cellWidget(row, 2)
            if isinstance(kind, QComboBox):
                kind.setCurrentText(item.kind)
        if not request.multipart_items:
            self._add_multipart_row()
        self.proxy_enabled.setChecked(request.proxy_enabled)
        self.proxy_type.setCurrentText(request.proxy_type)
        self.proxy_host.setText(request.proxy_host)
        self.proxy_port.setText(str(request.proxy_port))
        self.proxy_username.setText(request.proxy_username)
        self.proxy_password.setText(request.proxy_password)
        fingerprint_index = self.fingerprint_combo.findData(request.impersonate)
        self.fingerprint_combo.setCurrentIndex(fingerprint_index if fingerprint_index >= 0 else 0)
        version_index = self.http_version_combo.findData(request.http_version)
        self.http_version_combo.setCurrentIndex(version_index if version_index >= 0 else 0)
        self.verify_ssl.setChecked(request.verify_ssl)
        self.timeout_spin.setValue(request.timeout)
        self.follow_redirects.setChecked(request.follow_redirects)

    def _send_request(self) -> None:
        if self._active_worker is not None:
            return
        self.validation_error.hide()
        try:
            request = self._collect_request()
            if not request.url:
                raise ValueError("请输入请求 URL")
            if not re.match(r"^https?://", request.url, re.IGNORECASE):
                raise ValueError("URL 必须以 http:// 或 https:// 开头")
            if request.body_type == "JSON" and request.body_text.strip():
                json.loads(request.body_text)
        except Exception as exc:
            self.validation_error.setText(f"请求无效：{exc}")
            self.validation_error.show()
            if not self.url_edit.text().strip() or not re.match(r"^https?://", self.url_edit.text().strip(), re.IGNORECASE):
                self.url_edit.setFocus()
            return

        self._request_started = time.perf_counter()
        self.response_status.setText("请求发送中…  0.0 s")
        self.response_body.clear()
        self.formatted_body.clear()
        self.formatted_hint.setText("正在等待响应并自动识别 JSON / JSONP…")
        self.response_headers.clear()
        self.loading_bar.show()
        self._loading_timer.start()
        self.send_button.setText("取消")
        self.send_button.setObjectName("cancelButton")
        self.send_button.style().unpolish(self.send_button)
        self.send_button.style().polish(self.send_button)
        worker = RequestWorker(request)
        self._active_worker = worker
        self._workers.append(worker)
        worker.completed.connect(self._request_completed)
        worker.finished.connect(lambda current=worker: self._discard_worker(current))
        worker.start()

    def _send_or_cancel(self) -> None:
        if self._active_worker is None:
            self._send_request()
        else:
            self._active_worker.cancel()
            self._loading_timer.stop()
            self.response_status.setText("正在取消请求…")
            self.send_button.setText("取消中…")
            self.send_button.setEnabled(False)

    def _update_loading(self) -> None:
        self.response_status.setText(f"请求发送中…  {time.perf_counter() - self._request_started:.1f} s")

    def _discard_worker(self, worker: RequestWorker) -> None:
        if worker in self._workers:
            self._workers.remove(worker)
        worker.deleteLater()

    def _request_completed(self, request: RequestData, response: ResponseData) -> None:
        self._active_worker = None
        self._loading_timer.stop()
        self.loading_bar.hide()
        self.send_button.setText("发送")
        self.send_button.setObjectName("sendButton")
        self.send_button.style().unpolish(self.send_button)
        self.send_button.style().polish(self.send_button)
        self.send_button.setEnabled(True)
        self._display_response(response)
        try:
            self.storage.add_history(request, response)
            self._refresh_history()
        except Exception as exc:
            QMessageBox.warning(self, "历史记录错误", f"无法保存历史记录：{exc}")
        self.statusBar().showMessage("请求完成" if not response.error else response.error if response.error == "请求已取消" else "请求失败", 5000)

    def _display_response(self, response: ResponseData) -> None:
        if response.error:
            state = "请求已取消" if response.error == "请求已取消" else "请求失败"
            self.response_status.setText(f"{state}  |  {response.elapsed_ms:.0f} ms")
            self.response_body.setPlainText(response.error)
            self.formatted_body.clear()
            self.formatted_hint.setText("请求没有返回可格式化的响应体。")
            self.response_headers.clear()
            self.response_tabs.setCurrentIndex(0)
            return
        self.response_status.setText(
            f"{response.status_code} {response.reason}  |  {response.elapsed_ms:.0f} ms  |  {len(response.body)} bytes"
        )
        charset = "utf-8"
        content_type = ""
        for key, value in response.headers:
            if key.lower() == "content-type":
                content_type = value
                match = re.search(r"charset\s*=\s*['\"]?([^; '\"]+)", value, re.I)
                if match:
                    charset = match.group(1)
        try:
            body = response.body.decode(charset, errors="replace")
        except LookupError:
            body = response.body.decode("utf-8", errors="replace")
        if not body and response.body:
            body = response.body.decode("utf-8", errors="replace")
        self.response_body.setPlainText(body)
        formatted = _format_json_or_jsonp(body)
        if formatted:
            formatted_body, kind = formatted
            self.formatted_body.setPlainText(formatted_body)
            self.formatted_hint.setText(f"自动识别：{kind}")
            self.response_tabs.setCurrentIndex(self.formatted_tab_index)
        else:
            self.formatted_body.clear()
            self.formatted_hint.setText("未识别为 JSON / JSONP；原始响应内容仍可在“响应体”页签查看。")
            self.response_tabs.setCurrentIndex(0)
        self.response_headers.setPlainText("\n".join(f"{key}: {value}" for key, value in response.headers))

    def _export_curl(self) -> None:
        try:
            request = self._collect_request()
            if not request.url or not re.match(r"^https?://", request.url, re.IGNORECASE):
                raise ValueError("请输入以 http:// 或 https:// 开头的请求 URL")
            command, notes = export_curl(request)
        except Exception as exc:
            QMessageBox.warning(self, "无法导出 cURL", str(exc))
            return

        dialog = QDialog(self)
        dialog.setWindowTitle("导出 cURL")
        dialog.setMinimumSize(700, 440)
        dialog.resize(920, 620)
        layout = QVBoxLayout(dialog)
        layout.addWidget(QLabel("已按 Windows PowerShell 格式生成，复制后可直接粘贴到 PowerShell。"))

        command_view = QPlainTextEdit()
        command_view.setReadOnly(True)
        command_view.setLineWrapMode(QPlainTextEdit.LineWrapMode.NoWrap)
        command_view.setPlainText(command)
        layout.addWidget(command_view, 1)

        notice = QLabel("\n".join(notes) if notes else "命令已包含当前请求的参数、请求头、认证、请求体和代理设置。")
        notice.setObjectName("exportNotice")
        notice.setWordWrap(True)
        layout.addWidget(notice)

        actions = QHBoxLayout()
        copy_button = QPushButton("复制命令")
        copy_button.setObjectName("sendButton")
        copy_status = QLabel()
        copy_status.setObjectName("formattedHint")
        close_button = QPushButton("关闭")
        copy_button.clicked.connect(
            lambda: (
                QApplication.clipboard().setText(command_view.toPlainText()),
                copy_status.setText("已复制到剪贴板"),
                self.statusBar().showMessage("cURL 命令已复制", 3000),
            )
        )
        close_button.clicked.connect(dialog.accept)
        actions.addWidget(copy_button)
        actions.addWidget(copy_status)
        actions.addStretch(1)
        actions.addWidget(close_button)
        layout.addLayout(actions)
        dialog.exec()

    def _import_curl(self) -> None:
        from PySide6.QtWidgets import QDialog, QDialogButtonBox

        dialog = QDialog(self)
        dialog.setWindowTitle("导入 cURL")
        dialog.resize(740, 400)
        layout = QVBoxLayout(dialog)
        layout.addWidget(QLabel("粘贴 curl 命令："))
        command = QPlainTextEdit()
        command.setPlaceholderText("curl 'https://example.com' -H 'Accept: application/json'")
        layout.addWidget(command, 1)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Cancel | QDialogButtonBox.StandardButton.Ok)
        buttons.button(QDialogButtonBox.StandardButton.Ok).setText("导入")
        buttons.accepted.connect(dialog.accept)
        buttons.rejected.connect(dialog.reject)
        layout.addWidget(buttons)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        try:
            request, warnings = parse_curl(command.toPlainText())
            self._set_request(request)
            if warnings:
                QMessageBox.information(self, "导入完成（含提示）", "\n".join(warnings))
            self.statusBar().showMessage("cURL 已导入", 4000)
        except CurlImportError as exc:
            QMessageBox.warning(self, "无法导入 cURL", str(exc))

    def _save_collection(self) -> None:
        try:
            request = self._collect_request()
        except Exception as exc:
            QMessageBox.warning(self, "无法保存请求", str(exc))
            return
        current = self.collection_list.currentItem()
        default = current.text() if current and self.sidebar_tabs.currentWidget() == self.collection_list else ""
        name, accepted = QInputDialog.getText(self, "保存请求", "请求名称：", text=default)
        if not accepted or not name.strip():
            return
        self.storage.save_collection(name.strip(), request)
        self._refresh_collections()
        self.statusBar().showMessage(f"已保存：{name.strip()}", 4000)

    def _delete_selected(self) -> None:
        current = self.sidebar_tabs.currentWidget()
        if current == self.collection_list:
            item = self.collection_list.currentItem()
            if not item:
                return
            self.storage.delete_collection(item.text())
            self._refresh_collections()
        else:
            item = self.history_list.currentItem()
            if not item:
                return
            history_id = item.data(Qt.ItemDataRole.UserRole)
            self.storage.delete_history(history_id)
            self._refresh_history()

    def _refresh_lists(self) -> None:
        self._refresh_collections()
        self._refresh_history()

    def _refresh_collections(self) -> None:
        from PySide6.QtWidgets import QListWidgetItem

        selected = self.collection_list.currentItem().text() if self.collection_list.currentItem() else ""
        self.collection_list.clear()
        for entry in self.storage.list_collections():
            item = QListWidgetItem(entry["name"])
            item.setData(Qt.ItemDataRole.UserRole, entry["name"])
            self.collection_list.addItem(item)
            if entry["name"] == selected:
                self.collection_list.setCurrentItem(item)

    def _refresh_history(self) -> None:
        from PySide6.QtWidgets import QListWidgetItem

        self.history_list.clear()
        for entry in self.storage.list_history():
            request = RequestData.from_dict(json.loads(entry["request_json"]))
            moment = datetime.fromisoformat(entry["created_at"]).astimezone().strftime("%m-%d %H:%M:%S")
            status = f"{entry['status_code']}" if entry["status_code"] else "ERR"
            item = QListWidgetItem(f"{moment}  {request.method}  {status}\n{request.url}")
            item.setData(Qt.ItemDataRole.UserRole, entry["id"])
            self.history_list.addItem(item)

    def _load_collection(self, item: Any) -> None:
        request = self.storage.get_collection(item.text())
        if request:
            self._set_request(request)
            self.sidebar_tabs.setCurrentWidget(self.collection_list)

    def _load_history(self, item: Any) -> None:
        history_id = item.data(Qt.ItemDataRole.UserRole)
        request = self.storage.get_history_request(history_id)
        response = self.storage.get_history_response(history_id)
        if request:
            self._set_request(request)
        if response:
            self._display_response(response)

    def _preview_history(self, current: Any, previous: Any) -> None:
        if current is None or self.sidebar_tabs.currentWidget() != self.history_list:
            return
        history_id = current.data(Qt.ItemDataRole.UserRole)
        response = self.storage.get_history_response(history_id)
        if response:
            self._display_response(response)


class MainWindow(QMainWindow):
    def __init__(self) -> None:
        super().__init__()
        self.setWindowFlags(self.windowFlags() | Qt.WindowType.FramelessWindowHint)
        self.setWindowTitle("CurlDesk")
        self.resize(1220, 860)
        self.setMinimumSize(900, 650)
        self._closing_workspaces: list[RequestWorkspace] = []

        self.workspaces = QTabWidget()
        self.workspaces.setObjectName("workspaces")
        self.workspaces.setDocumentMode(True)
        self.workspaces.setTabsClosable(True)
        self.workspaces.setMovable(True)
        self.workspaces.setUsesScrollButtons(True)
        self.workspaces.tabCloseRequested.connect(self._close_workspace)
        self.workspaces.currentChanged.connect(self._workspace_changed)

        self.new_tab_button = QToolButton()
        self.new_tab_button.setText("+")
        self.new_tab_button.setToolTip("新建请求标签页")
        self.new_tab_button.setAccessibleName("新建请求标签页")
        self.new_tab_button.clicked.connect(self._new_workspace)
        self.workspaces.setCornerWidget(self.new_tab_button, Qt.Corner.TopRightCorner)

        shell = QWidget()
        shell.setObjectName("mainShell")
        shell_layout = QVBoxLayout(shell)
        shell_layout.setContentsMargins(1, 1, 1, 1)
        shell_layout.setSpacing(0)
        self.title_bar = AppTitleBar(self)
        shell_layout.addWidget(self.title_bar)
        shell_layout.addWidget(self.workspaces, 1)
        self.setCentralWidget(shell)

        self.setStyleSheet(
            """
            QMainWindow { background: #f2f4f5; }
            QWidget#mainShell { background: #f3f5f6; border: 1px solid #d5dfdf; }
            QWidget#appTitleBar { background: #ffffff; border-bottom: 1px solid #dce4e5; }
            QLabel#windowTitleText { color: #284247; font-size: 9pt; font-weight: 600; }
            QTabWidget#workspaces::pane { border: 0; background: #f3f5f6; }
            QTabWidget#workspaces > QTabBar::tab {
                min-width: 108px; max-width: 190px; height: 37px;
                padding: 0 12px; color: #65777a; background: #edf1f1;
                border: 0; border-right: 1px solid #dfe6e5;
                border-top: 2px solid transparent;
                font-family: 'Microsoft YaHei UI'; font-size: 10pt;
            }
            QTabWidget#workspaces > QTabBar::tab:selected {
                color: #176d68; background: #f3f5f6; border-top: 2px solid #15978e;
            }
            QTabWidget#workspaces > QTabBar::tab:hover:!selected { background: #e3ecea; }
            QToolButton#windowControlButton, QToolButton#windowCloseButton {
                min-width: 44px; max-width: 44px; min-height: 0; max-height: 36px;
                padding: 0; border: none; border-radius: 0;
                color: #48615f; background: transparent; font-size: 12pt;
            }
            QToolButton#windowControlButton:hover { color: #087f78; background: #e7f1ef; }
            QToolButton#windowCloseButton:hover { color: #ffffff; background: #d94e48; }
            QToolButton { min-width: 38px; min-height: 37px; border: none;
                color: #48615f; background: transparent; font-size: 19px; }
            QToolButton:hover { background: #dfece9; color: #087f78; }
            """
        )
        self._new_workspace()

    def nativeEvent(self, event_type: Any, message: int) -> tuple[bool, int]:
        if sys.platform == "win32":
            from ctypes import wintypes

            native_message = ctypes.cast(
                int(message), ctypes.POINTER(wintypes.MSG)
            ).contents
            if native_message.message == 0x0084:  # WM_NCHITTEST
                packed = int(native_message.lParam)
                x = ctypes.c_short(packed & 0xFFFF).value
                y = ctypes.c_short((packed >> 16) & 0xFFFF).value
                point = QPoint(x, y)

                for button in (
                    self.title_bar.minimize_button,
                    self.title_bar.maximize_button,
                    self.title_bar.close_button,
                ):
                    if button.rect().contains(button.mapFromGlobal(point)):
                        return True, 1  # HTCLIENT

                if not self.isMaximized():
                    frame = self.frameGeometry()
                    margin = 6
                    left = x < frame.left() + margin
                    right = x >= frame.right() - margin
                    top = y < frame.top() + margin
                    bottom = y >= frame.bottom() - margin
                    if top and left:
                        return True, 13  # HTTOPLEFT
                    if top and right:
                        return True, 14  # HTTOPRIGHT
                    if bottom and left:
                        return True, 16  # HTBOTTOMLEFT
                    if bottom and right:
                        return True, 17  # HTBOTTOMRIGHT
                    if left:
                        return True, 10  # HTLEFT
                    if right:
                        return True, 11  # HTRIGHT
                    if top:
                        return True, 12  # HTTOP
                    if bottom:
                        return True, 15  # HTBOTTOM

                title_point = self.title_bar.mapFromGlobal(point)
                if self.title_bar.rect().contains(title_point):
                    return True, 2  # HTCAPTION
        return super().nativeEvent(event_type, message)

    def changeEvent(self, event: QEvent) -> None:
        super().changeEvent(event)
        if event.type() == QEvent.Type.WindowStateChange and hasattr(self, "title_bar"):
            self.title_bar.update_maximize_button()

    def current_workspace(self) -> RequestWorkspace:
        return self.workspaces.currentWidget()

    def _workspace_changed(self, index: int) -> None:
        workspace = self.workspaces.widget(index)
        if isinstance(workspace, RequestWorkspace):
            workspace._refresh_lists()

    def _new_workspace(self) -> None:
        workspace = RequestWorkspace()
        workspace.setWindowFlags(Qt.WindowType.Widget)
        index = self.workspaces.addTab(workspace, "快速请求")
        workspace.title_changed.connect(
            lambda title, page=workspace: self._set_workspace_title(page, title)
        )
        self.workspaces.setCurrentIndex(index)
        workspace.url_edit.setFocus()

    def _set_workspace_title(self, workspace: RequestWorkspace, title: str) -> None:
        index = self.workspaces.indexOf(workspace)
        if index >= 0:
            self.workspaces.setTabText(index, title)
            self.workspaces.setTabToolTip(index, workspace.url_edit.text() or "快速请求")

    def _close_workspace(self, index: int) -> None:
        workspace = self.workspaces.widget(index)
        if not isinstance(workspace, RequestWorkspace):
            return
        answer = QMessageBox.question(
            self, "关闭标签页", "关闭此标签页？未保存到请求集合的内容将丢失。",
            QMessageBox.StandardButton.Close | QMessageBox.StandardButton.Cancel,
            QMessageBox.StandardButton.Cancel,
        )
        if answer != QMessageBox.StandardButton.Close:
            return
        self.workspaces.removeTab(index)
        if workspace._workers:
            workspace.hide()
            self._closing_workspaces.append(workspace)
            for worker in workspace._workers:
                worker.finished.connect(lambda page=workspace: self._release_closed_workspace(page))
        else:
            workspace.deleteLater()
        if self.workspaces.count() == 0:
            self._new_workspace()

    def _release_closed_workspace(self, workspace: RequestWorkspace) -> None:
        if any(worker.isRunning() for worker in workspace._workers):
            return
        if workspace in self._closing_workspaces:
            self._closing_workspaces.remove(workspace)
            workspace.deleteLater()

    def closeEvent(self, event: Any) -> None:
        pages = [self.workspaces.widget(i) for i in range(self.workspaces.count())]
        pages.extend(self._closing_workspaces)
        if any(page._workers for page in pages):
            QMessageBox.information(self, "请求进行中", "请等待正在发送的请求完成后再关闭应用。")
            event.ignore()
            return
        event.accept()
