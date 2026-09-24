# Developer mode

Developer mode makes Refrain measure itself: how long each step of a poll
takes, how long startup takes, how long requests to Apple, Last.fm, GitHub
and Discord take, how much memory it uses, and how its windows are used.
It is meant for finding slow spots and awkward paths through the app.

It is off by default. While it is off, nothing is measured and no file is
written.

## Turning it on and off

1. Open *Settings*.
2. Click the version number at the bottom right (*Refrain v…*) six times
   in a row.
3. A dialog explains what is measured. Click *Turn on*.

Once on, *Settings → Advanced → Developer* has a *Developer mode* switch;
untick it and click *Apply* to turn it off. The switch stays visible for the
rest of that session. After a restart with developer mode off, it is hidden
again until the next six clicks.

In `config.toml` the setting is:

```toml
[advanced]
developer_mode = false
```

While developer mode is on, the tray menu shows a *Developer mode* line;
clicking it opens the results.

## Where to see the results

*Live log* window → *Developer* tab (the tab only exists while developer mode
is on). It shows, refreshed every second while the tab is visible:

- **Poll stages (ms):** last, median, 95th percentile and maximum over the
  last 600 polls for each step: reading the player (`source`), working out
  the position (`position`), updating the tray (`tray`), Discord
  (`discord`), *Recently played* (`history`), Last.fm (`scrobble`), cover
  lookup (`covers`), the published MPRIS player (`mpris`) and the whole
  poll (`total`).
- **Startup:** milliseconds after the process started until the config was
  loaded, Qt was up, the tray icon was shown, the daemon started, the first
  poll finished and Discord was connected. Turning developer mode on
  while Refrain is already running leaves the early steps unmeasured; the
  table then says so instead of showing misleadingly large numbers.
- **Network (ms):** calls, failures, last and longest duration per service
  (`itunes`, `cover_image`, `lastfm`, `github`, `discord_api`).
- **Interactions:** how often windows were opened, tabs switched and buttons
  clicked.
- **Text that does not fit:** every window is checked when it opens, is
  resized, switches tab or changes language. A label, button, tab or group
  title that is cut off, a scroll bar at the window's normal size, or two
  widgets on top of each other shows here with the widget's internal name and
  how many pixels it is short, and is logged once as a warning
  (`UI: Settings › General › … needs 105 px more`). Song titles in the
  history are shortened on purpose and never reported.
- **Memory:** resident memory, threads and open files, sampled once a minute.

*Export…* saves all of it as one JSON file wherever you choose.

*System report…* shows what Refrain runs on — version and install type,
distribution, kernel, Python, PySide6 and Qt, desktop session, and which
settings are on — with a *Copy* button, ready to paste into a bug report.
It also lists every MPRIS player on the session bus, one line each:
its bus name, its `Identity`, whether it reports a page address at all
and whether that address is Apple Music — never the address itself and
never a song title, so a report about a browser that isn't recognised
needs no follow-up questions about what was playing. The report names no
songs, no credentials, no Bluetooth address and no Application ID, only
whether each is set; home directories are written as `~` and the time
zone is left out.

## What is stored

Records go to `$XDG_STATE_HOME/refrain/dev-metrics.jsonl` (normally
`~/.local/state/refrain/dev-metrics.jsonl`), one JSON object per line,
written owner-only (`0600`). At 5 MB the file is renamed to
`dev-metrics.jsonl.1` (replacing an older one) and a new file is started, so
it never takes more than about 10 MB.

Record types:

| `type` | Written | Fields |
|---|---|---|
| `session` | when developer mode is turned on or off | `state`, Refrain version, process ID |
| `startup` | once per startup step | `step`, `ms` since the process started |
| `polls` | once a minute | per stage: `n`, `median_ms`, `p95_ms`, `max_ms` |
| `resources` | once a minute | `rss_kb`, `threads`, `open_files` |
| `network` | after every request | `service`, `ms`, `ok`, and the error's class name if it failed |
| `window_build` | when the Settings or *Recently played* window is built | `window`, `ms` |
| `layout` | the first time a window shows text that does not fit | `window`, `message` |
| `interaction` | on every counted action | `kind`, `target`, and for some kinds `open_s`, `s_since_open`, `s_since_start`, `used`, `back` |

Interaction kinds: `window_opened`, `window_closed` (with how long it was
open), `tab_switched` (tab number, and whether it went back to an earlier
tab), `click` (the button's internal name, or its position in the window),
`dialog_cancelled`, `applied` (seconds from opening Settings to *Apply*),
`first_click` (seconds from start to the first click), and
`start_window_closed` (whether the window shown at startup was used, and
whether it was closed unused within 10 seconds).

Every line also carries `ts`, the Unix time it was written.

## Privacy

- **Nothing is ever sent.** The measuring code contains no network code at
  all, and a test in the test suite checks that it cannot open a connection.
- **No song titles, artists or albums**, no URLs, no search terms and
  nothing you type. Buttons are recorded by their internal name, never by
  their label; a failed request only by the kind of error.
- The file stays on your computer until you delete it. `refrain --uninstall`
  removes it along with everything else in `~/.local/state/refrain`.

## Cost

Measuring costs a little: a few microseconds per poll, an event filter on
Refrain's windows and a timer once a minute. With developer mode off, the
measuring calls do nothing and no timer or event filter exists.
