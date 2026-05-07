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

## Done — v0.2.0

- **Theme-aware tray icons** — Qt 6.5+ `colorScheme()` detection plus
  three new dark-glyph SVG variants. Re-renders live when the system
  theme flips.
- **Idle detection** — drops Discord + tray when a track has been
  reported as PLAYING for longer than its own duration plus a grace
  window. Handles closed-tab MPRIS handles cleanly. New config field
  `advanced.idle_grace_s` (default 30 s, 0 disables).
- **D-Bus PropertiesChanged listeners** for MPRIS + BlueZ via
  `QDBusConnection`. Track switches register instantly instead of
  within the next 1 Hz poll. Polling stays on as discovery fallback.
- **First-run wizard** — single-page welcome with tray-icon
  orientation, live Discord IPC + iTunes probes, Discord
  Application-ID input field with link to the Developer Portal.
  Skippable; the Settings tab still works.
- **Localization** — `tr()` wrapping + Qt Linguist `.ts` files under
  `i18n/`. German fully translated (62 strings). Stub `.ts` files
  for FR / ES / IT / PT / NL / PL / JA / ZH_CN waiting for
  community translation PRs. `make i18n` rebuilds the `.qm` files.
- **Discord RPC layout reworked** — title / artist / album each get
  their own line; album is filtered against artist + title so the
  bottom line never echoes what's above.
- **Flatpak tray fix** — manifest declares the Status-Notifier-Watcher
  D-Bus names, so the sandbox can reach KDE/GNOME's tray.
- **Window-title cleanup** — no more "Refrain — Settings — Refrain"
  triple; Qt's auto-suffix is the only place "Refrain" appears.

## Done — v0.2.1 (rolled into v0.2.2; never separately tagged)

- **Settings UI overhaul** — consistent `QGroupBox` + form-layout
  across every tab, left-aligned group titles via stylesheet
  (Plasma Breeze centers them by default), fixed-width inputs
  (220 px default, 360 px in the Discord group). Switched from
  `AllNonFixedFieldsGrow` to `FieldsStayAtSizeHint` because the
  former silently ignored fixed-width caps on Plasma Breeze.
- **Notification cover-flicker fix** — `notify-send -i` now
  carries the same image as the `--hint string:image-path:`
  payload so KDE Plasma can't briefly render the brand badge.
- **Discord RPC cover flicker fix** — defer the first activity
  update for a new track until the iTunes cover URL is in cache,
  capped at ~3 polls so cover-less songs still update.
- **Auto-restart on Discord client_id Apply** — same pattern as
  the language switch, since pypresence binds to the client_id
  at connect time.
- **AppImage update size validation** + tmp cleanup, **atomic
  config writes** (tmp + os.replace), **dead `dbus_watcher`
  module removed** (155 lines), **RPC reconnect backoff capped
  at 15 s** instead of 60 s, **`.ts` source files no longer ship
  in the wheel**, **MPRIS dispatch logging at INFO** so
  Next/Previous routing is visible in the live log.

## Done — v0.2.3

- **Distro-portability sweep** driven by hands-on testing.
  - Qt style plugin path augmentation extended to Debian / Ubuntu
    (`/usr/lib/<arch-triple>/qt6/plugins`) and Fedora / RHEL / openSUSE
    (`/usr/lib64/qt6/plugins`); v0.2.2 only handled the Arch layout.
  - Snap and Flatpak Discord builds now reachable out of the box —
    `_bridge_sandboxed_ipc_socket` symlinks the sandboxed
    `discord-ipc-N` into `$XDG_RUNTIME_DIR` before each pypresence
    connect attempt.
  - Bluetooth fast-fail via `NameHasOwner('org.bluez')` so VMs and
    minimal installs without bluez no longer block the daemon's
    poll cycle for 25 s waiting on D-Bus service activation.
  - "No system tray" error covers GNOME, MATE, XFCE, and the
    tiling-WM crowd (Hyprland / Sway / i3 / river); was
    GNOME-AppIndicator-only.
  - `--install-desktop` rewrites `Exec=` in the installed `.desktop`
    to match the running install (AppImage path / venv shim / system
    binary), so AppImage and source-checkout users get a working
    menu entry.
  - Browser hints expanded to cover Floorp, Waterfox, Mullvad
    Browser, Tor Browser, ungoogled-chromium.
