#!/usr/bin/env python
"""Generate the Nostalgia Launcher app icon.

Renders an ORIGINAL text monogram (a stylized gold "N" on a dark rounded
purple square) completely offscreen using PySide6 (QImage + QPainter), so no
display server is required. Produces:

  packaging/icons/NostalgiaLauncher.png  (256x256, also 512x512 capable)
  packaging/icons/NostalgiaLauncher.ico  (256x256 PNG-in-ICO, Win Vista+)

The art is intentionally a flat rounded square with a letterform — there is no
medallion, gem, or any World-of-Warcraft-style "W" branding.
"""

import os
import struct

from PySide6.QtCore import Qt
from PySide6.QtGui import (
    QColor,
    QFont,
    QImage,
    QPainter,
)
from PySide6.QtWidgets import QApplication

# Palette (mirrors the app theme — see ui/qt/theme.py).
C_PANEL = "#161120"  # dark purple background
C_PANEL_BDR = "#261d3a"  # subtle border
C_GOLD = "#c8922a"  # primary gold fill
C_GOLD_LIGHT = "#e8b84b"  # lighter gold highlight

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
PNG_PATH = os.path.join(HERE, "NostalgiaLauncher.png")
ICO_PATH = os.path.join(ROOT, "NostalgiaLauncher.ico")


def render_png(size: int = 256) -> bytes:
    """Draw the monogram and return the PNG bytes for the given size."""
    img = QImage(size, size, QImage.Format_ARGB32)
    img.fill(Qt.transparent)

    painter = QPainter(img)
    painter.setRenderHint(QPainter.Antialiasing)
    painter.setRenderHint(QPainter.TextAntialiasing)

    margin = max(2, int(size * 0.04))
    rect = img.rect().adjusted(margin, margin, -margin, -margin)

    # Rounded-square background.
    bg = QColor(C_PANEL)
    painter.setBrush(bg)
    painter.setPen(Qt.NoPen)
    radius = int(size * 0.18)
    painter.drawRoundedRect(rect, radius, radius)

    # Subtle border.
    bdr = QColor(C_PANEL_BDR)
    painter.setBrush(Qt.NoBrush)
    pen = painter.pen()
    pen.setColor(bdr)
    pen.setWidthF(max(1.0, size * 0.012))
    painter.setPen(pen)
    painter.drawRoundedRect(rect, radius, radius)

    # Bold "N".
    font = QFont()
    font.setFamily("Sans Serif")
    font.setBold(True)
    font.setPixelSize(int(size * 0.74))
    painter.setFont(font)

    text_rect = rect.adjusted(int(size * 0.04), 0, -int(size * 0.04), 0)
    text_rect.translate(0, int(size * 0.02))

    # Gold fill.
    painter.setPen(QColor(C_GOLD))
    painter.drawText(text_rect, Qt.AlignCenter, "N")

    # Lighter gold highlight (thin stroke for legibility).
    pen = painter.pen()
    pen.setColor(QColor(C_GOLD_LIGHT))
    pen.setWidthF(max(1.0, size * 0.01))
    painter.setPen(pen)
    painter.drawText(text_rect, Qt.AlignCenter, "N")

    painter.end()

    return qimage_to_png_bytes(img)


def qimage_to_png_bytes(img: QImage) -> bytes:
    """Encode a QImage as PNG bytes via an in-memory buffer."""
    from PySide6.QtCore import QBuffer, QByteArray, QIODevice

    byte_array = QByteArray()
    buf = QBuffer(byte_array)
    buf.open(QIODevice.WriteOnly)
    ok = img.save(buf, "PNG")
    buf.close()
    if not ok:
        raise RuntimeError("failed to encode PNG")
    return bytes(byte_array.data())


def write_ico(png_bytes: bytes, path: str) -> None:
    """Write a Windows Vista+ icon: ICO header + dir entry + PNG payload."""
    # ICONDIR (6 bytes): reserved, type, count.
    header = struct.pack("<HHH", 0, 1, 1)
    # ICONDIRENTRY (16 bytes).
    # width/height 0 means 256 in the format.
    entry = struct.pack(
        "<BBBBHHII",
        0,  # width (0 => 256)
        0,  # height (0 => 256)
        0,  # color count
        0,  # reserved
        1,  # planes
        32,  # bit count
        len(png_bytes),  # bytes in resource
        6 + 16,  # image offset
    )
    with open(path, "wb") as fh:
        fh.write(header)
        fh.write(entry)
        fh.write(png_bytes)


def main() -> None:
    QApplication.setAttribute(Qt.AA_UseSoftwareOpenGL, True)
    app = QApplication([])

    png_bytes = render_png(256)
    with open(PNG_PATH, "wb") as fh:
        fh.write(png_bytes)
    write_ico(png_bytes, ICO_PATH)

    # Optionally also emit a 512x512 PNG.
    png512 = render_png(512)
    with open(os.path.join(HERE, "NostalgiaLauncher@512.png"), "wb") as fh:
        fh.write(png512)

    app.quit()
    print(f"==> wrote {PNG_PATH} and {ICO_PATH}")


if __name__ == "__main__":
    main()
