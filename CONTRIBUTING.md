# Contributing to Refrain

Thanks for your interest! Refrain is a small, focused project — Discord Rich
Presence for Apple Music on Linux. The bar for changes is whether they make
that experience better for users on real Linux desktops.

## A note on the license

Refrain ships under the **Refrain License (Use-Only)** — see
[`LICENSE`](LICENSE). It is source-available but **not open source**.
Forks and modified redistributions are not permitted.

By submitting a contribution (pull request, patch, code in an issue), you
agree that your contribution becomes part of Refrain under the same
license, with copyright assigned to the project's maintainer for the
purpose of consolidating ownership.

If that's a deal-breaker for you, that's understandable — please don't
contribute. Bug reports and feature ideas (without code) remain very
welcome under any terms.

## Quick start

```sh
git clone https://github.com/Rockykln/refrain.git
cd refrain

# A venv with system-site-packages is fastest on Linux distros that already
# package PySide6 and dbus-python (Arch / Fedora / openSUSE / etc.).
python -m venv --system-site-packages .venv
source .venv/bin/activate

pip install -e ".[dev]"
refrain
```

If your distro doesn't ship Qt for Python or dbus-python, plain `python -m venv .venv`
works too — pip will pull `PySide6` and build `dbus-python` against system headers
(`libdbus-1-dev` / `dbus-devel`).

## Running tests

```sh
PYTHONPATH=src pytest
```

Tests are designed to run on any Linux box (and inside CI) without KDE,
Discord, BlueZ or a display server. Anything that touches D-Bus or Qt is
isolated behind module boundaries that the tests don't import directly.

```sh
PYTHONPATH=src pytest --cov --cov-report=term-missing
```

## Cleaning up

Use the Makefile to keep the project folder free of build artifacts:

```sh
make clean       # remove __pycache__, .pytest_cache, .ruff_cache, build/, dist/, *.egg-info
make clean-all   # also remove .venv
```

Runtime data (logs, cover-art cache) lives under `$XDG_STATE_HOME` and
`$XDG_CACHE_HOME`, never in the project folder. Refrain itself prunes the
cover-art cache on startup if it exceeds 200 entries (~10–30 MB).

## Style

```sh
ruff check .
ruff format .
```

CI runs both but does not block PRs on style alone. Format on save is
recommended; the project follows Ruff's defaults (line length 100,
double-quoted strings).

## Adding a playback source

The duck-typed source contract is:

```python
class Source:
    def read(self) -> TrackInfo: ...
    def play_pause(self) -> bool: ...
    def next(self) -> bool: ...
    def previous(self) -> bool: ...
```

`read()` returns a `TrackInfo` (see `src/refrain/sources/base.py`); the
control methods return `True` on success. Wire your new source up in
`src/refrain/daemon.py`'s `_poll` and `_control` and add a toggle to the
*Sources* tab in `src/refrain/ui/settings_window.py`.

## Reporting bugs

Use the issue templates — the bug-report form asks for the version, distro,
desktop, and a snippet of `~/.local/state/refrain/refrain.log`. Fill in
what you can, leave blank what you can't.

## Commit messages

Imperative present tense ("add Bluetooth picker", not "added"). One-line
summary, optional body. No need for ticket prefixes — link issues from PR
descriptions instead.

## What belongs in Refrain

- Anything that improves the Apple Music → Discord experience on Linux.
- Cross-distro fixes (Wayland, X11, KDE, GNOME, …).
- Better source selection logic, more browsers, more BlueZ device shapes.
- Distribution work — Flatpak, AUR, AppImage.

## What doesn't

- Other music services (Spotify, Tidal, …) — the project name is *Refrain*,
  but the *scope* is Apple Music. A separate fork is the right move there.
- Other operating systems — there are mature options on macOS and Windows
  already.
- Heavy dependencies for marginal features.

## Translations

User-visible strings live wrapped in `tr()` calls; the source list is
maintained in `src/refrain/i18n/refrain_<lang>.ts` (Qt Linguist format).
German is shipped complete. French, Spanish, Italian, Portuguese, Dutch,
Polish, Japanese, and Simplified Chinese have stub `.ts` files with all
source strings already extracted, marked
`<translation type="unfinished"></translation>` — those are the ones we'd
love community PRs for.

To translate one of the stub languages end-to-end:

```sh
# 1. Pick a language file. Linguist (the GUI) is friendlier than
#    editing XML by hand.
pyside6-linguist src/refrain/i18n/refrain_fr.ts

# 2. Replace each "unfinished" translation with the real string.

# 3. Compile to .qm and run the result locally.
make i18n
LANG=fr_FR.UTF-8 refrain
```

If you've wrapped any new strings on the source side, run
`make i18n-update` first — it refreshes every `.ts` file's
source-string list while preserving existing translations.

PRs that just translate a `.ts` (no source changes) are very welcome.
So is native-speaker review of an existing translation.
