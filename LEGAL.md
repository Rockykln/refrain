# Legal Notice

Refrain is an independent hobby project by **Rockykln**.
Contact: [contact@rockykln.com](mailto:contact@rockykln.com) ·
Source: <https://github.com/Rockykln/refrain>

---

## No affiliation, no endorsement

Refrain is **not affiliated with, sponsored by, endorsed by, or in any way
officially connected to** any of the companies, products, or services it
interoperates with. It is an independent third-party client that talks to
software already installed on the user's own machine.

In particular, Refrain has **no relationship with**:

- **Apple Inc.** — iTunes, Apple and Apple Music are trademarks of Apple Inc.
- **Discord Inc.** — Discord is a trademark of Discord Inc.
- **Last.fm Ltd.** — Last.fm is a trademark of Last.fm Ltd.
- **KDE e.V.** — KDE and Plasma are trademarks of KDE e.V.
- **GitHub, Inc.** — GitHub and the Invertocat logo are trademarks of GitHub, Inc.
- **Bluetooth SIG, Inc.** — Bluetooth is a trademark of Bluetooth SIG, Inc.
- **Linus Torvalds** — Linux is a trademark of Linus Torvalds.

All product names, logos, trademarks, and registered trademarks mentioned in
this project, its documentation, or its interface are the property of their
respective owners. They are used **only to describe what Refrain
interoperates with** (nominative fair use), never to suggest a partnership,
certification, or origin.

Refrain does not redistribute, modify, circumvent, or bundle any of these
products. It communicates with them through interfaces they expose on the
user's own system — Discord's local Rich Presence IPC socket, the MPRIS
D-Bus specification, and the public Last.fm, iTunes Search and GitHub web
APIs. The GitHub logo in the settings window is GitHub's unmodified mark,
used only as a link to Refrain's repository.

Cover art comes from Apple's iTunes Search API and remains the property of
its respective rights holders. Refrain keeps a local copy only to display it.

## Trademark status of "Refrain"

**"Refrain" is not a registered trademark.** The name and logo are used
by this project without any trademark registration or claim to exclusive
rights in any jurisdiction. No trademark rights are asserted, and none
should be inferred.

The name may nevertheless not be used to imply endorsement of, or
affiliation with, modified versions of the software — see the licence.

## Licence

Refrain is distributed under the **Refrain License (Use-Only)**, reproduced
in full in [`LICENSE`](LICENSE).

Refrain is **source-available, not open source**. In short:

- Anyone may run, copy, and redistribute the **unmodified** software,
  free of charge, for personal or commercial purposes.
- Anyone may read, study, and reference the source code.
- Modified versions and derivative works may **not** be redistributed.
  Forks on GitHub are fine only for preparing a pull request.
- The "Refrain" name and logo may not be used to imply endorsement of,
  or affiliation with, modified versions.

This summary is for orientation only; the text in [`LICENSE`](LICENSE)
governs.

### Third-party components

Refrain depends on software distributed under its own licences, which are
unaffected by the Refrain License and continue to apply to those components:

| Component | Licence |
| --- | --- |
| [PySide6 / Qt for Python](https://doc.qt.io/qtforpython/) | LGPL v3 |
| [pypresence](https://github.com/qwertyquerty/pypresence) | MIT |
| [dbus-python](https://gitlab.freedesktop.org/dbus/dbus-python) | MIT |
| [PyGObject](https://gitlab.gnome.org/GNOME/pygobject) (optional) | LGPL v2.1 |

The AppImage bundles these components together with Python and system
libraries from Ubuntu. Each of them stays under its own licence.

## Data

Refrain runs entirely on the user's own machine. It has no backend, no
account system, and no analytics or telemetry of any kind.

Two lookups are on by default and can be switched off in the settings: the
cover art lookup at Apple and the daily update check on GitHub. Everything
else only happens once the user sets it up:

- **Apple's iTunes Search API** — the artist and song title (and a store
  country taken from the desktop language) are sent to look up cover art,
  the song's length and its Apple Music link. On by default.
- **GitHub** — the releases API is queried once a day for updates. On by
  default. A new version is downloaded from GitHub, or from PyPI for pip
  and pipx installs, only when the user chooses to update.
- **Discord** — the currently playing track's metadata is sent to the local
  Discord client over its Rich Presence IPC socket, which Discord then
  displays on the user's profile. Optionally, the application ID can be
  sent to Discord's web API to look up the application's name (off by
  default).
- **Last.fm** — only when the user enables scrobbling and supplies their own
  credentials. Those credentials are stored in the operating system's
  keyring where one is available, and otherwise in a `0600`-mode file in
  the user's own configuration directory.

[`PRIVACY.md`](PRIVACY.md) lists every data flow in detail.

## No warranty

The software is provided **"as is", without warranty of any kind**, express
or implied, including but not limited to the warranties of merchantability,
fitness for a particular purpose, and non-infringement. See [`LICENSE`](LICENSE)
for the binding disclaimer and limitation of liability.

Refrain is not certified, audited, or supported by any third party. Users
are responsible for complying with the terms of service of any platform they
connect it to.
