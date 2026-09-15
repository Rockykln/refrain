# FAQ

## Does Refrain work without the Apple Music desktop app?

There is no Apple Music desktop app on Linux. Refrain reads metadata from
the **browser** (Apple Music Web at `music.apple.com`) and from
**Bluetooth AVRCP** when a phone or other device is connected. Both are
detected automatically.

## My track shows in Discord, but the cover art is missing.

Three things to check:

1. *Settings → General → Fetch album cover art from iTunes* is on.
2. The track exists in the iTunes catalog. Refrain asks the store of your
   own country first, then the US store, with "feat." credits and
   remaster tags left out of the search, and falls back to the first of
   several credited artists. It only takes a result whose artist *and*
   title match — no cover is better than a stranger's. A song the catalog
   doesn't have is asked about again after three days; a lookup that
   failed because iTunes couldn't be reached, after a minute. The log
   says *"Cover lookup: no catalog match for …"* for a real miss.
3. Your Discord client is running. Refrain only sends the URL; Discord
   fetches the actual image.

The same lookup supplies the song's length, so a song without a cover
usually shows no total time either.

## Notifications appear with the Refrain logo, not the cover.

Refrain waits up to two seconds for the cover image to download before
it shows the notification. If the iTunes lookup is slower than that
(rare on home internet, common on poor mobile tethering), the
notification appears with the themed icon and is swapped for the cover
once it arrives. Later plays of the same track use the cached image.

## Does Refrain support Spotify / Tidal / YouTube Music?

No, and there are no plans to. Refrain is Apple-Music-focused. Other
services have first-class Discord-RPC apps already.

## I don't have a system tray (GNOME Wayland).

Install the **AppIndicator and KStatusNotifierItem** GNOME Shell
extension. Refrain's tray uses `QSystemTrayIcon`, which on GNOME requires
that extension to be visible.

## Refrain isn't picking up my browser.

Refrain reads track metadata from the browser's MPRIS publication. If
nothing shows up while Apple Music is playing, run `playerctl -l` in a
terminal — it lists every MPRIS player on your session bus.

- **Empty list (or no Firefox / Chromium entry):** the browser isn't
  publishing MPRIS at all. Common causes:
  - **Firefox**: MPRIS is off by default on some installs. Open
    `about:config` → set `media.hardwaremediakeys.enabled = true` →
    fully quit Firefox (close all windows + wait for `pgrep firefox`
    to be empty) → relaunch.
  - **Snap-confined browsers** (Snap Firefox, Snap Chromium on
    Ubuntu): the snap sandbox blocks D-Bus session-bus access for
    MPRIS. Switch to a deb-channel browser:
    - Mozilla's official Firefox deb:
      <https://support.mozilla.org/en-US/kb/install-firefox-linux>
    - Brave deb: <https://brave.com/linux/>
    - Chromium from Debian/Ubuntu deb (not the Snap).
  - **Flatpak browsers** without `--talk-name=org.mpris.MediaPlayer2.*`:
    Flathub builds usually have it; self-built ones may not.
- **Browser shows but track isn't picked up:** check that `xesam:url`
  contains `music.apple.com`:
  ```sh
  playerctl --player=firefox metadata | grep xesam:url
  ```
  If the URL field is empty or points elsewhere, Apple Music's tab
  isn't the active media tab — switch to it and start playback.

## How do I add a browser that isn't in the Settings list?

*Settings → Sources → Detected browsers → Other (comma-separated)*.
Enter a substring of the browser's MPRIS bus name. Find it via
`playerctl -l` while a media tab is playing — e.g. for Floorp the
substring is `floorp`. Save with *Apply*.

## How do I know my Discord Application ID is right?

Tick *Look up the application's name on Discord* in
*Settings → General*. The application's name then appears next to the
Client ID — the same name Discord puts after "Listening to". If it says
*No such Discord application*, the ID is wrong; if it names something
you don't recognise, you pasted a different app's ID.

It is opt-in because it is the only thing Refrain would send to
Discord's *servers* rather than to your local Discord client. When you
switch it on it sends the Application ID and nothing else, and the
answer is cached for four hours. See [`PRIVACY.md`](../PRIVACY.md).

## Bluetooth: how do I get my phone showing up?

See the dedicated walkthrough at [`docs/bluetooth.md`](bluetooth.md).
Quick version: pair the phone in your desktop's Bluetooth manager,
connect it, start music on the phone, then in Refrain
*Settings → Sources → Bluetooth* turn the toggle on and pick the
device from the dropdown.

## Refrain is already running but the settings window won't reopen.

Click the tray icon. The settings window is normally hidden, not closed —
clicking the tray brings it back. *Quit Refrain* from the tray menu fully
exits.

## What does "Privacy: Off" do?

The Discord status is cleared and never updated. The tray icon, player
controls, and notifications keep working. Use this when streaming or when
you don't want your Discord profile to surface what you're listening to.

## The Discord status disappears when I pause.

