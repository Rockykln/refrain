# Bluetooth quick-start

Refrain's Bluetooth source reads playback metadata via BlueZ AVRCP —
the standard Bluetooth audio metadata profile. Anything that pairs as
an A2DP audio source and exposes `org.bluez.MediaPlayer1` over D-Bus
will work: phones, tablets, dedicated music players, even
some car head-units.

This guide walks through getting it set up the first time on KDE
Plasma, GNOME, or any other desktop with `bluez` running. If
something is missing on your distro, the
[Troubleshooting](#troubleshooting) section at the end covers the
usual suspects.

## Prerequisites

- BlueZ ≥ 5 with the AVRCP profile enabled (default on every modern
  desktop distro).
- A Bluetooth adapter that's powered on. `bluetoothctl show` should
  list at least one controller.
- Your desktop's Bluetooth applet running (KDE's
  bluedevil-applet, GNOME's bluetooth-panel, blueman, …). Refrain
  doesn't manage pairing itself — it only reads metadata from
  already-connected devices.

If `bluetoothctl` isn't installed, install it via your package
manager:

| Distro | Command |
|---|---|
| Arch / CachyOS / Manjaro | `sudo pacman -S bluez bluez-utils` |
| Debian / Ubuntu / Mint | `sudo apt install bluez bluez-tools` |
| Fedora / RHEL / Rocky | `sudo dnf install bluez bluez-tools` |
| openSUSE | `sudo zypper install bluez` |

## Pairing

1. Put the phone (or other source) into pairing mode, usually by
   opening its Bluetooth settings.
2. Open your desktop's Bluetooth applet, scan for new devices, click
   *Pair* on the phone entry, confirm the matching PIN on both ends.
3. Enable *Audio*. Some applets surface this as a toggle after
   pairing; others auto-enable it. Verify with:
   ```sh
   bluetoothctl info <MAC>
   ```
   Look for `UUIDs: ... A/V Remote Control Target ... AudioSource ...`
   in the output. If those are missing, the phone is paired but not
   advertising AVRCP — disconnect, repair, and tick the *Audio
   profile* box this time.

## First playback

1. Make sure the phone is **connected** (not just paired). The
   applet shows a connected indicator; CLI:
   ```sh
   bluetoothctl info <MAC> | grep "Connected:"
   ```
   should show `Connected: yes`.
2. Start a song in **Apple Music** on the phone. The phone names the app
   it is playing over the AVRCP profile, and Refrain shows Apple Music
   only — another music service, a stream or a video on the same
   headphones is left out.
3. In Refrain, open *Settings → Sources → Bluetooth*:
   - Toggle **Enable Bluetooth source** on.
   - Pick the device from the dropdown. Each entry shows a paired
     device's Bluetooth name and MAC address; connected ones are marked
     *(connected)*.
   - Hit **Apply**.
4. Within ~1 s, the tray menu shows the track title + artist.
   Within ~2 s, Discord renders the listening status.

The dropdown's `(auto-detect)` entry reads whichever connected device
exposes an AVRCP player, preferring one that is playing over one that
is paused — useful if you switch between a phone and a tablet with the
same Refrain config. Picking a specific device ignores all others.

## Per-source Discord profile (optional)

You can give Bluetooth its own Discord application so the status
renders with a different icon than the browser's Apple Music
playback:

1. Register a second Discord application at
   <https://discord.com/developers/applications> — call it e.g.
   "Refrain (Bluetooth)" and upload a Bluetooth glyph as the icon.
2. Copy the new Application ID.
3. *Settings → General*, tick *Use a separate Discord application per
   source*, paste it into *Bluetooth*, then *OK*.

Refrain reconnects RPC under the per-source ID the moment a track
arrives from Bluetooth.

## Troubleshooting

### The tray shows "(nothing playing)" while music plays on the phone

- Confirm AVRCP is actually working. BlueZ publishes the phone's
  player as an object below the device, usually `…/player0`:
  ```sh
  busctl --system tree org.bluez | grep player
  busctl --system get-property org.bluez \
      /org/bluez/hci0/dev_<MAC_with_underscores>/player0 \
      org.bluez.MediaPlayer1 Track
  ```
  The second command should return the current track. If the first
  lists no player at all, the phone isn't exposing AVRCP — disconnect
  and reconnect, and check that the audio profile is enabled for the
  pairing.
- Open the live log (tray → *Live log…*) and look for
  `Track change [bluetooth]: …` lines. If you see `[mpris]` instead,
  the browser is playing too: a source that is playing wins over a
  paused one, and when both play, the browser comes first. Pause or
  close the music tab.
- An INFO line `Bluetooth: <app> is playing — not Apple Music, ignored`
  means the phone is playing something else. Apple Music counts under its
  translated names as well, and a phone that names no app at all counts too.

### Bluetooth errors in the log

Refrain first asks D-Bus whether BlueZ is running at all, so a system
without `bluez` (VMs, minimal installs) costs nothing and logs nothing.
Everything that can go wrong while talking to BlueZ is logged at DEBUG
level only, so it shows up when you start Refrain with `--debug` or set
*Settings → Advanced → Log level* to DEBUG:

- `Bluetooth: cannot reach system bus: …`
- `Bluetooth: NameHasOwner(org.bluez) failed: …`
- `Bluetooth: GetManagedObjects failed: …`
- `Bluetooth player <path> unreadable: …`

Refrain retries on the next poll, so a single line after a disconnect
or a BlueZ restart is harmless.

### The dropdown only offers "(auto-detect)"

Either BlueZ isn't running or no device is paired yet. Pair at least
one device first (Section "Pairing" above), then click **Refresh**
next to the dropdown.

### Track shows but Play/Pause/Next/Previous don't work

Controls go over AVRCP too, and whether they work depends on the app
playing on the phone. Some battery-saver settings stop apps from
reacting to them — check the app's battery / background settings on
the phone.

### Multiple connected devices, wrong one gets picked

`(auto-detect)` prefers a device that is playing, then one that is
paused. If two are playing at once, or you always want the same one,
set *Settings → Sources → Bluetooth → Device* to that device.
