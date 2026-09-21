"""Live-log window: shows every log record as it happens, fed by ``attach_qt_log_bridge()``."""

from __future__ import annotations

import html
import logging
from collections import deque
from pathlib import Path

from PySide6.QtCore import QEvent, Qt, QTimer
from PySide6.QtGui import QColor, QFont, QFontDatabase, QGuiApplication, QIcon, QPalette
from PySide6.QtWidgets import (
    QApplication,
    QCheckBox,
    QComboBox,
    QDialog,
    QFileDialog,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from refrain import dev_metrics
from refrain.paths import assets_dir
from refrain.ui.cursors import apply_interactive_cursors

_MAX_LINES = 5000
# (on a light background, on a dark one)
_ALERT_COLORS = {
    logging.WARNING: ("#8a6400", "#e8b800"),
    logging.ERROR: ("#b3261e", "#ff7777"),
    logging.CRITICAL: ("#8c0000", "#ff6b6b"),
}


def level_colors(palette: QPalette) -> dict[int, str]:
    active = QPalette.ColorGroup.Active
    text = palette.color(active, QPalette.ColorRole.Text)
    base = palette.color(active, QPalette.ColorRole.Base)
    k = 0.7
    muted = QColor(
        round(text.red() * k + base.red() * (1 - k)),
        round(text.green() * k + base.green() * (1 - k)),
        round(text.blue() * k + base.blue() * (1 - k)),
    )
    dark = base.lightness() < 128
    colors = {level: pair[dark] for level, pair in _ALERT_COLORS.items()}
    colors[logging.DEBUG] = muted.name()
    colors[logging.INFO] = text.name()
    return colors


def _ms(value) -> str:
    if value is None:
        return "—"
    # Sub-millisecond steps are the norm; three digits after the point are noise above 100 ms.
    if value >= 100:
        return f"{value:.0f}"
    return f"{value:.2f}" if value >= 1 else f"{value:.3f}"


def _ordered(data: dict, order: tuple[str, ...]) -> list[str]:
    return [k for k in order if k in data] + sorted(k for k in data if k not in order)


def _duration(seconds: float) -> str:
    minutes, secs = divmod(int(seconds), 60)
    hours, minutes = divmod(minutes, 60)
    if hours:
        return f"{hours} h {minutes} min"
    return f"{minutes} min {secs} s" if minutes else f"{secs} s"


class _Columns(QLabel):
    """Numbers in aligned columns — a table's readability without its furniture."""

    def __init__(self, *headers: str) -> None:
        super().__init__()
        self.headers = headers
        self.rows: list[list[str]] = []
        self.setFont(QFontDatabase.systemFont(QFontDatabase.SystemFont.FixedFont))
        self.setTextFormat(Qt.TextFormat.PlainText)
        self.setAlignment(Qt.AlignmentFlag.AlignTop | Qt.AlignmentFlag.AlignLeft)
        self.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        self.set_rows([])

    def set_rows(self, rows: list[list[str]], empty_message: str | None = None) -> None:
        self.rows = rows
        widths = [max([len(h)] + [len(r[i]) for r in rows]) for i, h in enumerate(self.headers)]

        def line(cells) -> str:
            parts = [
                c.ljust(widths[0]) if i == 0 else c.rjust(widths[i]) for i, c in enumerate(cells)
            ]
            return "  ".join(parts).rstrip()

        body = [line(row) for row in rows] or [empty_message or self.tr("nothing measured yet")]
        text = "\n".join([line(self.headers), *body])
        self.setText(text)
        # Columns are only readable whole; ask the layout for the room they need.
        widest = max(self.fontMetrics().horizontalAdvance(line_) for line_ in text.splitlines())
        self.setMinimumWidth(widest + 8)


class DeveloperPanel(QWidget):
    """Live view of what developer mode measures, refreshed only while it is on screen."""

    REFRESH_MS = 1000

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        # The running config, so the report tells the truth about the keyring.
        self._config = None
        self.stages = self._table(
            self.tr("Stage"),
            self.tr("Last"),
            self.tr("Median"),
            self.tr("p95"),
            self.tr("Max"),
            self.tr("Polls"),
        )
        self.startup = self._table(self.tr("Step"), self.tr("Since start"))
        self.network = self._table(
            self.tr("Service"), self.tr("Calls"), self.tr("Failed"), self.tr("Last"), self.tr("Max")
        )
        self.interactions = self._table(self.tr("Event"), self.tr("Count"))
        self.layout_issues = self._table(self.tr("Problem"), self.tr("Seen"))
        self.memory = QLabel()

        self.report_btn = QPushButton(self.tr("System report…"))
        self.report_btn.setObjectName("systemReport")
        self.report_btn.setToolTip(
            self.tr("What Refrain runs on and how it is set up, to paste into a bug report")
        )
        self.report_btn.clicked.connect(self._show_report)

        self.export_btn = QPushButton(self.tr("Export…"))
        self.export_btn.setObjectName("export")
        self.export_btn.setToolTip(
            self.tr(
                "Measured on this computer only and never sent anywhere. Saved to {path}"
            ).format(path=dev_metrics.metrics_path())
        )
        self.export_btn.clicked.connect(self._export)

        grid = QGridLayout()
        grid.setHorizontalSpacing(12)
        grid.setVerticalSpacing(8)
        # Equal halves cut the longer translated headers off; let each column
        # take the width its own tables need.
        grid.setColumnStretch(0, 0)
        grid.setColumnStretch(1, 0)
        for row, col, title, table in (
            (0, 0, self.tr("Poll stages (ms)"), self.stages),
            (0, 1, self.tr("Startup (ms after process start)"), self.startup),
            (1, 0, self.tr("Network (ms)"), self.network),
            (1, 1, self.tr("Interactions"), self.interactions),
        ):
            group = QGroupBox(title)
            box = QVBoxLayout(group)
            box.setContentsMargins(8, 6, 8, 6)
            box.addWidget(table)
            grid.addWidget(group, row, col, Qt.AlignmentFlag.AlignTop)
        self.layout_group = QGroupBox(self.tr("Text that does not fit"))
        issues_box = QVBoxLayout(self.layout_group)
        issues_box.setContentsMargins(8, 6, 8, 6)
        issues_box.addWidget(self.layout_issues)
        grid.addWidget(self.layout_group, 2, 0, 1, 2, Qt.AlignmentFlag.AlignTop)

        bottom = QHBoxLayout()
        bottom.addWidget(self.memory, 1)
        bottom.addWidget(self.report_btn)
        bottom.addWidget(self.export_btn)

        layout = QVBoxLayout(self)
        layout.setSpacing(8)
        layout.addLayout(grid)
        layout.addStretch(1)
        layout.addLayout(bottom)

        self._timer = QTimer(self)
        self._timer.setInterval(self.REFRESH_MS)
        self._timer.timeout.connect(self.refresh)

    @staticmethod
    def _table(*headers: str) -> _Columns:
        return _Columns(*headers)

    @staticmethod
    def _fill(widget: _Columns, rows: list[list[str]], empty_message: str | None = None) -> None:
        widget.set_rows(rows, empty_message)

    def showEvent(self, event) -> None:
        super().showEvent(event)
        self.refresh()
        self._timer.start()

    def hideEvent(self, event) -> None:
        self._timer.stop()
        super().hideEvent(event)

    def refresh(self) -> None:
        data = dev_metrics.snapshot()
        polls = data.get("polls", {})
        self._fill(
            self.stages,
            [
                [
                    stage,
                    _ms(polls[stage].get("last_ms")),
                    _ms(polls[stage].get("median_ms")),
                    _ms(polls[stage].get("p95_ms")),
                    _ms(polls[stage].get("max_ms")),
                    str(polls[stage].get("n", 0)),
                ]
                for stage in _ordered(polls, dev_metrics.STAGES)
            ],
        )
        startup = data.get("startup_ms", {})
        if data.get("late_start"):
            # Developer mode came on after the app had already started: the
            # early steps never landed, and whatever did would misleadingly
            # look like it took minutes.
            self._fill(
                self.startup,
                [],
                empty_message=self.tr(
                    "developer mode turned on while running — startup times not measured"
                ),
            )
        else:
            self._fill(
                self.startup,
                [
                    [step, _ms(startup[step])]
                    for step in _ordered(startup, dev_metrics.STARTUP_STEPS)
                ],
            )
        network = data.get("network", {})
        self._fill(
            self.network,
            [
                [
                    name,
                    str(entry["count"]),
                    str(entry["failures"]),
                    _ms(entry["last_ms"]),
                    _ms(entry["max_ms"]),
                ]
                for name, entry in sorted(network.items())
            ],
        )
        counters = data.get("interactions", {})
        self._fill(
            self.interactions,
            [[key, str(count)] for key, count in sorted(counters.items(), key=lambda kv: -kv[1])],
        )
        issues = data.get("layout", {})
        self._fill(
            self.layout_issues,
            [[message, str(count)] for message, count in sorted(issues.items())],
        )
        res = data.get("resources", {})
        rss = res.get("rss_kb")
        self.memory.setText(
            self.tr(
                "Running for {uptime} · Memory: {rss} MB · Threads: {threads} · Open files: {files}"
            ).format(
                uptime=_duration(data.get("uptime_s", 0)),
                rss="—" if rss is None else f"{rss / 1024:.1f}",
                threads="—" if res.get("threads") is None else res["threads"],
                files="—" if res.get("open_files") is None else res["open_files"],
            )
        )
        # An empty findings box would suggest the check never ran.
        self.layout_group.setVisible(bool(issues))

    def use_config(self, config) -> None:
        self._config = config

    def _show_report(self) -> None:
        from refrain.config import Config
        from refrain.diagnostics import report

        text = report(self._config or Config.load())
        box = QDialog(self)
        box.setWindowTitle(self.tr("System report"))
        box.resize(640, 460)
        view = QPlainTextEdit(text)
        view.setReadOnly(True)
        view.setFont(QFontDatabase.systemFont(QFontDatabase.SystemFont.FixedFont))
        copy_btn = QPushButton(self.tr("Copy"))
        copy_btn.clicked.connect(lambda: QGuiApplication.clipboard().setText(text))
        close_btn = QPushButton(self.tr("Close"))
        close_btn.clicked.connect(box.accept)
        row = QHBoxLayout()
        row.addStretch(1)
        row.addWidget(copy_btn)
        row.addWidget(close_btn)
        layout = QVBoxLayout(box)
        layout.addWidget(QLabel(self.tr("Nothing here says who you are or what you listen to.")))
        layout.addWidget(view, 1)
        layout.addLayout(row)
        apply_interactive_cursors(box)
        box.exec()

    def _export(self) -> None:
        path, _ = QFileDialog.getSaveFileName(
            self,
            self.tr("Export developer metrics"),
            str(Path.home() / "refrain-dev-metrics.json"),
            self.tr("JSON files (*.json)"),
        )
        if not path:
            dev_metrics.interaction("dialog_cancelled", target="export")
            return
        try:
            dev_metrics.export(Path(path))
        except OSError as e:
            QMessageBox.warning(
                self,
                self.tr("Export failed"),
                self.tr("Could not write {path}:\n\n{error}").format(path=path, error=e),
            )


class LogWindow(QDialog):
    def __init__(self, bridge, parent: QWidget | None = None):
        super().__init__(parent)
        self.setWindowTitle(self.tr("Live log"))
        self.setMinimumSize(720, 480)
        # The Developer tab wants both columns side by side; small screens win.
        screen = self.screen() or QApplication.primaryScreen()
        room = screen.availableGeometry() if screen else None
        self.resize(
            min(980, room.width() - 80) if room else 980,
            min(680, room.height() - 80) if room else 680,
        )
        # Don't override windowFlags here — QDialog defaults are correct.
        # Adding Qt.WindowType.Window breaks visibility on some compositors.
        self.setModal(False)
        self._config = None
        icon_path = assets_dir() / "icons" / "refrain.svg"
        if icon_path.exists():
            self.setWindowIcon(QIcon(str(icon_path)))

        # ---- toolbar -------------------------------------------------------
        bar = QHBoxLayout()

        bar.addWidget(QLabel(self.tr("Level:")))
        self.level_combo = QComboBox()
        # Level names (DEBUG/INFO/…) stay untranslated — they're the
        # logging module's proper-noun constants and match the Advanced
        # tab's log-level combo. Only the "ALL" sentinel is a real word.
        for label, value in (
            (self.tr("ALL"), 0),
            ("DEBUG", logging.DEBUG),
            ("INFO", logging.INFO),
            ("WARNING", logging.WARNING),
            ("ERROR", logging.ERROR),
        ):
            self.level_combo.addItem(label, value)
        self.level_combo.setCurrentIndex(2)  # until the configured level arrives
        self.level_combo.currentIndexChanged.connect(self._redraw)
        bar.addWidget(self.level_combo)

        self.autoscroll_box = QCheckBox(self.tr("Auto-scroll"))
        self.autoscroll_box.setChecked(True)
        bar.addWidget(self.autoscroll_box)

        bar.addStretch()

        copy_btn = QPushButton(self.tr("Copy all"))
        copy_btn.setObjectName("copyAll")
        copy_btn.clicked.connect(self._copy_all)
        bar.addWidget(copy_btn)

        clear_btn = QPushButton(self.tr("Clear"))
        clear_btn.setObjectName("clear")
        clear_btn.clicked.connect(self._clear)
        bar.addWidget(clear_btn)

        close_btn = QPushButton(self.tr("Close"))
        close_btn.setObjectName("close")
        close_btn.clicked.connect(self.hide)
        bar.addWidget(close_btn)

        # ---- view ----------------------------------------------------------
        self.view = QPlainTextEdit()
        self.view.setReadOnly(True)
        font = QFont("monospace")
        font.setStyleHint(QFont.StyleHint.Monospace)
        self.view.setFont(font)
        # Cap memory: the underlying deque drops old blocks once the limit hits.
        self.view.setMaximumBlockCount(_MAX_LINES)
        self._lines: deque[tuple[str, int]] = deque(maxlen=_MAX_LINES)
        self._colors = level_colors(self.view.palette())

        log_page = QWidget()
        page_layout = QVBoxLayout(log_page)
        page_layout.setContentsMargins(0, 0, 0, 0)
        page_layout.addLayout(bar)
        page_layout.addWidget(self.view, 1)

        self.tabs = QTabWidget()
        self.tabs.setDocumentMode(True)
        # Only the developer tab ever joins the log, so a lone tab needs no bar.
        self.tabs.setTabBarAutoHide(True)
        self.tabs.addTab(log_page, self.tr("Log"))
        self.developer_panel: DeveloperPanel | None = None

        layout = QVBoxLayout(self)
        layout.addWidget(self.tabs)

        for message, level in list(getattr(bridge, "backlog", ())):
            self._append(message, level)
        bridge.log_record.connect(self._append)

        # Every clickable child gets the pointing hand, in one place —
        # see refrain.ui.cursors.
        apply_interactive_cursors(self)

    def set_log_level(self, level: int) -> None:
        """Start at the level Refrain itself logs at, as set under Advanced."""
        index = self.level_combo.findData(level)
        if index >= 0:
            self.level_combo.setCurrentIndex(index)

    def use_config(self, config) -> None:
        self._config = config
        if self.developer_panel is not None:
            self.developer_panel.use_config(config)

    def set_developer_mode(self, on: bool) -> None:
        if on and self.developer_panel is None:
            self.developer_panel = DeveloperPanel()
            self.developer_panel.use_config(self._config)
            self.tabs.addTab(self.developer_panel, self.tr("Developer"))
            apply_interactive_cursors(self.developer_panel)
        elif not on and self.developer_panel is not None:
            self.tabs.removeTab(self.tabs.indexOf(self.developer_panel))
            self.developer_panel.deleteLater()
            self.developer_panel = None

    def show_developer_tab(self) -> None:
        if self.developer_panel is not None:
            self.tabs.setCurrentWidget(self.developer_panel)

    # --------------------------------------------------------------- handlers

    def _append(self, msg: str, level: int) -> None:
        # Kept at every level, so a lower filter can show them later.
        self._lines.append((msg, level))
        if level < (self.level_combo.currentData() or 0):
            return
        self._write(msg, level)
        if self.autoscroll_box.isChecked():
            sb = self.view.verticalScrollBar()
            sb.setValue(sb.maximum())

    def _write(self, msg: str, level: int) -> None:
        color = self._colors.get(level, self._colors[logging.INFO])
        weight = "font-weight:bold;" if level >= logging.CRITICAL else ""
        self.view.appendHtml(f'<span style="color:{color};{weight}">{html.escape(msg)}</span>')

    def changeEvent(self, event) -> None:
        super().changeEvent(event)
        if event.type() in (QEvent.Type.PaletteChange, QEvent.Type.StyleChange):
            colors = level_colors(self.view.palette())
            if colors != self._colors:
                self._colors = colors
                self._redraw()

    def _redraw(self) -> None:
        threshold = self.level_combo.currentData() or 0
        self.view.clear()
        for msg, level in self._lines:
            if level >= threshold:
                self._write(msg, level)

    def _clear(self) -> None:
        self._lines.clear()
        self.view.clear()

    def _copy_all(self) -> None:
        QGuiApplication.clipboard().setText(self.view.toPlainText())
