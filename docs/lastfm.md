# Last.fm scrobbling quick-start

Refrain can scrobble what you listen to to [Last.fm](https://www.last.fm)
**alongside** the Discord Rich Presence — it's a second, independent
channel, never a replacement. It's **opt-in** and off by default:
scrobbling broadcasts your listening history to a third party, so you
turn it on explicitly.

Scrobbles are written to a local on-disk queue the moment a track
qualifies, so an offline window, a Last.fm outage, or quitting Refrain
mid-song never loses them — they're submitted on the next opportunity.

## 1. Register a Last.fm API account

Same "bring your own credentials" model as the Discord Application ID:
each user registers their own free API account (takes a minute).

1. Open <https://www.last.fm/api/account/create> (the
   *Settings → Last.fm → Create API account* button takes you there).
2. Fill in any name (e.g. "Refrain") and contact email. Callback URL
   and homepage can be left blank.
3. Submit. You'll land on a page showing an **API key** and a
   **shared secret**.

## 2. Enter the credentials

Open the *Settings → **Last.fm*** tab:

1. Tick **Enable Last.fm scrobbling**.
2. Paste the **API key** and **shared secret**.
3. Click **Connect…**.

## 3. Authorise in the browser

Connect opens a Last.fm page in your browser asking you to grant
Refrain access to your account. Approve it, return to Refrain, and
click **OK** on the "Authorise Refrain" dialog.

The *Account* line then reads **Connected as &lt;your-username&gt;**.
Click **Apply** to save. Scrobbling starts on the next track — no
restart needed.

## What gets scrobbled

A track is scrobbled once you've **played at least half of it, or four
minutes — whichever comes first** (Last.fm's standard rule), and only
if it's longer than 30 seconds. Pausing doesn't count toward that time;
seeking around doesn't inflate it. Apple Music preview clips (under
30 s) are never scrobbled.

If **“Also send a Now playing update”** is ticked (default), Refrain
also sets the ephemeral "now playing" indicator on your Last.fm
profile — the equivalent of the Discord status — as each track starts.

## Privacy

- Scrobbling is **off until you enable it**.
- Setting *Settings → General → Privacy* to **Off** silences Last.fm
  too — it's the global "no external broadcasting" kill switch (it
  also disables the Discord status). `Full` and `Minimal` don't affect
  scrobbling (it's its own opt-in).
- Nothing but the track's artist / title / album / timestamp goes to
  Last.fm, over HTTPS, only when scrobbling is enabled and connected.

### Where credentials are stored

The Last.fm **shared secret** and **session token** are the only real
credentials Refrain holds. They are stored in your **OS keyring**
(KWallet / GNOME Keyring) — encrypted at rest, unlocked with your
login session — and are **never written to `config.toml`** (which is
itself saved owner-only, `0600`). On a system with no Secret Service
keyring they fall back to a `0600` (owner-only) file
`$XDG_CONFIG_HOME/refrain/secrets.json`, clearly separate from the
config. The values only ever leave your machine to Last.fm over
HTTPS — that transfer *is* scrobbling; nothing else reads or forwards
them, and they are never logged.

## Troubleshooting

**The *Account* line says "Not connected" after I clicked OK.**
The authorisation wasn't completed in the browser before you clicked
OK, or the API key/secret are wrong. Re-check the key + secret and
click **Connect…** again.

**Live log shows "Last.fm session invalid — reconnect in Settings".**
The session was revoked on last.fm (or the API key changed). Open
*Settings → Last.fm*, click **Connect…**, re-authorise, **Apply**.
Queued scrobbles are kept and submit automatically once you reconnect.

**Scrobbles aren't showing up on my profile.**
- Confirm the *Account* line shows your username and you clicked
  **Apply**.
- Tracks under 30 s, or that you skipped before the half-way / 4-minute
  mark, are intentionally not scrobbled.
- If you were offline, they're queued
  (`$XDG_STATE_HOME/refrain/scrobble_queue.jsonl`) and submit on the
  next track change once the network is back.
- Open the live log (*tray → Live log…*) and watch for
  `Scrobble queued:` / `Scrobbled N queued track(s)` lines.

**Does this replace the Discord status?**
No. Discord Rich Presence and Last.fm run side by side and are
configured independently.
