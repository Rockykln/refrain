# Privacy & Data Protection

This document describes **every piece of data Refrain touches**, where it
goes, why, and how to erase it. It is written to satisfy the
transparency expectations of the EU GDPR (Arts. 13–14) and equivalent
regimes, and as an honest plain-language overview for everyone.

## TL;DR

- Refrain is a **local desktop application**. There is **no
  Refrain-operated server, no account, and no telemetry**. The author
  receives **nothing** and has no access to any of your data.
- Refrain does not profile you, does not make automated decisions about
  you, shows no ads, and never sells or shares data.
- Data leaves your machine **only** when *you* enable an optional
  feature, and then it goes **directly** to the third party that
  feature integrates with (Apple, Discord, GitHub, Last.fm) under
  *their* privacy policy and *your* relationship with them — never via
  the author.
- Everything Refrain stores is on your own machine, in standard XDG
  directories, and can be deleted at any time (see *Erasure*).

## Who is the "controller"?

Because Refrain runs entirely on your computer with no backend, **you**
are effectively the controller of your own data. The Refrain author is
not a processor of your data — the author never receives it. Each
external transfer below is initiated solely by your own configuration
choice (your consent / your own purposes) and is then subject to the
receiving service's own privacy policy and your account there.

## Data Refrain processes — and where it goes

### 1. Stays on your machine, never transmitted by Refrain

| Data | Purpose | Location |
|---|---|---|
| Now-playing metadata (title, artist, album, position) read from your media player via D-Bus / BlueZ AVRCP | Drive the tray, notifications, the published MPRIS player | In memory; reflected in the tray |
| Listening history lines (track changes, scrobble events) | Diagnostics / live log | `$XDG_STATE_HOME/refrain/refrain.log` — rotating, ≤ 4 × 1 MB, plaintext, local only |
| Album-cover images + iTunes URL/duration cache | Avoid re-fetching covers | `$XDG_CACHE_HOME/refrain/` — capped (default 200), oldest pruned |
| Pending scrobbles (artist/track/album/timestamp) | Survive offline / restart until submitted to Last.fm | `$XDG_STATE_HOME/refrain/scrobble_queue.jsonl` — capped at 1000, `0600` semantics, removed once submitted |
| Preferences + the *public* Discord/Last.fm application IDs | Your settings | `$XDG_CONFIG_HOME/refrain/config.toml` — written **owner-only (`0600`)**; contains **no secrets** |
| Last.fm **shared secret** and **session token** | Authenticate scrobbling | **OS keyring** (KWallet / GNOME Keyring), encrypted at rest; or, only if no keyring exists, a `0600` owner-only `secrets.json`. **Never** in `config.toml`; never logged |

Refrain never transmits any of the above over a network on its own.

### 2. Sent to a third party — only when you enable that feature

