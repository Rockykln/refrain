# Distribution packaging

Refrain targets three packaging channels. Together they cover ~99 % of
Linux desktops without leaving anyone reaching for `pip`.

| Channel | Audience | Status |
|---------|----------|--------|
| **AUR** | Arch / CachyOS / Manjaro / EndeavourOS | ✅ ready (`refrain` + `refrain-git`) |
| **Flatpak** | Every distro that ships Flatpak | ⚠️ manifest in place; needs `flatpak-pip-generator` for offline build |
| **AppImage** | Single-file portable use | ⚠️ recipe in place; tested only on Ubuntu host |

## AUR

Two packages, side-by-side:

- `refrain` — built from the latest tagged release
- `refrain-git` — built from the `main` branch (use this until the first
  tag exists)

```sh
# build & install locally (CachyOS / Arch)
cd packaging/aur/refrain-git
makepkg -si

# generate .SRCINFO before pushing to AUR
makepkg --printsrcinfo > .SRCINFO
```

When tagging a new release, update `pkgver` + `sha256sums` in
`packaging/aur/refrain/PKGBUILD`. The `git` variant auto-bumps via the
`pkgver()` function.

## Flatpak

Manifest at `packaging/flatpak/io.github.Rockykln.Refrain.yaml`. The
companion `.desktop` and `.metainfo.xml` files live next to it.

```sh
# one-time runtime/SDK install
flatpak install -y flathub org.kde.Platform//6.7 org.kde.Sdk//6.7

# local build + install for testing
flatpak-builder --user --install --force-clean \
    build packaging/flatpak/io.github.Rockykln.Refrain.yaml

flatpak run io.github.Rockykln.Refrain
```

### Before submitting to Flathub

1. Replace the local `dir` source under `modules.refrain.sources` with a
   `git` source pinned to a release tag.
2. Run `flatpak-pip-generator --requirements-file=../../requirements.txt
   -o python-deps` and add the generated JSON as a module before `refrain`.
   Flathub builds run **offline**, so every wheel must be vendored
   explicitly.
3. Submit at <https://github.com/flathub/flathub/wiki/App-Submission>.

## AppImage

Recipe at `packaging/appimage/AppImageBuilder.yml`. Builds a portable
single-file binary that runs on any glibc-based Linux.

```sh
pip install appimage-builder
cd packaging/appimage
appimage-builder --recipe AppImageBuilder.yml --skip-test
```

The recipe vendors:

- Python 3 (from Ubuntu 22.04)
- Qt 6 + libqt6dbus
- libdbus / libglib
- The `refrain` package and its Python deps via pip

The Ubuntu apt repo is used as a base so the resulting AppImage runs on
any reasonably recent distro.

## Files in this directory

```
packaging/
├── aur/
│   ├── refrain/PKGBUILD          — release build
│   └── refrain-git/PKGBUILD      — git build
├── flatpak/
│   ├── io.github.Rockykln.Refrain.yaml      — Flatpak manifest
│   ├── io.github.Rockykln.Refrain.desktop   — desktop entry (Flatpak ID)
│   └── io.github.Rockykln.Refrain.metainfo.xml — AppStream metadata
└── appimage/
    └── AppImageBuilder.yml       — AppImage recipe
```