- **iTunes-catalog duration as MPRIS override** —
  `pick_effective_duration_ms` chooses iTunes when MPRIS and iTunes
  disagree by more than 15 % or when MPRIS sits in the preview-clip
  band on a song iTunes considers full-length. Apple Music's MPRIS
  surface no longer makes Discord briefly flip a 2:11 song's total
  to 0:14 / 7:21 mid-track.
- **Update-dialog Cancel + orphan-download self-heal** — `Cancel`
  button + window-close handler abort an in-flight AppImage
  download cleanly and remove the partial `.new`. A separate
  `cleanup_orphan_downloads` runs at startup so a SIGKILL or power
  loss mid-download doesn't leave a stale `*.AppImage.new` next to
  the binary forever.
- **Per-window icons** on Settings / Log / Update dialogs so GNOME
  Shell's title-based heuristic on Wayland doesn't render them with
  the gnome-control-center gear icon in the dock.
- **Logging audit** closed six gaps: `setup_logging` runs before
  `Config.load` so config-load messages reach the file log; the
  log-level toggle in Settings applies live; `setup_logging` degrades
  gracefully when the XDG state dir is unwritable; `os.execvp`-based
  restarts flush log handlers first; catch-all exception branches in
  `DiscordRPC._ensure_connected`, `MPRISSource._call_method_on` and
  `BluetoothSource._call_method` switched to `log.exception()` so
  tracebacks land in `refrain.log`.
- **`docs/test-matrix.md`** — eight Tier-1 distros, Tier-2 spot-check
  list, Tier-3 desktop / compositor sweep, six-step smoke checks,
  out-of-scope reasons for the floor of unsupported distros (RHEL 9
  / Rocky 9 / Alma 9 / Debian 11 / Ubuntu 22.04 / Alpine).

## Done — v0.2.4

- **Time-display consistency across tray + Plasma panel + Discord**.
  v0.2.3 already fixed Discord; the tray's progress label and the
  published MPRIS Metadata still keyed off the raw `mpris:length`
  and showed inconsistent numbers when MPRIS briefly lied about a
  song's duration. `pick_effective_duration_ms` is now hoisted into
  `_dispatch` so all three surfaces see the same iTunes-corrected
  value every tick. Plus position is clamped to duration so a
  "2:30 / 0:14 (-0:00)" line can't render during a brief MPRIS
  preview-clip glitch.
- **Snap and Flatpak Discord builds reachable out of the box**.
  Their IPC socket lives inside the sandbox tree
  (`xdg-run/app/com.discordapp.Discord/`,
  `~/.var/app/com.discordapp.Discord/.../`,
  `~/snap/discord/current/.config/discord/`) which pypresence
  doesn't probe. `_bridge_sandboxed_ipc_socket` symlinks the first
  sandbox socket it finds into `$XDG_RUNTIME_DIR` before each
  connect attempt + sweeps stale symlinks left behind when the
  Discord install is removed.
- **Inline release notes in Settings → Updates tab**. The tab
  carries a QTextBrowser that renders the same Markdown as the
  popup, plus "Current version" + "Latest known" labels.
  `releaseInfoFetched(release | None)` signal fires after every
  check (auto / manual, success / failure) so the tab refreshes
  regardless of the result.
- **Discord activity card always shows the Refrain brand badge as
  small_image**. Previously the badge was only in `large_image` as
  a fallback when cover-art lookup failed; now it's the small-icon
  corner of every payload, so the cover gets the visual focus and
  hovering reveals "Refrain" as the source app. Plus the
  cover-wait defer is back to 3 polls (~1.5 s at 500 ms tick) so
  Discord usually goes straight from "no activity" to "cover" with
  no brand flash on the way.
- **Config drops unknown TOML keys** instead of nuking the whole
  file. A typo or a key written by a newer Refrain that the user
  has since downgraded from used to make `Config.from_dict` raise,
  caught by the surrounding except, and fall back to defaults for
  every setting. Now the offending key gets a single warning, the
  rest of the section survives.
- **DiscordRPC dedupes identical consecutive payloads**. The
  daemon ticks every 500 ms but Discord rate-limits presence
  updates to 5/20 s — most ticks were silently dropped on the
  Discord side anyway. Now the second-and-on identical payload
  is a no-op on our side too.
- **Browser hints expanded**: Floorp, Waterfox, Mullvad Browser,
  Tor Browser, ungoogled-chromium.
- **`docs/bluetooth.md`** — first-time-setup walkthrough covering
  bluez install per distro, pairing recipe, AVRCP verification,
  per-source Discord profile setup, and Troubleshooting.
