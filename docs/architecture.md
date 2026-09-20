# Architecture

Refrain is one process. Two long-lived Qt threads (main + daemon
worker), short-lived worker executors (cover fetch, Last.fm) and, only
where Qt isn't built on GLib, a GLib thread — all feeding one Discord
IPC socket and a few outbound HTTPS clients.

```
┌─────────────────────────────────────────────────────────────────┐
│  refrain process                                                │
│                                                                 │
│  Main thread (Qt event loop, GLib-backed)                       │
│  ├─ QApplication                                                │
│  ├─ StatusWindow (QDialog, opened by a start or a tray click)   │
│  ├─ TrayIcon (QSystemTrayIcon)         <─── status / track      │
│  ├─ SettingsWindow (QDialog, hidden after Apply)                │
│  ├─ LogWindow (QDialog, on-demand)                              │
│  ├─ HistoryWindow (QDialog, on-demand) <─── history snapshots   │
│  ├─ WelcomeDialog (first-run only)                              │
│  ├─ UpdateOrchestrator                                          │
│  ├─ UpdateDialog                                                │
│  └─ dispatches the shared session bus: the single-instance      │
│     name and the published MPRISServer                          │
│                                                                 │
│  Worker thread (Qt event loop, QThread)                         │
│  ├─ DaemonWorker                                                │
│  │    ├─ MPRISSource     ──► Session DBus (own connection)      │
│  │    ├─ BluetoothSource ──► System DBus (own connection)       │
│  │    ├─ CoverFetcher    ──► iTunes Search (HTTPS)              │
│  │    ├─ DiscordRPC      ──► Discord IPC socket                 │
│  │    ├─ Scrobbler       ──► Last.fm API (HTTPS, opt-in)        │
│  │    ├─ PlayHistory     ──► history.json (local only)          │
│  │    └─ MPRISServer     ──► handed to the main thread          │
│  └─ Timer (QTimer, 500 ms default poll, configurable)           │
│                                                                 │
│  GLib thread (only when Qt is not GLib-backed)                  │
│  └─ GLib.MainLoop — takes over dispatching the session bus      │
└─────────────────────────────────────────────────────────────────┘
```

## Why a worker thread?

The polling loop (default 500 ms, configurable via
`advanced.poll_interval_ms`) reads MPRIS / BlueZ properties via D-Bus.
Some of those calls block briefly. iTunes Search lookups can take up
to 5 s. Discord IPC writes can stall when Discord is restarting.
Doing any of that on the GUI thread would make the tray icon and
settings window freeze every poll.

The worker thread runs a Qt event loop (driven by `QTimer`, *not* a
`while/sleep` loop — that detail matters: a sleep loop blocks the event
loop and prevents cross-thread slot invocations like Play/Pause from the
tray menu from being delivered).

## D-Bus and threads

dbus-python's GLib integration keeps each connection's timeout and
watch bookkeeping without any locking, so **no connection may be used
from two threads**. Everything below follows from that:

- The process-wide session connection (`dbus.SessionBus()`) belongs to
  whichever thread dispatches it — the main thread, through Qt's GLib
  event dispatcher. The single-instance name and the published MPRIS
  player live on it.
- `MPRISSource` and `BluetoothSource` run on the daemon thread and open
  private connections of their own with no main loop
  (`private=True, mainloop=NULL_MAIN_LOOP`). Polling needs nothing but
  blocking method calls, which work without one. A connection that
  fails is closed and reopened on the next call.
- The daemon never touches the published player itself: registering it
  and every track change go through `mpris_server._on_bus_thread`,
  which runs them on the dispatching thread via `GLib.idle_add`.
- Plasma's Play/Pause/Next/Previous arrive on the main thread and are
  queued to the daemon's `control_*` slots (`_queue_control`), which own
  the sources.

Two threads on one connection corrupted the heap, and Refrain aborted
with `malloc(): unaligned tcache chunk detected` — the crash is only
ever detected later, in whichever `malloc` trips over it. Should
anything still crash inside Qt or libdbus, `faulthandler` writes every
thread's Python stack to `crash.log` next to the log.

