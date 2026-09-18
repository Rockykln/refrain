"""Render the GitHub social preview (1280x640) from the README screenshots.

python docs/screenshots/social.py docs/social-preview.png
"""

import sys
from pathlib import Path

from PySide6.QtCore import QRectF, Qt
from PySide6.QtGui import (
    QColor,
    QFont,
    QGuiApplication,
    QImage,
    QLinearGradient,
    QPainter,
    QPainterPath,
    QPen,
)
from PySide6.QtSvg import QSvgRenderer

repo = Path(__file__).resolve().parents[2]
out = Path(sys.argv[1])
app = QGuiApplication(sys.argv[:1])
shots = repo / "docs" / "screenshots"

W, H = 1280, 640
img = QImage(W, H, QImage.Format.Format_ARGB32)
p = QPainter(img)
p.setRenderHints(
    QPainter.RenderHint.Antialiasing
    | QPainter.RenderHint.SmoothPixmapTransform
    | QPainter.RenderHint.TextAntialiasing
)
bg = QLinearGradient(0, 0, W, H)
bg.setColorAt(0, QColor("#17181c"))
bg.setColorAt(1, QColor("#26222e"))
p.fillRect(0, 0, W, H, bg)
p.translate(0, 36)  # centre the content vertically


def rounded(image, rect, radius, shadow=True):
    if shadow:
        for i in range(14, 0, -2):
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(QColor(0, 0, 0, 10))
            p.drawRoundedRect(rect.adjusted(-i, -i + 6, i, i + 6), radius + i, radius + i)
    path = QPainterPath()
    path.addRoundedRect(rect, radius, radius)
    p.save()
    p.setClipPath(path)
    p.drawImage(rect, image)
    p.restore()
    p.setPen(QPen(QColor(255, 255, 255, 28), 1))
    p.setBrush(Qt.BrushStyle.NoBrush)
    p.drawRoundedRect(rect, radius, radius)


# Right: the history window, cut to its top rows, with the Discord card over it.
hist = QImage(str(shots / "history.png"))
hist = hist.copy(0, 0, hist.width(), 740)
hw = 470
hh = hist.height() * hw / hist.width()
hrect = QRectF(W - hw - 60, 64, hw, hh)
rounded(hist, hrect, 14)
card = QImage(str(shots / "discord-rpc.png"))
cw = 440
ch = card.height() * cw / card.width()
crect = QRectF(W - cw - 170, hrect.bottom() - 70, cw, ch)
rounded(card, crect, 14)

# Left: icon, name, what it does.
icon = QSvgRenderer(str(repo / "src" / "refrain" / "assets" / "icons" / "refrain.svg"))
icon.render(p, QRectF(80, 96, 104, 104))
p.setPen(QColor("#f2f2f5"))
f = QFont("Noto Sans", 64)
f.setWeight(QFont.Weight.Bold)
p.setFont(f)
p.drawText(
    QRectF(76, 210, 560, 100), Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter, "Refrain"
)
f = QFont("Noto Sans", 23)
p.setFont(f)
p.setPen(QColor("#d4d4dc"))
p.drawText(
    QRectF(82, 320, 540, 130),
    Qt.AlignmentFlag.AlignLeft | Qt.TextFlag.TextWordWrap,
    "Apple Music on Linux as your Discord status, with Last.fm scrobbling",
)
f = QFont("Noto Sans", 17)
p.setFont(f)
p.setPen(QColor("#9a9aa6"))
p.drawText(
    QRectF(82, 470, 540, 70),
    Qt.AlignmentFlag.AlignLeft | Qt.TextFlag.TextWordWrap,
    "From the browser or your phone over Bluetooth",
)
p.end()
img.save(str(out))
print("saved", out)
