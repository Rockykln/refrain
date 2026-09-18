"""Render the README screenshots from the real windows, filled with demo data.

    python docs/screenshots/render.py 0.5.3

Needs KDE Plasma's kwin_wayland (for an invisible display) and the Breeze
colour schemes. Writes every shot in Breeze Dark and Breeze Light (``-light``).
"""

from __future__ import annotations

import math
import os
import random
import re
import subprocess
import sys
import tempfile
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[1]
SCHEMES = Path("/usr/share/color-schemes")

SONGS = [
    ("Glass Tides", "Neon Harbor", "Low Light", 214_000, "Chromium", None),
    ("Paper Satellites", "Marlow Vance", "Signals", 187_000, "Chromium", "bubbles"),
    ("Northbound", "The Quiet Hours", "", 243_000, "Firefox", "waves"),
    ("Silk Road Radio", "Ilse Moreau", "Caravan", 201_000, "Brave", "rings"),
    ("Overexposed", "Kite Theory", "Afterimage (Deluxe Edition)", 176_000, "Chromium", "glow"),
    ("Rooftop Weather", "Juniper Lane", "Rooftop Weather", 229_000, "Zen", "bubbles"),
    ("Ferrous", "Oskar Lind", "Iron Garden", 318_000, "Chromium", "waves"),
    ("Late Train Home", "Velvet Static", "Commuter", 195_000, "Google Chrome", "rings"),
    ("Hanabi Afterglow", "Sora Minami", "Summer Lights", 208_000, "Chromium", "glow"),
    ("Salt Flats", "Wren & Ash", "Open Country", 234_000, "Chromium", "waves"),
    ("Tin Can Telephone", "Porchlight", "Porchlight", 162_000, "Firefox", "bubbles"),
    ("Undertow", "Mara Keel", "Tidal", 257_000, "Chromium", "rings"),
    ("Cloud Atlas Waltz", "The Paper Boats", "Drift", 199_000, "Brave", "glow"),
    ("Midnight Transit", "Lumen Row", "Afterhours", 221_000, "Chromium", "waves"),
]

LOG = [
    "refrain.app: Refrain {version} starting",
    "refrain.discord_rpc: Discord RPC connected (discord-ipc-0)",
    "refrain.startup_check: [startup-check] Last.fm: OK — authenticated as refrain_demo",
    "refrain.daemon: Track change [mpris]: Glass Tides — Neon Harbor (playing)",
    "refrain.daemon: Position: unknown → reported (source's own value)",
    "refrain.history: History: now playing Neon Harbor — Glass Tides [mpris]",
    "refrain.history: History: kept Neon Harbor — Glass Tides (played 1:47 of 3:34)",
    "refrain.scrobble: Scrobble queued: Neon Harbor — Glass Tides",
    "refrain.scrobble: Scrobbled 1 queued track(s) to Last.fm",
]