## What a poll produces

Each tick reads the sources, then puts the result through two stages
before anything renders it:

```
_poll_sources()      MPRIS + BlueZ read, best candidate wins
      │
      ▼
_resolve_position()  position, or a decision that we don't have one
      │
      ▼
_apply_idle_detection()   drop a dangling source's stale track
      │
      ▼
_dispatch()          tray · Discord RPC · published MPRIS · history · scrobbler
```

Both stages sit ahead of `_dispatch` so every consumer is handed the
same answer, and `_resolve_position` sits ahead of idle detection so its
anchor survives an idle clear — the source keeps reporting the track
either way.

### Position

Sources misreport position, and Apple Music's web player misreports it
in a way worth spelling out: it does not describe the track at all. Its
`Position` counts the *stream* and carries on across track boundaries
(the change from one song to the next arrives one poll after the
previous song's start plus its length), and its `mpris:length` is a
buffer marker — measured live, it grew by 135 s over 144 s of playback
on one unchanging track. Three songs into a session that reads as 11:08
elapsed of 6:52 on a 2:25 song. The same player also stops refreshing
`Position` mid-track while still reporting `Playing`, and has been seen
switching frames mid-track: counting the stream for one song, then the
song itself, with no track change in between.

On Plasma, the player Refrain actually reads is usually not the browser
itself but Plasma's browser integration, which publishes its own MPRIS
player and is the one reporting a title and an artist at all. It takes
those from the page's media session, but its `Position`,
`mpris:length` and `PlaybackStatus` from whichever media element is
playing — on Apple Music, most of the time, the album's looping artwork
video. Measured live, the position ran 0.5 s, 2.6 s, 1.1 s, 3.2 s,
5.0 s, 0 s while the length moved between 8433, 9999 and 11033 ms (or
sat at 10416 ms throughout), over and over, on a track four minutes
long; and paused in the browser, it kept saying `Playing`. Only as a
song starts does it report the song itself — its real length, at
position 0 — for a few seconds.

What Refrain makes of that:

- **Playing or paused** comes from the browser's own MPRIS entry for the
  Apple Music tab when there is one (no URL, but a page title ending in
  "Apple Music"), and Play/Pause goes to it first; plasma's toggle
  followed the video and could pause the music but never start it again.
- A source length under 30 s on a song the catalog knows to be longer is
  a *segment* source: its position is never shown, never read as a seek
  or a frame switch, and on first sight only places the clock's zero
  after a poll that saw nothing playing. After a restart mid-song the
  time stays hidden until the next start frame.
- A **start frame** — the song's own length at a position near zero,
  arriving there from well into the song or from a segment's length —
  well into a track is the song beginning again (repeat-one, or played
  again from the top). It places the zero afresh and counts in
  `PositionState.restarts`, which the daemon turns into a replay for the
  history and the Scrobbler. Seen while paused, it only counts once the
  song plays on from there: a tablet pausing can report 0:00 for a tenth of
  a second before its real position comes back.
- A Bluetooth player on **repeat-one** (`Repeat` = `singletrack`) may
  count its position on across loops — 5:13 into a 3:36 song — so there
  the song's own position is what's left after whole loops.

`timing.resolve_position` answers in three tiers, in order:

| Tier       | Used when                                                    |
|------------|--------------------------------------------------------------|
| `reported` | The source's value holds up: non-negative, not past the end of the track, moving while playing, and not from a source already caught carrying its position across a track change. |
| `computed` | It doesn't, but we witnessed this track start: wall-clock elapsed since that anchor, minus time paused. A stream-relative source's timeline is still followed through seeks; a source whose own reset gave us the anchor is not — see below. |
| `unknown`  | Neither. No anchor to count from, or our own clock has run past the end of the track. |

`unknown` renders as nothing at all — no tray progress line, no
`start`/`end` for Discord, no length published to Plasma's applet. A
clock known to be wrong is worse than an absent one, and the next track
change recovers it. `advanced.position_stall_s` (default 4 s) is the
window a playing track's position may stand still before the first tier
is withdrawn; 0 disables that check.

One question decides how much of a misbehaving source to believe: **did
we watch this track start?** It holds when the source reset its position
at the track change *and* that reset placed our clock's zero, and it
governs three things at once:

- A length that shifts mid-track latches the source as stream-relative.
  That normally voids the anchor too, since the anchor at that point
  came from having believed a stream position — but not when we watched
  the start, because then the zero is real whatever the length does.
- A mid-track return to zero is read as the player changing frames, and
  the latch comes off. Only where we don't already know the start: a
  segment source returns to zero every few seconds, and believing each
  of those re-anchored the clock and dropped the elapsed time back to
  0:00 all song long.
- A jump backwards is followed as a seek, shifting our zero by the same
  amount. That trusts the source's timeline while distrusting its
  absolute value — worth doing when the timeline is all we have, wrong
  when we timed the track's start ourselves.

So a segment source lands on `computed` and stays there: our own clock,
counting from a start we saw, ignoring everything the source does with
its position afterwards.

Ordinary sources — every other MPRIS player, Bluetooth AVRCP — stay on
`reported` throughout, and the machinery costs them nothing.

### Duration

Two parties can answer and either can be wrong. `mpris:length` describes
the element actually playing, so it normally wins; the catalog duration
comes from an artist-and-title search that can match the wrong record —
58 s for a 2:45 song, observed live. The catalog fills gaps rather than
overruling: no length reported, or a length under 30 s where the catalog
says otherwise (Apple Music's preview-clip representation).

Which one to believe depends on what the source has been shown to be:

| Source established as | Length used                            |
|-----------------------|----------------------------------------|
| track-relative        | the player's, catalog filling gaps      |
| stream-relative       | the catalog's — the player's describes its buffer |
| not yet established   | the player's, unless the two disagree — then neither |

That last row is the startup-mid-track case, and the disagreement there
is genuinely undecidable: a position past the catalog length fits "the
catalog matched the wrong record" and "this position belongs to a
stream" equally well. Refrain says so instead of picking, and
`resolve_position` withholds tier 1 from a track whose start it didn't
see, so the time is hidden until the next track change settles it.

The boundary worth knowing: when the catalog has no match at all, there
is nothing to contradict the source, and its numbers are used as
reported. On a stream-relative player that means one track may render a
stream position before the next track change — or the length growing —
gives it away.

### Idle detection

Its job is the dangling handle — a browser tab closed without releasing
MPRIS, still reporting `Playing` forever. The deadline is the track's
own duration plus `advanced.idle_grace_s`, but it only measures silence:
a position that moved recently is proof the source is alive and pushes
the anchor forward. Without that gate, any duration that came out too
short took the playing track down with it.

### Recently played

`PlayHistory` (`history.py`) is fed from `_dispatch` on every poll, with
the same length the scrobbler is given — so "counts as listened" and a
Last.fm scrobble are one decision: half the track or four minutes,
nothing of 30 s or less. What counts is time actually played, accrued
from the monotonic clock while the source says *playing*; pauses don't
add to it and seeking doesn't either.

- A song appears as "now playing" the moment it plays and is written to
  `history.json` the moment it counts. One that is skipped first is
  dropped without touching the disk.
- The song playing right now is saved with how much of it was heard and
  where the player had it — every 30 s of play, when it counts, and on
  quit. The first song seen after a start is compared with it through
  `scrobble.continues_play`, the same test the Scrobbler uses: the same
  song, soon enough, and — where the player reports a position — further
  along than it was, not back at the start. That carries on as the same
  entry (its count continuing, or, if it had counted already, as the
  list's newest song instead of a second copy); a song that ended and
  began again meanwhile is a new play.
- A song that begins again after it counted — the player's position
  jumping back to the start, or a start frame — is a new play with an
  entry of its own, and a scrobble of its own too.
- Apple Music reports "paused" for a poll or two between songs, so a
  pause only shows as one after 2 s, and a song that follows one that
  was just playing starts even while it still reports "paused".
- The window never touches `PlayHistory`: the daemon emits immutable
  `HistorySnapshot` copies through `historyChanged`, and clearing or
  removing a song is a queued slot on the daemon.

A click on a song opens its Apple Music page — or a catalog search when
the page isn't known — through `browser.open_url`: in the browser that
played it while that browser is still running (it is the one signed in
to Apple Music, often not the default browser), as a detached process
with Refrain's Qt and Python paths removed from its environment.
Anything else, including an AppImage or Flatpak Refrain, goes to the
default browser.

## Cross-thread message flow

| From → To              | Mechanism                                  |
|------------------------|--------------------------------------------|
| Tray → DaemonWorker    | Qt signal → `@Slot` (auto-queued)          |
| DaemonWorker → Tray    | Qt signal → main-thread slot               |
| SettingsWindow → DW    | `applied(Config)` → `update_config` slot   |
| DaemonWorker → HistoryWindow | `historyChanged(HistorySnapshot)`    |
| HistoryWindow → DW     | `clearRequested` / `removeRequested` → slots |
| MPRISServer (Plasma) → DW | `QMetaObject.invokeMethod(Queued)` on `control_*` |
| DW → MPRISServer       | `GLib.idle_add` (`_on_bus_thread`)         |
| Daemon shutdown        | `QMetaObject.invokeMethod(BlockingQueued)` |

Nothing else is shared; `Config` is treated as immutable after `Apply`.

## Threads created elsewhere

- **CoverFetcher** owns a 1-worker `ThreadPoolExecutor` for the iTunes
  lookup + image download. Lookups are cached in-memory + on-disk, images
  in a private temp dir; `keep_covers()` copies those of the songs in
  the history into the cover cache and deletes the rest there. The
  daemon polls the cache via `get()` / `get_local_path()`. The lookup
  asks the store of the desktop's country first, then the US store,
  with "feat." and remaster tags stripped and, failing that, only the
  first of several artists — and takes a result only when its artist
  *and* title match. When iTunes can't be reached at all it raises
  rather than returning a miss, so the fetcher retries after 60 s
  instead of remembering "no cover".
- **UpdateOrchestrator** spawns a one-shot QThread for the GitHub Releases
  API call so the GUI stays responsive.
- **Scrobbler** owns a 1-worker `ThreadPoolExecutor` for Last.fm
  now-playing + scrobble submission. The daemon feeds it the current
  track every tick (pure pause/seek-aware accounting); qualifying
  tracks are written to a persistent on-disk queue and submitted in
  the background. The play in progress is kept in
  `scrobble_current.json` (every 30 s of play, when it counts, on
  quit): after a restart the same play — by `continues_play`, as for
  the history — carries on under its own start time instead of counting
  again, and one queued on quit is marked so it is never queued twice.
  `reconfigure` only drops the play when the Last.fm identity changes,
  not on every Settings → Apply. Inert until the user opts in +
  connects an account.
- **SettingsWindow** spawns a short-lived QThread per Last.fm auth
  step (`auth.getToken` / `auth.getSession`), joined before the next
  step and on window close.

## Single-instance lock

Refrain claims the well-known D-Bus name `io.github.Rockykln.Refrain` on
the session bus at startup. Subsequent invocations fail to acquire the
name and exit. No lockfile in `/tmp`.

## File system surface

| Purpose            | Path                                         |
|--------------------|----------------------------------------------|
| Config             | `$XDG_CONFIG_HOME/refrain/config.toml` (`0600`; no secrets) |
| Credentials        | OS keyring via freedesktop Secret Service (KWallet / GNOME Keyring), encrypted at rest |
| Credentials (fallback) | `$XDG_CONFIG_HOME/refrain/secrets.json` (`0600`, owner-only) — only when no keyring is reachable |
| Logs (rotating)    | `$XDG_STATE_HOME/refrain/refrain.log{,.1,.2,.3}` |
| Crash stacks       | `$XDG_STATE_HOME/refrain/crash.log` (faulthandler; `0600`; ≤ 256 KB, then started afresh) |
| Cover URL cache    | `$XDG_CACHE_HOME/refrain/covers/<key>.txt` (versioned; a miss expires after 3 days; 200-entry cap) |
| Cover images       | `$XDG_CACHE_HOME/refrain/covers/<urlhash>.jpg` only for songs in the history; others in a private temp dir until the next song or quit |
| Scrobble queue     | `$XDG_STATE_HOME/refrain/scrobble_queue.jsonl` (`0600`, atomic; 1000-entry cap; each entry names its Last.fm account) |
| Scrobble in progress | `$XDG_STATE_HOME/refrain/scrobble_current.json` (`0600`, atomic; one play, kept across a restart) |
| Recently played    | `$XDG_STATE_HOME/refrain/history.json` (`0600`, atomic; 10–100 songs, 30 by default) |
| Measured song lengths | `$XDG_STATE_HOME/refrain/song_lengths.txt` (`0600`, atomic; 1000-song cap; hashed keys, one line each) |
| Developer-mode metrics | `$XDG_STATE_HOME/refrain/dev-metrics.jsonl` (`0600`, appended; rotated to `.1` at 5 MB; only while developer mode is on, see [developer-mode.md](developer-mode.md)) |
| Autostart entry    | `$XDG_CONFIG_HOME/autostart/refrain.desktop` (only when enabled) |

The user-installed desktop file (via `--install-desktop`) goes to
`~/.local/share/applications/refrain.desktop`. Distro packages install
to `/usr/share/applications/refrain.desktop` instead.

## D-Bus interfaces consumed

| Interface                               | Bus    | Used for                          |
|-----------------------------------------|--------|-----------------------------------|
| `org.mpris.MediaPlayer2`                | session| Player identity / desktop entry   |
| `org.mpris.MediaPlayer2.Player`         | session| Track metadata + Play/Next/Prev   |
| `org.bluez.MediaPlayer1`                | system | AVRCP track + Play/Pause/Next/Prev|
| `org.bluez.Device1` (via ObjectManager) | system | Paired-device enumeration         |
| `org.freedesktop.DBus.NameHasOwner`     | system | Fast-fail check before activating `org.bluez` (avoids a 25 s service-activation timeout on hosts without bluez)|

## D-Bus interfaces published

Refrain publishes two well-known names on the **session** bus:

| Bus name                              | Purpose                                        |
|---------------------------------------|------------------------------------------------|
| `io.github.Rockykln.Refrain`          | Single-instance lock. No object path; just the name reservation. |
| `org.mpris.MediaPlayer2.refrain`      | Refrain itself as an MPRIS player. KDE Plasma's panel media controls applet, KDE Connect, GNOME Shell etc. drive the same Play/Pause/Next/Previous as the tray, and render the same track Discord renders. Implements the standard MPRIS root + Player interfaces. |

The MPRIS-server publication needs PyGObject (`gi`) for a GLib main loop
to pump dbus-python signal dispatch. When PyGObject isn't installed,
Refrain logs a warning at startup and falls back to read-only mode —
the rest of the app works, but Plasma's panel can't drive playback.

The sources answer `Play` and `Pause` on their own, so the published
`Play`, `Pause` and `Stop` say what they mean and change nothing when the
music is already in the state they ask for. Plasma offers "Stop" for
every controllable player; here it pauses, and does nothing when the
music is already paused.

## Discord IPC discovery

The standard path is `$XDG_RUNTIME_DIR/discord-ipc-N` (N=0..9). Snap
and Flatpak Discord builds put their socket inside the sandbox tree
instead (`$XDG_RUNTIME_DIR/app/com.discordapp.Discord/discord-ipc-N`,
`~/.var/app/com.discordapp.Discord/config/discord/discord-ipc-N`,
`~/snap/discord/current/.config/discord/discord-ipc-N`).
`DiscordRPC._bridge_sandboxed_ipc_socket` symlinks the first sandbox
socket it finds into `$XDG_RUNTIME_DIR` before each connect attempt,
and sweeps stale symlinks left behind by previously-uninstalled
Discord builds.
