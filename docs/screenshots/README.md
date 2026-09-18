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
| `settings-history.png`  | Recently played on/off and how many songs to keep                             |
| `settings-updates.png`  | Auto-check, current / latest version, last-checked, the inline release-notes pane |
| `settings-advanced.png` | Poll interval, notification delay, cover cache size, language, log level, restart / reset / uninstall |
| `legal.png`             | The Legal notice behind the footer's *Legal* button                           |
| `welcome.png`           | First-run wizard, with both live diagnostics resolved rather than mid-check    |
| `update-dialog.png`     | The update-available popup                                                    |
| `live-log.png`          | The live-log window with a real session's records in it                       |
| `history.png`           | Recently played: covers, lengths, times, sources, the song playing on top, search and source filter |
| `tray-menu.png`         | The tray menu: track / artist / progress / Discord + Last.fm rows, transport, update, Recently played, Settings, Live log, Restart, Quit |
| `notification.png`      | A track-change desktop notification                                           |
| `discord-rpc.png`       | Discord's "Listening to" card                                                 |
| `demo-cover.png`        | Not a screenshot: the cover of the demo track, drawn for these shots — see below |

## Rendering

Every window is rendered from the real code, filled with demo data, in
Breeze Dark and Breeze Light (the `-light` files):

```sh
python docs/screenshots/render.py 0.5.3
```

The script starts its own invisible KWin display, so nothing appears on
screen, and writes the shots straight into this directory. Run it with
the release's version number: the settings footer, the Updates tab and
the update popup show it, and the release notes come from that version's
section of `CHANGELOG.md` (or *Unreleased*, before the tag). It needs
KDE Plasma's `kwin_wayland` and the Breeze colour schemes.

`notification.png` is drawn by the script too — Plasma draws the real
one, so the script rebuilds its layout.

`discord-rpc.png` is the one shot taken by hand, from Discord itself:
publish the demo track — *Glass Tides* by Neon Harbor, from *Low Light*,
with `demo-cover.png` as the cover — open your own profile, and capture
the activity card once in Discord's dark and once in its light theme.
Crop to the card alone, below its "Listening to" line (which follows
Discord's language), with an even margin and rounded, transparent
corners.

## The demo cover

`demo-cover.png` is abstract art drawn for these screenshots, with no
lettering and no one else's work in it. Real covers belong to their
labels and artists, and the ones Refrain fetches from Apple are licensed
for showing in the app, not for public screenshots. Every cover in the
set is drawn the same way; this is the one the demo track wears.

## Image conventions

- **Format**: PNG, lossless.
- **Theme**: KDE's Breeze Dark, plus a Breeze Light copy of each shot
  (`-light`). The README shows whichever matches the reader's GitHub
  theme through `<picture>`.
- **Language**: English, to match the README. The script sets both
  `advanced.language = "en"` and `LANGUAGE=en_US` — Qt's own stock buttons
  ("Close", "Cancel") follow the locale rather than Refrain's translator.
- **Cropping**: trim to the element itself, then give it an even margin
  of its own background — roughly 24 px looks right at these sizes.
  Nothing of the desktop behind it in frame.
- **Consistency**: the tray menu, the live log, the notification and the
  Discord card all show *the same track*. A set that disagrees with
  itself about what is playing reads as a set of mockups.
- **No real personal data.** No real Client ID, no Last.fm session, no
  MAC address, no Discord username or avatar. The Discord popout carries
  all three of the last — crop to the activity card alone.
