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

## Done — v0.2.5

- **AUR + Flatpak self-update spawns a terminal automatically**
  running the package-manager command (`yay -Syu refrain` /
  `flatpak update io.github.Rockykln.Refrain`) instead of just
  showing the command in a popup. Falls back to the previous
  message-box hint when no terminal is on PATH (probe list:
  konsole, gnome-terminal, xfce4-terminal, kitty, alacritty,
  foot, xterm, wezterm).
- **pip auto-update detects "no-op" exits.** PyPI's CDN can lag
  the GitHub Releases API by minutes, so a "0.2.X available"
  check could succeed while pip exited 0 with "Requirement
  already satisfied" (no upgrade). Refrain now parses pip's
  stdout to distinguish "Successfully installed" from
  "Requirement already satisfied" and tells the user to retry
  shortly instead of restarting into the same version.

## Done — v0.2.6

- **8 new UI languages** at 127/127 strings each: Spanish (`es`),
  French (`fr`), Portuguese (`pt`), Italian (`it`), Russian
  (`ru`), Polish (`pl`), Japanese (`ja`), Simplified Chinese
  (`zh_CN`). Settings → Advanced → Language dropdown lists each
  under its native endonym. Together with English + German this
  covers ~3 billion native speakers.
- **GitHub release bodies were one-line "Full Changelog: …"**
  because the workflow used `generate_release_notes: true`. Now
  extracts the matching `## [X.Y.Z]` section from CHANGELOG.md
  via awk and feeds it as `body_path`; existing v0.1.0 – v0.2.5
  bodies were backfilled out-of-band.
- **Reliability sweep**: `os.execvp` Restart fallback to
  `python -m refrain` when `argv[0]` isn't runnable;
  Config.save .tmp cleanup on disk-full / read-only;
  `Config._format_value` escapes `\n / \r / \t`;
  `Config._construct` coerces wrongly-typed primitives (e.g.
  `cover_cache_size = "200"`) instead of crashing in
  `_prune_cover_cache`; cover-art `_write_cache` +
  `download_cover_image` clean up `.tmp` siblings on disk
  failure; `_bridge_sandboxed_ipc_socket` permission-error wrap;
  iTunes Search shape-checks; `SessionBusUnavailable` exception
  surfacing instead of bare DBusException; Apply error toast for
  non-writable config dirs; `setup_logging`/`_apply_log_level`
  coerce non-string `log_level`.

## Done — v0.2.7

- **Tray-menu polish.** Every action carries an icon now —
  freedesktop theme icons (`configure`, `view-list-text`,
  `view-refresh`, `media-skip-{backward,forward}`,
  `media-playback-{start,pause}`) for built-in actions plus the
  bundled coloured-accent SVGs for Update / Quit. Source strings
  for the playback / restart / Discord-status entries dropped
  their unicode-glyph prefixes (`⏮ ⏵ ⏸ ⏭ ⟳ ●/○`); all 9 .ts
  files updated. Quit ✕ glyph redrawn to fill its 16×16 viewBox.
- **Song-info rows render with proper colour + icons.** Title /
  Artist / Progress / Discord-status used to be `setEnabled(False)`
  so KDE / GNOME's DBusMenu painted them muted-grey + indented +
  iconless. They're now enabled (a click opens Settings) and
  carry `view-media-{track,artist}`, `chronometer`, and
  `network-{connect,disconnect}` icons.
- **Middle-click on the tray icon toggles play/pause.** Same
  PlayPause command as the tray-menu Play/Pause item, but
  reachable without opening the menu.
- **Discord handshake errors no longer crash-log.** `pypresence.
  exceptions.DiscordError` (raised when Discord rejects the
  handshake — "User logged out", "Invalid Client ID") fell
  through the bare `except Exception:` and emitted a full
  traceback every retry cycle. Now caught explicitly, logged at
  INFO, with backoff jumping straight to the 15 s ceiling so
  Refrain doesn't retry tightly while you sign back into Discord.
- **First Discord push after a forced reconnect can no longer be
  deduped out.** `_last_payload` is reset in the connect-success
  path so the dedup cache from a previous Presence instance
  doesn't suppress the first push on the new pipe.
