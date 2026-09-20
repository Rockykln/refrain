# Security Policy

## Supported versions

Refrain ships from `main`. The latest tagged release is the only "supported"
version in any meaningful sense — when a security fix lands, it ships in the
next tag, and users update.

| Version | Supported |
|---------|-----------|
| Latest tag on `main` | Yes |
| Older tags | No |

## Reporting a vulnerability

**Do not open a public GitHub issue for security bugs.**

Instead, use GitHub's [private vulnerability reporting](https://github.com/Rockykln/refrain/security/advisories/new).
That gives me a private channel where we can triage and patch before the
issue becomes public knowledge.

If GitHub Security Advisories aren't an option for you, mail
**[report@rockykln.com](mailto:report@rockykln.com)** instead. That mailbox
is monitored for security reports specifically; please don't use it for
feature requests or general questions (use `contact@rockykln.com` for
those).

## What to expect

Refrain is a hobby project maintained by one person in their spare time,
so these are aims, not guarantees:

- I acknowledge a report within **7 days**.
- A fix, or a clear plan for one, follows within **30 days**. I'll keep
  you posted if it takes longer.
- Disclosure is coordinated: please keep the details private until a
  fixed release is out, or for up to **90 days** after your report. The advisory is published after the fix, and I
  credit you in it unless you'd rather not be named.

## What counts as a security issue

Refrain is a desktop app that talks to D-Bus and a single local IPC socket
(Discord). The realistic attack surface:

- **D-Bus method calls** — Refrain reads metadata and dispatches Play/Pause/
  Next/Previous on whichever player is active. A maliciously-crafted MPRIS
  bus name or BlueZ player path that crashes Refrain or exfiltrates data
  would qualify.
- **Discord IPC** — Refrain sends activity payloads via `pypresence`. Any
  content injected into the payload that could trigger a Discord-side issue
  qualifies.
- **iTunes Search API** — Refrain fetches cover-art URLs over HTTPS. A
  compromised response could in theory steer Refrain into rendering an
  attacker-chosen URL in the Discord status — but the URL is read by
  Discord, not executed locally, so the risk is very small.
- **Last.fm API** — opt-in scrobbling sends signed requests over
  HTTPS. The shared secret + session token are credentials; anything
  that exposes them (in a log, in `config.toml`, world-readable on
  disk, or sent anywhere other than Last.fm) qualifies.
- **Updater** — the update check asks the GitHub releases API over
  HTTPS. For an AppImage, *Update* downloads the new AppImage and
  `SHA256SUMS` only from
  `https://github.com/Rockykln/refrain/releases/download/`, refuses
  redirects to anything but HTTPS, picks the file for the machine's
  architecture, checks the size against the release, and compares the
  SHA-256 with the release's `SHA256SUMS` before it replaces the
  AppImage file. `SHA256SUMS` itself carries an Ed25519 signature
  (`SHA256SUMS.sig`) made on the maintainer's own machine, which Refrain
  checks against the public key built into it; a release whose checksum
  file is missing, unsigned or signed with another key is refused
  outright, and there is no fallback to checking the size alone. So an
  update survives a corrupted download and a release published without
  that key — not a leak of the key itself. pip and pipx
  installs are updated by running `pip install --upgrade refrain` or
  `pipx upgrade refrain` against PyPI; Refrain is published there from
  GitHub Actions with Trusted Publishing, without a stored token. Anything
  that makes the updater install a file that isn't from the Refrain
  releases or PyPI qualifies.
- **Files Refrain writes** — its config, logs, state and cache live in
  the `refrain` folders inside `$XDG_CONFIG_HOME`, `$XDG_STATE_HOME` and
  `$XDG_CACHE_HOME`. Besides those it writes the autostart entry
  (`$XDG_CONFIG_HOME/autostart/refrain.desktop`), the menu entry and icon
  from `--install-desktop` (`~/.local/share/applications`,
  `~/.local/share/icons`), and, during an AppImage update, the AppImage
  file itself (via a temporary `.AppImage.new` next to it). Anything that
  lets Refrain write outside these places, or that leaks the credentials
  out of the OS keyring / `0600` fallback, qualifies.

## What doesn't count

- "Discord shows a song name with a dirty word in it." That's working as
  intended.
- "I gave Refrain a malicious config file and it crashed." Don't do that —
  it's your config file.

## Defense in depth

Refrain's CI runs CodeQL, Bandit, pip-audit, and TruffleHog. Reports from
these tools are reviewed; not every false positive is suppressed, but real
findings get fixed.

Credentials (the Last.fm shared secret + session token) are stored in
the OS keyring via the freedesktop Secret Service (encrypted at rest),
falling back to a `0600` owner-only file only where no keyring exists;
they are never written to `config.toml` (itself `0600`) or to logs.

For the full data-flow / privacy picture (what leaves the machine,
when, to whom, retention and erasure) see [`PRIVACY.md`](PRIVACY.md).
