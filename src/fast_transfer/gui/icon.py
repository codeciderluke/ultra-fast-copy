"""The Ultra Fast Copy icon, drawn with QPainter rather than shipped as a binary.

A lightning bolt (fast) with an offset echo behind it (copy), on a dark tile.
The echo only appears at 32px and up; below that it muddies the silhouette.
"""

from __future__ import annotations

import struct
from io import BytesIO
from pathlib import Path

from PySide6.QtCore import QBuffer, QIODevice, QPointF, QRectF, Qt
from PySide6.QtGui import (
    QBrush,
    QColor,
    QIcon,
    QLinearGradient,
    QPainter,
    QPainterPath,
    QPixmap,
)

from .theme import Colors

ICON_SIZES: tuple[int, ...] = (16, 24, 32, 48, 64, 128, 256)

# Bolt outline in a 0..1 unit square. The x range is deliberately narrow (0.28
# to 0.74) so the shape stays a bolt when mapped into a square target rect.
_BOLT: tuple[tuple[float, float], ...] = (
    (0.60, 0.00),
    (0.28, 0.545),
    (0.475, 0.545),
    (0.40, 1.00),
    (0.735, 0.435),
    (0.535, 0.435),
)
_BOLT_MIN_X = min(x for x, _ in _BOLT)
_BOLT_MAX_X = max(x for x, _ in _BOLT)

# Fraction of the tile the bolt occupies, and the echo's offset behind it.
_BOLT_SCALE = 0.72
_ECHO_OFFSET = (-0.115, 0.055)


def _bolt_path(size: float, *, scale: float = _BOLT_SCALE, dx: float = 0.0, dy: float = 0.0) -> QPainterPath:
    """Bolt centred in the tile at `scale`, nudged by (dx, dy) in tile fractions."""
    height = size * scale
    width = height * (_BOLT_MAX_X - _BOLT_MIN_X)
    left = (size - width) / 2 + dx * size
    top = (size - height) / 2 + dy * size

    path = QPainterPath()
    for index, (x, y) in enumerate(_BOLT):
        point = QPointF(left + (x - _BOLT_MIN_X) * height, top + y * height)
        if index == 0:
            path.moveTo(point)
        else:
            path.lineTo(point)
    path.closeSubpath()
    return path


def render_pixmap(size: int, *, tile: bool = True) -> QPixmap:
    """Draw the icon at `size` px. `tile=False` gives a transparent-background mark."""
    pixmap = QPixmap(size, size)
    pixmap.fill(Qt.GlobalColor.transparent)

    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
    painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, True)

    if tile:
        _draw_tile(painter, size)
    if size >= 32:
        _draw_echo(painter, size)
    _draw_bolt(painter, size)

    painter.end()
    return pixmap


def _draw_tile(painter: QPainter, size: int) -> None:
    gradient = QLinearGradient(0, 0, size, size)
    gradient.setColorAt(0.0, QColor("#232a38"))
    gradient.setColorAt(1.0, QColor("#0d1016"))

    radius = size * 0.22
    painter.setPen(Qt.PenStyle.NoPen)
    painter.setBrush(QBrush(gradient))
    painter.drawRoundedRect(QRectF(0, 0, size, size), radius, radius)

    if size >= 32:  # hairline rim for definition against dark backgrounds
        pen = painter.pen()
        painter.setBrush(Qt.BrushStyle.NoBrush)
        pen.setColor(QColor(255, 255, 255, 26))
        pen.setWidthF(max(1.0, size * 0.008))
        pen.setStyle(Qt.PenStyle.SolidLine)
        painter.setPen(pen)
        inset = pen.widthF() / 2
        painter.drawRoundedRect(
            QRectF(inset, inset, size - pen.widthF(), size - pen.widthF()),
            radius - inset,
            radius - inset,
        )


def _draw_echo(painter: QPainter, size: int) -> None:
    """A dimmed bolt offset behind the main one: the copy metaphor."""
    painter.setPen(Qt.PenStyle.NoPen)
    painter.setBrush(QColor(Colors.ACCENT).darker(260))
    painter.drawPath(_bolt_path(float(size), dx=_ECHO_OFFSET[0], dy=_ECHO_OFFSET[1]))


def _draw_bolt(painter: QPainter, size: int) -> None:
    path = _bolt_path(float(size))

    if size >= 48:  # soft accent glow, only where it will actually be visible
        glow = QColor(Colors.ACCENT)
        glow.setAlpha(55)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(glow)
        painter.save()
        painter.translate(size * 0.014, size * 0.014)
        painter.drawPath(path)
        painter.restore()

    gradient = QLinearGradient(size * 0.25, 0, size * 0.75, size)
    gradient.setColorAt(0.0, QColor("#8fe0ff"))
    gradient.setColorAt(0.5, QColor(Colors.ACCENT))
    gradient.setColorAt(1.0, QColor("#2b9ede"))

    painter.setPen(Qt.PenStyle.NoPen)
    painter.setBrush(QBrush(gradient))
    painter.drawPath(path)


def app_icon() -> QIcon:
    """Multi-resolution icon for the window, taskbar, and tray."""
    icon = QIcon()
    for size in ICON_SIZES:
        icon.addPixmap(render_pixmap(size))
    return icon


def logo_pixmap(size: int = 28) -> QPixmap:
    """The mark used in the window's header bar."""
    return render_pixmap(size)


def _png_bytes(pixmap: QPixmap) -> bytes:
    buffer = QBuffer()
    buffer.open(QIODevice.OpenModeFlag.WriteOnly)
    pixmap.save(buffer, "PNG")
    data: bytes = buffer.data().data()
    buffer.close()
    return data


def save_ico(path: Path, sizes: tuple[int, ...] = ICON_SIZES) -> Path:
    """Write a multi-size .ico for PyInstaller and Explorer.

    Assembled by hand from PNG entries (read by every Windows since Vista), so
    it needs neither Pillow nor a Qt ICO writer.
    """
    images = [(size, _png_bytes(render_pixmap(size))) for size in sorted(sizes)]
    path.parent.mkdir(parents=True, exist_ok=True)

    header = struct.pack("<HHH", 0, 1, len(images))  # reserved, type=icon, count
    directory = BytesIO()
    payload = BytesIO()
    offset = len(header) + 16 * len(images)

    for size, data in images:
        # 256 is stored as 0 in the directory entry.
        dimension = 0 if size >= 256 else size
        directory.write(
            struct.pack(
                "<BBBBHHII",
                dimension,  # width
                dimension,  # height
                0,  # palette count
                0,  # reserved
                1,  # colour planes
                32,  # bits per pixel
                len(data),
                offset,
            )
        )
        payload.write(data)
        offset += len(data)

    path.write_bytes(header + directory.getvalue() + payload.getvalue())
    return path


def save_png(path: Path, size: int = 256) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    render_pixmap(size).save(str(path), "PNG")
    return path
