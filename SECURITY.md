# Security Policy

## Supported versions

Refrain ships from `main`. The latest tagged release is the only "supported"
version in any meaningful sense — when a security fix lands, it ships in the
next tag, and users update.

| Version | Supported |
|---------|-----------|
| Latest tag on `main` | ✅ |
| Older tags | ❌ |

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
- **Config / log paths** — Refrain writes only to `$XDG_CONFIG_HOME`,
  `$XDG_STATE_HOME`, `$XDG_CACHE_HOME`. Anything that lets these files
  escape those directories qualifies.

## What doesn't count

- "Discord shows a song name with a dirty word in it." That's working as
  intended.
- "I gave Refrain a malicious config file and it crashed." Don't do that —
  it's your config file.

## Defense in depth

Refrain's CI runs CodeQL, Bandit, pip-audit, and TruffleHog. Reports from
these tools are reviewed; not every false positive is suppressed, but real
findings get fixed.
