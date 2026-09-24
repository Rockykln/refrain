# Refrain

**Discord Rich Presence for Apple Music on Linux.**

[![tests](https://github.com/Rockykln/refrain/actions/workflows/tests.yml/badge.svg)](https://github.com/Rockykln/refrain/actions/workflows/tests.yml)
[![codeql](https://github.com/Rockykln/refrain/actions/workflows/codeql.yml/badge.svg)](https://github.com/Rockykln/refrain/actions/workflows/codeql.yml)
[![security](https://github.com/Rockykln/refrain/actions/workflows/security.yml/badge.svg)](https://github.com/Rockykln/refrain/actions/workflows/security.yml)
[![python](https://img.shields.io/badge/python-3.11%20%7C%203.12%20%7C%203.13-blue.svg)](#requirements)
[![license](https://img.shields.io/badge/license-Use--Only-orange.svg)](LICENSE)

Refrain shows what you're listening to on Apple Music as your Discord status
— whether the audio is playing in a browser tab on `music.apple.com` or
streaming from your phone over Bluetooth.

<p align="center">
  <picture>
    <source media="(prefers-color-scheme: light)" srcset="docs/screenshots/discord-rpc-light.png"/>
    <img src="docs/screenshots/discord-rpc.png" alt="Refrain on Discord" width="430"/>
  </picture>
</p>

## What it does

- Reads playback metadata from **MPRIS** (Apple Music in any major Linux
  browser) and **BlueZ AVRCP** (any AVRCP-capable Bluetooth source).
  Firefox and its relatives report the page they play; Chromium-based
  browsers (Chrome, Chromium, Brave, Edge, Opera, Vivaldi) only do so
  through KDE's Plasma Browser Integration — the
  `plasma-browser-integration` package plus the *Plasma Integration*
  extension, which also works outside KDE (confirmed on Cinnamon).
- Forwards track + cover art to Discord via the local IPC socket.
- Optionally **scrobbles to Last.fm** alongside Discord (opt-in, with a
  crash-safe offline queue).
- Lives in your **system tray** with Play/Pause/Next/Previous controls.
- Keeps a **recently played** list — cover, time, source — of the last
  30 songs by default, on your machine only.
- Provides a **settings window** (PySide6) for everything users typically want
  to tweak — privacy mode, sources, autostart, Bluetooth device picker.

```
        ┌─────────────────────────────────────────────────────────┐
        │   Refrain                                               │
        │                                                         │
        │   ┌──────────┐    ┌──────────┐    ┌────────────────┐    │
        │   │  MPRIS   │    │  BlueZ   │    │     Tray +     │    │
        │   │  source  │    │  AVRCP   │    │  Settings UI   │    │
        │   └────┬─────┘    └────┬─────┘    └───────┬────────┘    │
        │        │               │                  │             │
        │        ▼               ▼                  ▼             │
        │   ┌─────────────────────────────────────────────┐       │
        │   │             Background daemon               │       │
        │   └────────────────────┬────────────────────────┘       │
        │                        │                                │
        │                        ▼                                │
        │           Discord Rich Presence (IPC)                   │
        └─────────────────────────────────────────────────────────┘
```

## Requirements

- Linux with D-Bus
- **Python ≥ 3.11**
- Discord desktop client running (Refrain talks to its IPC socket)
- For Bluetooth: BlueZ with AVRCP enabled

## Install

| Channel | Install |
|---------|---------|
| **PyPI** *(any distro with Python ≥ 3.11)* | `pipx install --system-site-packages refrain` — see [below](#from-pypi) |
| **AUR** *(Arch / CachyOS / Manjaro / EndeavourOS)* | `yay -S refrain` *(stable)* or `yay -S refrain-git` *(latest main)* |
| **AppImage** *(portable single-file, any glibc-based distro)* | [Download from the latest release](https://github.com/Rockykln/refrain/releases/latest), `chmod +x`, run it |
| **From source** | See below |

The AppImage needs FUSE 2, which current distros no longer install by
default: `libfuse2t64` on Debian and Ubuntu, `fuse-libs` on Fedora,
`libfuse2` on openSUSE, `fuse2` on Arch. Without it, start the AppImage
with `--appimage-extract-and-run`.

A Flatpak manifest exists under `packaging/flatpak/` for users who want to
build it themselves; a Flathub submission is on the roadmap but not
currently active. Build files for the live channels live under
[`packaging/`](packaging/).
See [`packaging/README.md`](packaging/README.md) for build instructions.
Which versions can still be downloaded, and why the others were taken
down: [`docs/releases.md`](docs/releases.md).

### From PyPI

Current distros no longer let `pip install` into the system Python, so
install with [pipx](https://pipx.pypa.io/). dbus-python and PyGObject come
from your distro; `--system-site-packages` lets Refrain use them instead of
compiling its own:

| Distro | Command |
|--------|---------|
| Ubuntu 24.04, Linux Mint 22, Debian 13 | `sudo apt update && sudo apt install pipx python3-dbus python3-gi libxcb-cursor0` |
| Fedora 42 | `sudo dnf install pipx python3-dbus python3-gobject` |
| openSUSE Tumbleweed | `sudo zypper install python313-pipx python313-gobject gcc pkgconf dbus-1-devel glib2-devel python313-devel` |

On Debian and its relatives, `apt update` first: with an old package index
`apt install` fails with *404 Not Found* once a package has been replaced
in the archive. `libxcb-cursor0` is only needed under X11.

Then:

```sh
pipx install --system-site-packages refrain
pipx ensurepath   # once, if ~/.local/bin isn't on your PATH yet
refrain --install-desktop
```

openSUSE's dbus-python package carries no metadata pip can see, so pip
builds its own copy there — hence the compiler and headers in its line.

### From source (development)

```sh
git clone https://github.com/Rockykln/refrain.git
cd refrain

# On distros that already package Qt-for-Python and dbus-python (Arch, Fedora,
# openSUSE …) a venv with --system-site-packages avoids re-downloading them.
python -m venv --system-site-packages .venv
source .venv/bin/activate

pip install -e .
refrain
```

If your distro doesn't ship `PySide6` or `dbus-python`, plain
`python -m venv .venv` works too — pip will pull `PySide6` from PyPI and
build `dbus-python`, which needs a C compiler, `pkg-config` and the D-Bus,
GLib and Python headers (`gcc pkg-config libdbus-1-dev libglib2.0-dev
python3-dev` on Debian/Ubuntu, `gcc pkgconf dbus-devel glib2-devel
python3-devel` on Fedora). Without PyGObject, Plasma's media controls
can't reach Refrain.

### Pip-installed users: get a launcher

When installed via pipx or pip rather than a distro package, Refrain
doesn't register itself with your application menu. Run once after install:

```sh
refrain --install-desktop
```

This copies the `.desktop` file and icon to `~/.local/share/applications/`
and `~/.local/share/icons/`. To undo: `refrain --uninstall-desktop`.

### Tested on

See [`docs/test-matrix.md`](docs/test-matrix.md) for the full Tier-1 / Tier-2 list, the per-row smoke checks, and which distros are explicitly out-of-scope (Python or glibc floor too low).

- **Tier 1 (must pass every release):** CachyOS, Arch Linux, Fedora 42, Ubuntu 24.04 LTS, Debian 13, openSUSE Tumbleweed, Linux Mint 22, Manjaro Stable.
- **Desktops:** KDE Plasma 6 (Wayland is the primary target, X11 also covered), GNOME with the [AppIndicator and KStatusNotifierItem](https://extensions.gnome.org/extension/615/appindicator-support/) extension, XFCE / Cinnamon / LXQt / Budgie via their native or AppIndicator-bridged tray, MATE with `mate-applet-statusnotifier`, tiling WMs (Hyprland / Sway / i3 / river) via a SNI-capable status bar.

## First-time setup

Refrain needs a Discord Application ID to push status updates. Each user
registers their own (free, takes 30 seconds):

1. Open <https://discord.com/developers/applications> and click **New Application**.
2. Give it a name you're entitled to use — that name is what shows up
   under *"Listening to ..."* in your Discord status. You can also upload a
   square image as the application icon; Discord uses it as the
   fallback when there's no album cover.
3. Copy the **Application ID** from the *General Information* page.
4. Launch Refrain → *Settings → General → Application ID* → paste,
   *OK*.
5. In Discord: *Settings → Activity privacy → Share your detected
   activities with others*. A fresh Discord install has this switch off,
   and with it off nobody sees your status — Refrain looks connected
   either way. The Discord desktop app is required; Discord in a browser
   can't receive a status.

The first time you launch Refrain without a configured ID, the
welcome wizard pops up with the setup steps + a live diagnostics
panel that probes your D-Bus session and Discord IPC socket so you
know up front whether your environment can host the RPC at all.

<p align="center">
  <picture>
    <source media="(prefers-color-scheme: light)" srcset="docs/screenshots/welcome-light.png"/>
    <img src="docs/screenshots/welcome.png" alt="Welcome wizard" width="560"/>
  </picture>
</p>

After the wizard, the **Status window** says *You're all set*. Play a
song and it shows up in Discord within a few seconds.

## Configuration

Settings live at `$XDG_CONFIG_HOME/refrain/config.toml`
(typically `~/.config/refrain/config.toml`). The settings window edits the
same file; you almost never need to touch it by hand.

```toml
[discord]
client_id = ""                     # default Application ID — paste yours here
client_id_mpris = ""               # optional per-source override (browser / Apple Music)
client_id_bluetooth = ""           # optional per-source override (Bluetooth headphones)
all_clients = false                # send the status to every running Discord client, not just the first
resolve_app_name = false           # opt-in: ask Discord what your Application ID is called, and show it in Settings

[sources]
mpris_enabled = true
bluetooth_enabled = true
bluetooth_device = ""              # empty = auto-detect, or "AA:BB:CC:DD:EE:FF"
browser_hints = "firefox,chromium,…"  # which MPRIS players count as a browser; edit if yours isn't detected

[privacy]
mode = "full"                      # "full" | "minimal" | "off"
resume_mode = "full"               # what *Resume sharing* goes back to after *Pause sharing*

[behavior]
autostart = false
notifications = false              # new installs; a config from before 0.5.3 keeps its value
cover_art = true                   # look songs up in Apple's catalog: cover, song link, length
show_buttons = true
notify_delay_ms = 0                # 0 = fire ASAP; the cover-art retry loop still waits up to ~2 s
tray_icon = "white"                # not in Settings: "white", "black", or "auto" to follow the system colour scheme

[advanced]
poll_interval_ms = 500
log_level = "INFO"
idle_grace_s = 30                  # clear status when same track plays past duration + grace; 0 disables
position_stall_s = 4               # seconds a playing track's position may stand still before Refrain stops trusting it; 0 disables
language = "system"                # "system" follows QLocale; "cs", "de", "en", "es", "fr", "it", "ja", "ko", "nl", "pl", "pt", "ru", "sv", "tr", "uk", "zh_CN" force a translation
time_format = "system"             # not in Settings: "system" follows the desktop clock, "12h" and "24h" override it
time_zone = ""                     # not in Settings: empty follows the desktop, or an IANA name like "Europe/Berlin"
hover_scroll_ms = 1500             # rest this long on a song in the Status window and its title scrolls past once; 0 = never
developer_mode = false             # local timing + usage metrics, see docs/developer-mode.md
developer_unlocked = false         # keeps the Developer switch in Settings once unlocked

[lastfm]
enabled = false                    # opt-in, alongside (never replacing) the Discord RPC
api_key = ""                       # register your own at last.fm/api/account/create
username = ""                      # display only
scrobble_now_playing = true        # also send the ephemeral "now playing" indicator
# NOTE: the Last.fm shared secret and session key are credentials and
# are deliberately NOT stored here. They live in your OS keyring
# (KWallet / GNOME Keyring), encrypted at rest — see "Last.fm" below.

[history]
enabled = true                     # the "Recently played" list; false also deletes what it stored
max_entries = 30                   # songs kept, 1–100 (Settings offers 10, 20, 30, 50, 75, 100)
window_width = 0                   # the history window's size when last closed; 0 = default
window_height = 0

[update]
auto_check = true                  # look for a newer release shortly after start
last_check_ts = 0                  # when that last happened; Refrain keeps this current
```

Per-source `client_id_*` fields let Apple Music render under one Discord
application (with the album-grid as artwork) and Bluetooth headphones under
another (with a generic Bluetooth glyph). Empty falls back to the default
`client_id`.

## MPRIS server

Refrain publishes itself as `org.mpris.MediaPlayer2.refrain` on the session
bus, so KDE Plasma's panel media-controls applet (and KDE Connect, GNOME
Shell, Mako, …) drive the same Play/Pause/Next/Previous as the tray and
render the same track Discord renders. Plasma also offers these controls
when you right-click Refrain in the task manager, including a *Stop*
that it adds for every player — Apple Music has no stop, so there it
pauses, and does nothing when the music is already paused.

## Tray

<p align="center">
  <picture>
    <source media="(prefers-color-scheme: light)" srcset="docs/screenshots/tray-menu-light.png"/>
    <img src="docs/screenshots/tray-menu.png" alt="Tray menu" width="320"/>
  </picture>
</p>

Every item carries a theme-matched icon (freedesktop icon names on
Plasma / GNOME / Breeze; bundled accent SVGs for Update and Quit) —
no unicode-glyph prefixes.

| Item              | What it does                                              |
|-------------------|-----------------------------------------------------------|
| Title             | Currently playing track (click opens the Status window)   |
| Artist • Album    | Currently playing artist + album (hidden when idle)       |
| X:XX / Y:YY (–Z:ZZ) | Elapsed / track length / remaining (hidden when idle)   |
| Discord: …        | What Discord shows right now: *ready — waiting for music*, *visible on your profile*, *showing “Listening to music”*, *hidden while paused*, *hidden — sharing is off*, *app isn't running*, *not answering*, *not set up — add your Application ID*, *Application ID rejected — check it* |
| Last.fm: …        | *scrobbling as …*, *N scrobbles waiting*, *sign-in expired — reconnect* (hidden while Last.fm was never set up) |
| Previous          | Skip backward on the active source                        |
| Play / Pause      | Toggle on the active source (label follows playback state)|
| Next              | Skip forward on the active source                         |
| Update available — vX.Y.Z | Only visible when a newer release exists          |
| Recently played…  | Open the history window (hidden while the history is off) |
| Settings…         | Open the settings window                                  |
| Troubleshooting ▸ | *Live log…* and *Restart Refrain* (releases the D-Bus name and Discord connection, then starts the same binary again) |
| Quit Refrain      | Stop the daemon and exit                                  |

Left-click the tray icon opens the Status window — which also holds
*Pause sharing* — **middle-click toggles play/pause**,
right-click shows this menu. (DBusMenu keeps an open
menu's text static, so the progress line is a snapshot from when you
opened it — hover the tray icon for a live-updating tooltip.)

## Status window

<p align="center">
  <picture>
    <source media="(prefers-color-scheme: light)" srcset="docs/screenshots/status-light.png"/>
    <img src="docs/screenshots/status.png" alt="Status window" width="420"/>
  </picture>
</p>

A small window that answers "is it working?": the song playing now, one
line each for Discord and Last.fm in plain words, and a button only when
there is something to do (*Set up…*, *Fix…*, *Reconnect…*). Below are
player controls, the time played so far, the songs before this one,
*Pause sharing*, *Settings…* and a link to the project on GitHub. After a
crash it also carries a banner naming the report. The list holds as many
songs as the window has room for, so making the window taller shows more
and there is never a scroll bar. It updates live; a title too long for the
window scrolls past twice, and resting the mouse on a song in the list
scrolls that one once. While the window is on screen Refrain keeps quiet
and sends no notifications.

Every link that leaves Refrain — a song on Apple Music, the GitHub page,
Last.fm — asks first and names the page it is about to open.

It opens when you start Refrain from the menu, when you click the tray
icon, and when you start Refrain again while it is already running. A
start at login (`--silent`, what autostart uses) stays in the tray and
opens it only when something needs you — Discord not set up, the
Application ID rejected, the Last.fm sign-in expired — once per problem.

## Settings

The settings window opens from *Settings…* in the Status window. **Apply** saves to `config.toml` and keeps the window open,
**OK** saves and closes it, **Cancel** closes it without saving. Apply
stays greyed out until something differs from what is saved. Closing the
window with unsaved changes — Cancel, <kbd>Esc</kbd> or the window's close
button — asks first: *Save*, *Discard* or *Keep editing*.

A few things are saved the moment they happen, because they are results
rather than drafts: connecting or disconnecting Last.fm (connecting also
switches scrobbling on), *Reset all settings to defaults* after its
confirmation, and unlocking developer mode. Only a new language needs a
restart; Refrain says so and asks before it restarts.

*Privacy* on the General tab decides what is shared: *Full*, *Minimal*
(only “Listening to music”) or *Off*, which pauses both the Discord status
and Last.fm scrobbling. The recently played list keeps working either way.
Next to it, *Look up songs in Apple's catalog* sends artist and title to
Apple for the cover, the song link and the length; without it Discord shows
no cover and often no progress bar.

<table>
  <tr>
    <td align="center">
      <b>General</b><br/>
      <picture>
        <source media="(prefers-color-scheme: light)" srcset="docs/screenshots/settings-general-light.png"/>
        <img src="docs/screenshots/settings-general.png" alt="Settings — General" width="420"/>
      </picture>
      <br/><sub>Discord Application ID with the application's name beside it, privacy and the Apple catalog lookup, autostart, notifications</sub>
    </td>
    <td align="center">
      <b>Sources</b><br/>
      <picture>
        <source media="(prefers-color-scheme: light)" srcset="docs/screenshots/settings-sources-light.png"/>
        <img src="docs/screenshots/settings-sources.png" alt="Settings — Sources" width="420"/>
      </picture>
      <br/><sub>MPRIS / Bluetooth toggles + paired-device picker</sub>
    </td>
  </tr>
  <tr>
    <td align="center">
      <b>Last.fm</b><br/>
      <picture>
        <source media="(prefers-color-scheme: light)" srcset="docs/screenshots/settings-lastfm-light.png"/>
        <img src="docs/screenshots/settings-lastfm.png" alt="Settings — Last.fm" width="420"/>
      </picture>
      <br/><sub>Opt-in scrobbling, API key + secret, connect / disconnect an account</sub>
    </td>
    <td align="center">
      <b>Recently played</b><br/>
      <picture>
        <source media="(prefers-color-scheme: light)" srcset="docs/screenshots/settings-history-light.png"/>
        <img src="docs/screenshots/settings-history.png" alt="Settings — Recently played" width="420"/>
      </picture>
      <br/><sub>Recently played on or off, how many songs to keep</sub>
    </td>
  </tr>
  <tr>
    <td align="center">
      <b>Updates</b><br/>
      <picture>
        <source media="(prefers-color-scheme: light)" srcset="docs/screenshots/settings-updates-light.png"/>
        <img src="docs/screenshots/settings-updates.png" alt="Settings — Updates" width="420"/>
      </picture>
      <br/><sub>Auto-check, last-checked, manual <i>Check for updates now</i></sub>
    </td>
    <td align="center">
      <b>Advanced</b><br/>
      <picture>
        <source media="(prefers-color-scheme: light)" srcset="docs/screenshots/settings-advanced-light.png"/>
        <img src="docs/screenshots/settings-advanced.png" alt="Settings — Advanced" width="420"/>
      </picture>
      <br/><sub>Poll interval, notification delay, language, log level, restart, reset, uninstall</sub>
    </td>
  </tr>
  <tr>
    <td align="center">
      <b>Legal</b><br/>
      <picture>
        <source media="(prefers-color-scheme: light)" srcset="docs/screenshots/legal-light.png"/>
        <img src="docs/screenshots/legal.png" alt="Legal notice" width="420"/>
      </picture>
      <br/><sub>License, trademark and affiliation notices — the <i>Legal</i> button in the footer</sub>
    </td>
    <td></td>
  </tr>
</table>

## Recently played

<p align="center">
  <picture>
    <source media="(prefers-color-scheme: light)" srcset="docs/screenshots/history-light.png"/>
    <img src="docs/screenshots/history.png" alt="Recently played" width="520"/>
  </picture>
</p>

*Tray → Recently played…* lists the last songs Refrain saw — 30 by
default, up to 100 — with cover, length, when each started, where it
came from (which browser, which Bluetooth device) and whether it went to
Last.fm. A song shows up the moment it plays and stays once it counts
as listened: half its length or four minutes, the rule Last.fm uses, so
skipping through a playlist leaves nothing behind. Restarting Refrain
mid-song doesn't lose or duplicate it; a song heard through and played
again is listed twice, once per play.

- Click a song to open it in Apple Music — in the browser it played in,
  while that one is still open; a search when the song's page isn't
  known.
- Right-click to copy artist and title or to remove the song from the
  list; *Clear history…* empties it.
- From ten songs on there's a search over title, artist and album —
  blind to case and accents, found words marked — and with more than
  one source a filter by source. <kbd>Ctrl</kbd>+<kbd>F</kbd> jumps into
  the search, <kbd>Esc</kbd> clears it.
- The window opens at the size you last left it.

The list stays on your machine (`history.json`, readable only by you)
and is never sent anywhere, so the privacy mode doesn't affect it.
*Settings → Recently played* turns it off — which deletes the file — or changes
how many songs it keeps.

## Notifications

Refrain can show a desktop notification on each track change, with the
album cover, song title, artist and album — the same data that's going to
your Discord status. It is off for new installs; turn it on under
*Settings → General → Behavior*. Configs from before 0.5.3 keep the setting
they had.

<p align="center">
  <picture>
    <source media="(prefers-color-scheme: light)" srcset="docs/screenshots/notification-light.png"/>
    <img src="docs/screenshots/notification.png" alt="Track-change notification" width="520"/>
  </picture>
</p>

## Updates

Refrain checks the [GitHub Releases API](https://api.github.com/repos/Rockykln/refrain/releases/latest)
on startup, at most once per day (*Settings → Updates* has the switch and
a *Check for updates now* button). When a newer version exists, Refrain
opens this dialog, and the tray menu shows an *Update available* item
that brings it back:

<p align="center">
  <picture>
    <source media="(prefers-color-scheme: light)" srcset="docs/screenshots/update-dialog-light.png"/>
    <img src="docs/screenshots/update-dialog.png" alt="Update-available dialog" width="560"/>
  </picture>
</p>

What the update does depends on how Refrain was installed:

- **AppImage** — downloads the new `.AppImage`, checks it against the
  release's signed checksums and replaces the running file, then asks
  you to restart.
- **pipx** — runs `pipx upgrade refrain`.
- **pip / venv** — runs `pip install --upgrade refrain`.
- **AUR** — opens a terminal running your AUR helper
  (`yay -Syu refrain` or similar), so the package manager stays in
  charge and you confirm sudo yourself. Other distro packages get a
  hint to use the package manager.
- **Source checkout** — never updated in place; `git pull` and
  `pip install -e .` yourself.

There is no published Flatpak; a self-built one gets
`flatpak update` in a terminal the same way. Details in the
[FAQ](docs/faq.md#how-do-i-update).

## Last.fm scrobbling

Refrain can scrobble to [Last.fm](https://www.last.fm) **alongside** the
Discord status — a second, independent channel, never a replacement.
It's **opt-in** and off by default.

Register your own free [API account](https://www.last.fm/api/account/create),
then open the *Settings → Last.fm* tab: tick **Enable**, paste the
**API key** + **shared secret**, click **Connect…** and approve the
browser prompt. Scrobbling starts on the next track — no restart.

The **shared secret and session token are stored in your OS keyring**
(KWallet / GNOME Keyring), encrypted at rest — never in `config.toml`
(which is itself written owner-only, `0600`). On a system with no
keyring they fall back to a `0600` file. Credentials only ever leave
the machine to Last.fm over HTTPS, which is what scrobbling *is*.

A track is scrobbled once you've played at least half of it, or four
minutes (Last.fm's rule), and only if it's longer than 30 s. Scrobbles
are queued to disk the instant they qualify, so being offline, a
Last.fm outage, or quitting mid-song never loses them — they submit on
the next opportunity. Restarting Refrain mid-song carries the play on
instead of counting it again, and a song on repeat is scrobbled once per
play. Privacy mode `Off` silences scrobbling too.

Full walkthrough + troubleshooting: [`docs/lastfm.md`](docs/lastfm.md).

## Uninstalling

Refrain can wipe everything it ever wrote — on any distro, any
install method — with one command:

```sh
refrain --uninstall          # asks for confirmation; -y to skip
```

This deletes the config, logs, cover cache, scrobble queue, autostart
entry and menu entry, **and purges the Last.fm credentials from your
OS keyring**, then prints the exact command to remove the program
itself for your install type (pip / pipx / AUR / Flatpak / AppImage).
There's also a *Settings → Advanced → Uninstall Refrain…* button.

Removing just the program (keeping your settings) is the package
command for how you installed it — `pip uninstall refrain`,
`pipx uninstall refrain`, `yay -R refrain`,
`flatpak uninstall io.github.Rockykln.Refrain`, or deleting the
`.AppImage`. (`refrain --uninstall-desktop` removes only the menu
entry + icon.)

## File locations

| What          | Where                                       |
|---------------|---------------------------------------------|
| Config        | `$XDG_CONFIG_HOME/refrain/config.toml`      |
| Scrobble queue| `$XDG_STATE_HOME/refrain/scrobble_queue.jsonl` |
| Scrobble in progress | `$XDG_STATE_HOME/refrain/scrobble_current.json` |
| Recently played | `$XDG_STATE_HOME/refrain/history.json`    |
| Measured song lengths | `$XDG_STATE_HOME/refrain/song_lengths.txt` |
| Logs          | `$XDG_STATE_HOME/refrain/refrain.log` (rotates) |
| Crash stacks  | `$XDG_STATE_HOME/refrain/crash.log` (written only if Refrain crashes; the next start says so and opens it on click) |
| Developer-mode metrics | `$XDG_STATE_HOME/refrain/dev-metrics.jsonl` (only while developer mode is on) |
| Cover cache   | `$XDG_CACHE_HOME/refrain/covers/` (lookups; images only for songs in *Recently played*) |
| Autostart     | `$XDG_CONFIG_HOME/autostart/refrain.desktop` (when enabled) |

## Diagnostics — live log

Tray menu → *Troubleshooting* → *Live log…* (or launch with
`refrain --debug`) opens a
streaming view of every log line as it happens, color-coded by level and
filterable. Same content as `~/.local/state/refrain/refrain.log`, but
without tailing it from a terminal.

For finding slow spots there is also a **developer mode** (off by default):
click the version number in *Settings* six times. It measures poll,
startup and request timings, memory and which windows and buttons get used,
shows them in a *Developer* tab of the live log, and keeps them in a local
file. It never sends anything. See [`docs/developer-mode.md`](docs/developer-mode.md).

<p align="center">
  <picture>
    <source media="(prefers-color-scheme: light)" srcset="docs/screenshots/live-log-light.png"/>
    <img src="docs/screenshots/live-log.png" alt="Live-log window" width="640"/>
  </picture>
</p>

## Privacy

Refrain is **local-first**: no Refrain server, no account, **no
telemetry**, and the author receives nothing. Two lookups are on by
default and can be switched off (cover art at Apple, the update check at
GitHub); everything else only runs once *you* set it up. Data always goes
directly to that provider:

- **Discord** — only if you set a Discord Application ID; track
  metadata goes to the *local* Discord IPC socket (the Discord client
  then broadcasts it under your account).
- **Apple iTunes Search** (HTTPS) — artist + track name, while cover art
  is enabled (the default), to fetch album art. Untick it for zero egress.
- **GitHub** (HTTPS) — a daily update check sends only the Refrain
  version + your IP. Disable in *Updates*.
- **Last.fm** (HTTPS) — opt-in scrobbling only; credentials live in
  your **OS keyring** (encrypted at rest), never in `config.toml`.

`Privacy → Off` is the global kill switch (no Discord status, no
scrobbling) while keeping the tray + controls running. The *Recently
played* list never leaves your machine, so it has its own switch in
*Settings → Recently played* instead.

Full data-flow, retention and erasure details — written to GDPR
transparency expectations — are in [`PRIVACY.md`](PRIVACY.md).

## Documentation

- [Architecture overview](docs/architecture.md) — threads, D-Bus surface, file paths
- [FAQ](docs/faq.md)
- [Known limitations and troubleshooting](docs/troubleshooting.md) — what doesn't work, and what to check when something fails
- [Bluetooth quick-start](docs/bluetooth.md) — pair + AVRCP setup walkthrough
- [Last.fm scrobbling](docs/lastfm.md) — API account + connect walkthrough
- [Developer mode](docs/developer-mode.md) — local timing and usage metrics, never sent
- [Test matrix](docs/test-matrix.md) — supported distros, smoke-check checklist
- [Roadmap](docs/roadmap.md)
- [Changelog](CHANGELOG.md)
- [Contributing](CONTRIBUTING.md) — dev setup, testing, code style
- [Privacy & data protection](PRIVACY.md) — every data flow, retention, erasure
- [Security policy](SECURITY.md)
- [Packaging guide](packaging/README.md) — AUR, Flatpak, AppImage build steps
- [Releases](docs/releases.md) — every version, and why some were removed

## Contributing

See [`CONTRIBUTING.md`](CONTRIBUTING.md) for dev setup, testing, and the
source/UI architecture. PRs welcome — especially for distribution packaging
(Flatpak, AUR, AppImage) and for additional Bluetooth device shapes.

## Contact

- General questions, feedback, feature ideas → **[contact@rockykln.com](mailto:contact@rockykln.com)**
- Security reports → **[report@rockykln.com](mailto:report@rockykln.com)** (also: [GitHub private advisory](https://github.com/Rockykln/refrain/security/advisories/new))
- Bugs → please use the [issue tracker](https://github.com/Rockykln/refrain/issues)

## License

**Refrain License (Use-Only)** — see [`LICENSE`](LICENSE).

Refrain is **source-available but not open source**. In short:

- Anyone may use, copy, and redistribute the unmodified Software.
- Anyone may read, study, and reference the source code.
- Modifications and derivative works may **not** be redistributed.
  Forks on GitHub are fine only for preparing a pull request.
- The "Refrain" name and logo may not be used to imply endorsement of
  or affiliation with modified versions.

Third-party dependencies (`PySide6`, `pypresence`, `dbus-python`) retain
their original licenses (LGPL / MIT).

## Legal

Refrain is an independent project. It is **not affiliated with, sponsored
by, or endorsed by** Apple, Discord, Last.fm or KDE, and **"Refrain" is not
a registered trademark**. All product names and trademarks belong to their
respective owners and are used only to describe what Refrain interoperates
with.

Full notice — trademarks, licence, third-party components, and what data
leaves your machine — in [`LEGAL.md`](LEGAL.md). The same text is reachable
inside the app under **Settings → Legal**.

<!-- stats:start -->
## Stats

| Stat | Value |
|---|---|
| Lines of code | 13,038 |
| Lines in the repository | 87,382 |
| Words in the repository | 306,980 |
| Words of documentation | 46,967 |
| Automated tests | 2,133 |
| Test coverage | 100 % |
| Days since the first release | 138 |
| Versions released | 24 |
| Downloads | 2,676 |
| Commits | 213 |
| Languages | 16 |
| Runtime dependencies | 3 |
| Browsers tested | 5 (Chrome, Chromium, Brave, Firefox, Zen) |
| Ways to install | 3 (PyPI, AUR, AppImage) |

As of v0.5.3.
<!-- stats:end -->
