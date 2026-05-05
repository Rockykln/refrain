"""Refrain settings window — opens on launch, hides on Apply."""

from __future__ import annotations

import logging

from PySide6.QtCore import QSize, Qt, QUrl, Signal
from PySide6.QtGui import QDesktopServices, QIcon
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QSpinBox,
    QTabWidget,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from refrain import __version__
from refrain.config import Config
from refrain.paths import assets_dir
from refrain.sources.bluetooth import BluetoothSource

GITHUB_URL = "https://github.com/Rockykln/refrain"

log = logging.getLogger(__name__)


class SettingsWindow(QDialog):
    """Tabbed settings dialog. Emits `applied(Config)` when the user hits Apply."""

    applied = Signal(object)
    checkUpdatesRequested = Signal()
    showLogRequested = Signal()

    def __init__(self, config: Config, parent: QWidget | None = None):
        super().__init__(parent)
        self.setWindowTitle("Refrain — Settings")
        self.setMinimumSize(560, 480)
        self._config = config

        self.tabs = QTabWidget()
        self.tabs.addTab(self._build_general_tab(), "General")
        self.tabs.addTab(self._build_sources_tab(), "Sources")
        self.tabs.addTab(self._build_updates_tab(), "Updates")
        self.tabs.addTab(self._build_advanced_tab(), "Advanced")

        self.cancel_btn = QPushButton("Cancel")
        self.apply_btn = QPushButton("Apply")
        self.apply_btn.setDefault(True)
        self.cancel_btn.clicked.connect(self.reject)
        self.apply_btn.clicked.connect(self._on_apply_clicked)

        button_row = QHBoxLayout()
        button_row.addStretch()
        button_row.addWidget(self.cancel_btn)
        button_row.addWidget(self.apply_btn)

        version_label = QLabel(f"Refrain v{__version__}")
        version_label.setStyleSheet("color: gray;")

        github_btn = QToolButton()
        github_btn.setIcon(QIcon(str(assets_dir() / "icons" / "github-mark.svg")))
        github_btn.setIconSize(QSize(16, 16))
        github_btn.setAutoRaise(True)
        github_btn.setCursor(Qt.PointingHandCursor)
        github_btn.setToolTip("View Refrain on GitHub")
        github_btn.clicked.connect(lambda: QDesktopServices.openUrl(QUrl(GITHUB_URL)))

        version_row = QHBoxLayout()
        version_row.setContentsMargins(0, 0, 0, 0)
        version_row.addStretch()
        version_row.addWidget(version_label)
        version_row.addWidget(github_btn)

        layout = QVBoxLayout(self)
        layout.addWidget(self.tabs)
        layout.addLayout(button_row)
        layout.addLayout(version_row)

        self._load_into_form()

    # ------------------------------------------------------------------ tabs

    def _build_general_tab(self) -> QWidget:
        w = QWidget()
        f = QFormLayout(w)
        self.client_id_input = QLineEdit()
        self.client_id_input.setPlaceholderText("Discord Application Client ID")
        f.addRow("Discord Client ID:", self.client_id_input)

        self.privacy_combo = QComboBox()
        self.privacy_combo.addItem("Full — title, artist, album, cover", "full")
        self.privacy_combo.addItem("Minimal — only 'Listening to music'", "minimal")
        self.privacy_combo.addItem("Off — disable Discord status entirely", "off")
        f.addRow("Privacy:", self.privacy_combo)

        self.autostart_box = QCheckBox("Start Refrain automatically on login")
        f.addRow(self.autostart_box)

        self.notifications_box = QCheckBox("Show desktop notifications on track change")
        f.addRow(self.notifications_box)

        self.cover_art_box = QCheckBox("Fetch album cover art from iTunes")
        f.addRow(self.cover_art_box)

        self.buttons_box = QCheckBox("Show 'Listen on Apple Music' button in Discord")
        f.addRow(self.buttons_box)
        return w

    def _build_sources_tab(self) -> QWidget:
        w = QWidget()
        f = QFormLayout(w)

        self.mpris_box = QCheckBox("Apple Music Web (browser)")
        f.addRow(self.mpris_box)

        self.bluetooth_box = QCheckBox("Bluetooth (AVRCP)")
        f.addRow(self.bluetooth_box)

        self.bluetooth_device = QComboBox()
        self.bluetooth_device.setEditable(True)

        refresh_row = QHBoxLayout()
        refresh_row.addWidget(self.bluetooth_device, 1)
        refresh_btn = QPushButton("Refresh")
        refresh_btn.clicked.connect(self._populate_bluetooth_devices)
        refresh_row.addWidget(refresh_btn)
        f.addRow("Bluetooth Device:", refresh_row)

        self._populate_bluetooth_devices()

        self.browser_hints_input = QLineEdit()
        self.browser_hints_input.setPlaceholderText(
            "firefox,zen,chromium,chrome,brave,edge,vivaldi,opera,…"
        )
        f.addRow("Browser hints:", self.browser_hints_input)

        hint = QLabel(
            "<i>Comma-separated substrings to identify browsers playing Apple Music. "
            "Edit only if your browser isn't detected.</i>"
        )
        hint.setWordWrap(True)
        f.addRow(hint)

        return w

    def _populate_bluetooth_devices(self) -> None:
        previous = self.bluetooth_device.currentData() if self.bluetooth_device.count() else None
        self.bluetooth_device.clear()
        self.bluetooth_device.addItem("(auto-detect)", userData="")
        for d in BluetoothSource.list_paired_devices():
            label = f"{d.get('name') or '?'} — {d.get('address', '')}"
            if d.get("connected"):
                label = f"● {label}"
            self.bluetooth_device.addItem(label, userData=d.get("address", ""))
        if previous:
            for i in range(self.bluetooth_device.count()):
                if self.bluetooth_device.itemData(i) == previous:
                    self.bluetooth_device.setCurrentIndex(i)
                    return

    def _build_updates_tab(self) -> QWidget:
        from datetime import datetime

        w = QWidget()
        f = QFormLayout(w)

        self.auto_check_box = QCheckBox(
            "Automatically check for updates on startup (max once per day)"
        )
        f.addRow(self.auto_check_box)

        self.last_check_label = QLabel("—")
        f.addRow("Last checked:", self.last_check_label)

        self._last_check_dt_format = lambda ts: (
            "never" if not ts else datetime.fromtimestamp(ts).strftime("%Y-%m-%d %H:%M:%S")
        )

        check_btn = QPushButton("Check for updates now")
        check_btn.clicked.connect(self.checkUpdatesRequested.emit)
        f.addRow(check_btn)

        hint = QLabel(
            "<i>Refrain queries the GitHub Releases API. Update behavior depends "
            "on how Refrain was installed (AppImage / pip / Flatpak / AUR).</i>"
        )
        hint.setWordWrap(True)
        f.addRow(hint)

        return w

    def _build_advanced_tab(self) -> QWidget:
        w = QWidget()
        f = QFormLayout(w)
        self.poll_spin = QSpinBox()
        self.poll_spin.setRange(250, 10000)
        self.poll_spin.setSingleStep(250)
        self.poll_spin.setSuffix(" ms")
        f.addRow("Poll interval:", self.poll_spin)

        self.notify_delay_spin = QSpinBox()
        self.notify_delay_spin.setRange(0, 10000)
        self.notify_delay_spin.setSingleStep(250)
        self.notify_delay_spin.setSuffix(" ms")
        f.addRow("Notification delay:", self.notify_delay_spin)

        self.cover_cache_spin = QSpinBox()
        self.cover_cache_spin.setRange(10, 5000)
        self.cover_cache_spin.setSingleStep(50)
        self.cover_cache_spin.setSuffix(" covers")
        f.addRow("Cover cache size:", self.cover_cache_spin)

        self.log_level_combo = QComboBox()
        for lvl in ("DEBUG", "INFO", "WARNING", "ERROR"):
            self.log_level_combo.addItem(lvl, lvl)
        f.addRow("Log level:", self.log_level_combo)

        log_btn = QPushButton("Open live-log window")
        log_btn.clicked.connect(self.showLogRequested.emit)
        f.addRow(log_btn)

        return w

    # ----------------------------------------------------------------- form

    def _load_into_form(self) -> None:
        c = self._config
        self.client_id_input.setText(c.discord.client_id)
        self.autostart_box.setChecked(c.behavior.autostart)
        self.notifications_box.setChecked(c.behavior.notifications)
        self.cover_art_box.setChecked(c.behavior.cover_art)
        self.buttons_box.setChecked(c.behavior.show_buttons)

        self.auto_check_box.setChecked(c.update.auto_check)
        self.last_check_label.setText(self._last_check_dt_format(c.update.last_check_ts))

        self.mpris_box.setChecked(c.sources.mpris_enabled)
        self.bluetooth_box.setChecked(c.sources.bluetooth_enabled)
        self.browser_hints_input.setText(c.sources.browser_hints)

        if c.sources.bluetooth_device:
            matched = False
            for i in range(self.bluetooth_device.count()):
                if self.bluetooth_device.itemData(i) == c.sources.bluetooth_device:
                    self.bluetooth_device.setCurrentIndex(i)
                    matched = True
                    break
            if not matched:
                self.bluetooth_device.setEditText(c.sources.bluetooth_device)
        else:
            self.bluetooth_device.setCurrentIndex(0)

        for i in range(self.privacy_combo.count()):
            if self.privacy_combo.itemData(i) == c.privacy.mode:
                self.privacy_combo.setCurrentIndex(i)
                break

        self.poll_spin.setValue(c.advanced.poll_interval_ms)
        self.notify_delay_spin.setValue(c.behavior.notify_delay_ms)
        self.cover_cache_spin.setValue(c.advanced.cover_cache_size)
        for i in range(self.log_level_combo.count()):
            if self.log_level_combo.itemData(i) == c.advanced.log_level:
                self.log_level_combo.setCurrentIndex(i)
                break

    def _on_apply_clicked(self) -> None:
        c = self._config
        c.discord.client_id = self.client_id_input.text().strip() or c.discord.client_id
        c.behavior.autostart = self.autostart_box.isChecked()
        c.behavior.notifications = self.notifications_box.isChecked()
        c.behavior.cover_art = self.cover_art_box.isChecked()
        c.behavior.show_buttons = self.buttons_box.isChecked()
        c.behavior.notify_delay_ms = self.notify_delay_spin.value()

        c.update.auto_check = self.auto_check_box.isChecked()

        c.sources.mpris_enabled = self.mpris_box.isChecked()
        c.sources.bluetooth_enabled = self.bluetooth_box.isChecked()
        hints_text = self.browser_hints_input.text().strip()
        c.sources.browser_hints = hints_text if hints_text else c.sources.browser_hints

        bt_data = self.bluetooth_device.currentData()
        if bt_data is None:
            text = self.bluetooth_device.currentText().strip()
            bt_data = "" if text in ("", "(auto-detect)") else text
        c.sources.bluetooth_device = bt_data

        c.privacy.mode = self.privacy_combo.currentData() or "full"
        c.advanced.poll_interval_ms = self.poll_spin.value()
        c.advanced.cover_cache_size = self.cover_cache_spin.value()
        c.advanced.log_level = self.log_level_combo.currentData() or "INFO"

        c.save()
        self.applied.emit(c)
        self.hide()
