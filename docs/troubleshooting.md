# Known limitations and troubleshooting

This page lists what Refrain can't do, and what to check when something
doesn't work. Many single questions are already answered in the
[FAQ](faq.md); this page links there instead of repeating them.

Most answers point to a line in the **live log**: tray menu → *Live log…*,
or start Refrain with `refrain --debug`. The same lines are written to
`~/.local/state/refrain/refrain.log`.

## Known limitations

### Linux only

Refrain needs Linux with a D-Bus session bus and Python 3.11 or newer.
There is no Windows or macOS version, and none is planned: on those two
systems Apple Music already has grown-up Discord integrations, and Linux is
where that was missing.

### A system tray is required

Refrain has no main window. It lives in the system tray and refuses to
start without one.

- **KDE Plasma, Cinnamon, LXQt, Budgie:** works out of the box.
- **GNOME:** install the *AppIndicator and KStatusNotifierItem Support*
  extension. GNOME has no tray without it.
- **MATE:** install `mate-applet-statusnotifier`.
- **XFCE:** add `xfce4-statusnotifier-plugin` to your panel.
- **Hyprland, Sway, i3, river:** use a status bar with a tray
  (StatusNotifierItem), for example waybar's `tray` module.

### Browsers

There is no Apple Music app for Linux, so Refrain reads Apple Music Web
(`music.apple.com`) from your browser over MPRIS, the standard Linux
interface for media players.

- Tested: **Firefox**, **Zen**, **Google Chrome**, **Chromium** and
  **Brave**, on KDE Plasma.
- Firefox and Zen report the Apple Music tab themselves.
- Chrome, Chromium and Brave don't say which page is playing. Refrain
  gets that from KDE's Plasma Browser Integration: the
  `plasma-browser-integration` package plus the *Plasma Integration*
  extension in the browser.
- Other browsers are recognised by name (Vivaldi, Edge, Opera,
  LibreWolf, Floorp and more) but haven't been tested.