That's intentional — pausing is implicitly "not listening". If you want
the status to persist while paused, tell us in
[a feature request](https://github.com/Rockykln/refrain/issues/new?template=feature_request.yml).

## The elapsed time freezes mid-song, jumps back to the start, or disappears.

Apple Music's web player doesn't report a per-track position. It reports
a position in the *stream*, which carries on across track boundaries, and
its `mpris:length` is a buffer marker rather than a song length. Three
songs into a session that reads as 11:08 elapsed of 6:52 on a 2:25
track — which used to pin the tray at `2:24 / 2:25 (–0:00)`. It also
sometimes stops refreshing the position altogether while still reporting
the track as playing.

On Plasma there is a second layer to it. Plasma's browser integration
publishes its own MPRIS player, and Refrain prefers it because it is the
one reporting a title and an artist at all — but it has its own version
of the problem: its position and length describe the album's looping
artwork video on the page, not the song, so the position falls back to
zero every eight to sixteen seconds. That is what made the elapsed time
restart over and over mid-song. Refrain never shows a position like that;
it counts from the song's start itself — and after a restart in the
middle of a song, when that start wasn't seen, it hides the time until
the next song (or the next loop of this one) begins.

Refrain resolves the position in three tiers. It uses what the source
reports while that holds up; when it doesn't, it counts from the start of
the track itself, discounting pauses and following seeks; and when it has
no honest answer — a song that was already playing when Refrain started,
so there is no witnessed start to count from — it hides the time rather
than show a wrong one. Where it *did* see the track start, that start
wins over anything the source does with its position afterwards, which
is what keeps a segment source's resets from dragging the clock back.
That is why the progress line and Discord's timer can be absent for one
track and come back at the next track change.

The song's total length then comes from the iTunes catalog, since the
player's own number doesn't describe the song. When the catalog has no
match, the tray shows the elapsed count on its own.

`advanced.position_stall_s` (default 4) is how many seconds a playing
track's position may stand still before Refrain stops trusting it; 0
switches that check off. The live log names the tier on every change:
*"Position: reported → computed"*.

## The Discord status vanishes partway through a song.

Idle detection clearing too early. It exists for a browser tab closed
without releasing its MPRIS handle — the player keeps reporting
`Playing` forever — and it fires when a track has been playing for
longer than its own length plus `advanced.idle_grace_s`. That deadline is
only as good as the length, and a catalog search that matches the wrong
record can make it far too short: 58 s for a 2:45 song, in one live
session, which cleared the status a minute in.

Two things now prevent it. The song's length comes from the player
itself wherever it reports one, with the catalog filling gaps rather
than overruling. And the deadline only measures silence — a position
that is still moving is proof the handle isn't dangling, and pushes the
deadline back. Setting `idle_grace_s = 0` disables idle detection
entirely, at the cost of a closed tab leaving a stale status behind.

## Where is my listening history, and how do I get rid of it?

*Tray → Recently played…* shows it; *Settings → History* switches it off
or sets how many songs it keeps (10 to 100, 30 by default). It lives in
`~/.local/state/refrain/history.json`, readable only by you, and is never
sent anywhere — which is why the privacy mode doesn't affect it.
Right-click a song to remove just that one, *Clear history…* empties the
list, and switching the history off deletes the file.

## A song I played isn't in the history.

A song stays once it counts as listened — half its length or four
minutes, the rule Last.fm uses. It's the time you actually heard that
counts, not where the song is: listening ten seconds and skipping to the
last ten is twenty seconds, and the song leaves again when the next one
starts. Songs of 30 s or less never count. Restarting Refrain mid-song
doesn't reset the count, as long as the same song is still playing when
it comes back and has carried on from where it was.

## A song is in the history twice.

Every play that counts gets a row of its own — a song on repeat, or
played again from the top once it had counted, is listed once per play,
and Last.fm gets a scrobble per play too. A restart in the middle of a
song is not a second play: Refrain saves where the song was, and if it
is further on from there when Refrain comes back, it stays one row. Only
if the song is back near the start — it ended and began again meanwhile
— is it a new play.

## Right-clicking Refrain in the task manager offers "Stop".

That menu is Plasma's: it adds media controls for any application that
publishes an MPRIS player, which Refrain does so Plasma's media widget
can drive it. Plasma shows "Stop" for every controllable player. Apple
Music has no stop, so there it pauses — and does nothing when the music
is already paused.

## Refrain was suddenly gone.

Look at `~/.local/state/refrain/crash.log`. If Refrain died inside Qt or
D-Bus, it holds the Python stack of every thread at that moment — attach
it to a [bug report](https://github.com/Rockykln/refrain/issues/new?template=bug_report.yml)
together with the end of `refrain.log`. `coredumpctl list` shows whether
the system recorded a crash at the same time.

## How do I update?

| Install method | How to update                                      |
|----------------|----------------------------------------------------|
| AUR            | `yay -Syu refrain` (or your AUR helper of choice)  |
| Flatpak        | `flatpak update io.github.Rockykln.Refrain`        |
| AppImage       | *Settings → Updates → Check for updates now* — Refrain replaces the running AppImage in place |
| pip            | *Settings → Updates → Check for updates now* — runs `pip install --upgrade refrain` for you |

The "Check for updates now" button always tells you the result, even
when you're already on the latest version.

## Where are the logs?

`~/.local/state/refrain/refrain.log`. They rotate at 1 MiB with three
backups. The live-log window (tray menu → *Live log…* or `--debug` flag)
shows the same stream live. `crash.log` in the same folder only ever
gets written when Refrain crashes.

## The Refrain process won't quit.

`pkill refrain` kills it cleanly via `SIGTERM`. The handler clears the
Discord status and releases the D-Bus name before exiting.
