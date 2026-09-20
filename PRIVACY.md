# Privacy & Data Protection

This document describes **every piece of data Refrain touches**, where it
goes, why, and how to erase it. It is written to satisfy the
transparency expectations of the EU GDPR (Arts. 13–14) and equivalent
regimes, and as an honest plain-language overview for everyone.

## TL;DR

- Refrain is a **local desktop application**. There is **no
  Refrain-operated server, no account, and no telemetry**. The author
  receives **nothing** from the app and has no access to any of your
  data.
- Refrain does not profile you, does not make automated decisions about
  you, shows no ads, and never sells or shares data.
- Two lookups are **on by default** and can be switched off: cover art
  from Apple's iTunes Search API and a daily update check on GitHub
  (see the table below for the switches). Everything else — the Discord
  status, Last.fm scrobbling, the Discord name lookup — only happens
  once you set it up.
- Whatever leaves your machine goes **directly** to the third party
  involved (Apple, Discord, GitHub, Last.fm, PyPI) under *their* privacy
  policy — never via the author.
- Everything Refrain stores is on your own machine, in standard XDG
  directories, and can be deleted at any time (see *Erasure*).

## Who is the "controller"?

Because Refrain runs entirely on your computer with no backend, **you**
are effectively the controller of your own data. The Refrain author is
not a processor of your data — the author never receives it through the
app. Each external transfer below is made by Refrain on your own device,
under settings you control, and is then subject to the receiving
service's own privacy policy and your account there. The one exception
is when you contact the author yourself (see *Contact*).

## Data Refrain processes — and where it goes

### 1. Stays on your machine, never transmitted by Refrain

| Data | Purpose | Location |
|---|---|---|
| Now-playing metadata (title, artist, album, position) read from your media player via D-Bus / BlueZ AVRCP | Drive the tray, notifications, the published MPRIS player | In memory; reflected in the tray |
| Listening history lines (track changes, scrobble events) | Diagnostics / live log | `$XDG_STATE_HOME/refrain/refrain.log` — rotating, ≤ 4 × 1 MB, plaintext, local only |
| Start stamps, and Python stack traces if Refrain ever crashes | Diagnosing crashes inside Qt or D-Bus | `$XDG_STATE_HOME/refrain/crash.log` — ≤ 256 KB, then started afresh; written **owner-only (`0600`)**; local only |
| Album-cover images | Show covers in *Recently played* and in notifications | `$XDG_CACHE_HOME/refrain/covers/` — kept only for the songs in *Recently played*; with the history off, no covers are kept on disk. The image for a notification sits in a private temporary folder (`$XDG_RUNTIME_DIR`, else the system temp folder) and is deleted when the next song starts or Refrain quits |
| iTunes lookup results (cover URL, Apple Music link, song length) — no images | Avoid asking Apple again for the same song | `$XDG_CACHE_HOME/refrain/covers/` — cut back to the latest 200 songs at each start; a song Apple didn't know is asked about again after 3 days |
| Pending scrobbles (artist/track/album/timestamp, and the Last.fm username they were heard under) | Survive offline / restart until submitted to Last.fm — and never to another account | `$XDG_STATE_HOME/refrain/scrobble_queue.jsonl` — capped at 1000, written **owner-only (`0600`)**, removed once submitted |
| The song being scrobbled right now (artist/track/album, when it began, how much of it was heard, where the player had it) | Carry that play on across a restart of Refrain instead of counting it — or scrobbling it — twice | `$XDG_STATE_HOME/refrain/scrobble_current.json` — one song, written **owner-only (`0600`)**, removed when the song ends; only while Last.fm scrobbling is on |
| Recently played (title, artist, album, cover URL, source + browser/device name, start time, length, whether it was scrobbled) | The *Recently played* window | `$XDG_STATE_HOME/refrain/history.json` — the last 30 songs by default (10–100), written **owner-only (`0600`)**; *Settings → History* turns it off and deletes the file |
| How long a song ran, under a hash of its title, artist and album | Give songs the iTunes catalog doesn't know a length, so they can still be scrobbled | `$XDG_STATE_HOME/refrain/song_lengths.txt` — at most 1000 songs, written **owner-only (`0600`)**; holds **no titles**, only hashes and seconds |
| Developer-mode measurements: timings of polls, startup and requests (service name and duration only), memory use, and which windows, tabs and buttons were used — **no song data, URLs or typed text** | Finding slow spots and awkward paths through the app; only while *developer mode* is switched on (off by default) | `$XDG_STATE_HOME/refrain/dev-metrics.jsonl` — written **owner-only (`0600`)**, rotated at 5 MB (one older file kept); nothing is written while developer mode is off. See [docs/developer-mode.md](docs/developer-mode.md) |
| Preferences + the *public* Discord/Last.fm application IDs | Your settings | `$XDG_CONFIG_HOME/refrain/config.toml` — written **owner-only (`0600`)**; contains **no secrets** |
| Last.fm **shared secret** and **session token** | Authenticate scrobbling | **OS keyring** (KWallet / GNOME Keyring), encrypted at rest; or, only if no keyring exists, a `0600` owner-only `secrets.json`. **Never** in `config.toml`; never logged |

