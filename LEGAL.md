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

- **Apple Inc.** — Apple and Apple Music are trademarks of Apple Inc.
- **Discord Inc.** — Discord is a trademark of Discord Inc.
- **Last.fm Ltd.** — Last.fm is a trademark of Last.fm Ltd.
- **KDE e.V.** — KDE and Plasma are trademarks of KDE e.V.

All product names, logos, trademarks, and registered trademarks mentioned in
this project, its documentation, or its interface are the property of their
respective owners. They are used **only to describe what Refrain
interoperates with** (nominative fair use), never to suggest a partnership,
certification, or origin.

Refrain does not redistribute, modify, circumvent, or bundle any of these
products. It communicates with them through interfaces they expose on the
user's own system — Discord's local Rich Presence IPC socket, the MPRIS
D-Bus specification, and the public Last.fm and iTunes Search web APIs.

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

- ✅ Anyone may run, copy, and redistribute the **unmodified** software,
  free of charge, for personal or commercial purposes.
- ✅ Anyone may read, study, and reference the source code.
- ❌ Modified versions and derivative works (including forks) may **not**
  be redistributed.
- ❌ The "Refrain" name and logo may not be used to imply endorsement of,
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

## Data

Refrain runs entirely on the user's own machine. It has no backend, no
account system, and no analytics or telemetry of any kind.

Data leaves the machine only where the user's own configuration makes it
necessary:

- **Discord** — the currently playing track's metadata is sent to the local
  Discord client over its Rich Presence IPC socket, which Discord then
  displays on the user's profile.
- **Apple's iTunes Search API** — track and album names are sent to look up
  cover artwork.
- **Last.fm** — only when the user enables scrobbling and supplies their own
  credentials. Those credentials are stored in the operating system's
  keyring where one is available, and otherwise in a `0600`-mode file in
  the user's own configuration directory.
- **GitHub** — the releases API is queried to check for updates.

## No warranty

The software is provided **"as is", without warranty of any kind**, express
or implied, including but not limited to the warranties of merchantability,
fitness for a particular purpose, and non-infringement. See [`LICENSE`](LICENSE)
for the binding disclaimer and limitation of liability.

Refrain is not certified, audited, or supported by any third party. Users
are responsible for complying with the terms of service of any platform they
connect it to.
