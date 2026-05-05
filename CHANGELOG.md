# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added

- **All-in-GUI settings.** New fields exposed in the settings window:
  - *Browser hints* (Sources tab) — comma-separated substrings used to
    identify MPRIS browser players, formerly hardcoded in `mpris.py`.
  - *Notification delay* (Advanced tab) — controls how long Refrain
    waits after a track change before firing the notification, so the
    cover-art download lands on disk first.
  - *Cover cache size* (Advanced tab) — pruning cap for the on-disk
    cover-art cache.
- **Single-file AppImage** built by the release workflow and attached to
  every GitHub release, so end users can grab one binary instead of the
  whole repo.
- **Contact channels** — `contact@rockykln.com` for general questions /
  feedback / Code-of-Conduct reports; `report@rockykln.com` for security
  vulnerabilities (alongside GitHub Private Advisories).

### Changed

- **License** changed from MIT to the **Refrain License (Use-Only)**:
  source-available, anyone may use and redistribute unmodified, no
  modifications or derivative works permitted.

## [0.1.0] - 2026-05-05

Initial public release. Refrain replaces the original `played_music.py` script
with a proper, installable Linux app.

### Added

- **GUI settings window** (PySide6 / Qt 6) with four tabs: General, Sources,
  Privacy, Advanced. Hitting *Apply* saves the config and hides the window;
  the daemon and tray keep running.
- **System tray icon** (`QSystemTrayIcon`) — always visible, follows playback
  state (playing / paused / stopped). Tray menu shows the current track and
  hosts player controls.
- **Player controls** — Play / Pause, Next, Previous routed to the active
  source via D-Bus (MPRIS `Player` interface for browsers, BlueZ
  `MediaPlayer1` for AVRCP).
- **MPRIS source** — picks up Apple Music Web in Firefox, Zen, Chromium,
  Chrome, Brave, Edge, Vivaldi, Opera. Highest-scoring `music.apple.com`
  player wins.
- **Bluetooth (AVRCP) source** — auto-detects the active player via
  `org.bluez.MediaPlayer1`. Settings window can pin to a specific paired
  device.
- **Discord Rich Presence** with optional cover-art lookup via the iTunes
  Search API (cached on disk, including negative results) and a "Listen on
  Apple Music" button when the source is the browser.
- **Privacy modes** — Full / Minimal / Off.
- **Autostart toggle** — writes `~/.config/autostart/refrain.desktop`.
- **Single-instance lock** via D-Bus name acquisition (`io.github.Rockykln.Refrain`).
- **Exponential backoff** when the Discord IPC socket is unreachable.
- **Asynchronous cover-art prefetch** — `CoverFetcher` runs the iTunes Search
  lookup in a background thread so the daemon's polling tick is never blocked
  by network I/O.
- **Stable Discord elapsed timer** — the RPC `start` timestamp is recomputed
  only when the playing track actually changes, eliminating per-tick jitter
  in Discord's progress bar.
- **Distribution packaging** — AUR (`refrain` + `refrain-git`),
  Flatpak manifest (`io.github.Rockykln.Refrain`), and AppImage recipe.
- **`--install-desktop` / `--uninstall-desktop` flags** — when installed via
  pip, copies the `.desktop` file and icon to `~/.local/share/` so Refrain
  appears in the application menu.
- **XDG-compliant paths** — config in `$XDG_CONFIG_HOME/refrain/`, logs in
  `$XDG_STATE_HOME/refrain/`, cover-art cache in `$XDG_CACHE_HOME/refrain/`.
- **Rotating log file** (`refrain.log`, 1 MiB × 3 backups).
- Public-repo scaffolding: GitHub Actions (tests, lint, CodeQL, bandit,
  pip-audit, trufflehog, release), Dependabot, issue + PR templates,
  `CONTRIBUTING.md`, `CODE_OF_CONDUCT.md`, `SECURITY.md`.

[Unreleased]: https://github.com/Rockykln/refrain/compare/v0.1.0...HEAD
[0.1.0]: https://github.com/Rockykln/refrain/releases/tag/v0.1.0
