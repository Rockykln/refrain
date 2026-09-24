# Installing Refrain

The short version is in the [README](../README.md#install). This page has the
per-distro commands, the source build, the desktop launcher, where the files
land and how to remove them again.

## From PyPI

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

## From source (development)

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

## Pip-installed users: get a launcher

When installed via pipx or pip rather than a distro package, Refrain
doesn't register itself with your application menu. Run once after install:

```sh
refrain --install-desktop
```

This copies the `.desktop` file and icon to `~/.local/share/applications/`
and `~/.local/share/icons/`. To undo: `refrain --uninstall-desktop`.

## Tested on

See [`docs/test-matrix.md`](test-matrix.md) for the full Tier-1 / Tier-2 list, the per-row smoke checks, and which distros are explicitly out-of-scope (Python or glibc floor too low).

- **Tier 1 (must pass every release):** CachyOS, Arch Linux, Fedora 42, Ubuntu 24.04 LTS, Ubuntu 25.04, Ubuntu 26.04 LTS, Debian 13 (Plasma and GNOME), openSUSE Tumbleweed, Linux Mint 22, Manjaro Stable.
- **Desktops:** KDE Plasma 6 (Wayland is the primary target, X11 also covered), GNOME with the [AppIndicator and KStatusNotifierItem](https://extensions.gnome.org/extension/615/appindicator-support/) extension, XFCE / Cinnamon / LXQt / Budgie via their native or AppIndicator-bridged tray, MATE with `mate-applet-statusnotifier`, tiling WMs (Hyprland / Sway / i3 / river) via a SNI-capable status bar.

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
| Last.fm length references | `$XDG_STATE_HOME/refrain/song_lengths_lastfm.txt` |
| Logs          | `$XDG_STATE_HOME/refrain/refrain.log` (rotates) |
| Crash stacks  | `$XDG_STATE_HOME/refrain/crash.log` (written only if Refrain crashes; the next start says so and opens it on click) |
| Developer-mode metrics | `$XDG_STATE_HOME/refrain/dev-metrics.jsonl` (only while developer mode is on) |
| Cover cache   | `$XDG_CACHE_HOME/refrain/covers/` (lookups; images only for songs in *Recently played*) |
| Autostart     | `$XDG_CONFIG_HOME/autostart/refrain.desktop` (when enabled) |
