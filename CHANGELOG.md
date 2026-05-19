# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added

- **Last.fm scrobbling** — opt-in, *alongside* the Discord Rich
  Presence (never a replacement), off by default. Register your own
  free Last.fm API account (same bring-your-own-credentials model as
  the Discord Application ID), enter the key + shared secret in
  *Settings → General → Last.fm scrobbling*, and **Connect** via the
  in-app browser auth flow. A track is scrobbled at Last.fm's standard
  threshold (half the track or four minutes, whichever first; > 30 s
  only), with pause/seek-aware play-time accounting. Optional
  `track.updateNowPlaying`. No new dependency — the signed API surface
  is hand-rolled on `urllib` + `hashlib`. Walkthrough:
  [`docs/lastfm.md`](docs/lastfm.md).
- **Crash-safe persistent scrobble queue**
  (`$XDG_STATE_HOME/refrain/scrobble_queue.jsonl`). A qualifying track
  is queued the instant it earns it, so being offline, a Last.fm
  outage, or quitting Refrain mid-song never loses a scrobble — it's
  submitted (≤ 50-item batches) on the next opportunity. SHA-256
  dedup, 1000-entry cap, atomic writes, corrupt-line tolerant. All
  Last.fm network runs on a worker executor so the poll tick never
  blocks; Privacy `Off` silences scrobbling too.
- **Cover-art replacement notifications.** When iTunes is slow and the
  ~2 s cover-retry window times out, Refrain now fires the
  brand-fallback notification immediately, then re-issues it with
  `notify-send --replace-id` once the cover finishes downloading — the
  album art swaps into the *same* bubble instead of never appearing
  (or a second popup).

### Changed

- **Per-source Discord Client IDs are now opt-in.** The General tab
  showed three Client ID fields; most users need one. The two
  per-source overrides (Apple Music / Bluetooth) are hidden behind a
  *"Use a separate Discord application per source (advanced)"* toggle
  (off by default, auto-enabled when an override is already
  configured so existing setups are untouched).
- **Source priority now prefers the actively-playing source.** A
  stale *paused* Apple Music browser tab no longer masks music
  actively playing over Bluetooth headphones (idle detection is
  PLAYING-only, so the paused tab never cleared either). MPRIS keeps
  the tie-break when neither is playing.
- **Config saves preserve comments and unknown keys.** `Config.save()`
  now rewrites only the `key = value` lines Refrain owns; hand-added
  comments, ordering, and keys written by a newer Refrain survive the
  daily silent update-check that stamps `last_check_ts`.
- **More UI strings localised** — the Updates "last checked"
  timestamp now formats in the active locale (`QLocale`) instead of a
  hard-coded ISO string; the Bluetooth picker labels and the
  live-log "ALL" sentinel are now translatable.

### Fixed

- `welcome_dialog.py` relied on `urllib.error` transitively via
  `urllib.request`; an `except urllib.error.URLError` could have
  raised `AttributeError` during handler matching. Imported
  explicitly; dropped the unused `urllib.parse` import.
- ROADMAP section ordering corrected — forward-looking sections no
  longer sit above shipped releases.
- Settings dialog no longer crushes/overlaps form rows: the Last.fm
  controls moved to their own tab and every tab page is wrapped in a
  scroll area; the default window size fits the tallest tab with no
  visible scrollbar in English and German.

### Security

- **Last.fm credentials are never written to `config.toml`.** The
  shared secret and session token are stored in the OS keyring
  (freedesktop Secret Service — KWallet / GNOME Keyring), encrypted
  at rest, hand-rolled on `dbus-python` (no new dependency). When no
  keyring is reachable they fall back to a `0600`, owner-only file
  separate from the config. `config.toml` itself is now written
  `0600`, and a legacy plaintext secret left by an earlier build is
  auto-migrated into the keyring and scrubbed from disk on the next
  save. Secrets are never logged and only ever leave the machine to
  Last.fm over HTTPS (which is scrobbling itself).

## [0.2.7] - 2026-05-07

Tray-menu polish + Discord-RPC reliability fixes. Every tray action
now carries an icon, song-info rows render in normal menu text
colour instead of greyed-out indented rows, middle-click on the
tray toggles play/pause, and Discord handshake errors no longer
spam the log with unexpected-exception tracebacks.

### Added

- **Middle-click on the tray icon toggles play/pause.** Clicks the
  PlayPause command on whichever MPRIS source Refrain is currently
  attached to — same path as the tray-menu Play/Pause item, but
  reachable without opening the menu. Plain left-click still opens
  Settings; right-click still shows the menu.
- **Tray menu actions now all carry icons.** Once any QMenu item
  has an icon, the menu reserves the icon column for *all* items —
  so adding the red ✕ to Quit Refrain made every previously
  text-only action read as misaligned with blank icon space. Action
  items now use freedesktop theme icons (`configure`,
  `view-list-text`, `view-refresh`, `media-skip-backward`,
  `media-playback-start`, `media-playback-pause`,
  `media-skip-forward`) which match the user's Plasma / GNOME /
  Breeze icon set. Update + Quit keep their bundled coloured-accent
  SVGs. Source strings for the playback / restart actions dropped
  their unicode-glyph prefixes (`⏮ ⏵ ⏸ ⏭ ⟳`) so the new icons
  don't read as duplicates; all 9 .ts files updated to match. The
  Quit ✕ glyph was redrawn to fill its 16×16 viewBox edge-to-edge
  so it visually matches the theme icons' pixel weight.