- **FAQ entries** for "Refrain isn't picking up my browser"
  (covers `playerctl` diagnostic + Snap-confined-browser workaround
  + Firefox `about:config` toggle) and "How do I add a browser
  that isn't in the list".
- **`docs/architecture.md` refreshed** to v0.2.x reality — diagram
  no longer claims "1 Hz tick" (default has been 500 ms since
  v0.2.2), `MPRISServer` block added, `org.mpris.MediaPlayer2.refrain`
  publish documented, GLib thread for dbus-python signal dispatch
  documented, new section on Discord IPC sandbox bridging.
- **+13 unit tests**: `DiscordRPC` payload dedup (4),
  `_bridge_sandboxed_ipc_socket` (4), `compute_idle_state` with
  `effective_duration_ms` (2), `cleanup_orphan_downloads` (3).
  Total: 113 → 125, all green.

## Up next — v0.3

- **Polishing the wrapped i18n surface** — wrap the remaining
  fixed-position strings (date formatters, advanced-tab subtitles,
  bluetooth picker labels) so the German build covers everything a
  user sees, not just the high-traffic widgets.
- **Stable-release AUR build** that doesn't rely on the GitHub
  release tarball — switch to a `git`-source PKGBUILD pinned to the
  signed tag, so AUR users get the exact same commit the release
  workflow ships.

## Done — v0.2.2

- **Multiple Discord profiles** — per-source `client_id_mpris` and
  `client_id_bluetooth` overrides on top of the default `client_id`,
  so Apple Music can render under one Discord application (with the
  album-grid as artwork) and Bluetooth headphones under another (with
  a generic Bluetooth glyph). Daemon reconnects RPC the moment the
  active source flips.
- **MPRIS-server mode** — Refrain publishes itself as
  `org.mpris.MediaPlayer2.refrain` so KDE Plasma's panel media-controls
  applet, KDE Connect, GNOME Shell, etc. drive the same Play/Pause/
  Next/Previous as the tray and render the same track. Built on
  `dbus-python` with a GLib main loop in its own thread so it
  doesn't conflict with Qt's event loop.
- **Welcome wizard redesign** — icon-badge header, subtitle, dedicated
  diagnostics card with two clearly-labelled probe rows, readable
  helper text on Plasma Breeze Dark (was invisible with `palette(mid)`).
  Apply now opens Settings automatically with the just-saved Discord
  ID pre-filled.
- **Live-tier responsiveness** — default `poll_interval_ms` 1 s → 500 ms,
  `notify_delay_ms` 600 ms → 0 ms (cache hits fire instantly), Discord
  RPC connect happens within ~1 s of first track detection (was up to
  51 s due to dbus-python's 25 s default reply timeout combined with
  plasma-browser-integration introspection hangs — fixed via
  `introspect=False` + per-property `timeout=0.5` + per-player
  blacklist when a player times out).
- **iTunes `trackTimeMillis` fallback** for Apple Music preview-clip
  durations (< 30 s). Discord drops `start`/`end` entirely on preview
  mode so the elapsed counter doesn't reset every 8 s.
- **Skip / Previous reach Apple Music reliably** — chromium-native
  MPRIS dispatched first, plasma-browser-integration as fallback,
  cascade follow-up polls (0/50/150/350/750 ms) so the track-change
  reflects in Discord + tray within ~250 ms of detection.
- **Reset preserves all three Discord IDs**, dialog button labels
  localised (`Reset` / `Zurücksetzen`), text states what's preserved
  explicitly.
- **Welcome wizard X / Esc** marks `first_run_complete=True` so the
  wizard doesn't re-appear; Apply with empty ID asks for confirmation.
- **Theme-aware text colors** — every helper-text label switched from
  `palette(mid)` to `palette(text)` after Plasma Breeze Dark
  rendered them invisible.

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

## Deliberately not in scope

- **Other music services** (Spotify, Tidal, Deezer, …). The project is
  Apple-Music-focused. Other services already have mature Discord-RPC
  apps; pretending to support them all would dilute the focus.
- **Other operating systems** (macOS, Windows). They already have first-
  party + community Discord-RPC integrations; Refrain is for Linux.
- **Heavy frameworks**. Refrain has three runtime deps (`pypresence`,
  `dbus-python`, `PySide6`). Anything that adds to that has to earn its
  place.