Refrain never transmits any of the above over a network on its own.

### 2. Sent to a third party

| Recipient | When | What is sent | Default | How to stop it |
|---|---|---|---|---|
| **Discord** (your local Discord client → Discord's servers) | Only if you set a Discord Application ID | Track title / artist / album, cover-art URL, optional "Listen on Apple Music" link | **Off** (no ID configured) | Leave the Application ID blank, or set *Privacy → Off*, or *Minimal* (only "Listening to music") |
| **Discord** (`discord.com/api`, HTTPS — direct, **not** via your Discord client) | Only if you switch on *General → Look up the application's name on Discord*: then at startup and every 4 h | The **Application ID** you typed and your IP / User-Agent. No account, no token, no listening data | **Off** (opt-in) | Leave it unticked — it is off unless you turn it on; or set *Privacy → Off* |
| **Apple** (`itunes.apple.com` + artwork CDN, HTTPS) | If "Look up songs in Apple's catalog" is on **and** a track with artist+title plays | The **artist and song title** (the title without "feat." or version tags; as a last try the title alone), a store country taken from your desktop language (e.g. `DE`, then `US`), a `User-Agent` containing the Refrain version, and your IP — to look up cover, length and Apple Music link; then the cover image is fetched. No album, no account, no auth, no cookies | **On** | Untick *General → Privacy → Look up songs in Apple's catalog* |
| **Apple** (`itunes.apple.com`, HTTPS) | Once, when the welcome window opens on first start | A fixed test search (`term=test`) to show whether cover lookups will work, and your IP. No listening data | Runs on first start | — (one request; not tied to the cover-art switch) |
| **GitHub** (`api.github.com`, HTTPS) | If auto-update check is on: once per day on start, or a manual check | A `User-Agent` containing the Refrain version, and your IP. **No personal data, no account** | **On** | Untick *Updates → Automatically check on startup* |
| **GitHub** (`github.com/Rockykln/refrain/releases/download/…` and GitHub's download servers, HTTPS) | AppImage only, when you click *Update* | Downloads `SHA256SUMS` and the new AppImage; your IP and a `User-Agent` containing the Refrain version | Only on click | Don't click *Update* |
| **PyPI** (`pypi.org`, `files.pythonhosted.org`, HTTPS — or whichever package index your pip is set up to use) | pip and pipx installs, when you click *Update* (Refrain runs `pip install --upgrade refrain` or `pipx upgrade refrain`) | What pip sends to any package index: your IP and a `User-Agent` with pip, Python, OS and distribution versions | Only on click | Don't click *Update*; update however you prefer |
| **Last.fm** (`ws.audioscrobbler.com` / `last.fm`, HTTPS) | Only if you enable scrobbling **and** connect an account | Artist / track / album / timestamp, your Last.fm API key + session token + a request signature | **Off** (opt-in) | Don't enable it; or *Disconnect*; or set *Privacy → Off* |

Notes:

- **Privacy modes** (*Settings → General → Privacy*): `Full` sends full
  metadata to Discord; `Minimal` sends only "Listening to music";
  `Off` is the global **no-external-broadcasting** kill switch — it
  disables the Discord status **and** silences Last.fm scrobbling.
- The Apple cover-art lookup and the GitHub update check have their own
  switches and are **independent of Privacy mode** — if you want zero
  network egress even in `Off` mode, also untick "Look up songs in Apple's catalog"
  and "Automatically check on startup".
- Flatpak, AUR and system packages are never updated by Refrain itself;
  it only shows the command, and your package manager does the rest.
- Discord itself then displays/broadcasts your status to your Discord
  contacts. That processing is Discord's, under your Discord account
  and Discord's policy — the status itself is only ever written to the
  **local** Discord IPC socket.
- The optional application-name lookup is the only thing Refrain would
  ever send to Discord's servers directly, and it is **off until you
  turn it on**. It exists because a mistyped Application ID is otherwise
  completely invisible: Discord rejects it and the status simply never
  appears, with nothing on screen to point at. When enabled it sends the
  ID and nothing else — an ID that is public by construction, since it
  travels in every status you publish.

### 3. Never collected at all

No analytics, no telemetry, no crash reporting, no advertising
identifiers, no device/hardware fingerprinting, no machine ID, no
contacts, no email, no location, no special-category data, no data
about children. There is nothing to opt out of because none of it
exists.

## Legal basis (GDPR Art. 6)

Every external transfer above is made by software running on your own
device, for your own purposes and under settings you control — the two
lookups that are on by default included. The author is not a controller
of this processing and receives none of it, so no processing in the app
relies on the author having a legal basis. If you contact the author,
see *Contact* below.

## Recipients & international transfers

Data goes directly to the provider involved, which may process it
outside your country under its own framework and policy. Refrain has no
influence over this. Review their policies:

- Apple — <https://www.apple.com/legal/privacy/>
- Discord — <https://discord.com/privacy>
- GitHub — <https://docs.github.com/site-policy/privacy-policies/github-general-privacy-statement>
- Last.fm — <https://www.last.fm/legal/privacy>
- PyPI — <https://policies.python.org/pypi.org/Privacy-Notice/>

## Security of processing (GDPR Art. 32)

- All network egress is **HTTPS** (the code refuses non-`https://`
  endpoints for its lookups/updates).
- Credentials are stored in the **OS keyring, encrypted at rest**;
  the fallback file and `config.toml` are written **owner-only
  (`0600`)**. Secrets are **never written to logs** or to
  `config.toml`, and a legacy plaintext secret from an older build is
  auto-migrated into the keyring and scrubbed from disk.
- Refrain keeps its data only in the `refrain` folders inside
  `$XDG_CONFIG_HOME`, `$XDG_STATE_HOME` and `$XDG_CACHE_HOME`. Beyond
  that it writes the autostart entry
  (`$XDG_CONFIG_HOME/autostart/refrain.desktop`, if you turn autostart
  on), the menu entry and icon from `refrain --install-desktop`
  (`~/.local/share/applications`, `~/.local/share/icons`), and, when you
  update an AppImage from inside the app, the AppImage file itself.
- See [`SECURITY.md`](SECURITY.md) for the threat model and how to
  report a vulnerability.

## Retention & your control

- **Logs** rotate automatically (≤ 4 files of 1 MB); old data ages out.
- **Cover images** are kept only for the songs in *Recently played*
  and go when a song drops out of it; with the history off, none are
  kept. A notification's image is temporary and deleted with the next
  song or on quit.
- **Scrobble queue** entries are deleted once submitted; the queue is
  capped at 1000.
- Everything else persists until you change or delete it.

## Erasure ("right to be forgotten", locally)

One command removes **everything** Refrain stored on your machine —
all data files **and** the Last.fm credentials in your OS keyring:

```sh
refrain --uninstall          # confirms first; add -y to skip
```

(Or *Settings → Advanced → Uninstall Refrain…*.) It also prints the
command to remove the program package itself.

Prefer to do it by hand? The data directories are:

```sh
rm -rf "${XDG_CONFIG_HOME:-$HOME/.config}/refrain" \
       "${XDG_STATE_HOME:-$HOME/.local/state}/refrain" \
       "${XDG_CACHE_HOME:-$HOME/.cache}/refrain"
```

- **Last.fm credentials in the keyring:** `refrain --uninstall`
  purges them automatically; manually, click *Disconnect* in
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

If you email either address or open an issue, I receive what you send —
your address, your message and anything attached, such as a log — and
use it only to answer you and fix the problem. GitHub issues are public
and fall under GitHub's privacy statement. A Refrain log lists the songs
you played and, at debug level, your Bluetooth device's address, so
check it before you post it.