- **Song-info rows in the tray menu now render with proper icons
  and full-strength text colour.** Title / Artist / Progress /
  Discord-status used to be `setEnabled(False)` so KDE's DBusMenu
  rendered them muted-grey + indented + iconless next to the white
  action labels below. They're now enabled (a stray click opens
  Settings — their natural "tell me more" target) and carry
  `view-media-track`, `view-media-artist`, `chronometer`, and
  `network-connect` / `network-disconnect` icons. The `♪` prefix
  on the title and the `●/○` prefix on the Discord-status row are
  gone — the icons replace them.

### Fixed

- **Settings → Updates "Latest release notes" QTextBrowser was
  parent-less.** `_build_updates_tab` called
  `notes_group.setLayout(QVBoxLayout())` on a QGroupBox that
  already had a QFormLayout installed by `_new_group`. Qt refuses
  to install a second layout (logs "QLayout: Attempting to add
  QLayout to QGroupBox which already has a layout") so the new
  VBoxLayout was discarded and the QTextBrowser ended up as a
  free-floating top-level widget kept alive only by the Python
  reference. In production it still painted because the parent
  page laid it out somewhere reasonable, but it was outside the
  widget tree (`findChildren(QTextBrowser)` returned 0). The view
  is now added via the existing form layout's `addRow`.
- **Discord handshake errors logged as ERROR-level unexpected
  exceptions with a full traceback every retry.** When Discord
  responded to a `pypresence.Presence.connect()` with an error
  message (e.g. "User logged out" — DiscordError code 1000, or
  "Invalid Client ID"), the bare `except Exception:` clause in
  `_ensure_connected` swallowed it as a programmer bug and emitted
  a noisy traceback every backoff cycle. `pypresence.exceptions.
  DiscordError` is now caught explicitly and logged at INFO; the
  back-off jumps straight to the 15 s ceiling so Refrain doesn't
  retry tightly while the user is signing back into Discord or
  fixing a typo'd client_id.
- **First Discord push after a forced reconnect could be deduped
  out of existence.** The presence-update dedupe cache lived on
  the orchestrator across `Presence` instance replacements; after
  a reconnect with byte-identical payload data, the new pipe
  carried no state on Discord's side but our cache still claimed
  "we already pushed this", so the user saw "no activity" until
  metadata changed. `_last_payload` is now reset to None in the
  successful-connect path of `_ensure_connected`.
- **Empty artist row in tray menu when nothing was playing.** The
  artist QAction stayed visible with empty text whenever no track
  was active, rendering as a tall blank line right under
  "(nothing playing)". Now hidden until a real track populates it.
- **Settings → Updates "Latest release notes" pane stayed empty
  on every Refrain restart within the 24-hour update-check
  cooldown.** `maybe_check_on_startup` short-circuited *before*
  fetching anything when the cooldown was active, so
  `releaseInfoFetched` never fired and `set_latest_release` never
  populated the inline pane. The v0.2.6 workaround — re-fetching
  on Updates-tab activation with `manual=True` — additionally
  fired the "up to date" / "update available" popups on every tab
  visit, which was its own bug. Startup now always fetches the
  release info (silent, no popups) so the pane is populated
  immediately; the cooldown only gates whether that fetch *also*
  triggers the auto-popup. The on-tab-activation fetch is gone.

## [0.2.6] - 2026-05-07

Localization expansion plus a bundle of reliability fixes accumulated
since v0.2.5 across the config layer, restart path, cover cache, IPC
sandbox bridge, and update flow.

### Added

- **8 new UI languages.** Refrain ships full translations (127 / 127
  strings each) for Spanish (`es`), French (`fr`), Portuguese (`pt`),
  Italian (`it`), Russian (`ru`), Polish (`pl`), Japanese (`ja`), and
  Simplified Chinese (`zh_CN`) on top of the existing English source
  + German. The Settings → Advanced → Language dropdown lists each
  language under its native endonym ("Español", "Français",
  "Português", "Italiano", "Русский", "Polski", "日本語",
  "简体中文"). Picking a language restarts Refrain so the
  QTranslator picks up the new `.qm`. Together with English and
  German, this covers roughly 3 billion native speakers.

### Fixed

- **GitHub release bodies were one-line "Full Changelog: …"** — the
  release workflow used `generate_release_notes: true`, which is
  GitHub's auto-generated content (just a compare-link to the
  previous tag). The in-app inline release-notes pane and the
  popup dialog rendered that 79-character body verbatim. The
  workflow now extracts the matching `## [X.Y.Z]` section from
  CHANGELOG.md via awk and feeds it as the release `body_path`.
  Existing v0.1.0 – v0.2.5 release bodies were backfilled out-of-
  band via `gh release edit --notes-file …` against the same
  extraction.
- **Release-notes pane stayed empty after every Refrain restart** —
  the orchestrator's 24-hour startup cooldown blocks
  `maybe_check_on_startup` whenever `last_check_ts` is recent, and
  a fresh process has no cached `_latest`. `SettingsWindow` now
  emits `updatesTabRequested` when the user activates the Updates
  tab; app.py wires that to a slot that triggers
  `updater.check_now(manual=True)` only when no cached release is
  available this session. First visit fetches in the background;
  subsequent visits use the cache.