def main() -> int:
    version = sys.argv[1]
    socket = f"refrain-shots-{os.getpid()}"
    kwin = subprocess.Popen(
        ["kwin_wayland", "--virtual", "--socket", socket, "--width", "2400", "--height", "1600"],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    try:
        runtime = Path(os.environ.get("XDG_RUNTIME_DIR", f"/run/user/{os.getuid()}"))
        for _ in range(50):
            if (runtime / socket).exists():
                break
            time.sleep(0.1)
        for scheme, suffix in (("BreezeDark", ""), ("BreezeLight", "-light")):
            with tempfile.TemporaryDirectory() as tmp:
                config = Path(tmp) / "config"
                config.mkdir()
                colors = (SCHEMES / f"{scheme}.colors").read_text(encoding="utf-8")
                (config / "kdeglobals").write_text(
                    f"{colors}\n[General]\nColorScheme={scheme}\n[KDE]\nwidgetStyle=Breeze\n",
                    encoding="utf-8",
                )
                env = dict(
                    os.environ,
                    XDG_CONFIG_HOME=str(config),
                    XDG_STATE_HOME=f"{tmp}/state",
                    XDG_DATA_HOME=f"{tmp}/data",
                    XDG_CACHE_HOME=f"{tmp}/cache",
                    LANG="en_US.UTF-8",
                    LC_ALL="en_US.UTF-8",
                    LANGUAGE="en_US",
                    WAYLAND_DISPLAY=socket,
                    QT_QPA_PLATFORM="wayland",
                    QT_QPA_PLATFORMTHEME="kde",
                    QT_SCALE_FACTOR="1.5",
                )
                subprocess.run(
                    [sys.executable, __file__, "--render", version, suffix], env=env, check=True
                )
    finally:
        kwin.terminate()
    return 0


def render(version: str, suffix: str) -> None:
    import refrain

    refrain.__version__ = version
    from PySide6.QtCore import QObject, QPoint, QPointF, Qt, Signal
    from PySide6.QtGui import QColor, QImage, QLinearGradient, QPainter, QPen, QRadialGradient
    from PySide6.QtWidgets import QApplication

    app = QApplication(sys.argv[:1])

    import refrain.ui.settings_window as sw
    import refrain.ui.update_dialog as ud
    from refrain import updater
    from refrain.config import Config
    from refrain.cover_art import image_path_for_url
    from refrain.history import HistoryEntry, HistorySnapshot
    from refrain.sources.base import PlaybackStatus, TrackInfo
    from refrain.sources.bluetooth import BluetoothSource
    from refrain.startup_check import OK, CheckResult
    from refrain.ui.history_window import HistoryWindow
    from refrain.ui.legal_dialog import LegalDialog
    from refrain.ui.log_window import LogWindow
    from refrain.ui.tray import TrayIcon
    from refrain.ui.welcome_dialog import WelcomeDialog

    sw.__version__ = version
    updater.__version__ = version
    # No lookups against Discord or BlueZ: every value on screen is demo data.
    BluetoothSource.list_paired_devices = staticmethod(lambda: [])
    sw.SettingsWindow._lookup_application_name = lambda self: self._show_application_name(
        self.client_id_input.text(), sw.FOUND, "Apple Music"
    )

    def settle(ms: int = 700) -> None:
        end = time.monotonic() + ms / 1000
        while time.monotonic() < end:
            app.processEvents()
            time.sleep(0.02)

    def shot(widget, name: str) -> None:
        widget.show()
        settle()
        widget.grab().save(str(HERE / f"{name}{suffix}.png"))
        widget.hide()
        print(f"{name}{suffix}.png")

    def cover(path: Path, seed: int, style: str) -> None:
        rnd = random.Random(seed)
        img = QImage(600, 600, QImage.Format.Format_RGB32)
        p = QPainter(img)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        a = QColor.fromHsv(rnd.randrange(360), 150, 210)
        b = QColor.fromHsv((a.hue() + rnd.randrange(40, 120)) % 360, 190, 120)
        g = QLinearGradient(0, 0, 600, 600)
        g.setColorAt(0, a)
        g.setColorAt(1, b)
        p.fillRect(0, 0, 600, 600, g)
        if style == "bubbles":
            p.setPen(Qt.PenStyle.NoPen)
            for _ in range(9):
                p.setBrush(QColor.fromHsv((a.hue() + rnd.randrange(-30, 30)) % 360, 120, 240, 90))
                r = rnd.randrange(60, 170)
                p.drawEllipse(QPointF(rnd.randrange(600), rnd.randrange(600)), r, r)
        elif style == "waves":
            p.setPen(QPen(QColor(255, 255, 255, 110), 3))
            for k in range(12):
                pts = [
                    QPointF(x, 180 + k * 22 + 30 * math.sin(x / 70 + k)) for x in range(0, 601, 10)
                ]
                for i in range(len(pts) - 1):
                    p.drawLine(pts[i], pts[i + 1])
        elif style == "rings":
            p.setPen(QPen(QColor(255, 255, 255, 90), 3))
            for r in range(20, 420, 26):
                p.drawEllipse(QPointF(300, 300), r, r)
        else:
            rg = QRadialGradient(300, 330, 160)
            rg.setColorAt(0, QColor(235, 215, 255, 230))
            rg.setColorAt(1, QColor(255, 255, 255, 0))
            p.fillRect(0, 0, 600, 600, rg)
        p.end()
        img.save(str(path), "JPG", 92)

    now = int(time.time())
    entries = []
    for i, (title, artist, album, duration, player, style) in enumerate(SONGS):
        url = f"https://example.invalid/cover/{i}.jpg"
        path = image_path_for_url(url)
        path.parent.mkdir(parents=True, exist_ok=True)
        if style is None:
            QImage(str(HERE / "demo-cover.png")).save(str(path), "JPG", 92)
        else:
            cover(path, i * 7 + 3, style)
        entries.append(
            HistoryEntry(
                title=title,
                artist=artist,
                album=album,
                source="mpris",
                player=player,
                started_at=now - 60 - i * 215,
                duration_ms=duration,
                cover_url=url,
                scrobbled=i % 2 == 1,
            )
        )

    config = Config()
    config.discord.client_id = "1234567890123456789"
    config.discord.resolve_app_name = True
    config.behavior.autostart = True
    config.advanced.language = "en"
    config.lastfm.enabled = True
    config.lastfm.api_key = "0123456789abcdef0123456789abcdef"
    config.lastfm.shared_secret = "fedcba9876543210fedcba9876543210"
    config.lastfm.session_key = "demo-session"
    config.lastfm.username = "refrain_demo"
    config.update.last_check_ts = now - 3600

    changelog = (REPO / "CHANGELOG.md").read_text(encoding="utf-8")
    section = re.search(
        rf"## \[(?:{re.escape(version)}|Unreleased)\][^\n]*\n(.*?)\n## \[", changelog, re.S
    )
    release = updater.ReleaseInfo(
        tag=f"v{version}",
        version=version,
        name=f"Refrain v{version}",
        body=section.group(1).strip() if section else "",
        html_url="https://github.com/Rockykln/refrain/releases",
    )

    settings = sw.SettingsWindow(config)
    settings.set_latest_release(release)
    for i, name in enumerate(["general", "sources", "lastfm", "history", "updates", "advanced"]):
        settings.tabs.setCurrentIndex(i)
        shot(settings, f"settings-{name}")

    shot(LegalDialog(), "legal")

    history = HistoryWindow(size=(600, 660))
    history.set_snapshot(
        HistorySnapshot(entries=tuple(entries), now_playing=True, playing=True, limit=30)
    )
    shot(history, "history")

    class Bridge(QObject):
        log_record = Signal(str, int)

    bridge = Bridge()
    log = LogWindow(bridge)
    log.resize(720, 480)
    log.show()
    settle(200)
    stamp = time.strftime("%Y-%m-%d %H:%M")
    for n, line in enumerate(LOG):
        bridge.log_record.emit(f"{stamp}:{n * 3 + 5:02d} [INFO] {line.format(version=version)}", 20)
    shot(log, "live-log")

    welcome = WelcomeDialog()
    welcome.show()
    settle(1500)
    welcome._on_diag_finished(
        True,
        "Found Discord IPC at /run/user/1000/discord-ipc-0",
        True,
        "iTunes Search API reachable.",
    )
    shot(welcome, "welcome")

    major, minor, patch = (int(n) for n in version.split(".")[:3])
    ud.__version__ = f"{major}.{minor}.{max(patch - 1, 0)}"
    ud.detect_install_type = lambda: "pipx"
    update = ud.UpdateDialog(release)
    update.resize(640, 480)
    shot(update, "update-dialog")

    shot_notification(app, settle, suffix)

    tray = TrayIcon()
    tray.set_track(
        TrackInfo(
            source="mpris",
            title="Glass Tides",
            artist="Neon Harbor",
            album="Low Light",
            duration_ms=214_000,
            position_ms=41_000,
            status=PlaybackStatus.PLAYING,
            player="Chromium",
        )
    )
    tray.set_status(PlaybackStatus.PLAYING)
    tray.set_progress(41_000, 214_000)
    tray.set_discord_connected(True)
    tray.set_startup_check(CheckResult(state=OK, detail="refrain_demo"), CheckResult(state=OK))
    menu = tray._tray.contextMenu()
    menu.popup(QPoint(200, 100))
    settle()
    menu.grab().save(str(HERE / f"tray-menu{suffix}.png"))
    print(f"tray-menu{suffix}.png")
    app.quit()


def shot_notification(app, settle, suffix: str) -> None:
    """Plasma draws the real one, so it is rebuilt here in the same layout."""
    from PySide6.QtCore import QRectF, QSize, Qt
    from PySide6.QtGui import QIcon, QImage, QPainter, QPainterPath, QPalette, QPixmap
    from PySide6.QtWidgets import QFrame, QHBoxLayout, QLabel, QVBoxLayout, QWidget

    box = QWidget()
    box.setFixedWidth(360)
    outer = QVBoxLayout(box)
    outer.setContentsMargins(12, 8, 8, 12)
    outer.setSpacing(6)

    head = QHBoxLayout()
    icon = QLabel()
    icon.setPixmap(QIcon(str(REPO / "src/refrain/assets/icons/refrain.svg")).pixmap(QSize(16, 16)))
    head.addWidget(icon)
    name = QLabel("Refrain")
    head.addWidget(name, 1)
    close = QLabel()
    close.setPixmap(QIcon.fromTheme("window-close").pixmap(QSize(16, 16)))
    head.addWidget(close)
    outer.addLayout(head)

    line = QFrame()
    line.setFixedHeight(2)
    line.setAutoFillBackground(True)
    pal = line.palette()
    pal.setColor(QPalette.ColorRole.Window, box.palette().color(QPalette.ColorRole.Highlight))
    line.setPalette(pal)
    track = QHBoxLayout()
    track.setSpacing(0)
    track.addWidget(line, 2)
    track.addStretch(1)
    outer.addLayout(track)

    body = QHBoxLayout()
    text = QVBoxLayout()
    title = QLabel("Glass Tides")
    font = title.font()
    font.setPointSizeF(font.pointSizeF() * 1.15)
    font.setBold(True)
    title.setFont(font)
    text.addWidget(title)
    text.addWidget(QLabel("Neon Harbor — Low Light"))
    text.addStretch(1)
    body.addLayout(text, 1)
    art = QLabel()
    art.setPixmap(
        QPixmap(str(HERE / "demo-cover.png")).scaled(
            64, 64, Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation
        )
    )
    body.addWidget(art)
    outer.addLayout(body)

    box.show()
    settle()
    grab = box.grab().toImage()
    out = QImage(grab.size(), QImage.Format.Format_ARGB32_Premultiplied)
    out.setDevicePixelRatio(grab.devicePixelRatio())
    out.fill(Qt.GlobalColor.transparent)
    p = QPainter(out)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    path = QPainterPath()
    size = grab.deviceIndependentSize()
    path.addRoundedRect(QRectF(0, 0, size.width(), size.height()), 8, 8)
    p.setClipPath(path)
    p.drawImage(0, 0, grab)
    p.end()
    out.save(str(HERE / f"notification{suffix}.png"))
    box.hide()
    print(f"notification{suffix}.png")


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "--render":
        render(sys.argv[2], sys.argv[3])
    else:
        sys.exit(main())
