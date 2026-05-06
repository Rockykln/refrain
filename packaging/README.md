# Distribution packaging

Refrain ships through three channels that together cover ~99 % of Linux
desktops without leaving anyone reaching for `pip install -e .` from a
clone. A Flatpak manifest also lives under `flatpak/` for self-build
users; a Flathub submission is on the roadmap but not currently active.

| Channel | Audience | Status |
|---------|----------|--------|
| **PyPI** | Any distro with Python ≥ 3.11 | ✅ live — `pip install refrain` |
| **AUR**  | Arch / CachyOS / Manjaro / EndeavourOS | ✅ live — `refrain` + `refrain-git` |
| **AppImage** | Single-file portable use, any glibc Linux | ✅ live — attached to every GitHub release |
| **Flatpak** | Every distro that ships Flatpak | 🛠️ self-build only — manifest validated locally, no Flathub submission |

## PyPI

Built + uploaded by `.github/workflows/release.yml` on every `v*.*.*`
tag push, via PyPA Trusted Publishers (OIDC). No API token to manage.
The publisher is configured on pypi.org under:

```
project: refrain
owner:   Rockykln
repo:    refrain
workflow: release.yml
environment: pypi
```

If the upload step ever fails with `invalid-publisher`, double-check
that the **environment name** on pypi.org is exactly `pypi` (not a typo
like `phpi`).

## AUR

Two packages, side-by-side, both maintained by Rockykln:

- [`refrain`](https://aur.archlinux.org/packages/refrain) — built from the latest tagged release (pinned tarball SHA)
- [`refrain-git`](https://aur.archlinux.org/packages/refrain-git) — built from `main` HEAD; auto-bumps version via `pkgver()`

```sh
# build & install locally (CachyOS / Arch)
cd packaging/aur/refrain
makepkg -si

# regenerate .SRCINFO before pushing to AUR
makepkg --printsrcinfo > .SRCINFO
```

When tagging a new release, the AUR side needs:

1. Bump `pkgver` in `packaging/aur/refrain/PKGBUILD`.
2. Recompute `sha256sums`:
   ```sh
   curl -sLO "https://github.com/Rockykln/refrain/archive/v${PKGVER}.tar.gz"
   sha256sum "v${PKGVER}.tar.gz"
   ```
3. Push to AUR:
   ```sh
   git clone ssh://aur@aur.archlinux.org/refrain.git /tmp/aur-refrain
   cp packaging/aur/refrain/PKGBUILD /tmp/aur-refrain/
   cd /tmp/aur-refrain
   makepkg --printsrcinfo > .SRCINFO
   git add PKGBUILD .SRCINFO
   git commit -m "Bump to ${PKGVER}"
   git push
   ```

`refrain-git` does not need a touch per release — its `pkgver()` reads
the version from `pyproject.toml` at build time.

## AppImage

Recipe at [`appimage/AppImageBuilder.yml`](appimage/AppImageBuilder.yml).
Builds a portable single-file binary (~245 MB, includes Qt 6 + Python +
all deps) that runs on any glibc-based Linux.

The release workflow rewrites `version:` from the git tag at build time,
so the recipe's hardcoded version is just a placeholder.

```sh
pip install appimage-builder
cd packaging/appimage
appimage-builder --recipe AppImageBuilder.yml --skip-test
```

The recipe vendors:

- Python 3 (from Ubuntu 22.04 jammy, glibc-compatible widely)
- Qt 6 + libqt6dbus, libdbus, libglib
- The `refrain` package and its Python deps via pip

## Flatpak

Manifest at
[`flatpak/io.github.Rockykln.Refrain.yaml`](flatpak/io.github.Rockykln.Refrain.yaml),
plus the AppStream metadata in
[`io.github.Rockykln.Refrain.metainfo.xml`](flatpak/io.github.Rockykln.Refrain.metainfo.xml)
and the desktop entry in
[`io.github.Rockykln.Refrain.desktop`](flatpak/io.github.Rockykln.Refrain.desktop).

Key choices:

- **Runtime**: `org.kde.Platform//6.10` (current Flathub stable)
- **PySide6** comes from the Flathub `io.qt.PySide.BaseApp//6.10`
  BaseApp instead of being vendored as a Python wheel — saves ~80 % on
  the final image size, and matches Flathub policy.
- **patchelf** is built inline from a 0.18.0 tarball because the KDE
  SDK doesn't ship it (meson-python needs it for dbus-python's build).
- **Python deps** (meson-python, hatchling, pypresence, dbus-python)
  are vendored into [`flatpak/python-deps.json`](flatpak/python-deps.json)
  via `flatpak-pip-generator` because Flathub builds run offline.

```sh
# one-time runtime/SDK install
flatpak install -y flathub org.kde.Platform//6.10 org.kde.Sdk//6.10 \
    io.qt.PySide.BaseApp//6.10

# local build + install for testing
cd packaging/flatpak
flatpak-builder --user --install --force-clean \
    flatpak-build io.github.Rockykln.Refrain.yaml

flatpak run io.github.Rockykln.Refrain
```

### Refreshing python-deps.json after a release

Whenever `requirements.txt` changes (new dep, version bump that pulls
in a transitive change), regenerate from inside the SDK so the Python
version matches:

```sh
cd packaging/flatpak
cat > /tmp/refrain-flatpak-requirements.txt <<EOF
meson-python
hatchling
pypresence>=4.3
dbus-python>=1.3
EOF
flatpak-pip-generator \
    --runtime org.kde.Sdk//6.10 \
    --requirements-file=/tmp/refrain-flatpak-requirements.txt \
    --output python-deps
git add python-deps.json
```

`meson-python` and `hatchling` are intentionally vendored even though
they aren't runtime deps — they are PEP 517 build backends required to
build dbus-python and refrain itself in Flathub's offline sandbox.

### Flathub status

Not currently submitted. The manifest under `flatpak/` is fully
validated against `org.kde.Platform//6.10` and the
`io.qt.PySide.BaseApp//6.10` BaseApp; a future submission will pin
`tag:` + `commit:` in the manifest and add a matching `<release/>`
entry to `metainfo.xml` per Flathub policy.

## Files in this directory

```
packaging/
├── aur/
│   ├── refrain/PKGBUILD               — release build (stable, pinned tarball)
│   └── refrain-git/PKGBUILD           — git build (auto-bumping pkgver)
├── appimage/
│   └── AppImageBuilder.yml            — AppImage recipe
└── flatpak/
    ├── io.github.Rockykln.Refrain.yaml          — Flatpak manifest
    ├── io.github.Rockykln.Refrain.desktop       — desktop entry (Flatpak ID)
    ├── io.github.Rockykln.Refrain.metainfo.xml  — AppStream metadata
    └── python-deps.json               — vendored Python deps for offline build
```