- **`os.execvp` failure during Restart had no fallback** — if
  `sys.argv[0]` wasn't a runnable script (typical of
  `python -m refrain` source-checkout invocation, where argv[0] is
  `__main__.py`), execvp raised an unhandled OSError. Detect
  unrunnable paths up front and fall back to
  `<sys.executable> -m refrain`, which always works because
  Refrain is currently importable. The fallback is also retried
  from the actual OSError handler if the first execvp fails for
  any other reason.
- **`Config.save` leaked .tmp files on write failure** — a disk-
  full / read-only / permission-denied error during the atomic
  write left a stale `*.tmp` sibling next to the real config
  forever. Now wrapped in a `try/finally` that unlinks the partial
  file before re-raising.
- **`Config._format_value` didn't escape newline / tab / CR** —
  any string value with those characters (extremely rare in our
  schema, but possible via a hand-edited config or a future
  field) would produce broken TOML that tomllib rejects on next
  load, tripping the "config unreadable, using defaults" fallback
  and silently losing every setting. Added `\n` / `\r` / `\t`
  escapes alongside the existing `\\` / `\"`.
- **CHANGELOG link `[0.2.1]` was a 404** — pointed at a non-
  existent `v0.2.1` tag (the v0.2.1 commit was made but never
  tagged or published; its content rolled into v0.2.2). Replaced
  with a commit-hash link to `6b339ba`. ROADMAP gained a matching
  `(rolled into v0.2.2; never separately tagged)` suffix.
- **Settings → Apply with a non-writable config directory crashed
  silently.** `Config.save` raised OSError (read-only home, disk
  full, permissions). PySide6's signal-slot machinery logged
  "uncaught exception in slot" and Apply visibly did nothing.
  Now wrapped in try/except: the user gets a `QMessageBox.critical`
  naming the path + the OS error, and the in-memory daemon state
  still updates so the rest of the session works.
- **`autostart.enable` / `disable` raised on filesystem failure**
  with no user feedback. Now they return a bool and log a warning
  on failure; Settings → Apply continues instead of crashing.
- **Update dialog `_on_update_clicked` could TypeError on a
  re-entered click** — `close_btn.clicked.disconnect()` raises if
  the signal has no connections (unusual but possible during
  fast double-clicks while runner setup is in flight). Wrapped
  in `contextlib.suppress(TypeError)` like the matching
  `_on_runner_finished` path.
- **Refrain crashed at startup when no D-Bus session bus was
  available** — `single_instance.acquire()` propagated
  `dbus.DBusException` past `app.py`'s `except AlreadyRunning`,
  giving the user an unhandled exception traceback instead of a
  message box. New `SessionBusUnavailable` exception type
  surfaces the actual reason ("D-Bus session bus unavailable")
  with hints about `dbus-daemon` / `DBUS_SESSION_BUS_ADDRESS`.
- **`setup_logging(level)` and `_apply_log_level` crashed on
  non-string `log_level`** — a hand-edited config with
  `log_level = false` (boolean) or `log_level = 5` (int) would
  AttributeError on `.upper()`. Now coerced to str defensively.
- **`Config._construct` accepted wrongly-typed values silently** —
  `cover_cache_size = "200"` (string) would survive the dataclass
  __init__ but then crash in `_prune_cover_cache` with TypeError on
  `len(files) - "200"`. The new `_coerce_value()` helper coerces
  primitives where it makes sense (int from numeric string,
  bool from "true"/"false") and drops + warns when it doesn't
  (e.g. `log_level = false` → use the field default). 1 new test
  in test_config.py.
- **Cover-art cache writes leaked .tmp files on disk failure** —
  `cover_art._write_cache` and `download_cover_image` called
  `tmp.write_bytes()` / `os.replace()` without exception
  handling. A read-only `~/.cache/refrain/covers/` left a stale
  `*.tmp` next to the real file forever. Both now wrap in
  try/except, clean up the partial tmp, and log debug.
- **`_bridge_sandboxed_ipc_socket` could raise on locked-down
  sandbox dirs** — a permission-denied `iterdir()` of one of the
  Snap/Flatpak candidate paths used to propagate up through
  `DiscordRPC._ensure_connected` → daemon `_tick`'s except, killing
  the connect attempt every poll. Now wrapped: failures log debug
  and the connect proceeds without sandbox-bridging.
- **iTunes search shape-check.** A malformed response (string
  error, list-not-dict, missing fields) used to AttributeError
  on `data.get("results", [])` or `first.get(...)`. Now both
  layers check `isinstance(...)` first and treat unexpected
  shapes as "no result" — caller falls back to the bundled
  brand icon.
- **`_prune_cover_cache` could crash daemon startup** on a
  permission-denied or corrupt cache dir. Now wraps the
  enumerate/sort in try/except, logs debug, returns 0 — the
  daemon starts normally and lives without prune until next
  session. Plus defensive int() coercion of `max_entries`.

### Tests

- 3 new in `tests/test_config.py` covering newline-escape
  round-trip and the .tmp cleanup-on-failure path.

## [0.2.5] - 2026-05-07

A same-day patch on top of v0.2.4 driven by user feedback that the
AUR / Flatpak update path was effectively unusable from the GUI — the
dialog only displayed the package-manager command in a popup and
expected the user to copy + paste it into a terminal themselves.

### Changed