- **Settings → Updates "Latest release notes" pane populates on
  every restart.** v0.2.6's startup auto-check short-circuited
  before fetching when the 24h cooldown was active; the v0.2.6
  workaround re-fetched on every Updates-tab visit. Both gone —
  startup runs a silent fetch (no popups, no cooldown bump) so
  the pane is populated immediately, while the cooldown still
  gates the auto-nag popup.
- **Empty artist row in tray menu when nothing was playing**
  (`setVisible(False)` until a real track populates it).

## Done — v0.3.0

Shipped as one tag; folds in the accumulated reliability fixes that
were tracked internally as "v0.2.8" but never separately tagged.

- **Source priority now prefers the actively-playing source.**
  `_poll_sources` used a static "MPRIS before Bluetooth" order, so
  a stale *paused* Apple Music browser tab (`has_track=True`,
  `PAUSED`) permanently masked music actively playing over
  Bluetooth headphones — and idle detection (PLAYING-only) never
  cleared the paused tab either. New pure `select_source_track`
  ranks a PLAYING source above a paused/loaded one; MPRIS keeps
  the tie-break when neither is playing so the active source
  doesn't flip-flop. MPRIS-playing short-circuit keeps the common
  case at one D-Bus round-trip. +7 unit tests.
- **Cover-art replacement notifications.** When iTunes is slow and
  the ~2 s cover-retry window times out, Refrain now fires the
  brand-fallback notification immediately, captures its id via
  `notify-send --print-id`, and watches ~8 s longer — once the
  cover finishes downloading it re-issues with `--replace-id` so
  the album art swaps into the *same* bubble instead of the user
  never seeing it (or getting a second popup). Pure
  `build_notify_argv` / `parse_notify_id` helpers, +8 unit tests;
  the non-blocking fire-and-forget path is unchanged when the
  cover is already cached or cover-art is off.
- **Comment- and unknown-key-preserving config writer.**
  `Config.save()` previously rewrote `config.toml` from scratch on
  every silent write (e.g. the daily update-check stamping
  `last_check_ts`), discarding user comments and any keys Refrain
  didn't recognise. It now does a line-oriented in-place rewrite
  that only touches the `key = value` lines it owns, leaving
  comments, blank lines, ordering and unknown keys intact;
  full-serialize fallback when the file is absent or unparseable.
  Dependency-free — no `tomlkit`, in keeping with the three-runtime-
  deps rule.
- **Remaining fixed-position UI strings wrapped in `tr()`** (the
  Updates "last checked" date formatter, the Bluetooth
  paired-device picker labels). Catalog regeneration + translation
  across the nine shipped languages is community-PR follow-up
  (tracked under *Up next*).
- **`urllib.error` import made explicit** in `welcome_dialog.py`
  (was relied upon transitively via `urllib.request`; an
  `except urllib.error.URLError` could have raised `AttributeError`
  during handler matching). Dropped the unused `urllib.parse`
  import. ROADMAP section ordering fixed (forward-looking sections
  no longer sit above shipped releases).
- **Last.fm scrobbling** — opt-in, *alongside* the Discord Rich
  Presence (never a replacement), off by default. Each user
  registers their own Last.fm API account (same bring-your-own-
  credentials model as the Discord client_id) and connects it via
  the in-app desktop auth flow (`auth.getToken` → browser authorize
  → `auth.getSession`). No new dependency — the three signed API
  methods are hand-rolled on `urllib` + `hashlib` like
  `cover_art.py` (protocol-mandated MD5 request signature, marked
  `usedforsecurity=False`; SHA-256 for the parts Refrain controls).
  - **Crash-safe persistent offline queue** (`scrobble_queue.jsonl`
    under `$XDG_STATE_HOME`): a played track is banked the instant
    it qualifies, so an offline window, a Last.fm outage, or
    quitting mid-song never loses it; submitted in ≤ 50-item
    batches on the next opportunity. SHA-256 dedup, 1000-entry cap
    (oldest dropped), atomic writes, corrupt-line tolerant.
  - **Scrobble rule** = Last.fm's standard "half the track or four
    minutes, whichever first; > 30 s only". Play time is
    accumulated pause/seek-aware off monotonic time and clamped so
    a suspended laptop can't credit phantom hours. Preview clips
    (< 30 s effective) are never scrobbled, matching the
    Discord/idle paths.
  - Optional `track.updateNowPlaying` (the Last.fm equivalent of
    the Discord status), once per track.
  - All network is offloaded to a single-worker executor so the
    poll tick never blocks (same pattern as `CoverFetcher`).
    Privacy `Off` silences scrobbling too; an invalid/revoked
    session latches and surfaces a "reconnect" hint while keeping
    the queue intact. Its own **Last.fm** Settings tab with
    Connect/Disconnect; reconfigures in place (no restart, unlike
    the Discord client_id). +57 unit tests.
  - **Credentials in the OS keyring, never plaintext.** The shared
    secret + session token go to the freedesktop Secret Service
    (KWallet / GNOME Keyring), encrypted at rest, hand-rolled on
    `dbus-python` (no new dependency); `0600` owner-only file
    fallback only when no keyring exists. `config.toml` never holds
    them (and is itself written `0600`); a legacy plaintext copy is
    auto-migrated and scrubbed. Live KWallet round-trip verified.
  - Every Settings tab is scroll-safe (Last.fm on its own page);
    no visible scrollbar at the default size in English or German.

