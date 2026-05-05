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