- **AUR + Flatpak updates now spawn a terminal automatically** with
  the package-manager command pre-typed. The user confirms any sudo
  prompt in the terminal, the package manager retains state, and
  Refrain just needs a restart afterwards. The
  "Refrain never modifies system files directly" guarantee is
  preserved — we only spawn the user's own AUR helper / `flatpak`,
  and only inside a terminal the user can read / cancel.
  - `_run_in_terminal()` walks a probe list (konsole, gnome-terminal,
    xfce4-terminal, kitty, alacritty, foot, xterm, wezterm) and
    spawns the first hit. Falls back to the previous "show command
    in a popup" behaviour when no terminal is on PATH.
  - `_aur_helper()` picks `yay` → `paru` → `trizen` → `pikaur` in
    that order; falls back to `sudo pacman -Syu refrain` when no
    helper is installed.
  - Update dialog button label flips from "Show update command" to
    "Run update in terminal" when the install is AUR or Flatpak.

### Tests

- 4 new tests in `tests/test_updater.py` covering the fallback
  message-box path, terminal-spawn for both AUR + Flatpak, and the
  `_aur_helper` pacman fallback when no helper is installed.

## [0.2.4] - 2026-05-07

A polish + reliability release built on the v0.2.3 distro-portability
work. The headline items are time-display consistency across all
three surfaces (tray / Discord / Plasma panel), Snap/Flatpak Discord
support out of the box, a full inline changelog in Settings, and
~20 % more test coverage to lock the new behaviour in. No breaking
changes — same config schema, same Python floor, same UI layout.

### Added

- **Inline release notes in Settings → Updates tab.** The tab
  previously only had an auto-check toggle, last-checked label and a
  "Check now" button — the actual release notes only showed up in
  the popup when an update was available. The tab now carries a
  QTextBrowser that renders the same Markdown the popup uses, plus
  "Current version" and "Latest known" labels. `UpdateOrchestrator`
  gained a `releaseInfoFetched(release | None)` signal that fires
  after every check (auto or manual, success or failure) so the tab
  refreshes its contents regardless of the result.
- **`docs/bluetooth.md`** — first-time-setup walkthrough covering
  bluez install per distro, pairing recipe, AVRCP verification,
  per-source Discord profile setup, and a Troubleshooting section
  for the common breakage modes (no AVRCP exposed, missing
  control, multiple paired devices, empty dropdown).
- **FAQ entries** for "Refrain isn't picking up my browser"
  (covers the `playerctl` diagnostic, the Snap-confined-browser
  workaround, and the Firefox `about:config` toggle), and
  "How do I add a browser that isn't in the list".

### Changed

- **Tray progress + published MPRIS metadata + Discord activity
  payload all use the iTunes-corrected duration.** v0.2.3 already
  fixed Discord; the tray's "0:42 / 7:21 (-6:39)" ticker and the
  `mpris:length` we publish to Plasma's panel were still using the
  raw `mpris:length` and showed inconsistent numbers when MPRIS
  briefly lied about a song's duration. Hoisted
  `pick_effective_duration_ms` into `_dispatch` so all three
  surfaces see the same value every tick. Tray position is also
  now clamped to duration so a "2:30 / 0:14 (-0:00)" display can't
  happen during a brief MPRIS preview-clip glitch on a longer song.
- **`os.chmod(tmp, 0o755)` on AppImage update replaced by mode
  preservation** — read the running AppImage's mode and mirror it,
  with `0o700` as fallback. CodeQL's `py/overly-permissive-file`
  warning is gone, and an AppImage that the user installed at
  `0o700` stays `0o700` across upgrades.
- **DiscordRPC dedupes identical consecutive payloads** instead of
  hammering the IPC channel on every poll. The daemon ticks every
  500 ms but Discord rate-limits presence updates to 5 per 20 s, so
  ~75 % of those ticks were silently being dropped on the Discord
  side anyway. Now the second-and-on identical payload is a no-op
  on our side too.
- **Cover-wait defer back to 3 polls (~1.5 s)**. v0.2.2 lowered it
  to 1 poll to feel more "live", but that meant Discord briefly
  rendered the `refrain` brand fallback as the *large* image while
  iTunes search returned, then flipped to the cover — visible
  flicker on every track change. With 3 polls Discord typically
  transitions straight from "no activity" to the cover with no
  flash. Bounded: songs that have no iTunes match still update
  after 1.5 s with the brand fallback.
- **Tray menu's "Update available" line now carries a coloured
  icon** (Breeze accent blue, `assets/icons/menu-update.svg`)
  instead of just a unicode `⬆` arrow in the same white as every
  other line. Visually distinguishes the update notification from
  Settings / Live log / Restart in the menu's icon column.
- **Browser hint defaults expanded** with Floorp, Waterfox, Mullvad
  Browser, Tor Browser and ungoogled-chromium. Existing configs
  keep their saved list (no auto-migration); the new entries appear
  unticked in Settings → Sources until the user toggles them.
- **~20 English-only UI strings wrapped in `tr()`** —
  `update_dialog.py` (status labels, button labels, header HTML),
  `log_window.py` (toolbar Level/Auto-scroll/Copy/Clear/Close), and
  `app.py` module-level QMessageBox calls (now via
  `QCoreApplication.translate`). Re-ran `lupdate6` + `lrelease6`
  against `refrain_de.ts`: 127/127 finished German translations.
