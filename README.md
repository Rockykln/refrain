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
  browsers need KDE's Plasma Browser Integration, which works outside KDE
  too — see [troubleshooting](docs/troubleshooting.md).
- Forwards track + cover art to Discord via the local IPC socket.
- Optionally **scrobbles to Last.fm** alongside Discord (opt-in, with a
  crash-safe offline queue).
- Lives in your **system tray** with Play/Pause/Next/Previous controls.
- Keeps a **recently played** list — cover, time, source — of the last
  30 songs by default, on your machine only.
- Provides a **settings window** (PySide6) for everything users typically
  want to tweak — privacy mode, sources, autostart, Bluetooth device picker.

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
compiling its own. On Debian and Ubuntu:

```sh
sudo apt update && sudo apt install pipx python3-dbus python3-gi libxcb-cursor0
pipx install --system-site-packages refrain
pipx ensurepath   # once, if ~/.local/bin isn't on your PATH yet
refrain --install-desktop
```

Commands for Fedora and openSUSE, the source build, where the files land
and how to remove them again: [`docs/install.md`](docs/install.md).

### Tested on

See [`docs/test-matrix.md`](docs/test-matrix.md) for the full Tier-1 / Tier-2
list, the per-row smoke checks, and which distros are explicitly out-of-scope.

- **Tier 1 (must pass every release):** CachyOS, Arch Linux, Fedora 42,
  Ubuntu 24.04 LTS, Debian 13, openSUSE Tumbleweed, Linux Mint 22,
  Manjaro Stable.
- **Desktops:** KDE Plasma 6 (Wayland is the primary target, X11 also
  covered), GNOME with the [AppIndicator and KStatusNotifierItem](https://extensions.gnome.org/extension/615/appindicator-support/)
  extension, XFCE / Cinnamon / LXQt / Budgie via their native or
  AppIndicator-bridged tray, MATE with `mate-applet-statusnotifier`, tiling
  WMs (Hyprland / Sway / i3 / river) via a SNI-capable status bar.

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

## Last.fm scrobbling

Refrain can scrobble to [Last.fm](https://www.last.fm) **alongside** the
Discord status — a second, independent channel, never a replacement.
It's **opt-in** and off by default. Register your own free
[API account](https://www.last.fm/api/account/create), then fill in
*Settings → Last.fm*. The shared secret and session token are stored in
your **OS keyring**, never in `config.toml`.

Full walkthrough, the scrobble rules and troubleshooting:
[`docs/lastfm.md`](docs/lastfm.md).

## Privacy

Refrain is **local-first**: no Refrain server, no account, **no
telemetry**, and the author receives nothing. Two lookups are on by
default and can be switched off — cover art at Apple and the update check
at GitHub. Everything else only runs once *you* set it up: Discord needs
an Application ID, Last.fm is opt-in. Data always goes directly to that
provider, never through anything of ours.

`Privacy → Off` is the global kill switch (no Discord status, no
scrobbling) while keeping the tray + controls running. The *Recently
played* list never leaves your machine.

Every data flow, what is kept and for how long, and how to erase it —
written to GDPR transparency expectations — is in
[`PRIVACY.md`](PRIVACY.md).

## Documentation

**Using Refrain**

- [Installing](docs/install.md) — per-distro commands, source build, uninstall, file locations
- [Interface](docs/interface.md) — tray, Status window, Recently played, notifications
- [Settings](docs/settings.md) — every tab, field by field
- [Configuration file](docs/configuration.md) — what `config.toml` holds
- [Updates](docs/updates.md) — update check and AppImage self-update
- [Bluetooth quick-start](docs/bluetooth.md) — pair + AVRCP setup walkthrough
- [Last.fm scrobbling](docs/lastfm.md) — API account + connect walkthrough

**When something is wrong**

- [FAQ](docs/faq.md)
- [Known limitations and troubleshooting](docs/troubleshooting.md) — what doesn't work, and what to check when something fails
- [Developer mode](docs/developer-mode.md) — local timing and usage metrics, and the live log. Never sent
- [Test matrix](docs/test-matrix.md) — supported distros, smoke-check checklist

**About the project**

- [Architecture overview](docs/architecture.md) — threads, D-Bus surface, file paths
- [Privacy & data protection](PRIVACY.md) — every data flow, retention, erasure
- [Security policy](SECURITY.md)
- [Changelog](CHANGELOG.md) · [Releases](docs/releases.md) — every version, and why some were removed
- [Roadmap](docs/roadmap.md)
- [Contributing](CONTRIBUTING.md) — dev setup, testing, code style
- [Packaging guide](packaging/README.md) — AUR, Flatpak, AppImage build steps

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
