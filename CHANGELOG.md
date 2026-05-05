# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

## [0.1.2] - 2026-05-05

A second polish pass after v0.1.1 — focused on real production-readiness.
No breaking changes.

### Added

- **Discord connection indicator** in the tray menu — `●  Discord:
  connected` / `○  Discord: not connected` so you can tell at a glance
  whether the RPC channel is live.
- **Reset all settings to defaults** button in *Settings → Advanced*.
  Preserves your Discord client_id and resets everything else to its
  shipped default. Click *Apply* afterwards to persist.
- **Open log folder** button in *Settings → Advanced* — opens
  `$XDG_STATE_HOME/refrain/` in the file manager for easy log access.
- **`prepare_release_notes()`** in `refrain.updater` — preprocesses any
  Markdown body so bare GitHub compare URLs (containing `...`) stay
  one indivisible link in the update dialog. 7 unit tests.

### Fixed

- **Empty Discord `client_id` no longer log-spams.** When the field is
  empty (the default for new installs), `DiscordRPC` short-circuits the
  reconnect loop and logs the disabled state once instead of retrying
  on every poll with exponential backoff.
- **Notification respects `cover_art = off`.** Previously the
  refrain.svg fallback was emitted regardless of the user's toggle —
  now the big-image hint is only added when cover art is enabled.
- **AppImage restart picks the right binary.** Inside an AppImage
  `$APPIMAGE` is the original mount path, which is what `os.execvp`
  needs; falls back to `sys.argv[0]` for venv / system installs.
- **AppImage build recipe** — `pip install` step failed in CI because
  Python 3.12's `ensurepip` no longer creates a `bin/pip` launcher.
  Switched to `python3 -m pip` everywhere.

### Changed

- **Update-dialog release notes** are now passed through
  `prepare_release_notes()` so bare URLs become Markdown autolinks
  before Qt's parser sees them — fixes the "clickable link ends at
  the `...`" problem on GitHub-generated release bodies.
- **`is_newer()` variable rename** (`l` → `loc`) so the linter no
  longer needs an `E741` suppression.

### Removed (dead code)

- One more `try/except/pass` block in `updater._apply_appimage` →
  `contextlib.suppress(Exception)`.
- One more `try/except/pass` block in `discord_rpc.close` → same.

## [0.1.1] - 2026-05-05

A reliability + polish pass over the initial release. No breaking changes.

### Added

- **Restart Refrain** entry in the tray menu and **Restart Refrain**
  button in *Settings → Advanced*. Both flag a clean shutdown
  (daemon stop, Discord status cleared, D-Bus name released) and then
  `os.execvp` the same binary, so the new process always claims the
  single-instance lock cleanly.
- **All-in-GUI settings.** Three formerly hardcoded values now have
  fields in the settings window:
  - *Browser hints* (Sources tab) — comma-separated MPRIS player-name
    substrings, formerly hardcoded in `mpris.py`.
  - *Notification delay* (Advanced tab) — initial wait before firing
    `notify-send`, in milliseconds.
  - *Cover cache size* (Advanced tab) — pruning cap for the on-disk
    cover-art cache.
- **AppImage** built by the release workflow and attached to GitHub
  releases, so end users can grab one binary instead of cloning the
  whole repo.
- **Contact channels** — `contact@rockykln.com` for general questions /
  feedback / Code-of-Conduct reports; `report@rockykln.com` for security
  vulnerabilities (alongside GitHub Private Advisories).
- **First-time setup** section in the README that walks new users
  through registering their own Discord application.

### Changed

- **License** changed from MIT to the **Refrain License (Use-Only)**:
  source-available, anyone may use and redistribute the unmodified
  Software, no modifications or derivative works permitted.
- **Notification cover image** now reaches `notify-send` via the
  spec-compliant `--hint=string:image-path:<path>` (KDE Plasma 6
  silently ignored `-i <abs path>`, so covers never showed). Added the
  `x-kde-iconName` hint as belt-and-braces.
- **Notification timing** — when the initial delay fires but the cover
  hasn't finished downloading yet, Refrain now polls every 250 ms for up
  to 2 s before notifying. Worst case: notification arrives ~3.5 s after
  the track change, but always with the album cover.
- **Notification fallback image** — when iTunes has no match, Refrain
  embeds the bundled `refrain.svg` as the notification image so the
  visual style stays consistent across all tracks.
- **Discord ID** — the default `discord.client_id` is now empty; users
  register their own Discord application and paste the ID in
  *Settings → General*. The previously hardcoded ID is gone from the
  source, README, and screenshots.
- **GitHub Releases API button** in the update dialog now uses
  `--hint=string:image-path:` for the new-release dialog cover image —
  see "Notification cover image" above (same root cause).
- **`compute_rpc_start_ts`** extracted into `refrain.timing` so the
  pause/resume + seek correctness is unit-testable without Qt or D-Bus.

### Fixed

- **Discord elapsed timer** drifted off the actual track position after
  pause/resume or user seek. The new drift detector resyncs `start`
  whenever the wall-clock view diverges from the source's reported
  position by more than 3 s. 17 dedicated tests in `test_timing.py`.
- **`pip-audit` workflow** failed to install `dbus-python` for CVE
  scanning because libdbus headers were missing on the runner.
- **Hosted CI test** (`test_detect_install_type_pip_for_venv`) flipped
  to "system" on `setup-python` runners; the assertion now accepts the
  hosted-toolcache `system` bucket too.
- **CodeQL false positives** — the `_qt_bridge` lazy-init memo and the
  `chmod 0o755` on the in-place AppImage replacement no longer surface
  as alerts.

### Removed

- `cover_art.lookup_cover_url` backwards-compatibility facade (only
  callers were tests; rewritten to test `lookup_track_info` directly).
- `TrackLookup.is_empty` property (unused).
- `DiscordRPC.connected` property (unused).
- Two `try: ... except Exception: pass` blocks in `daemon.cleanup`
  replaced with `contextlib.suppress(Exception)`.

### Security

- `report@rockykln.com` mailbox set up specifically for vulnerability
  reports; security policy in `SECURITY.md` updated.
- `LICENSE` now bundles a third-party-deps notice (PySide6 / pypresence
  / dbus-python).

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

[Unreleased]: https://github.com/Rockykln/refrain/compare/v0.1.2...HEAD
[0.1.2]: https://github.com/Rockykln/refrain/compare/v0.1.1...v0.1.2
[0.1.1]: https://github.com/Rockykln/refrain/compare/v0.1.0...v0.1.1
[0.1.0]: https://github.com/Rockykln/refrain/releases/tag/v0.1.0