- **`docs/architecture.md` refreshed** to v0.2.x reality — the
  diagram showed a "1 Hz tick" (default has been 500 ms since
  v0.2.2), the worker-thread block was missing `MPRISServer`, the
  GLib thread for dbus-python signal dispatch was undocumented, and
  the "does not export any custom interfaces" line was wrong since
  the v0.2.2 MPRIS-server publication. Plus a new section on
  Discord IPC sandbox bridging (Snap/Flatpak).

### Fixed

- **Tray tooltip cleared on track change.** After a paused-to-paused
  track switch, the tooltip briefly showed "Song B • 1:30 / 2:11"
  using Song A's elapsed counter while the new track waited for its
  first `progressTick`. `set_track` now drops the stale progress
  line when the title text actually changed.
- **`compute_idle_state` honours the iTunes-corrected duration.**
  Previously the deadline keyed off `track.duration_ms` so a
  7:21-playlist-total-on-a-2:11-song lie made dangling-tab cleanup
  fire 5 minutes too late. New optional `effective_duration_ms`
  parameter; the daemon passes the same value the RPC payload
  uses.
- **Snap and Flatpak Discord builds reachable out of the box.**
  Their IPC socket lives inside the sandbox tree
  (`$XDG_RUNTIME_DIR/app/com.discordapp.Discord/`,
  `~/.var/app/com.discordapp.Discord/config/discord/`,
  `~/snap/discord/current/.config/discord/`), so pypresence's stock
  discovery never finds it. `_bridge_sandboxed_ipc_socket` symlinks
  the first sandbox socket it finds into `$XDG_RUNTIME_DIR` before
  each connect attempt, and sweeps stale symlinks left behind when
  the sandbox path's target is removed (Snap uninstall, Flatpak
  remove, host reboot).
- **Config drops unknown TOML keys instead of nuking the whole
  file.** A single typo or a key written by a *newer* Refrain that
  the user has since downgraded from used to make
  `Config.from_dict` raise `TypeError`, caught by the surrounding
  `except` and falling back to defaults for *every* setting.
  `_construct()` now filters the payload through dataclass fields
  before `**`-splatting; the offending keys get a single WARNING in
  the log naming the section, the rest of the section survives.
- **Stale comments + doc references cleaned up.** Four "1 Hz" call-
  outs in `mpris.py` / `daemon.py` (default has been 500 ms since
  v0.2.2), plus one reference to the AppImage runtime that no
  longer matches the AppRun layout.

### Tests

- **+13 unit tests** covering the new helpers — `DiscordRPC` payload
  dedup (4), `_bridge_sandboxed_ipc_socket` (4), `compute_idle_state`
  with `effective_duration_ms` (2), `cleanup_orphan_downloads` (3).
  Total: 113 → 125, all green, ruff + bandit + pip-audit clean,
  Dependabot 0 outstanding alerts.

## [0.2.3] - 2026-05-07

A reliability + portability pass driven by hands-on testing across
Ubuntu 25.04, CentOS Stream 10, and the existing CachyOS daily driver.
No breaking changes — same config, same UI, same Python floor; the
release just makes Refrain behave correctly on more distros and tightens
a handful of long-standing rough edges.

### Added

- **Sandboxed Discord IPC bridge** — Snap and Flatpak Discord builds
  publish their `discord-ipc-N` socket inside the sandbox tree instead
  of `$XDG_RUNTIME_DIR`, so pypresence's stock discovery never finds it.
  `_bridge_sandboxed_ipc_socket` probes the three known sandbox paths
  (Flatpak instance dir, Flatpak config-dir layout, Snap path) and
  symlinks the first hit into `$XDG_RUNTIME_DIR` before each connect
  attempt. The welcome-dialog probe walks the same paths so first-run
  diagnostics turn green on those installs.
- **Cancel button in the update dialog** — `_apply_appimage` now
  reads in 64 KiB chunks and polls a cancellation callback between
  blocks. The dialog repurposes its "Later" button as "Cancel" while a
  download is running, treats window-close as cancel, and removes the
  partial `.AppImage.new` on abort. `UpdateResult.cancelled` lets the
  dialog suppress the failure popup for user-initiated aborts.
- **Orphan-download self-heal** — `cleanup_orphan_downloads` runs once
  at startup so a `*.AppImage.new` left behind by a SIGKILL or power
  loss mid-download doesn't sit next to the binary forever.
- **Browser hints expanded** — Floorp, Waterfox, Mullvad Browser, Tor
  Browser, and ungoogled-chromium are now in `DEFAULT_BROWSER_HINTS`
  and surface as Settings checkboxes. Existing configs keep their
  saved list (no auto-migration); the new entries appear unticked
  until the user toggles them.
- **iTunes-catalog duration as MPRIS override** — when MPRIS reports
  an obviously-wrong `mpris:length` (Apple Music's preview-clip 14 s,
  the playlist-total instead of the track length, or a stale value
  carried over from the previous track), `pick_effective_duration_ms`
  prefers the iTunes value if the two disagree by more than 15 % or
  if MPRIS sits in the preview-clip band on a song iTunes considers
  full-length. 8 new unit tests in `test_timing.py`. Discord no
  longer briefly flips a 2:11 song's total to 0:14 / 7:21 mid-track.
