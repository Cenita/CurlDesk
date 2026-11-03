import sys
from pathlib import Path

from PySide6.QtGui import QIcon
from PySide6.QtWidgets import QApplication

from curldesk.ui import MainWindow


def main() -> int:
    app = QApplication(sys.argv)
    app.setStyle("Fusion")
    app.setPalette(app.style().standardPalette())
    app.setStyleSheet("""
        QDialog, QMessageBox { background-color: #ffffff; color: #24343a; }
        QDialog QLabel, QMessageBox QLabel { color: #24343a; }
        QDialog QPushButton, QMessageBox QPushButton {
            color: #24343a; background-color: #f6f8f8;
            border: 1px solid #ccd8d8; border-radius: 4px; padding: 6px 16px;
        }
        QDialog QPushButton:hover, QMessageBox QPushButton:hover { background-color: #e5f2ef; }
    """)
    app.setApplicationName("CurlDesk")
    app.setOrganizationName("CurlDesk")
    assets = Path(__file__).resolve().parent / "curldesk" / "assets"
    app.setWindowIcon(QIcon(str(assets / "curldesk.ico")))
    window = MainWindow()
    window.show()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
