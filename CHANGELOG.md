# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

## [0.2.0] - 2026-05-06

First minor-version bump. Five planned v0.2 features land together
along with a clutch of UX fixes that surfaced during demo recording.

### Added

- **Theme-aware tray icons** — Refrain now reads
  `QStyleHints.colorScheme()` (Qt 6.5+) and switches between bright
  glyphs on dark trays and dark glyphs on light trays automatically,
  including live re-render when the system theme flips at runtime.
  Three new SVG variants under `assets/icons/tray-*-dark.svg`.
- **Idle detection** — when a track has been reported as PLAYING for
  longer than its own duration plus a grace window (default 30 s,
  config field `advanced.idle_grace_s`), Refrain treats the source as
  dangling (typical: closed browser tab whose MPRIS handle never
  released) and clears Discord + tray. Disabled by setting the grace
  to 0. 8 unit tests in `test_daemon_idle.py`.
- **D-Bus PropertiesChanged listeners** for MPRIS and BlueZ via
  `QDBusConnection`. Track switches, pauses, and seeks register
  instantly instead of within the next 1 Hz poll tick. Polling stays
  on as the fallback for source discovery. Lives in
  `refrain.sources.dbus_watcher`.
- **First-run wizard** — single-page welcome dialog that shows the
  tray-icon orientation, runs a live Discord IPC probe + a live
  iTunes Search probe (off the GUI thread) so the user knows whether
  both are reachable, and prompts for the Discord Application ID with
  a direct link to the Developer Portal. Triggers when
  `behavior.first_run_complete` is False AND no client_id is set;
  skipping is fine — Settings still works.
- **Localization infrastructure**, `i18n/refrain_<lang>.ts`. Source
  strings under tray, settings window, log window, update dialog,
  and welcome dialog wrapped in `tr()`. German fully translated
  (62/62 strings). Stub `.ts` files for French, Spanish, Italian,
  Portuguese, Dutch, Polish, Japanese, and Simplified Chinese — all
  ready for community translation PRs. Pre-compiled `.qm` files ship
  in the wheel; `make i18n` regenerates them.

### Changed

- **Discord RPC layout reworked** to one-piece-of-metadata-per-line:
  - line 1 (`details`) — track title
  - line 2 (`state`) — artist (was "artist • album")
  - line 3 (`large_text`) — album, with artist/title prefixes
    stripped so it never echoes line 2
- **Window titles** dropped their manual "Refrain — " prefixes —
  Qt's `applicationDisplayName` already auto-appends "Refrain", so
  the Settings header no longer reads "Refrain — Settings — Refrain".
- **Bandit suppression** for B606 (`os.execvp` in app.py — intended
  Restart implementation, no shell wanted).

### Fixed

- **Flatpak no longer fails with "No system tray available"** — the
  manifest now declares `--talk-name=org.kde.StatusNotifierWatcher`
  and the canonical AppMenu / AppIndicator names, so Qt's tray
  detection can reach KDE / GNOME's tray watcher from inside the
  sandbox.
- **`updater._detect_install_type` try/except/pass** replaced with
  `contextlib.suppress` (B110 cleanup, consistent with the v0.1.2
  sweep).

### Notes

- Flathub submission has been deferred indefinitely after a friction
  with maintainer review; the manifest stays under
  `packaging/flatpak/` for users who want to self-build, and a
  re-submission attempt is listed in the roadmap under "Maybe v0.3+".

## [0.1.5] - 2026-05-06

Three real bugs surfaced during the Flathub demo recording. All
fixed, all covered by new regression tests.

### Fixed

- **Skip Next / Previous now works on Apple Music in Chromium.**
  The browser exposes two MPRIS players: KDE's
  `plasma-browser-integration` (rich metadata, but `CanGoNext=False`
  / `CanGoPrevious=False`) and the browser's own MPRIS (capable of
  skip but with a tab-title-only `xesam:title` and no `xesam:url`).
  v0.1.4 picked the plasma player for both metadata AND control,
  which made skip silently no-op. v0.1.5 keeps plasma for metadata
  but tracks the control-capable browser MPRIS as a fallback target,
  so skip dispatches onto whichever player can actually do the
  action. 5 dedicated unit tests in `test_mpris_dispatch.py`.
