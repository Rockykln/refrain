# Configuration file

What `config.toml` holds, and what each key does.

Settings live at `$XDG_CONFIG_HOME/refrain/config.toml`
(typically `~/.config/refrain/config.toml`). The settings window edits the
same file; you almost never need to touch it by hand.

```toml
[discord]
client_id = ""                     # default Application ID — paste yours here
client_id_mpris = ""               # optional per-source override (browser / Apple Music)
client_id_bluetooth = ""           # optional per-source override (Bluetooth headphones)
all_clients = false                # send the status to every running Discord client, not just the first
resolve_app_name = false           # opt-in: ask Discord what your Application ID is called, and show it in Settings

[sources]
mpris_enabled = true
bluetooth_enabled = true
bluetooth_device = ""              # empty = auto-detect, or "AA:BB:CC:DD:EE:FF"
browser_hints = "firefox,chromium,…"  # which MPRIS players count as a browser; edit if yours isn't detected

[privacy]
mode = "full"                      # "full" | "minimal" | "off"
resume_mode = "full"               # what *Resume sharing* goes back to after *Pause sharing*

[behavior]
autostart = false
notifications = false              # new installs; a config from before 0.5.3 keeps its value
cover_art = true                   # look songs up in Apple's catalog: cover, song link, length
show_buttons = true
notify_delay_ms = 0                # 0 = fire ASAP; the cover-art retry loop still waits up to ~2 s
tray_icon = "white"                # not in Settings: "white", "black", or "auto" to follow the system colour scheme

[advanced]
poll_interval_ms = 500
log_level = "INFO"
idle_grace_s = 30                  # clear status when same track plays past duration + grace; 0 disables
position_stall_s = 4               # seconds a playing track's position may stand still before Refrain stops trusting it; 0 disables
language = "system"                # "system" follows QLocale; "cs", "de", "en", "es", "fr", "it", "ja", "ko", "nl", "pl", "pt", "ru", "sv", "tr", "uk", "zh_CN" force a translation
time_format = "system"             # not in Settings: "system" follows the desktop clock, "12h" and "24h" override it
time_zone = ""                     # not in Settings: empty follows the desktop, or an IANA name like "Europe/Berlin"
hover_scroll_ms = 1500             # rest this long on a song in the Status window and its title scrolls past once; 0 = never
developer_mode = false             # local timing + usage metrics, see docs/developer-mode.md
developer_unlocked = false         # keeps the Developer switch in Settings once unlocked

[lastfm]
enabled = false                    # opt-in, alongside (never replacing) the Discord RPC
api_key = ""                       # register your own at last.fm/api/account/create
username = ""                      # display only
scrobble_now_playing = true        # also send the ephemeral "now playing" indicator
# NOTE: the Last.fm shared secret and session key are credentials and
# are deliberately NOT stored here. They live in your OS keyring
# (KWallet / GNOME Keyring), encrypted at rest — see "Last.fm" below.

[history]
enabled = true                     # the "Recently played" list; false also deletes what it stored
max_entries = 30                   # songs kept, 1–100 (Settings offers 10, 20, 30, 50, 75, 100)
window_width = 0                   # the history window's size when last closed; 0 = default
window_height = 0

[update]
auto_check = true                  # look for a newer release shortly after start
last_check_ts = 0                  # when that last happened; Refrain keeps this current
```

Per-source `client_id_*` fields let Apple Music render under one Discord
application (with the album-grid as artwork) and Bluetooth headphones under
another (with a generic Bluetooth glyph). Empty falls back to the default
`client_id`.
