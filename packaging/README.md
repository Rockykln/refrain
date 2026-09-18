# Distribution packaging

Refrain ships through three channels that together cover ~99 % of Linux
desktops without leaving anyone reaching for `pip install -e .` from a
clone. A Flatpak manifest also lives under `flatpak/` for self-build
users; a Flathub submission is on the roadmap but not currently active.

| Channel | Audience | Status |
|---------|----------|--------|
| **PyPI** | Any distro with Python ≥ 3.11 | Live — `pipx install refrain` (see the README) |
| **AUR**  | Arch / CachyOS / Manjaro / EndeavourOS | Live — `refrain` + `refrain-git` |
| **AppImage** | Single-file portable use, any glibc Linux | Live — attached to every GitHub release |
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

Both are pushed per release. `refrain-git` builds from `main` whatever
its recorded `pkgver` says, but the AUR listing shows that recorded
value, so leaving it stale makes the package look abandoned.

The tag has to exist first — the tarball it points at is what gets
hashed. Then, in this repo:

1. Bump `pkgver` in `packaging/aur/refrain/PKGBUILD`, and in
   `packaging/aur/refrain-git/PKGBUILD` to `<version>.r<count>.g<short>`
   (`git rev-list --count HEAD`, `git rev-parse --short=7 HEAD`).
2. Recompute `sha256sums` for `refrain`. Let makepkg do it, rather than
   hashing a file you downloaded yourself:
   ```sh
   cd packaging/aur/refrain
   updpkgsums          # pacman-contrib; downloads, hashes, edits in place
   makepkg --verifysource
   ```
   If you do fetch it by hand, use `curl -fL` and check the file is not
   empty. `curl -sLO` writes a zero-byte file on a failed request and
   `sha256sum` will happily hash it — the result,
   `e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855`,
   is the hash of nothing at all and looks entirely plausible.
3. Regenerate both `.SRCINFO` files and commit them alongside the
   PKGBUILDs.

Then push each package to the AUR, from a clone of that package's own
repo — the AUR repo holds only `PKGBUILD`, `.SRCINFO` and a `.gitignore`:

```sh
git clone ssh://aur@aur.archlinux.org/refrain.git /tmp/aur-refrain
cp packaging/aur/refrain/PKGBUILD /tmp/aur-refrain/
cd /tmp/aur-refrain
makepkg --printsrcinfo > .SRCINFO
git add PKGBUILD .SRCINFO
git commit -m "Update to ${PKGVER}"
git push
```

The `aur.archlinux.org` RPC index lags the push by a minute or two, so
the old version showing there straight afterwards is not a failed push.

## AppImage

Recipe at [`appimage/AppImageBuilder.yml`](appimage/AppImageBuilder.yml).
Builds a portable single-file binary (~76 MB, includes Qt 6 + Python +
all deps) that runs on any glibc-based Linux.

The release workflow rewrites `version:` from the git tag at build time,
so the recipe's hardcoded version is just a placeholder.

Build on Ubuntu 24.04: the recipe installs Refrain with `/usr/bin/python3`,
which has to be the Python 3.12 it bundles.

```sh
pip install appimage-builder "packaging<22"
cd packaging/appimage
appimage-builder --recipe AppImageBuilder.yml --skip-test
./Refrain-*-x86_64.AppImage --appimage-extract-and-run --version
```

The recipe vendors:

- Python 3.12, dbus-python and PyGObject from Ubuntu 24.04
- The libraries Qt needs on Wayland and X11 (EGL, fontconfig, xkbcommon, XCB)
- The `refrain` package, PySide6-Essentials (with Qt 6) and pypresence via pip;
  `prune_qt.py` drops the Qt modules Refrain doesn't use, including every
  GPL-only one

The AppImage bundles third-party software, so it has to carry their
licences: `third_party_notices.py` writes `usr/share/doc/refrain/THIRD-PARTY-NOTICES`
(every bundled component with version, licence and where its source is)
and `LICENSES/` with the full texts at build time.

The release workflow starts the AppImage once with `--version`, checks
that the notices are inside and no GPL-only Qt module is, and stops the
release otherwise. It publishes `THIRD-PARTY-NOTICES` and `SHA256SUMS`
next to the AppImage.

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
`io.qt.PySide.BaseApp//6.10` BaseApp. The manifest pins only `tag:`:
it is part of the tagged tree, so it can never name the commit the tag
points to. A submission copies it into the Flathub repository, where the
tag's `commit:` can be added, and needs a matching `<release/>` entry
in `metainfo.xml` per Flathub policy.

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