- Browsers installed as a **Snap** can't publish MPRIS at all. See the
  [FAQ](faq.md#refrain-isnt-picking-up-my-browser).

### Bluetooth needs BlueZ and a cooperative phone

The Bluetooth source reads what a connected phone or tablet sends over
AVRCP. That needs BlueZ running on your computer and a device that
actually sends title and artist. Refrain doesn't pair devices itself.
Apps that aren't music (video, live streams, podcasts) are left out on
purpose. Setup: [Bluetooth quick-start](bluetooth.md).

### Songs the iTunes catalog doesn't know

Apple Music in the browser doesn't report a usable song length, so
Refrain looks the length up in the iTunes catalog, together with the
cover. For a song the catalog doesn't have:

- there is no cover and no total time, only the elapsed time;
- Refrain measures the length itself, but only from two full plays from
  start to finish that agree to within two seconds;
- until then the song can't count as listened, so it isn't scrobbled to
  Last.fm and doesn't stay in *Recently played*.

Once measured, the live log says
`Length: <artist> — <title> runs 3:12, measured from whole plays`.

### The Discord desktop app is required

Refrain talks to Discord through a local socket that only the desktop
app opens. **Discord in a web browser can't receive Rich Presence**,
from Refrain or from anything else.

Discord installed as a **Flatpak** or **Snap** keeps that socket inside
its sandbox. Refrain looks in the usual places and links the socket to
where it's expected. When that happens, the live log says
`Bridged sandboxed Discord IPC socket: …`. If your sandboxed Discord is
still not found, the Discord package from discord.com or your
distribution avoids the sandbox.

Discord plus a second client such as Vesktop are two separate programs.
By default Refrain only talks to the first one it finds; see
[Discord shows nothing](#discord-shows-nothing).

### One Apple Music tab at a time

Refrain follows one Apple Music tab. If several tabs are open, a
playing tab wins over a paused one, and the tray buttons control that
tab. With two tabs playing at the same time, which one wins is not
defined. Keep one Apple Music tab playing.

## Troubleshooting

### Refrain doesn't start

When Refrain doesn't start there is no live log. Look at the end of
`~/.local/state/refrain/refrain.log`, or start `refrain` in a terminal:
it prints the same lines there.

**No system tray**

- *What you see:* a dialog "No system tray".
- *Why:* your desktop has no tray Refrain can live in.
- *What to do:* see [A system tray is required](#a-system-tray-is-required),
  then start Refrain again.
- *Log:* `No system tray available — refusing to start`

**A system library is missing**

- *What you see:* a notification "Refrain can't start", or in a
  terminal: `Refrain can't open a window: Qt needs libxcb-cursor.so.0,
  which this system doesn't have.` On common distributions an
  `Install with:` line with the right command follows.
- *Why:* Qt, the toolkit behind Refrain's windows, needs a few system
  libraries that minimal installs leave out.
- *What to do:* run the command it shows, or install the named library
  with your package manager.
- *Log:* the same message on one line.

**Already running**

- *What you see:* normally nothing but the running Refrain's Status
  window — a second start asks it to show itself and then exits. The
  dialog "Refrain is already running." only appears when the running one
  doesn't answer within two seconds (it hangs, or is just quitting).
- *Why:* only one Refrain runs at a time.
- *What to do:* use the tray icon of the running one. If you can't find
  it, see the [FAQ](faq.md#refrain-is-already-running-but-no-window-opens).
  To end it, `pkill refrain`.
- *Log:* `Asked the running Refrain to show its window`, or
  `The running Refrain did not answer Activate: …` followed by
  `Another Refrain owns io.github.Rockykln.Refrain — not starting a second one`

**No session bus**

- *What you see:* a dialog "D-Bus session bus unavailable".
- *Why:* Refrain needs the D-Bus session bus. It isn't reachable, which
  mostly happens outside a normal desktop session (over SSH, in a
  container, on a minimal system).
- *What to do:* start Refrain from your desktop session. Check that
  `dbus-daemon` (or `dbus-broker`) runs and that
  `DBUS_SESSION_BUS_ADDRESS` is set.
- *Log:* `Session bus unreachable: …` and `Cannot start without a session bus`

**The AppImage doesn't open**

The AppImage needs FUSE 2. See [Install](../README.md#install) for the
package name on your distribution, or start it with
`--appimage-extract-and-run`.

### No song is detected

- *What you see:* music plays in the browser, but the tray shows no
  song.
- *What to look for:* a line `Track change [mpris]: <title> — <artist>
  (playing)`. If it never appears, Refrain doesn't see the tab.

Check, in this order:

1. *Settings → Sources*: *Enable browser source* is on, and your browser is
   ticked under *Detected browsers*. A browser that isn't listed goes
   into *Other (comma-separated)*; see the
   [FAQ](faq.md#how-do-i-add-a-browser-that-isnt-in-the-settings-list).
2. In Chrome, Chromium or Brave: Plasma Browser Integration is installed
   and the extension is enabled. Without it Refrain can't tell which
   tab plays Apple Music.
3. The browser publishes MPRIS at all. `playerctl -l` lists every player;
   the [FAQ](faq.md#refrain-isnt-picking-up-my-browser) explains what to
   do when yours is missing (Firefox setting, Snap and Flatpak browsers).
4. The Apple Music tab is the one playing, and only one is playing.

### Discord shows nothing

First check that a song is detected (see above). The status is also
cleared on purpose while the music is paused
([FAQ](faq.md#the-discord-status-disappears-when-i-pause)).

The tray menu shows the connection: *Discord: connected*,
*Discord: not connected*, or *Discord: rejected — check Application ID*.
A few seconds after start-up the live log has a line starting with
`[startup-check] Discord:`.

**No Application ID**

- *Why:* Refrain needs your own Discord Application ID.
- *What to do:* follow [First-time setup](../README.md#first-time-setup).
- *Log:* `Discord RPC disabled — no client_id configured.`

**Discord isn't running, or runs in the browser**

- *Why:* nothing is listening for Refrain. Web Discord never listens.
- *What to do:* start the Discord desktop app. While music plays,
  Refrain tries again at least every 15 seconds; there's no need to
  restart it.
- *Log:* `[startup-check] Discord: no client running (nothing listening on discord-ipc-0..9)`

The line `[startup-check] Discord: client is running (…) — not dialled
yet` is normal: Refrain connects once something plays.

**The Application ID is rejected**

- *What you see:* *Discord: rejected — check Application ID* in the tray.
- *Why:* Discord answered but refused the ID. Either the ID is wrong,
  or Discord isn't signed in.
- *What to do:* check the ID in *Settings → General*. The
  [FAQ](faq.md#how-do-i-know-my-discord-application-id-is-right) shows
  how to let Refrain look up the application's name. Make sure you're
  signed in to Discord.
- *Log:* `Discord RPC handshake rejected on …` and
  `[startup-check] Discord: handshake REJECTED (…)`

**Privacy is set to Off**

- *Why:* *Settings → General → Privacy → Off* clears the Discord status
  and pauses scrobbling. The tray and controls keep working.
- *What to do:* set it to *Full*, or to *Minimal* for a plain
  "Listening to music".

**Discord hides your activity**

Discord has its own switch for showing activity to others, under
*Activity Privacy* in Discord's settings. If it's off, Refrain connects
fine but nobody sees the status.

**Several Discord clients**

- *Why:* Discord and, for example, Vesktop each open their own
  connection. A status sent to one doesn't show in the other, and
  Refrain uses the first one it finds.
- *What to do:* tick *Send the status to every running Discord client*
  in *Settings → General*.
- *Log:* `Discord IPC: 2 clients listening (discord-ipc-0, discord-ipc-1) — using discord-ipc-0`

### The cover is missing

The [FAQ](faq.md#my-track-shows-in-discord-but-the-cover-art-is-missing)
covers this in detail. In short:

- *Look up songs in Apple's catalog* is on in *Settings → General → Privacy*;
- the song is in the iTunes catalog. If not, the log says
  `Cover lookup: no catalog match for <artist> — <title>`, and Discord
  shows your application's icon instead, if you uploaded one;
- privacy is *Full*. *Minimal* never sends a cover.

A notification without the cover is a different case:
[FAQ](faq.md#notifications-appear-with-the-refrain-logo-not-the-cover).

### Last.fm isn't scrobbling

The tray menu has a Last.fm line once scrobbling is enabled and
connected. At start-up the live log has a line starting with
`[startup-check] Last.fm:`. The walkthrough is in
[Last.fm scrobbling](lastfm.md), with more cases under its
[Troubleshooting](lastfm.md#troubleshooting).

**Not connected**

- *Why:* scrobbling is enabled, but no account is connected yet.
- *What to do:* *Settings → Last.fm* → *Connect…*, approve in the
  browser, then *Apply*.
- *Log:* `[startup-check] Last.fm: not connected (missing credentials)`

**Session expired**

- *What you see:* *Last.fm: session expired — reconnect* in the tray.
- *Why:* the access was revoked on last.fm, or the API key changed.
- *What to do:* connect again as above. Nothing is lost: songs waiting
  in the queue, and the ones you play meanwhile, are sent afterwards.
- *Log:* `[startup-check] Last.fm: session REJECTED (…)` or
  `Last.fm session invalid (…) — reconnect in Settings → Last.fm.`

**The song doesn't count**

- *Why:* Last.fm's rule. A song counts after half its length or four
  minutes, and only if it's longer than 30 seconds. Skipped songs and
  short clips never count. Neither does a song whose length Refrain
  doesn't know yet (see
  [Songs the iTunes catalog doesn't know](#songs-the-itunes-catalog-doesnt-know)).
- *Log:* a song that counts gives `Scrobble queued: <artist> — <title>`.
  No such line means it didn't count.

**Offline, or Last.fm is down**

- *Why:* scrobbles wait in a queue on disk
  (`~/.local/state/refrain/scrobble_queue.jsonl`) and are sent later.
  The queue keeps up to 1000 songs; beyond that the oldest are dropped.
- *What to do:* nothing. They go out on the next song once Last.fm can
  be reached.
- *Log:* `Scrobble submit failed (…) — 3 entries kept queued`, later
  `Scrobbled 3 queued track(s) to Last.fm`.

Privacy *Off* stops scrobbling too.

### A Bluetooth device isn't detected

- *What to look for:* a line `Track change [bluetooth]: <title> — <artist>
  (playing)`.

Check:

1. BlueZ runs and the Bluetooth adapter is on. `bluetoothctl show` lists
   the adapter.
2. The phone is paired **and connected**, not only paired, and its audio
   profile is enabled. Pair it in your desktop's Bluetooth settings.
3. *Settings → Sources*: *Enable Bluetooth source* is on, and the device
   picker shows your phone or *(auto-detect)*.
4. Music plays on the phone. A browser that plays at the same time wins
   over Bluetooth, so pause it.
5. The phone sends title and artist over AVRCP. Some phones and apps
   don't.

A line `Bluetooth: <app> is playing — not Apple Music, ignored` means the
phone named an app other than Apple Music. The
[Bluetooth quick-start](bluetooth.md#troubleshooting) goes through
each step with commands.

### Updates

Refrain checks GitHub once a day and shows *Update available* in the
tray. *Settings → Updates → Check for updates now* checks right away.
What happens next depends on how Refrain was installed:

| Installed with | What Refrain does |
|----------------|-------------------|
| AppImage | Downloads the new AppImage, checks it, replaces the old file and asks for a restart. |
| pipx | Runs `pipx upgrade refrain`. |
| pip | Runs `pip install --upgrade refrain`. |
| AUR | Opens a terminal with your AUR helper, for example `yay -Syu refrain`. |
| Flatpak you built yourself | Opens a terminal with `flatpak update -y io.github.Rockykln.Refrain`. |
| Distribution package | Nothing. Update with your package manager. |
| Source checkout | Nothing. Run `git pull` and `pip install -e .`. |

If no terminal can be found, Refrain shows the command to copy instead.
Restart Refrain after an update. If the check fails, the log says
`Update check failed: …`, usually because GitHub couldn't be reached.

### Logs, and sharing them safely

- **Where:** `~/.local/state/refrain/refrain.log`, plus up to three
  older files `refrain.log.1` to `.3`. Only you can read them.
  `crash.log` in the same folder is only written when Refrain crashes
  ([FAQ](faq.md#refrain-was-suddenly-gone)).
- **Live:** tray → *Live log…*. *Copy all* copies what the window
  holds.
- **More detail:** *Settings → Advanced → Log level → DEBUG*, or start
  with `refrain --debug`.

Before you post a log in an issue, read it through:

- It contains your **listening history**: every song change, with title
  and artist, and every scrobble.
- At **DEBUG** level it also contains the **last two bytes of your
  Bluetooth device's address**; the rest is replaced with `XX`.
- It contains file paths, which include your user name.

Cut the log down to the part around the problem, and replace what you
don't want to share. Issues on GitHub are public. You can also send the
log by email instead; see [Contact](../README.md#contact).
