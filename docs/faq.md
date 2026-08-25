# FAQ

## Does Refrain work without the Apple Music desktop app?

There is no Apple Music desktop app on Linux. Refrain reads metadata from
the **browser** (Apple Music Web at `music.apple.com`) and from
**Bluetooth AVRCP** when an iPhone or other device is connected. Both are
detected automatically.

## My track shows in Discord, but the cover art is missing.

Three things to check:

1. *Settings → General → Fetch album cover art from iTunes* is on.
2. The track exists in the iTunes catalog. Obscure releases sometimes
   don't. Refrain caches negative results so it stops trying after the
   first miss.
3. Your Discord client is running. Refrain only sends the URL; Discord
   fetches the actual image.

## Notifications appear with the Refrain logo, not the cover.

Refrain delays notifications by 1.5 s so the cover image has time to
download. If the iTunes lookup is slower than that (rare on home internet,
common on poor mobile tethering), the notification falls back to the
themed icon. Subsequent plays of the same track will use the cached
image.

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

## Bluetooth: how do I get my iPhone / phone showing up?

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

## The elapsed time freezes mid-song, or disappears.

Apple Music's web player misreports position in two ways. It counts
`Position` across the whole queue instead of restarting it per track, so
the third song of a session reports a position far past its own length —
which used to pin the tray at `2:24 / 2:25 (–0:00)`. And it sometimes
stops refreshing `Position` altogether while still reporting the track as
playing.

Refrain resolves the position in three tiers. It uses what the source
reports while that holds up; when it doesn't, it counts from the start of
the track itself, discounting pauses; and when it has no honest answer —
a song that was already playing when Refrain started, so there is no
witnessed start to count from — it hides the time rather than showing a
wrong one. That is why the progress line and Discord's timer sometimes
disappear for one track and come back at the next track change.

`advanced.position_stall_s` (default 4) is how many seconds a playing
track's position may stand still before Refrain stops trusting it; 0
switches that check off. The live log names the tier on every change:
*"Position: reported → computed"*.

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
shows the same stream live.

## The Refrain process won't quit.

`pkill refrain` kills it cleanly via `SIGTERM`. The handler clears the
Discord status and releases the D-Bus name before exiting.
