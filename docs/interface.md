# Interface

A tour of what Refrain puts on your screen: the tray icon and its menu,
the Status window, the Recently played list and the desktop notifications.

## Tray

<p align="center">
  <picture>
    <source media="(prefers-color-scheme: light)" srcset="screenshots/tray-menu-light.png"/>
    <img src="screenshots/tray-menu.png" alt="Tray menu" width="320"/>
  </picture>
</p>

Every item carries a theme-matched icon (freedesktop icon names on
Plasma / GNOME / Breeze; bundled accent SVGs for Update and Quit) —
no unicode-glyph prefixes.

| Item              | What it does                                              |
|-------------------|-----------------------------------------------------------|
| Title             | Currently playing track (click opens the Status window)   |
| Artist • Album    | Currently playing artist + album (hidden when idle)       |
| X:XX / Y:YY (–Z:ZZ) | Elapsed / track length / remaining (hidden when idle)   |
| Discord: …        | What Discord shows right now: *ready — waiting for music*, *visible on your profile*, *showing “Listening to music”*, *hidden while paused*, *hidden — sharing is off*, *app isn't running*, *not answering*, *not set up — add your Application ID*, *not logged in — log in to show your status*, *Application ID rejected — check it* |
| Last.fm: …        | *scrobbling as …*, *N scrobbles waiting*, *sign-in expired — reconnect* (hidden while Last.fm was never set up) |
| Previous          | Skip backward on the active source                        |
| Play / Pause      | Toggle on the active source (label follows playback state)|
| Next              | Skip forward on the active source                         |
| Update available — vX.Y.Z | Only visible when a newer release exists          |
| Recently played…  | Open the history window (hidden while the history is off) |
| Settings…         | Open the settings window                                  |
| Troubleshooting ▸ | *Live log…* and *Restart Refrain* (releases the D-Bus name and Discord connection, then starts the same binary again) |
| Quit Refrain      | Stop the daemon and exit                                  |

Left-click the tray icon opens the Status window — which also holds
*Pause sharing* — **middle-click toggles play/pause**,
right-click shows this menu. (DBusMenu keeps an open
menu's text static, so the progress line is a snapshot from when you
opened it — hover the tray icon for a live-updating tooltip.)

## Status window

<p align="center">
  <picture>
    <source media="(prefers-color-scheme: light)" srcset="screenshots/status-light.png"/>
    <img src="screenshots/status.png" alt="Status window" width="420"/>
  </picture>
</p>

A small window that answers "is it working?": the song playing now, one
line each for Discord and Last.fm in plain words, and a button only when
there is something to do (*Set up…*, *Fix…*, *Reconnect…*). Below are
player controls, the time played so far, the songs before this one,
*Pause sharing*, *Settings…* and a link to the project on GitHub. After a
crash it also carries a banner naming the report. The list holds as many
songs as the window has room for, so making the window taller shows more
and there is never a scroll bar. It updates live; a title too long for the
window scrolls past twice, and resting the mouse on a song in the list
scrolls that one once. While the window is on screen Refrain keeps quiet
and sends no notifications.

Every link that leaves Refrain — a song on Apple Music, the GitHub page,
Last.fm — asks first and names the page it is about to open.

It opens when you start Refrain from the menu, when you click the tray
icon, and when you start Refrain again while it is already running. A
start at login (`--silent`, what autostart uses) stays in the tray and
opens it only when something needs you — Discord not set up, the
Application ID rejected, the Last.fm sign-in expired — once per problem.

## Recently played

<p align="center">
  <picture>
    <source media="(prefers-color-scheme: light)" srcset="screenshots/history-light.png"/>
    <img src="screenshots/history.png" alt="Recently played" width="520"/>
  </picture>
</p>

*Tray → Recently played…* lists the last songs Refrain saw — 30 by
default, up to 100 — with cover, length, when each started, where it
came from (which browser, which Bluetooth device) and whether it went to
Last.fm. A song shows up the moment it plays and stays once it counts
as listened: half its length or four minutes, the rule Last.fm uses, so
skipping through a playlist leaves nothing behind. Restarting Refrain
mid-song doesn't lose or duplicate it; a song heard through and played
again is listed twice, once per play.

- Click a song to open it in Apple Music — in the browser it played in,
  while that one is still open; a search when the song's page isn't
  known.
- Right-click to copy artist and title or to remove the song from the
  list; *Clear history…* empties it.
- From ten songs on there's a search over title, artist and album —
  blind to case and accents, found words marked — and with more than
  one source a filter by source. <kbd>Ctrl</kbd>+<kbd>F</kbd> jumps into
  the search, <kbd>Esc</kbd> clears it.
- The window opens at the size you last left it.

The list stays on your machine (`history.json`, readable only by you)
and is never sent anywhere, so the privacy mode doesn't affect it.
*Settings → Recently played* turns it off — which deletes the file — or changes
how many songs it keeps.

## Notifications

Refrain can show a desktop notification on each track change, with the
album cover, song title, artist and album — the same data that's going to
your Discord status. It is off for new installs; turn it on under
*Settings → General → Behavior*. Configs from before 0.5.3 keep the setting
they had.

<p align="center">
  <picture>
    <source media="(prefers-color-scheme: light)" srcset="screenshots/notification-light.png"/>
    <img src="screenshots/notification.png" alt="Track-change notification" width="520"/>
  </picture>
</p>
