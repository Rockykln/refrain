# Settings

Every tab of the settings window, field by field.

The settings window opens from *Settings…* in the Status window. **Apply** saves to `config.toml` and keeps the window open,
**OK** saves and closes it, **Cancel** closes it without saving. Apply
stays greyed out until something differs from what is saved. Closing the
window with unsaved changes — Cancel, <kbd>Esc</kbd> or the window's close
button — asks first: *Save*, *Discard* or *Keep editing*.

A few things are saved the moment they happen, because they are results
rather than drafts: connecting or disconnecting Last.fm (connecting also
switches scrobbling on), *Reset all settings to defaults* after its
confirmation, and unlocking developer mode. Only a new language needs a
restart; Refrain says so and asks before it restarts.

*Privacy* on the General tab decides what is shared: *Full*, *Minimal*
(only “Listening to music”) or *Off*, which pauses both the Discord status
and Last.fm scrobbling. The recently played list keeps working either way.
Next to it, *Look up songs in Apple's catalog* sends artist and title to
Apple for the cover, the song link and the length; without it Discord shows
no cover and often no progress bar.

<table>
  <tr>
    <td align="center">
      <b>General</b><br/>
      <picture>
        <source media="(prefers-color-scheme: light)" srcset="screenshots/settings-general-light.png"/>
        <img src="screenshots/settings-general.png" alt="Settings — General" width="420"/>
      </picture>
      <br/><sub>Discord Application ID with the application's name beside it, privacy and the Apple catalog lookup, autostart, notifications</sub>
    </td>
    <td align="center">
      <b>Sources</b><br/>
      <picture>
        <source media="(prefers-color-scheme: light)" srcset="screenshots/settings-sources-light.png"/>
        <img src="screenshots/settings-sources.png" alt="Settings — Sources" width="420"/>
      </picture>
      <br/><sub>MPRIS / Bluetooth toggles + paired-device picker</sub>
    </td>
  </tr>
  <tr>
    <td align="center">
      <b>Last.fm</b><br/>
      <picture>
        <source media="(prefers-color-scheme: light)" srcset="screenshots/settings-lastfm-light.png"/>
        <img src="screenshots/settings-lastfm.png" alt="Settings — Last.fm" width="420"/>
      </picture>
      <br/><sub>Opt-in scrobbling, API key + secret, connect / disconnect an account</sub>
    </td>
    <td align="center">
      <b>Recently played</b><br/>
      <picture>
        <source media="(prefers-color-scheme: light)" srcset="screenshots/settings-history-light.png"/>
        <img src="screenshots/settings-history.png" alt="Settings — Recently played" width="420"/>
      </picture>
      <br/><sub>Recently played on or off, how many songs to keep</sub>
    </td>
  </tr>
  <tr>
    <td align="center">
      <b>Updates</b><br/>
      <picture>
        <source media="(prefers-color-scheme: light)" srcset="screenshots/settings-updates-light.png"/>
        <img src="screenshots/settings-updates.png" alt="Settings — Updates" width="420"/>
      </picture>
      <br/><sub>Auto-check, last-checked, manual <i>Check for updates now</i></sub>
    </td>
    <td align="center">
      <b>Advanced</b><br/>
      <picture>
        <source media="(prefers-color-scheme: light)" srcset="screenshots/settings-advanced-light.png"/>
        <img src="screenshots/settings-advanced.png" alt="Settings — Advanced" width="420"/>
      </picture>
      <br/><sub>Poll interval, notification delay, language, log level, restart, reset, uninstall</sub>
    </td>
  </tr>
  <tr>
    <td align="center">
      <b>Legal</b><br/>
      <picture>
        <source media="(prefers-color-scheme: light)" srcset="screenshots/legal-light.png"/>
        <img src="screenshots/legal.png" alt="Legal notice" width="420"/>
      </picture>
      <br/><sub>License, trademark and affiliation notices — the <i>Legal</i> button in the footer</sub>
    </td>
    <td></td>
  </tr>
</table>
