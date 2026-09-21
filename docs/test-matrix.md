# Test matrix

The set of distros + desktop combinations Refrain is expected to work on,
and a six-step smoke test to run on each before declaring a release good
to ship. Tick boxes as you verify; leave a date + Refrain version in the
"Last verified" column.

## How to use this document

For each row in the matrix:

1. Run through the six checks under [Smoke checks](#smoke-checks).
2. If everything passes, tick the row's checkbox and write the date +
   Refrain version into "Last verified".
3. If something fails, file an issue and leave the row unticked. Note
   the failing check in "Notes".

A row failing one minor optional check (e.g. notifications missing
because `notify-send` isn't installed) is fine — that's documented
graceful-degradation, not a regression.

## Tier 1 — primary support, must pass every release

These are the eight systems that together cover ≈ 95 % of the realistic
user base. CachyOS / KDE Plasma 6 / Wayland is the maintainer's daily
driver and should be tested on every commit; the rest before each tag.

| ✓ | Distro | Desktop | Display server | Channel | Last verified | Notes |
|---|---|---|---|---|---|---|
| ✓ | **CachyOS** (rolling) | KDE Plasma 6 | Wayland | AUR `refrain` | 2026-09-18 / 0.5.3 (dev) | maintainer daily |
| [ ] | **Arch Linux** (rolling) | KDE Plasma 6 | X11 | AUR `refrain` | | alternate display server |
| [ ] | **Fedora 42 Workstation** | GNOME 47 | Wayland | AppImage + PyPI | | RPM world + GNOME tray ext. 2026-09-18 / 0.5.3 (dev): in a distrobox container on a Plasma 6 host — install, start, tray, Apple Music in Chrome; the distro's own desktop not tested. |
| [ ] | **Ubuntu 24.04 LTS** | GNOME 46 | Wayland | AppImage | | LTS; needs `libfuse2t64` for the AppImage. 2026-09-18 / 0.5.3 (dev): in a distrobox container on a Plasma 6 host — install, start, tray, Apple Music in Chrome; the distro's own desktop not tested. |
| [ ] | **Ubuntu 25.04** | GNOME 48 | Wayland | PyPI | | Failed once, 2026-05-07 against v0.2.2, for three reasons that have since been fixed: the settings window wore the gnome-control-center icon (`1e759ba`), and a missing bluez cost 25 s per poll (`NameHasOwner` fast-fail). What remains is distro-external: snap-sandboxed Firefox and Chrome publish no MPRIS, so Refrain sees no player — use a browser from a deb channel (Brave, Mozilla's apt repo). Not re-tested since; the row is open, not failed. |
| [ ] | **Debian 13** (Trixie) | KDE Plasma 6 | Wayland | AppImage | | Plasma outside Arch; needs `libfuse2t64` for the AppImage. 2026-09-18 / 0.5.3 (dev): in a distrobox container on a Plasma 6 host — install, start, tray, Apple Music in Chrome; the distro's own desktop not tested. |
| ✓ | **Debian 13** (Trixie) | GNOME 48 | Wayland | PyPI (pipx) | 2026-09-20 / v0.5.3 | Install, menu entry, tray with the AppIndicator extension, Discord, Firefox, cover, Recently played, developer mode. Without a tray Refrain stops without a word when started from the menu (fix planned). |
| [ ] | **openSUSE Tumbleweed** | KDE Plasma 6 | Wayland | PyPI in venv | | rolling non-Arch; pip builds dbus-python here (see README). 2026-09-18 / 0.5.3 (dev): in a distrobox container on a Plasma 6 host — install, start, tray, Apple Music in Chrome; the distro's own desktop not tested. |
| ✓ | **Linux Mint 22.3** | Cinnamon 6 | X11 | PyPI (pipx) | 2026-09-21 / v0.5.3 | Needs `libxcb-cursor0` under X11 — Refrain names it and the command. Cinnamon's own tray works. Vivaldi recognised with the Plasma Browser Integration (see Players). The AppImage is untested here. |
| [ ] | **Manjaro** Stable | KDE Plasma 6 | Wayland | AUR `refrain` | | delayed Arch mirror |

## Tier 2 — supported, spot-check

Less frequent verification — once per minor release is enough.

| ✓ | Distro | Desktop | Channel | Notes |
|---|---|---|---|---|
| [ ] | **EndeavourOS** | KDE | AUR `refrain` | Arch derivative |
| [ ] | **Garuda Linux** | KDE | AUR `refrain` | Arch derivative |
| [ ] | **Pop!_OS 24.04** | GNOME (or COSMIC) | AppImage | COSMIC tray TBD |
| [ ] | **KDE Neon** (User) | KDE Plasma 6 | PyPI / AppImage | Plasma testing target |
| [ ] | **Ubuntu 25.04** | GNOME | PyPI in venv | Python 3.13 |
| [ ] | **Debian 12** (Bookworm) | KDE / GNOME | AppImage | Python 3.11, Qt 5 — AppImage required. 2026-09-18 / 0.5.3 (dev): AppImage starts in a container |
| [ ] | **Fedora 41 KDE Spin** | KDE Plasma 6 | Wayland | AppImage / PyPI | Fedora KDE flavour |
| [ ] | **NixOS** unstable | KDE / GNOME | PyPI in nix-shell | flake.nix would be nice-to-have |

## Tier 3 — desktop / compositor edge cases

Not full distro entries — just verify the tray + tray icon rendering
on these specifically.

| ✓ | Compositor / DE | Tray status | Notes |
|---|---|---|---|
| [ ] | KDE Plasma 6 — Wayland | native ✓ | primary target |
| [ ] | KDE Plasma 6 — X11 | native ✓ | |
| [ ] | GNOME 45+ | needs [AppIndicator extension](https://extensions.gnome.org/extension/615/appindicator-support/) | refuses start without it |
| [ ] | XFCE 4.18+ | OK with `xfce4-statusnotifier-plugin` | |
| [ ] | Cinnamon 6 | native ✓ | |
| [ ] | LXQt 2 | native ✓ | |
| [ ] | Budgie | OK via AppIndicator | |
| [ ] | MATE | needs `mate-applet-statusnotifier` | |
| [ ] | Hyprland / Sway / i3 | needs SNI-capable bar (waybar `tray` module, polybar) | |
| [ ] | Pantheon (elementary) | unofficial SNI support | untested |
| [ ] | COSMIC (Pop!_OS 24.04+) | own protocol | untested |

## Players

What plays the music matters as much as the desktop: each reports its
track, position and length differently.

| ✓ | Player | Reports through | Last verified | Notes |
|---|---|---|---|---|
| ✓ | Apple Music in **Chromium** | plasma-browser-integration + the tab's own MPRIS entry | 2026-09-18 / v0.5.2 | segment lengths; the tab's entry decides playing vs. paused |
| ✓ | Apple Music in **Firefox** | Firefox's own MPRIS (no Plasma extension) | 2026-09-20 / v0.5.3 | on KDE no length reported, Firefox ESR 140 on Debian 13 reports one; position in whole seconds |
| ✓ | Apple Music in **Google Chrome** | plasma-browser-integration + the tab's own MPRIS entry | 2026-09-18 / v0.5.2 | as Chromium |
| ✓ | Apple Music in **Brave** | plasma-browser-integration + Brave's own MPRIS entry | 2026-09-18 / 0.5.3 (dev) | as Chrome |
| ✓ | Apple Music in **Zen** | Zen's own MPRIS | 2026-09-18 / v0.5.2 | as Firefox; position to the second |
| ✓ | Apple Music in **Vivaldi** | Vivaldi's own MPRIS + the Plasma Browser Integration | 2026-09-21 / v0.5.3 | on Linux Mint 22.3 / Cinnamon. Without the integration Vivaldi reports no page and Refrain doesn't recognise it — the same goes for every Chromium-based browser outside KDE; the integration works outside Plasma too. Its own length grows with the buffer. |
| ✓ | **Tablet** over Bluetooth (AVRCP) | BlueZ `MediaPlayer1` | 2026-09-18 / v0.5.2 | names the playing app; Twitch is left out |
| ✓ | **Phone** over Bluetooth (AVRCP), tablet connected too | BlueZ `MediaPlayer1` | 2026-09-18 / 0.5.3 (dev) | the playing device is picked; position to the second |

## Out of scope (won't work)

These are documented as **unsupported** — don't open issues for them.

| Distro | Reason |
|---|---|
| Debian 11 (Bullseye) | Python 3.9 — too old |
| Ubuntu 20.04 LTS | Python 3.8 too old |
| Ubuntu 22.04 LTS (PyPI) | Python 3.10 too old — the AppImage (0.5.3+) runs, tested in a container 2026-09-18 |
| Linux Mint 21.x | Python 3.10 — too old |
| **CentOS Stream 10** | Tested 2026-05-07 against v0.2.2 — failed, and only one of the two reasons is gone: the AppImage's `No module named refrain` was the bug fixed in 0.5.3, but a stock GNOME here still ships no AppIndicator package, so Refrain refuses to start with "No system tray" until one is installed by hand. Mounted mode also needs `fuse`, not just `fuse-libs`. Not worth Tier 1 or 2. |
| CentOS Stream 9 | glibc 2.34 < 2.35 (AppImage breaks); Python 3.9 default |
| RHEL 9 / Rocky 9 / AlmaLinux 9 | same family as CentOS Stream 9 |
| RHEL 8 / Rocky 8 / AlmaLinux 8 | glibc 2.28; Python 3.6 default |
| Alpine Linux | musl libc — PySide6 wheels are glibc-only |
| openSUSE Leap 15.5 | Python 3.6 default |

## Smoke checks

Run all seven on every Tier 1 row, in order. Each should take under a
minute; the whole sweep is ≈ 6 minutes per system.

### 1. Tray + theme parity

- Refrain icon appears in the system tray on launch.
- Icon style matches the system theme (light glyph on dark panel,
  dark glyph on light panel).
- Settings window styling matches the rest of the desktop (Breeze
  on KDE, Adwaita-via-Qt on GNOME, etc.). The pip / pipx / AppImage
  builds should look the same as the AUR / system build because of
  the Qt-plugin-path augmentation in `app.py`.

### 2. Settings round-trip

- Tray icon → Status window → *Settings…*, or *Settings…* in the tray menu, opens the window.
- Toggle any setting (e.g. *Notifications*), hit *Apply*.
- Window stays open, *Apply* greys out again, setting persists in
  `~/.config/refrain/config.toml`; *OK* saves and closes.
- No console errors, no Qt crashes.

### 3. MPRIS / Apple Music — Discord push

- Open `https://music.apple.com` in any major Linux browser.
- Start playing a track.
- Within ≈ 1 s, the tray menu shows the track title + artist.
- Within ≈ 2 s, Discord status shows "Listening to ..." with the
  cover art.
- **Duration check**: progress bar in Discord shows the *correct*
  total length for the track (e.g. a 2:11 song shows 2:11, not 0:14
  or 7:21).

### 4. Bluetooth source (optional, hardware-dependent)

Skip if no AVRCP-capable Bluetooth source is available. Otherwise:

- Pair + connect a phone playing music via Bluetooth.
- Tray menu shows the track within ≈ 1 s.
- Discord status pushes the metadata.
- Per-source `client_id_bluetooth` (if set) routes to the right
  Discord application.

### 5. Update dialog cleanup

Triggered automatically when a newer version exists; or set
`update.last_check_ts = 0` in the config and restart.

- Update dialog opens with a working *Cancel* button while
  downloading.
- Hitting Cancel mid-download removes the `*.AppImage.new` file
  next to the binary (only relevant for AppImage installs).
- Closing the dialog window via the X button while downloading
  also cancels cleanly.
- After cancel, no orphan `.new` file is left behind.

### 6. Restart cycle

- Tray → *Restart Refrain*.
- The process exits cleanly, releases the D-Bus name
  `io.github.Rockykln.Refrain`, and re-execs itself.
- Tray icon disappears and reappears within ≈ 2 s.
- Discord RPC reconnects and the same track shows up again.
- `refrain.log` has a `Re-execing for restart` line followed by a
  fresh `Refrain ... starting` line — no duplicate D-Bus name
  errors.

### 7. Recently played

- Tray → *Recently played…* opens the window; the track playing
  shows at the top as *Now playing* with its cover.
- Skip to the next song within a few seconds: the skipped one leaves
  the list again. Play one past half its length: it stays, and
  `history.json` lists it.
- Restart Refrain (check 6) while that song is still playing: it is
  listed **once**, still as *Now playing*.
- Put that song on repeat and let it run through once more: it is
  listed **twice**, and the log says `History: … started over — a new
  play`.
- Click the song: its Apple Music page opens in the browser that is
  playing it. Right-click → *Remove from history* takes it out.
- Between two songs the row doesn't flash *Paused*.
- Pause Apple Music in the browser: within a second the tray and the
  row say *Paused*, and the elapsed time stops. Tray → *Play* starts
  the music again.
- Over Bluetooth, with the phone on repeat-one: the elapsed time starts
  again from 0:00 at every loop instead of disappearing, and each loop
  that counts is a row — and a scrobble — of its own.

## Failure-mode reference

If a check fails, the most common causes:

| Symptom | Likely cause |
|---|---|
| Refrain refuses to start with "No system tray" | Missing AppIndicator extension on GNOME, or bar with no tray on tiling WM |
| Tray icon visible but no track shown when playing in browser | `plasma-browser-integration` not enabled in browser, or `mpris_enabled = false` |
| Discord status never appears | No `client_id` set, or Discord IPC socket not reachable (Snap/Flatpak Discord can hide it) |
| Cover art missing | iTunes has no song whose artist *and* title match (the log says `Cover lookup: no catalog match for …`) — fallback brand icon shown; asked again after three days |
| Refrain gone without a trace | Look in `~/.local/state/refrain/crash.log` for the Python stack of every thread at the crash, and `coredumpctl list` |
| Settings window has no icons / wrong style on pip/pipx install | Qt plugin-path augmentation didn't fire — check log for `Augmenting Qt plugin path` |
| Duration shows as 0:14 or some too-short value | Pre-`pick_effective_duration_ms` build, or iTunes lookup hasn't resolved yet for that track |
| AppImage fails with `failed to exec fusermount` (RHEL family) | `fuse-libs` alone is not enough — install the `fuse` package too, or run with `--appimage-extract-and-run` |
