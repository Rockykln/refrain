# Roadmap

What's done, what's next, what's deliberately not in scope.

## Done — v0.1.0

- Settings window + tray icon + background daemon (single Qt event loop)
- MPRIS source (Apple Music in any major Linux browser)
- BlueZ AVRCP source with paired-device picker
- Discord Rich Presence with cover art (iTunes Search) + buttons
- Player controls (Play / Pause / Next / Previous) routed to active source
- Privacy modes: Full / Minimal / Off
- Track progress display in tray menu (`0:42 / 3:45 (–3:03)`)
- Discord elapsed timer bounded by track length (shows `1:23 / 3:45`)
- Single-instance lock via D-Bus name acquisition
- Exponential backoff on Discord IPC reconnect
- Async cover-art prefetch (does not block daemon poll)
- Cover-cache pruning (200-entry cap, oldest first)
- XDG-compliant paths + rotating log file
- GitHub-based update checker with GUI dialog (install-type-aware: AppImage/pip/Flatpak/AUR/system)
- Live-log window (`--debug` flag)
- Autostart toggle
- Distribution packaging: AUR (`refrain` + `refrain-git`), Flatpak manifest, AppImage recipe
- Tests, ruff, bandit, CodeQL, Dependabot, release workflow, issue/PR templates

## Done — v0.1.1

- **Restart Refrain** entry in tray + Settings (clean shutdown + os.execvp)
- **All-in-GUI settings**: browser hints, notification delay, cover cache size
- **Notification reliability**: retry up to 2 s for the cover image to land,
  fall back to the bundled refrain.svg when iTunes has no match, and use
  the spec-compliant `--hint=string:image-path:` so KDE Plasma actually
  renders the embedded cover.
- **Discord elapsed-timer correctness**: drift detector resyncs `start`
  on pause/resume + user seeks. 17 dedicated unit tests in `test_timing.py`.
- **Source-available, no-derivatives license** (Refrain License — Use-Only).
- **AppImage in release workflow** + email channels (`contact@`, `report@`).

## Done — v0.1.2

- **Discord connection indicator** in tray (`● connected` / `○ not connected`)
- **Reset all settings to defaults** + **Open log folder** buttons in
  *Settings → Advanced*
- **Empty `client_id` no longer log-spams** the reconnect loop
- **Notification cover toggle is honored** (was previously fallback-anyway)
- **AppImage restart uses `$APPIMAGE`** explicitly so re-exec inside the
  AppImage points at the right mount
- **AppImageBuilder recipe fix** (Python 3.12 `ensurepip` no longer creates
  `bin/pip`, so the build step crashed)
- Update-dialog release notes auto-wrap bare URLs in Markdown autolink
  syntax to keep `...`-containing GitHub compare links clickable
- More dead-code sweeps (try/except/pass → contextlib.suppress)

## Done — v0.1.3

- **Start on Login fixed for non-system installs.** Autostart
  `.desktop` no longer relies on a bare `refrain` on `$PATH`; resolves
  the launch command in order `$APPIMAGE` → `shutil.which` →
  `sys.argv[0]` → `<sys.executable> -m refrain`, with proper Exec=
  quoting. Toggling the setting in Settings always rewrites the file
  with a path that actually works for the current install.
- **Qt XDG-portal warning silenced.** A `qInstallMessageHandler` now
  routes all Qt-internal log messages through Python's `logging`
  (under the `qt` logger) and drops the noisy
  `qt.qpa.services: Failed to register with host portal` line.
- **PyPI publishing wired into the release workflow.** Uses PyPA
  Trusted Publishers (OIDC) — no API token to manage, just a one-time
  pending-publisher setup on pypi.org (project: refrain, owner:
  Rockykln, repo: refrain, workflow: release.yml, environment: pypi).
  Builds are staged in `pypi-dist/` so the AppImage doesn't get
  uploaded by accident.
- **AppImage filename auto-syncs to the tag.** Release workflow
  rewrites `version:` in `AppImageBuilder.yml` from `${GITHUB_REF}`,
  so `Refrain-<version>-x86_64.AppImage` always matches the release.

## Done — v0.1.4

