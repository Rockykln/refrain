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