- **`docs/test-matrix.md`** — eight Tier-1 distros that together
  cover ≈ 95 % of the realistic user base, plus Tier-2 spot-checks,
  Tier-3 desktop / compositor combinations, and a six-step smoke
  check ("tray + theme parity", "settings round-trip", "MPRIS to
  Discord", "Bluetooth", "update-dialog cleanup", "restart cycle")
  per row.

### Changed

- **Qt style plugin path augmentation also walks Debian / Ubuntu and
  Fedora / RHEL / openSUSE layouts** — the v0.2.2 fix only checked
  `/usr/lib/qt6/plugins` (Arch). pip / pipx installs on those distros
  now also pick up the system Breeze plugin instead of falling back
  to Fusion. Detection picks the first existing path among
  `/usr/lib/<arch-triple>/qt6/plugins`, `/usr/lib64/qt6/plugins`,
  `/usr/lib/qt6/plugins`.
- **"No system tray" error** rewritten to cover GNOME, MATE, XFCE,
  Hyprland / Sway / i3 / river, plus a "should work, try logging
  out" hint for Plasma / Cinnamon / LXQt / Budgie. Previously the
  fix advice was GNOME-AppIndicator-only, leaving everyone else to
  guess.
- **`refrain --install-desktop`** rewrites the installed
  `Exec=` line to point at the actual launcher path of the running
  install. Previously the bundled `Exec=refrain` only worked when
  `refrain` was on `$PATH` (distro packages, pipx); AppImage and
  source-checkout users got a broken menu entry. Resolution order
  matches the autostart logic — extracted into the new
  `autostart.resolve_exec_line(extra_args)` helper.
- **MPRIS source duration handling decouples drift-resync from the
  payload's preview-clip flag** — the drift-skip still keys off the
  raw MPRIS-reported duration (because the position-field looping is
  a property of MPRIS' preview-clip mode), but the Discord activity
  payload's start/end fields key off the *effective* duration so a
  brief MPRIS preview-clip glitch on a full-length song doesn't kill
  the progress bar.
- **PyGObject "not installed" warning fires once per startup**
  instead of twice (eager init from `app.py` plus lazy fallback in
  `MPRISServer.start` both used to log). The install hint also lists
  the Fedora / RHEL / openSUSE package name (`python3-gobject`)
  alongside the Arch and Debian ones.

### Fixed

- **Settings / Log / Update windows now carry the Refrain icon
  explicitly** — on GNOME Wayland the title-based heuristic was
  matching "Settings" against `org.gnome.Settings` and rendering the
  gnome-control-center gear icon in the dock. The WelcomeDialog
  already set its own icon; the other three top-level windows did
  not.
- **Bluetooth fast-fail when `org.bluez` is not present** — every
  poll cycle on a VM / minimal install previously triggered a
  `service_start_timeout=25000ms` D-Bus activation timeout, blocking
  the worker thread for the full 25 s. `NameHasOwner` returns
  instantly without triggering activation; the three entry points
  (`read`, `_call_method`, `list_paired_devices`) now short-circuit
  when the owner check returns False.
- **Logging gaps closed in six places** — `setup_logging` runs
  before `Config.load` so the "created default config" /
  "config unreadable" messages reach the file log; toggling the log
  level in Settings now applies to the running root logger via
  `_apply_log_level` on `settings.applied`; the "no system tray"
  early-exit gets a `log.error()`; `os.execvp`-based restarts call
  `logging.shutdown()` first so the file handler's last buffered
  line isn't lost; `Config.load` exception path adds `exc_info=True`;
  `setup_logging` degrades gracefully when the XDG state dir or log
  file can't be opened (console handler always attaches first).
- **Catch-all exception branches in `DiscordRPC._ensure_connected`,
  `MPRISSource._call_method_on`, and `BluetoothSource._call_method`**
  now use `log.exception()` so the traceback lands in `refrain.log`
  for unexpected errors. Previously they only logged `str(exc)`,
  losing the stack exactly when it would matter most.
- **Qt-internal log records routed through `refrain.qt`** instead
  of the bare `qt` logger, so they parent under the project
  namespace and show up alongside everything else when the user
  filters by name.

## [0.2.2] - 2026-05-06

Two roadmap features pulled forward from v0.3 plus a substantial batch
of reliability + UX work driven by live-testing the v0.2.1 build.

### Added

- **Multiple Discord profiles** — per-source `client_id_mpris` and
  `client_id_bluetooth` fields in `Config.discord` plus matching UI in
  Settings → General → Discord. Empty falls back to the default
  Client ID. The daemon reconnects RPC under the source-specific ID
  the moment the active source flips, so each source can render with
  its own application name + uploaded artwork in the user's profile.
- **MPRIS-server mode** — Refrain registers itself as
  `org.mpris.MediaPlayer2.refrain` on the session bus so KDE Plasma's
  panel media-controls applet (and KDE Connect, GNOME Shell, Mako
  notifications, …) drive the same Play/Pause/Next/Previous as the
  tray, and render the same track Discord renders. Implemented with
  `dbus-python` + a daemon GLib main loop running in its own thread
  so it doesn't compete with Qt's event loop. Falls back gracefully
  when PyGObject isn't installed.
- **iTunes track duration in the cover-fetcher cache** —
  `trackTimeMillis` from the iTunes Search API rides along with
  `cover_url` / `song_url` in the on-disk cache and exposed via
  `CoverFetcher.get_duration_ms()`. Used as a defensive fallback when
  MPRIS reports an obviously-wrong preview-clip length.
- **Per-player MPRIS timeout blacklist** — if a property `Get` times
  out we banish that bus name for 5 s so a hung player (typically
  plasma-browser-integration on a frozen Apple Music tab) can't keep
  eating the daemon's poll cycle one tick at a time.

### Changed

- **Welcome wizard** redesigned: 56×56 icon-badge + title + subtitle
  header, intro line about tray-driven controls, dedicated diagnostics
  card with two clearly-labelled rows (Discord / iTunes), Discord
  Application ID block with helper text + readable italic styling
  (the previous `palette(mid)` tone went invisible on Plasma Breeze
  dark themes), Skip ghost-style on the left + Apply primary on the
  right.
- **Welcome wizard now opens Settings on confirmation** instead of
  silently dropping into the tray, and reloads the Settings form so
  the just-saved Discord Client ID appears in the input field.
- **`compute_rpc_start_ts` accepts `is_preview_clip=True`** to skip
  the drift-resync path on tracks whose MPRIS Position field loops
  0→8s instead of advancing monotonically. Discord's elapsed counter
  no longer resets every ~8 s on preview-mode playback.
- **Reset-all-settings preserves every Discord Client ID** (default +
  per-source mpris + per-source bluetooth) and the dialog text now
  states this explicitly. Reset-dialog button labels are localised
  (`Reset` / `Zurücksetzen` / `Cancel` / `Abbrechen`) instead of the
  generic Yes / No.
- **Default `notify_delay_ms` lowered from 600 ms to 0 ms** — cover
  cache hits fire instantly, cache misses still get the existing
  retry loop up to 2 s for the iTunes download.
- **Default `poll_interval_ms` lowered from 1 s to 500 ms** — track
  changes, position updates, and tray controls feel noticeably more
  responsive without measurable CPU impact.
- **Idle detection skips preview-clip-length tracks** (duration <
  30 s) — Apple Music keeps reporting the same metadata while a
  preview replays, and clearing Discord under those conditions hid
  the activity mid-listen.
- **Idle-detection log fires once per dangling-track instance**
  instead of every poll tick.

### Fixed

- **Discord RPC connect now happens within ~1 s of the first track
  detection** (was: up to 51 s). Root cause: dbus-python's default
  25 s reply timeout combined with plasma-browser-integration
  introspection hangs blocked the daemon's poll. Fix: pass
  `introspect=False` on every `bus.get_object` and `timeout=0.5` on
  every property `Get` and method dispatch.
- **Discord RPC reconnects eagerly when the user changes their
  Application ID** (welcome wizard or Settings → General). Previously
  the new pipe wasn't established until the next *playing-state*
  poll, so paused users sat there waiting forever.
- **Skip / Previous reach Apple Music reliably**: dispatch tries
  the browser-native MPRIS player (chromium / firefox) first, then
  falls back to plasma-browser-integration. Plasma's wrapper claims
  `CanGoNext=True` on Apple Music but its Next routes through a
  flaky mediaSession path; the browser-native player reaches the
  same handler via a more reliable path.
- **Per-property `_safe_get` inside MPRIS read** — chromium MPRIS
  rejecting one optional property (notably `DesktopEntry`, with a
  generic `Error.Failed`) no longer drops the whole player from our
  candidate list, which previously hid the chromium player entirely
  from the skip-fallback chain.
- **All apple-music candidates** beyond the metadata winner join the
  control-fallback list, so the chromium player is reachable for
  skip dispatches even when plasma wins the metadata pick.
- **Notification fires immediately on cover-cache hits** (50 ms vs.
  the previous unconditional 600-1500 ms `notify_delay_ms` wait).
- **Discord progress bar dropped entirely on preview-clip mode**
  (MPRIS duration < 30 s) — no `start`, no `end` in the activity
  payload — so the elapsed timer doesn't loop nonsensically with
  the 8-second preview.
- **`update_config` invocation from the welcome wizard** now goes
  through `settings.applied.emit(config)` (already QueuedConnection-
  wired to the worker thread) instead of `QMetaObject.invokeMethod
  (… Q_ARG(object, …))`, which PySide6 rejects with "Unable to find
  a QMetaType for 'object'" — that exception was silently breaking
  the post-wizard handshake on first-run.
- **Welcome wizard X / Esc** marks `first_run_complete=True` (via
  `reject()` override) so the wizard doesn't re-appear on the next
  launch when the user dismisses without confirming.
- **Welcome wizard Apply with empty ID** asks for confirmation
  before silently saving "no Discord".
- **Refrain's own published MPRIS bus name skipped** during the
  source read loop — reading our own publish path back was
  circular and added noticeable latency.
- **`_control` exception handling** — a TypeError in
  `getattr(src, action)()` no longer kills the entire dispatch
  before the follow-up polls fire.
- **Reset preserves per-source Discord IDs** (`client_id_mpris` /
  `client_id_bluetooth`), not just the default `client_id`.
- **dbus.proxies / dbus.connection log spam silenced** — their
  Introspect-error tracebacks against unrelated MPRIS players
  drowned the live log in noise that has nothing to do with
  refrain.

### Removed

- **Dead `_diag_label` shim** in `WelcomeDialog` that was only there
  for a backwards-compat path no longer in use.

## [0.2.1] - 2026-05-06

> **Note:** v0.2.1 was never tagged or published as a separate
> release. The changes documented below were rolled into the
> subsequent v0.2.2 release. Kept here for changelog continuity —
> see [v0.2.2](#022---2026-05-06) for the actually-shipped delta.

Polish release built around the v0.2.0 surface. The Settings window got
a UI overhaul, two notification cover-art races are gone, and the
external code-review punch list landed in full (atomic config writes,
AppImage size validation, `.ts` source files no longer ship in the
wheel, dead D-Bus watcher module deleted).

### Added

- **Auto-restart on Discord client_id change** — Apply now triggers an
  in-process re-exec when the client_id field has changed, same
  pattern as the language switch. pypresence binds to the client_id
  at connect time, so a fresh process is the simplest way to re-init
  cleanly.
- **AppImage update size validation** — `_apply_appimage` now compares
  the downloaded file against the GitHub Releases manifest's
  `appimage_size` before replacing the live binary. A truncated
  download is rejected and the tmp file cleaned up instead of
  bricking the next launch.
- **MPRIS dispatch logging** — `Next` / `Previous` / `PlayPause` log
  which player actually received the call (INFO level). Diagnoses
  "Next pauses instead of skipping" type bugs where a fallback
  player handles the action wrong.
- **RPC track-change diagnostics** — log raw `pos`, `dur`, `start_ts`
  on every recompute so duration mismatches (browser MPRIS reporting
  preview-clip lengths, etc.) are visible in the live log.

### Changed

- **Settings window UI overhaul** — every tab uses a consistent
  `QGroupBox` + form-layout with left-aligned titles via stylesheet
  (overrides Plasma Breeze's centered default). Inputs are
  fixed-width: 220 px default, 360 px for the Discord group whose
  Client ID placeholder + privacy-mode labels need more room.
  `FieldsStayAtSizeHint` + per-widget `setFixedWidth` is the only
  combo that holds across both Fusion (offscreen tests) and Breeze
  (Plasma) — `AllNonFixedFieldsGrow` was ignoring fixed-width caps.
- **Discord RPC reconnect cap lowered to 15 s** — autostart launches
  refrain before Discord is ready; the previous 60 s ceiling meant
  the user could sit there for almost a minute after Discord
  finished loading before refrain noticed and connected.
- **Atomic config writes** — `Config.save` now writes to a `.tmp`
  sibling and `os.replace`s into place. A crash mid-write would
  otherwise leave an empty / half-written TOML and refrain
  silently falls back to defaults on parse error, losing every
  setting the user picked.
- **Discord client_id Apply re-init** — wired `restartRequested`
  emission so picking up a new client_id no longer requires a
  manual restart.

### Fixed

- **Notification cover flicker** — `notify-send -i` now carries the
  same image as the `--hint string:image-path:...` payload. KDE
  Plasma briefly rendered the `-i refrain` brand badge (~50–100 ms)
  while it loaded the image-path file from disk; using the same
  file for both makes the transition invisible.
- **Discord activity-card cover flicker** — defer the first RPC
  update for a new track until the iTunes-search cover URL is in
  cache, capped at 3 polls (~3 s). Without this, Discord briefly
  rendered the `refrain` brand fallback before the real cover
  paint, exactly the issue cover-fetcher was supposed to fix.
- **Group titles centered on Plasma Breeze** — explicit stylesheet
  overrides Breeze's default centered `QGroupBox::title`.

### Removed

- **Dead `dbus_watcher` module** — 155 lines of `MPRISWatcher` /
  `BluetoothWatcher` machinery that was never wired up after the
  signal-driven path was abandoned for v0.2.x. The companion
  `_refresh_watchers` and `_on_external_change` slots in
  `Daemon` went with it.
- **`.ts` translation sources from the wheel** — only the compiled
  `.qm` files ship now. Linguist sources stay in the source tree
  for translator PRs.

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

[Unreleased]: https://github.com/Rockykln/refrain/compare/v0.2.7...HEAD
[0.2.7]: https://github.com/Rockykln/refrain/compare/v0.2.6...v0.2.7
[0.2.6]: https://github.com/Rockykln/refrain/compare/v0.2.5...v0.2.6
[0.2.5]: https://github.com/Rockykln/refrain/compare/v0.2.4...v0.2.5
[0.2.4]: https://github.com/Rockykln/refrain/compare/v0.2.3...v0.2.4
[0.2.3]: https://github.com/Rockykln/refrain/compare/v0.2.2...v0.2.3
[0.2.2]: https://github.com/Rockykln/refrain/compare/v0.2.1...v0.2.2
[0.2.1]: https://github.com/Rockykln/refrain/commit/6b339ba
[0.2.0]: https://github.com/Rockykln/refrain/compare/v0.1.5...v0.2.0
[0.1.5]: https://github.com/Rockykln/refrain/compare/v0.1.4...v0.1.5
[0.1.4]: https://github.com/Rockykln/refrain/compare/v0.1.3...v0.1.4
[0.1.3]: https://github.com/Rockykln/refrain/compare/v0.1.2...v0.1.3
[0.1.2]: https://github.com/Rockykln/refrain/compare/v0.1.1...v0.1.2
[0.1.1]: https://github.com/Rockykln/refrain/compare/v0.1.0...v0.1.1
[0.1.0]: https://github.com/Rockykln/refrain/releases/tag/v0.1.0
