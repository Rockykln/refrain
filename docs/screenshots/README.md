# Screenshots

The README references the images in this directory. They are captured
from the real windows, not mocked up, and they carry the version number
in the settings footer — so **every release needs a fresh set**, along
with anything else that changes what a window says (a new tab, a new
control, a reworded hint).

| File                    | What it shows                                                                 |
|-------------------------|-------------------------------------------------------------------------------|
| `settings-general.png`  | Discord Client ID with the resolved application name beside it, per-source and all-clients toggles, Privacy, notifications, cover art, autostart |
| `settings-sources.png`  | Browser source + the detected-browser picks, Bluetooth toggle and paired-device dropdown |
| `settings-lastfm.png`   | Opt-in scrobbling, API key + shared secret, a connected account, "Now playing" |
| `settings-updates.png`  | Auto-check, current / latest version, last-checked, the inline release-notes pane |
| `settings-advanced.png` | Poll interval, notification delay, cover cache size, language, log level, restart / reset / uninstall |
| `legal.png`             | The Legal notice behind the footer's *Legal* button                           |
| `welcome.png`           | First-run wizard, with both live diagnostics resolved rather than mid-check    |
| `update-dialog.png`     | The update-available popup                                                    |
| `live-log.png`          | The live-log window with a real session's records in it                       |
| `tray-menu.png`         | The tray menu: track / artist / progress / Discord + Last.fm rows, transport, update, Settings, Live log, Restart, Quit |
| `notification.png`      | A track-change desktop notification                                           |
| `discord-rpc.png`       | Discord's "Listening to" card                                                 |

## Capturing

On Wayland nothing may screenshot another window unattended, so these are
taken by hand with Spectacle:

```sh
# Rectangular region, 5 s delay — long enough to open a menu or a popup
# and let it settle before the shutter fires.
spectacle -bnro docs/screenshots/<name>.png
```

`tray-menu.png` and `notification.png` need the delay: right-click the
tray icon, or trigger a track change, and let the shutter catch it.

For `discord-rpc.png`, have Refrain running with a configured Application
ID and play something so a status is actually published — pick a
full-length track, since on a DJ-mix playlist each entry is under a
minute and the card changes while you are still framing the shot. Then
click your own avatar in Discord to open the profile popout.

## Image conventions

- **Format**: PNG, lossless.
- **Theme**: KDE's Breeze Dark. Refrain follows the system theme, so
  capture from a Breeze Dark session and the set stays consistent.
- **Language**: English, to match the README. On a translated desktop,
  set `advanced.language` to `en` *and* run with `LANGUAGE=en_US` — Qt's
  own stock buttons ("Close", "Cancel") follow the locale rather than
  Refrain's translator, and a German "Schließen" in an otherwise English
  dialog is exactly the kind of thing that gets noticed.
- **Cropping**: trim to the element itself, then give it an even margin
  of its own background — roughly 24 px looks right at these sizes.
  Nothing of the desktop behind it in frame.
- **Consistency**: the tray menu, the live log, the notification and the
  Discord card all show *the same track*. A set that disagrees with
  itself about what is playing reads as a set of mockups.
- **No real personal data.** No real Client ID, no Last.fm session, no
  MAC address, no Discord username or avatar. The Discord popout carries
  all three of the last — crop to the activity card alone.
