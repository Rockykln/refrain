# Updates

How Refrain checks for a new version, and how the AppImage updates itself.

Refrain checks the [GitHub Releases API](https://api.github.com/repos/Rockykln/refrain/releases/latest)
on startup, at most once per day (*Settings → Updates* has the switch and
a *Check for updates now* button). When a newer version exists, Refrain
opens this dialog, and the tray menu shows an *Update available* item
that brings it back:

<p align="center">
  <picture>
    <source media="(prefers-color-scheme: light)" srcset="screenshots/update-dialog-light.png"/>
    <img src="screenshots/update-dialog.png" alt="Update-available dialog" width="560"/>
  </picture>
</p>

What the update does depends on how Refrain was installed:

- **AppImage** — downloads the new `.AppImage`, checks it against the
  release's signed checksums and replaces the running file, then asks
  you to restart.
- **pipx** — runs `pipx upgrade refrain`.
- **pip / venv** — runs `pip install --upgrade refrain`.
- **AUR** — opens a terminal running your AUR helper
  (`yay -Syu refrain` or similar), so the package manager stays in
  charge and you confirm sudo yourself. Other distro packages get a
  hint to use the package manager.
- **Source checkout** — never updated in place; `git pull` and
  `pip install -e .` yourself.

There is no published Flatpak; a self-built one gets
`flatpak update` in a terminal the same way. Details in the
[FAQ](faq.md#how-do-i-update).