| Recipient | When | What is sent | Default | How to stop it |
|---|---|---|---|---|
| **Discord** (your local Discord client → Discord's servers) | Only if you set a Discord Application ID | Track title / artist / album, cover-art URL, optional "Listen on Apple Music" link | **Off** (no ID configured) | Leave the Client ID blank, or set *Privacy → Off*, or *Minimal* (only "Listening to music") |
| **Apple** (`itunes.apple.com` + artwork CDN, HTTPS) | If "Fetch album cover art" is on **and** a track with artist+title plays | The **artist + track name** (to look up cover/duration/song URL) and your IP / User-Agent; then an image fetch. No account, no auth, no cookies | **On** | Untick *Notifications → Fetch album cover art from iTunes* |
| **GitHub** (`api.github.com`, HTTPS) | If auto-update check is on: once per day on start, or a manual check | A `User-Agent` containing the Refrain version, and your IP. **No personal data, no account** | **On** | Untick *Updates → Automatically check on startup* |
| **Last.fm** (`ws.audioscrobbler.com` / `last.fm`, HTTPS) | Only if you enable scrobbling **and** connect an account | Artist / track / album / timestamp, your Last.fm API key + session token + a request signature | **Off** (opt-in) | Don't enable it; or *Disconnect*; or set *Privacy → Off* |

Notes:

- **Privacy modes** (*Settings → General → Privacy*): `Full` sends full
  metadata to Discord; `Minimal` sends only "Listening to music";
  `Off` is the global **no-external-broadcasting** kill switch — it
  disables the Discord status **and** silences Last.fm scrobbling.
- The Apple cover-art lookup is gated by the *cover-art* toggle, which
  is **independent of Privacy mode** — if you want zero network egress
  even in `Off` mode, also untick "Fetch album cover art".
- Discord itself then displays/broadcasts your status to your Discord
  contacts. That processing is Discord's, under your Discord account
  and Discord's policy — Refrain only writes to the **local** Discord
  IPC socket.

### 3. Never collected at all

No analytics, no telemetry, no crash reporting, no advertising
identifiers, no device/hardware fingerprinting, no machine ID, no
contacts, no email, no location, no special-category data, no data
about children. There is nothing to opt out of because none of it
exists.

## Legal basis (GDPR Art. 6)

Every external transfer above happens **only** because you enabled the
corresponding optional integration for your own purposes — i.e. your
**consent / your own legitimate use**. No processing relies on the
author having any legal basis, because the author processes nothing.

## Recipients & international transfers

When you enable an integration, data goes directly to that provider,
which may process it outside your country under its own framework and
policy. Refrain has no influence over this. Review their policies:

- Apple — <https://www.apple.com/legal/privacy/>
- Discord — <https://discord.com/privacy>
- GitHub — <https://docs.github.com/site-policy/privacy-policies/github-general-privacy-statement>
- Last.fm — <https://www.last.fm/legal/privacy>

## Security of processing (GDPR Art. 32)

- All network egress is **HTTPS** (the code refuses non-`https://`
  endpoints for its lookups/updates).
- Credentials are stored in the **OS keyring, encrypted at rest**;
  the fallback file and `config.toml` are written **owner-only
  (`0600`)**. Secrets are **never written to logs** or to
  `config.toml`, and a legacy plaintext secret from an older build is
  auto-migrated into the keyring and scrubbed from disk.
- Refrain writes only inside `$XDG_CONFIG_HOME`, `$XDG_STATE_HOME`,
  `$XDG_CACHE_HOME`.
- See [`SECURITY.md`](SECURITY.md) for the threat model and how to
  report a vulnerability.

## Retention & your control

- **Logs** rotate automatically (≤ 4 files of 1 MB); old data ages out.
- **Cover cache** is capped and pruned oldest-first.
- **Scrobble queue** entries are deleted once submitted; the queue is
  capped at 1000.
- Everything else persists until you change or delete it.

## Erasure ("right to be forgotten", locally)

To remove all data Refrain stored on your machine:

```sh
rm -rf "${XDG_CONFIG_HOME:-$HOME/.config}/refrain" \
       "${XDG_STATE_HOME:-$HOME/.local/state}/refrain" \
       "${XDG_CACHE_HOME:-$HOME/.cache}/refrain"
```

- **Last.fm credentials in the keyring:** click *Disconnect* in
  *Settings → Last.fm* (then Apply), or delete the
  `io.github.Rockykln.Refrain` entries in your keyring tool
  (KWallet Manager / Seahorse).
- **Data already sent to a third party** (your Discord activity
  history, your Last.fm scrobbles, etc.) is held by *that* provider —
  exercise your access/erasure rights with them directly via their
  links above (e.g. Last.fm lets you delete individual scrobbles or
  your account).
- Autostart entry (if enabled):
  `${XDG_CONFIG_HOME:-$HOME/.config}/autostart/refrain.desktop`.

## Changes

Material changes to data handling are recorded in
[`CHANGELOG.md`](CHANGELOG.md) (notably under **Security**) and here.

## Contact

- General / privacy questions → **contact@rockykln.com**
- Security reports → **report@rockykln.com** (see [`SECURITY.md`](SECURITY.md))
