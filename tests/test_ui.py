import asyncio
import json
import os
import tempfile
import threading
import time
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QEventLoop, QTimer
from PySide6.QtWidgets import QApplication, QMessageBox, QTableWidgetItem

from curldesk.import_curl import parse_curl
from curldesk.models import ResponseData
from curldesk.storage import Storage
from curldesk.ui import MainWindow, _read_table, _set_table


class UiSmokeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def test_manual_row_movement_and_http_version(self):
        with tempfile.TemporaryDirectory() as folder:
            with patch("curldesk.ui.Storage", return_value=Storage(Path(folder) / "ui.sqlite3")):
                shell = MainWindow()
            self.addCleanup(shell.close)
            workspace = shell.current_workspace()
            table = workspace.params_table
            _set_table(table, [("a", "1"), ("b", "2"), ("c", "3")])
            table.setCurrentCell(2, 0)
            workspace._move_table_row(table, -1)
            self.assertEqual(_read_table(table), [("a", "1"), ("c", "3"), ("b", "2")])
            table.move_row(0, 2, True)
            self.assertEqual(_read_table(table), [("c", "3"), ("b", "2"), ("a", "1")])
            workspace.http_version_combo.setCurrentIndex(workspace.http_version_combo.findData("1.1"))
            request = workspace._collect_request()
            workspace._set_request(request)
            self.assertEqual(workspace._collect_request().http_version, "1.1")

    def test_editor_import_response_and_background_request(self):
        with tempfile.TemporaryDirectory() as folder:
            storage = Storage(Path(folder) / "ui.sqlite3")
            with patch("curldesk.ui.Storage", return_value=storage):
                shell = MainWindow()
            self.addCleanup(shell.close)
            window = shell.current_workspace()

            request, warnings = parse_curl(
                "curl -X POST https://example.test/items -H 'Content-Type: application/json' "
                "--data '{\"ok\":true}'"
            )
            self.assertEqual(warnings, [])
            window._set_request(request)
            self.assertEqual(window._collect_request(), request)

            window._display_response(
                ResponseData(
                    status_code=200,
                    reason="OK",
                    elapsed_ms=4,
                    headers=[("Content-Type", "application/json")],
                    body=b'{"ok":true}',
                )
            )
            self.assertEqual(window.response_body.toPlainText(), '{"ok":true}')
            self.assertIn('"ok": true', window.formatted_body.toPlainText())
            self.assertEqual(window.response_tabs.currentIndex(), window.formatted_tab_index)

            class EchoHandler(BaseHTTPRequestHandler):
                def do_GET(self):
                    body = b"{\"ui\":true}"
                    self.send_response(200)
                    self.send_header("Content-Type", "application/json")
                    self.send_header("Content-Length", str(len(body)))
                    self.end_headers()
                    self.wfile.write(body)

                def log_message(self, format, *args):
                    pass

            server = ThreadingHTTPServer(("127.0.0.1", 0), EchoHandler)
            server_thread = threading.Thread(target=server.serve_forever, daemon=True)
            server_thread.start()
            self.addCleanup(server.server_close)
            self.addCleanup(server.shutdown)
            self.addCleanup(server_thread.join, 2)

            window.url_edit.setText(f"http://127.0.0.1:{server.server_port}/")
            window.method_combo.setCurrentText("GET")
            loop = QEventLoop()
            timer = QTimer()
            timer.setSingleShot(True)
            timer.timeout.connect(loop.quit)
            timer.start(5000)
            worker = None
            original = window._request_completed

            def completed(req, response):
                original(req, response)
                loop.quit()

            window._request_completed = completed
            window._send_request()
            worker = window._workers[-1]
            loop.exec()
            if worker.isRunning():
                worker.wait(2000)
            self.assertTrue(window.send_button.isEnabled())
            self.assertEqual(window.response_body.toPlainText(), '{"ui":true}')
            self.assertIn('"ui": true', window.formatted_body.toPlainText())
            self.assertEqual(len(storage.list_history()), 1)

    def test_tabs_keep_requests_and_async_responses_separate(self):
        with tempfile.TemporaryDirectory() as folder:
            storage = Storage(Path(folder) / "tabs.sqlite3")
            with patch("curldesk.ui.Storage", return_value=storage):
                shell = MainWindow()
                self.addCleanup(shell.close)
                first = shell.current_workspace()
                first.url_edit.setText("https://example.test/first")
                first.raw_body_text.setPlainText("first draft")
                first.body_combo.setCurrentText("原始文本")
                shell._new_workspace()
                second = shell.current_workspace()
                second.url_edit.setText("https://example.test/second")
                second.body_combo.setCurrentText("JSON")
                second.json_body_text.setPlainText('{"second": true}')

                shell.workspaces.setCurrentIndex(0)
                self.assertEqual(first.raw_body_text.toPlainText(), "first draft")
                self.assertEqual(first.url_edit.text(), "https://example.test/first")
                shell.workspaces.setCurrentIndex(1)
                self.assertEqual(second.json_body_text.toPlainText(), '{"second": true}')

                async def fake_request(request):
                    if request.url.endswith("first"):
                        await asyncio.sleep(0.15)
                    return ResponseData(status_code=200, body=request.url.encode())

                with patch("curldesk.ui.perform_request_async", side_effect=fake_request):
                    first._send_request()
                    second._send_request()
                    deadline = time.monotonic() + 5
                    while (first._workers or second._workers) and time.monotonic() < deadline:
                        self.app.processEvents()
                        time.sleep(0.01)
                self.assertFalse(first._workers or second._workers)
                self.assertIn("/first", first.response_body.toPlainText())
                self.assertIn("/second", second.response_body.toPlainText())
                self.assertEqual(len(storage.list_history()), 2)

    def test_closing_in_flight_tab_and_automatic_parameter_row(self):
        with tempfile.TemporaryDirectory() as folder:
            storage = Storage(Path(folder) / "close.sqlite3")
            with patch("curldesk.ui.Storage", return_value=storage):
                shell = MainWindow()
                self.addCleanup(shell.close)
                first = shell.current_workspace()
                first.params_table.setItem(0, 0, QTableWidgetItem("key"))
                self.assertEqual(first.params_table.rowCount(), 2)
                first.url_edit.setText("https://example.test/slow")
                shell._new_workspace()

                async def slow_request(request):
                    await asyncio.sleep(0.15)
                    return ResponseData(status_code=200, body=b"done")

                with patch("curldesk.ui.perform_request_async", side_effect=slow_request):
                    first._send_request()
                    with patch("curldesk.ui.QMessageBox.question", return_value=QMessageBox.StandardButton.Close):
                        shell._close_workspace(0)
                    self.assertEqual(shell.workspaces.count(), 1)
                    deadline = time.monotonic() + 5
                    while shell._closing_workspaces and time.monotonic() < deadline:
                        self.app.processEvents()
                        time.sleep(0.01)
                self.assertEqual(shell._closing_workspaces, [])
                self.assertEqual(len(storage.list_history()), 1)

    def test_cancel_interrupts_a_waiting_http_request(self):
        started = threading.Event()
        release = threading.Event()

        class SlowHandler(BaseHTTPRequestHandler):
            def do_GET(self):
                started.set()
                release.wait(5)
                try:
                    self.send_response(200)
                    self.end_headers()
                    self.wfile.write(b"done")
                except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
                    pass

            def log_message(self, format, *args):
                pass

        server = ThreadingHTTPServer(("127.0.0.1", 0), SlowHandler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        self.addCleanup(release.set)
        with tempfile.TemporaryDirectory() as folder:
            storage = Storage(Path(folder) / "cancel.sqlite3")
            with patch("curldesk.ui.Storage", return_value=storage):
                shell = MainWindow()
            self.addCleanup(shell.close)
            page = shell.current_workspace()
            page.url_edit.setText(f"http://127.0.0.1:{server.server_port}/")
            page._send_request()
            deadline = time.monotonic() + 5
            while not started.is_set() and time.monotonic() < deadline:
                self.app.processEvents()
                time.sleep(0.01)
            self.assertTrue(started.is_set())
            page._send_or_cancel()
            deadline = time.monotonic() + 3
            while page._workers and time.monotonic() < deadline:
                self.app.processEvents()
                time.sleep(0.01)
            self.assertFalse(page._workers)
            self.assertEqual(page.send_button.text(), "发送")
            self.assertIn("请求已取消", page.response_status.text())
            self.assertEqual(storage.list_history()[0]["error"], "请求已取消")

    def test_invalid_url_shows_inline_error(self):
        with tempfile.TemporaryDirectory() as folder:
            storage = Storage(Path(folder) / "validation.sqlite3")
            with patch("curldesk.ui.Storage", return_value=storage):
                shell = MainWindow()
            self.addCleanup(shell.close)
            page = shell.current_workspace()
            page._send_request()
            self.assertIn("请输入请求 URL", page.validation_error.text())
            self.assertFalse(page.validation_error.isHidden())
            self.assertFalse(page._workers)


if __name__ == "__main__":
    unittest.main()
