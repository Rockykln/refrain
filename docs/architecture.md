# Architecture

Refrain is one process with two threads and one external IPC socket.

```
┌─────────────────────────────────────────────────────────────────┐
│  refrain process                                                │
│                                                                 │
│  Main thread (Qt event loop)                                    │
│  ├─ QApplication                                                │
│  ├─ TrayIcon (QSystemTrayIcon)         ←─── status / track      │
│  ├─ SettingsWindow (QDialog, hidden after Apply)                │
│  ├─ LogWindow (QDialog, on-demand)                              │
│  ├─ WelcomeDialog (first-run only)                              │
│  ├─ UpdateOrchestrator                                          │
│  └─ UpdateDialog                                                │
│                                                                 │
│  Worker thread (Qt event loop, QThread)                         │
│  ├─ DaemonWorker                                                │
│  │    ├─ MPRISSource         ──► Session DBus (read)            │
│  │    ├─ BluetoothSource     ──► System DBus (org.bluez)        │
│  │    ├─ CoverFetcher        ──► iTunes Search (HTTPS)          │
│  │    ├─ DiscordRPC          ──► Discord IPC socket             │
│  │    └─ MPRISServer         ──► Session DBus (publish own)     │
│  └─ Timer (QTimer, 500 ms default poll, configurable)           │
│                                                                 │
│  GLib thread (only when PyGObject is available)                 │
│  └─ GLib.MainLoop — pumps dbus-python signals so Plasma's panel │
│     PlayPause / Next / Previous reach our MPRISServer methods.  │
└─────────────────────────────────────────────────────────────────┘
```

## Why a worker thread?

The polling loop (default 500 ms, configurable via
`advanced.poll_interval_ms`) reads MPRIS / BlueZ properties via D-Bus.
Some of those calls block briefly. iTunes Search lookups can take up
to 5 s. Discord IPC writes can stall when Discord is restarting.
Doing any of that on the GUI thread would make the tray icon and
settings window freeze every poll.

The worker thread runs a Qt event loop (driven by `QTimer`, *not* a
`while/sleep` loop — that detail matters: a sleep loop blocks the event
loop and prevents cross-thread slot invocations like Play/Pause from the
tray menu from being delivered).

## Cross-thread message flow

| From → To              | Mechanism                                  |
|------------------------|--------------------------------------------|
| Tray → DaemonWorker    | Qt signal → `@Slot` (auto-queued)          |
| DaemonWorker → Tray    | Qt signal → main-thread slot               |
| SettingsWindow → DW    | `applied(Config)` → `update_config` slot   |
| Daemon shutdown        | `QMetaObject.invokeMethod(BlockingQueued)` |

Nothing else is shared; `Config` is treated as immutable after `Apply`.

## Threads created elsewhere

- **CoverFetcher** owns a 1-worker `ThreadPoolExecutor` for the iTunes
  lookup + image download. Results are cached in-memory + on-disk; the
  daemon polls the cache via `get()` / `get_local_path()`.
- **UpdateOrchestrator** spawns a one-shot QThread for the GitHub Releases
  API call so the GUI stays responsive.

## Single-instance lock

Refrain claims the well-known D-Bus name `io.github.Rockykln.Refrain` on
the session bus at startup. Subsequent invocations fail to acquire the
name and exit. No lockfile in `/tmp`.

## File system surface

| Purpose            | Path                                         |
|--------------------|----------------------------------------------|
| Config             | `$XDG_CONFIG_HOME/refrain/config.toml`       |
| Logs (rotating)    | `$XDG_STATE_HOME/refrain/refrain.log{,.1,.2,.3}` |
| Cover URL cache    | `$XDG_CACHE_HOME/refrain/<key>.txt`          |
| Cover image cache  | `$XDG_CACHE_HOME/refrain/<urlhash>.jpg` (200-entry cap) |
| Autostart entry    | `$XDG_CONFIG_HOME/autostart/refrain.desktop` (only when enabled) |

The user-installed desktop file (via `--install-desktop`) goes to
`~/.local/share/applications/refrain.desktop`. Distro packages install
to `/usr/share/applications/refrain.desktop` instead.

## D-Bus interfaces consumed

| Interface                               | Bus    | Used for                          |
|-----------------------------------------|--------|-----------------------------------|
| `org.mpris.MediaPlayer2`                | session| Player identity / desktop entry   |
| `org.mpris.MediaPlayer2.Player`         | session| Track metadata + Play/Next/Prev   |
| `org.bluez.MediaPlayer1`                | system | AVRCP track + Play/Pause/Next/Prev|
| `org.bluez.Device1` (via ObjectManager) | system | Paired-device enumeration         |
| `org.freedesktop.DBus.NameHasOwner`     | system | Fast-fail check before activating `org.bluez` (avoids a 25 s service-activation timeout on hosts without bluez)|

## D-Bus interfaces published

Refrain publishes two well-known names on the **session** bus:

| Bus name                              | Purpose                                        |
|---------------------------------------|------------------------------------------------|
| `io.github.Rockykln.Refrain`          | Single-instance lock. No object path; just the name reservation. |
| `org.mpris.MediaPlayer2.refrain`      | Refrain itself as an MPRIS player. KDE Plasma's panel media controls applet, KDE Connect, GNOME Shell etc. drive the same Play/Pause/Next/Previous as the tray, and render the same track Discord renders. Implements the standard MPRIS root + Player interfaces. |

The MPRIS-server publication needs PyGObject (`gi`) for a GLib main loop
to pump dbus-python signal dispatch. When PyGObject isn't installed,
Refrain logs a warning at startup and falls back to read-only mode —
the rest of the app works, but Plasma's panel can't drive playback.

## Discord IPC discovery

The standard path is `$XDG_RUNTIME_DIR/discord-ipc-N` (N=0..9). Snap
and Flatpak Discord builds put their socket inside the sandbox tree
instead (`$XDG_RUNTIME_DIR/app/com.discordapp.Discord/discord-ipc-N`,
`~/.var/app/com.discordapp.Discord/config/discord/discord-ipc-N`,
`~/snap/discord/current/.config/discord/discord-ipc-N`).
`DiscordRPC._bridge_sandboxed_ipc_socket` symlinks the first sandbox
socket it finds into `$XDG_RUNTIME_DIR` before each connect attempt,
and sweeps stale symlinks left behind by previously-uninstalled
Discord builds.