## Done — v0.4.0

- **Full uninstall — one command, any distro.** `refrain
  --uninstall` (and a *Settings → Advanced → Uninstall Refrain…*
  button) wipes everything Refrain wrote — config, logs, cover
  cache, scrobble queue, autostart entry, menu `.desktop` + icon —
  and purges the Last.fm credentials from the OS keyring, then
  prints the exact package-removal one-liner for the detected
  install type (pip / pipx / AUR / Flatpak / AppImage / system).
  One-shot before the GUI/lock so it works headless; idempotent and
  failure-tolerant; confirms before deleting (`-y` skips). New
  `refrain.uninstall` core (collect_paths / removal_command /
  purge), +13 strictly-hermetic tests. This is the GDPR
  right-to-erasure as a single command.
- **pipx self-update fixed.** A pipx-installed Refrain self-update
  died with "No module named pip": the pipx app venv has
  `sys.prefix != base_prefix` (looked like a plain pip install) but
  ships no pip. `detect_install_type()` now returns `pipx` for the
  `/pipx/venvs/` layout (checked before the generic venv→pip
  branch); `apply_update` runs `pipx upgrade refrain` via a new
  `_apply_pipx()` with an "already at latest" no-op guard and a
  clear hint when the `pipx` binary isn't on PATH. UpdateDialog
  treats pipx as auto-updatable. +5 tests.
- **README tray-menu section refreshed** to post-v0.2.7 reality —
  unicode-glyph prefixes gone (theme icons), the Discord-connection
  row added, middle-click play/pause + tooltip-vs-open-menu
  behaviour documented.

## Done — v0.4.1

- **Honest Last.fm connection status.** Status keyed off
  `session_key` alone, so it rendered "Connected as (connected)"
  with no username and a misleading "Connected" when a keyring
  session survived without a usable api_key/secret (scrobbling
  inert). New pure `lastfm_connection_state()` requires the usable
  triple (api_key + shared_secret + session_key): "Connected[ as
  <user>]" only when all three are present, an explicit
  re-enter-and-Connect prompt for a desynced leftover, else "Not
  connected"; the Connect button no longer treats an incomplete
  leftover as Disconnect. +13 tests (pure + offscreen).

## Up next — v0.5.x

- **Stable-release AUR build** that doesn't rely on the GitHub
  release tarball — switch to a `git`-source PKGBUILD pinned to the
  signed tag, so AUR users get the exact same commit the release
  workflow ships.
- **Complete the translation catalogs** for the newly-wrapped
  strings: regenerate the `.ts` / `.qm` files and bring every
  shipped language back to 100% coverage via community PRs (the
  code-side `tr()` wrapping landed in v0.3.0).

## Maybe — v0.5+

- **Flathub submission**, second attempt. The manifest under
  `packaging/flatpak/` is fully validated locally and ready to ship —
  what's missing is a clean re-submission later when the time is
  right. No fixed timeline.

## Deliberately not in scope

- **Other music services** (Spotify, Tidal, Deezer, …). The project is
  Apple-Music-focused. Other services already have mature Discord-RPC
  apps; pretending to support them all would dilute the focus.
- **Other operating systems** (macOS, Windows). They already have first-
  party + community Discord-RPC integrations; Refrain is for Linux.
- **Heavy frameworks**. Refrain has three runtime deps (`pypresence`,
  `dbus-python`, `PySide6`). Anything that adds to that has to earn its
  place.
