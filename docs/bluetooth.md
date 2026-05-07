# Bluetooth quick-start

Refrain's Bluetooth source reads playback metadata via BlueZ AVRCP —
the standard Bluetooth audio metadata profile. Anything that pairs as
an A2DP audio source and exposes `org.bluez.MediaPlayer1` over D-Bus
will work: iPhones, Android phones, dedicated music players, even
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

1. Put the phone (or other source) into pairing mode. On iOS this is
   *Settings → Bluetooth*; on Android it's *Settings → Connected
   devices*.
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
2. Start music on the phone. Spotify, Apple Music, the iOS Music
   app, anything that publishes track metadata via the AVRCP
   profile.
3. In Refrain, open *Settings → Sources → Bluetooth*:
   - Toggle **Enable Bluetooth source** on.
   - Pick the device from the dropdown. It should show the phone's
     Bluetooth name (e.g. *Rocky's iPhone*) and its MAC address.
   - Hit **Apply**.
4. Within ~1 s, the tray menu shows the track title + artist.
   Within ~2 s, Discord renders the listening status.

The dropdown's `(auto-detect)` entry picks whichever device is
currently exposing AVRCP — useful if you switch between phone and
headphones with the same Refrain config. The MAC-pinned variant is
stricter but stable when multiple sources are connected at once.

## Per-source Discord profile (optional)

You can give Bluetooth its own Discord application so the status
renders with a different icon than the browser's Apple Music
playback:

1. Register a second Discord application at
   <https://discord.com/developers/applications> — call it e.g.
   "Refrain (Bluetooth)" and upload a Bluetooth glyph as the icon.
2. Copy the new Client ID.
3. *Settings → General → Bluetooth Client ID* — paste, *Apply*.

Refrain reconnects RPC under the per-source ID the moment a track
arrives from Bluetooth.

## Troubleshooting

### Refrain says "no track" while music is clearly playing on the phone

- Confirm AVRCP is actually working:
  ```sh
  busctl --system call org.bluez /org/bluez/hci0/dev_<MAC_with_underscores> \
      org.freedesktop.DBus.Properties Get ss org.bluez.MediaPlayer1 Track
  ```
  This should return the current track. If it errors with
  `org.bluez.Error.DoesNotExist`, the phone isn't exposing AVRCP — try
  disconnect / reconnect, and verify the *Audio profile* box on the
  pairing.
- Check the tray-menu source label or the live log
  (tray → *Live log…*). Look for `Track change [bluetooth]: …` lines.
  If you see `[mpris]` instead, the browser is winning the source
  race — close the music tab so Bluetooth becomes the only candidate.

### `bluetoothd` D-Bus activation timeout warning in the log

You'll see something like
`Bluetooth: GetManagedObjects failed: ... service_start_timeout=25000ms`
on a system without `bluez` installed (typical of VMs and minimal
installs). v0.2.3+ fast-fails before the activation timeout fires;
on older builds you'd want to disable the Bluetooth source toggle.

### The dropdown is empty

That means BlueZ is running but no `org.bluez.Device1` entries exist.
Pair at least one device first (Section "Pairing" above), then click
**Apply** in Refrain to refresh the dropdown.

### Track shows but Play/Pause/Next/Previous don't work

AVRCP control depends on the phone's app supporting
`AVRCP-CT 1.4` or higher. iOS Music, Apple Music, and Spotify all
do. Some Android battery-saver settings disable AVRCP control —
check the per-app battery / background settings on the phone.

### Multiple paired devices, wrong one gets picked

Set *Settings → Sources → Bluetooth → Device* to the specific MAC.
The `(auto-detect)` mode picks the first eligible AVRCP player from
BlueZ's enumeration order, which isn't stable when several phones
are paired.
