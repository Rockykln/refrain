# Screenshots

The README references screenshots from this directory. To regenerate them:

| File                       | What to capture                                      |
|----------------------------|------------------------------------------------------|
| `tray-menu.png`            | Right-click the Refrain tray icon while a track plays. Captures title / artist / progress / Discord-status info rows + Previous / Play-Pause / Next / Settings / Live log / Restart / Quit, all with theme icons. |
| `settings-general.png`     | Settings window → *General* tab, showing Discord client ID, Privacy combo, autostart, notifications, cover art, buttons. |
| `settings-sources.png`     | Settings window → *Sources* tab, showing MPRIS toggle, Bluetooth toggle, and the paired-device dropdown populated. |
| `settings-updates.png`     | Settings window → *Updates* tab. Auto-check on, last-checked timestamp visible, the "Latest release notes" pane populated from CHANGELOG. |
| `settings-advanced.png`    | Settings window → *Advanced* tab, showing the Language dropdown (11 entries: System default + 10 native endonyms), poll interval, log level, cover cache size, idle grace, and the Reset-all-settings button. |
| `update-dialog.png`        | The update-available dialog (only appears when a new version is found). Trigger it locally by editing `__version__` to something older and clicking *Check for updates now*. |
| `discord-rpc.png`          | Cropped section of your Discord profile showing the "Listening to" card with cover art + elapsed/total timer. |
| `notification.png`         | A desktop notification fired by Refrain on track change. KDE Plasma's notification with the album cover at left. |
| `live-log.png`             | The live-log window, opened via tray → *Live log…*, with at least 10 lines of streamed log. |
| `welcome.png`              | First-run welcome wizard — shown when no `client_id` is configured yet. Diagnostics panel + Discord-app guidance. |

## How to capture on KDE Plasma

```sh
# Spectacle, full screen, with delay so menus stay open.
# Run from the repo root; <name> is one of the rows in the table above.
spectacle -bnod 5 -o docs/screenshots/<name>.png
```

For tray-menu screenshots, use spectacle's *Rectangular Region* mode with a
3-5 second delay so you can right-click the tray icon and the menu remains
visible when the screenshot fires.

## Image conventions

- **Format**: PNG, lossless.
- **Width**: 800 - 1200 px (HiDPI sources are fine; the README shrinks them).
- **No real personal data** in screenshots: log lines, MAC addresses,
  Discord usernames. Use a throwaway Discord profile or scrub IDs in
  `gimp` before committing.
- **Light or dark theme**: pick one and stay consistent across the set.
  Refrain itself follows the system theme, so screenshots from KDE's
  Breeze Dark are the canonical look.