- **Discord status now renders as "Listening to &lt;song&gt;"
  instead of "Playing Refrain".** v0.1.4 sent activity payloads
  without an `activity_type`, defaulting to `PLAYING`, which made
  the status look wrong for a music app — easy to mistake for
  "Discord status missing" entirely. Now defaults to
  `ActivityType.LISTENING`. 2 unit tests in
  `test_discord_listening.py`.
- **Tray-icon tooltip mirrors live progress.** The progress line
  in the tray menu visibly froze mid-song because KDE's DBusMenu
  doesn't propagate action-text changes while the menu is open.
  The tooltip DOES refresh in real time, so hovering the tray icon
  now gives users a working "0:42 / 3:45 (–3:03)" ticker. The menu
  line stays as a "near-current" indicator that's accurate at the
  moment the menu is opened.

## [0.1.4] - 2026-05-06

A same-day follow-up to v0.1.3 that fixes the two issues v0.1.3 shipped
with (broken AppImage filename + broken icon bundling), plus the
distribution-side bookkeeping that v0.1.3 enabled but didn't complete.

### Fixed

- **AppImage filename** — v0.1.3 shipped as
  `Refrain-.version.-x86_64.AppImage` because appimage-builder rendered
  the `{version}` placeholder as the literal string `.version.`.
  Dropped the custom `file_name:` override; appimage-builder's default
  filename template uses `app_info.version` directly and resolves
  correctly.
- **AppImage icon bundling** — v0.1.3's release CI crashed on
  `IconBundler.Error: Unable to find any app icon named: refrain`
  because `files.include:` copies into AppDir but to a path the
  icon_bundler never inspects. The icon now ships at
  `AppDir/usr/share/icons/hicolor/scalable/apps/refrain.svg`, the
  canonical XDG icon-theme path.

### Added

- **AUR `refrain` and `refrain-git` published.** Both PKGBUILDs are
  live on aur.archlinux.org under maintainer `Rockykln`. The stable
  `refrain` package pins the v0.1.3 source tarball's SHA-256;
  `refrain-git` auto-bumps via `pkgver()`.
- **Flatpak manifest validated locally.** Builds + runs cleanly with
  `flatpak-builder` against KDE Platform 6.10 + the
  `io.qt.PySide.BaseApp//6.10` BaseApp. Includes an inline patchelf
  0.18.0 module (KDE SDK doesn't ship it; meson-python needs it for
  dbus-python's build) and a `python-deps.json` generated via
  `flatpak-pip-generator` so Flathub's offline build can resolve every
  transitive dep.

### Changed

- **README install table** now leads with `pip install refrain` and
  marks AUR + AppImage as live (no longer "until first tag" caveats).
- **`packaging/README.md`** rewritten with per-channel runbooks: PyPI
  Trusted Publisher setup gotcha (`environment: pypi`, not `phpi`),
  step-by-step AUR push, Flathub manifest refresh recipe.
- **Bug-report issue template** version placeholder bumped from
  `0.1.0` to `0.1.4`.
- **AppImageBuilder.yml** drops the broken `file_name:` line and
  documents why in a comment.

## [0.1.3] - 2026-05-06

The "you should not need a clone to install Refrain" pass. Wires up
PyPI publishing in CI and tightens autostart for non-system installs.

### Added

- **PyPI publishing in the release workflow** via PyPA Trusted
  Publishers (OIDC, no token to manage). A `v*.*.*` tag now uploads
  the wheel + sdist to PyPI alongside the GitHub release, so
  `pip install refrain` becomes the canonical install path. Requires
  a one-time pending-publisher setup on pypi.org.
- **AppImage version auto-sync.** The release workflow now rewrites
  `version:` in `packaging/appimage/AppImageBuilder.yml` from the git
  tag, so the AppImage filename always matches the release.

### Fixed

- **Start on Login now works for venv / pipx / pip --user installs.**
  The autostart `.desktop` file used a bare `Exec=refrain --silent`,
  which fails when `refrain` is not on the desktop session's `$PATH`
  (the common case for any non-system install). Refrain now resolves
  the launch command in this order: `$APPIMAGE` → `shutil.which`
  → `sys.argv[0]` → `<sys.executable> -m refrain`, and writes the
  absolute path into `Exec=` with proper quoting.
- **Noisy `qt.qpa.services: Failed to register with host portal`
  warning is suppressed.** Installed a `qInstallMessageHandler` that
  routes Qt log messages through Python's `logging` (under the `qt`
  logger) and drops this one specific harmless line so the live-log
  window isn't confusing on first open.

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
