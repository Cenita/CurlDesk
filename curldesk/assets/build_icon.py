from __future__ import annotations

import struct
import tempfile
from pathlib import Path

from PySide6.QtCore import QByteArray, QSize, Qt
from PySide6.QtGui import QImage
from PySide6.QtSvg import QSvgRenderer


ROOT = Path(__file__).parent
SVG_PATH = ROOT / "curldesk.svg"
ICO_PATH = ROOT / "curldesk.ico"
SIZES = (16, 24, 32, 48, 64, 128, 256)


def main() -> None:
    renderer = QSvgRenderer(QByteArray(SVG_PATH.read_bytes()))
    if not renderer.isValid():
        raise RuntimeError(f"Could not load logo SVG: {SVG_PATH}")
    images: list[tuple[int, bytes]] = []
    with tempfile.TemporaryDirectory() as temporary_directory:
        for size in SIZES:
            image = QImage(QSize(size, size), QImage.Format.Format_ARGB32_Premultiplied)
            image.fill(Qt.GlobalColor.transparent)
            from PySide6.QtGui import QPainter

            painter = QPainter(image)
            painter.setRenderHint(QPainter.RenderHint.Antialiasing)
            renderer.render(painter)
            painter.end()
            png_path = Path(temporary_directory) / f"icon-{size}.png"
            if not image.save(str(png_path), "PNG"):
                raise RuntimeError(f"Could not render {size}px icon")
            images.append((size, png_path.read_bytes()))

    header = struct.pack("<HHH", 0, 1, len(images))
    directory = bytearray()
    image_data = bytearray()
    offset = 6 + 16 * len(images)
    for size, png in images:
        directory.extend(
            struct.pack(
                "<BBBBHHII",
                0 if size == 256 else size,
                0 if size == 256 else size,
                0,
                0,
                1,
                32,
                len(png),
                offset,
            )
        )
        image_data.extend(png)
        offset += len(png)
    ICO_PATH.write_bytes(header + directory + image_data)


if __name__ == "__main__":
    main()