- **AppImage filename fix.** v0.1.3 shipped as
  `Refrain-.version.-x86_64.AppImage` because appimage-builder's
  `{version}` placeholder is rendered as the literal string. Dropped
  the custom `file_name` override and let appimage-builder default to
  `<app_info.name>-<app_info.version>-<arch>.AppImage`, which resolves
  at recipe-parse time.
- **AppImage icon path fix.** The icon_bundler walks XDG icon paths
  (`AppDir/usr/share/icons/...`); the v0.1.3 recipe used `files.include`
  which writes to a path the bundler never inspects. Now installs to
  `AppDir/usr/share/icons/hicolor/scalable/apps/refrain.svg` directly.
- **AUR `refrain` and `refrain-git` published.** Both PKGBUILDs pushed
  to AUR with SSH key + signed git tags. `refrain` pinned to the v0.1.3
  source tarball SHA; `refrain-git` auto-bumps via `pkgver()`.
- **Flatpak manifest validated locally.** Builds and runs cleanly via
  `flatpak-builder` against `org.kde.Platform//6.10` + the
  `io.qt.PySide.BaseApp//6.10` BaseApp + an inline patchelf 0.18.0
  module + vendored `python-deps.json`. Lives under
  `packaging/flatpak/` for users who want to build it themselves.
- **Markdown refresh.** README install table now leads with PyPI and
  marks AUR/AppImage as live; `packaging/README.md` rewritten with
  per-channel runbooks (PyPI Trusted Publisher gotcha, Flathub
  refresh recipe, AUR push checklist).
- **Bug-report template** placeholder bumped from `0.1.0` to `0.1.4`
  so users see a current example.

## Done — v0.1.5

- **Skip Next / Previous works on Apple Music in Chromium.** MPRIS
  source now keeps a control-fallback list of capable browser-native
  players, so skip dispatches onto whichever player advertises
  `CanGoNext` / `CanGoPrevious` even when KDE's
  `plasma-browser-integration` (the rich-metadata player) reports
  `False` for both.
- **Discord status renders as "Listening to &lt;song&gt;"** instead
  of "Playing Refrain" — the activity_type was defaulting to
  PLAYING which made the RPC look broken even though it was
  delivering correctly.
- **Tray-icon tooltip mirrors live progress** because DBusMenu
  doesn't refresh open menus' action text. Hovering the tray icon
  now gives a working ticker; the menu line stays as a snapshot
  that's accurate at the moment it's opened.

## Up next — v0.2

- **D-Bus PropertiesChanged signals** for MPRIS and BlueZ instead of the
  1 Hz polling loop. Lower CPU, instant track-change reactions. Polling
  stays as a fallback for source-discovery.
- **Idle-detection**: drop the Discord status when the same track has been
  shown longer than its duration + N seconds (handles dangling MPRIS
  metadata after a tab close).
- **Localization** (German + English to start) via Qt Linguist `.ts` files.
- **First-run wizard**: a one-page welcome that points out the tray icon,
  validates the Discord IPC socket, and runs an iTunes lookup to confirm
  network access works.
- **Theme-aware tray icons** that pick light or dark variants based on the
  Qt palette luminance.

## Maybe — v0.3+

- **Flathub submission**, second attempt. The manifest under
  `packaging/flatpak/` is fully validated locally and ready to ship —
  what's missing is a clean re-submission later when the time is
  right. No fixed timeline.
- **Last.fm scrobbling** as an opt-in alongside the Discord Rich Presence
  (no replacement, just an extra channel).
- **Cover-art replacement notifications**: re-send the desktop notification
  via `--replace-id` once the cover finishes downloading, so the embed
  swaps in even when the initial retry window times out.
- **Multiple Discord profiles**: per-source client IDs so Bluetooth and
  Apple Music Web can render with different artwork in the user's profile.
- **MPRIS-server mode**: expose Refrain as an MPRIS player itself, so KDE
  Plasma's media controls in the panel show the same track Refrain shows
  in Discord.

## Deliberately not in scope

- **Other music services** (Spotify, Tidal, Deezer, …). The project is
  Apple-Music-focused. Other services already have mature Discord-RPC
  apps; pretending to support them all would dilute the focus.
- **Other operating systems** (macOS, Windows). They already have first-
  party + community Discord-RPC integrations; Refrain is for Linux.
- **Heavy frameworks**. Refrain has three runtime deps (`pypresence`,
  `dbus-python`, `PySide6`). Anything that adds to that has to earn its
  place.
